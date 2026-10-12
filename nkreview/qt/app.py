"""Main window: image list · image view · cells and settings.

Flow: open a folder → Cellpose runs in the background (DAB-ROI crops, the
image on screen first) → go through the images with ←/→, discard or draw
cells, adjust the threshold → Export writes outputs/.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressDialog, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..analysis import ImageResult, analyze
from ..segment import (
    METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, has_cellpose_cache,
)
from ..state import MANUAL_ID_BASE, Review, Settings, app_support_dir, list_images, load_image
from .style import apply_look
from .view import TOOL_DRAW, TOOL_ROI, TOOL_SELECT, ImageView
from .worker import CellposeWorker

METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD-Rand)",
                 METHOD_CELLPOSE: "Nur Cellpose",
                 METHOD_THRESHOLD: "Nur OD-Schwelle (Backup)"}
VIEWS = (("rgb", "Original"), ("dab", "DAB-Kanal"), ("od", "Gesamt-OD"))
OD_DISPLAY_MAX = 0.6          # fixed scale so images stay comparable

COLUMNS = [("zelle", "#"), ("flaeche_px", "Fläche px"), ("dab_od_mean", "DAB mean"),
           ("dab_iod", "DAB IOD"), ("gesamt_od_mean", "Gesamt mean"), ("hinweis", "")]

ST_WAIT, ST_RUN, ST_SEG, ST_DONE, ST_ERR = "○", "◐", "●", "✔", "✕"


def _last_folder_file() -> Path:
    return app_support_dir() / "app.json"


class MainWindow(QMainWindow):
    def __init__(self, folder: Path | None = None):
        super().__init__()
        self.setWindowTitle(f"NKReview {__version__}")
        self.resize(1500, 900)
        self.folder: Path | None = None
        self.settings = Settings()
        self.images: list[Path] = []
        self.idx = -1
        self.img: np.ndarray | None = None
        self.review = Review()
        self.result: ImageResult | None = None
        self.selected = 0
        self.view_mode = "rgb"
        self.undo_stack: list[Review] = []
        self.running: set[str] = set()
        self.failed: dict[str, str] = {}

        self.worker = CellposeWorker()
        self.worker.started.connect(self._cp_started)
        self.worker.finished.connect(self._cp_finished)
        self.worker.failed.connect(self._cp_failed)
        self.worker.idle.connect(self._update_status)

        self._build_ui()
        self._build_shortcuts()
        if folder is None:
            try:
                last = json.loads(_last_folder_file().read_text("utf-8")).get("folder")
                folder = Path(last) if last and Path(last).is_dir() else None
            except (OSError, ValueError):
                folder = None
        if folder is not None:
            QTimer.singleShot(0, lambda: self.open_folder(folder))

    # ── UI ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        tb = QFrame(objectName="toolbar")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 6, 10, 6)
        b_open = QPushButton("📂 Ordner öffnen"); b_open.clicked.connect(self.choose_folder)
        tl.addWidget(b_open)
        seg = QFrame(objectName="segment"); sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        self.tool_group = QButtonGroup(self)
        for i, (tool, text) in enumerate(((TOOL_SELECT, "1  Auswählen"), (TOOL_DRAW, "2  Zelle zeichnen"),
                                          (TOOL_ROI, "3  Hintergrund-ROI"))):
            b = QPushButton(text); b.setCheckable(True)
            b.setProperty("first", i == 0); b.setProperty("last", i == 2)
            b.clicked.connect(lambda _=False, t=tool: self.set_tool(t))
            self.tool_group.addButton(b); sl.addWidget(b)
            b.setProperty("tool", tool)
        self.tool_group.buttons()[0].setChecked(True)
        tl.addWidget(seg)
        tl.addSpacing(12)
        self.view_combo = QComboBox()
        for key, text in VIEWS:
            self.view_combo.addItem(text, key)
        self.view_combo.currentIndexChanged.connect(lambda _: self.set_view_mode(self.view_combo.currentData()))
        tl.addWidget(QLabel("Ansicht (D):")); tl.addWidget(self.view_combo)
        self.cb_contours = QCheckBox("Konturen (O)"); self.cb_contours.setChecked(True)
        self.cb_contours.toggled.connect(lambda on: self.view.set_contours_visible(on))
        tl.addWidget(self.cb_contours)
        tl.addStretch(1)
        self.status = QLabel(""); self.status.setObjectName("caption")
        tl.addWidget(self.status)
        b_exp = QPushButton("⤓ Export", objectName="export"); b_exp.clicked.connect(self.export)
        tl.addWidget(b_exp)

        # left: images
        self.list = QListWidget(objectName="sidebar")
        self.list.currentRowChanged.connect(self._list_row)
        self.list.setFocusPolicy(Qt.NoFocus)

        # centre
        self.view = ImageView()
        self.view.cellClicked.connect(self.select_cell)
        self.view.polygonDrawn.connect(self.add_manual)
        self.view.roiDrawn.connect(self.set_roi)
        self.banner = QLabel("", objectName="banner"); self.banner.setWordWrap(True)
        centre = QWidget(); cl = QVBoxLayout(centre); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.view, 1); cl.addWidget(self.banner)

        # right: image settings + cells
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(8, 8, 8, 8)
        form = QFormLayout()
        self.method_combo = QComboBox()
        for k, v in METHOD_LABELS.items():
            self.method_combo.addItem(v, k)
        self.method_combo.activated.connect(lambda _: self._method_changed())
        self.thr_spin = QDoubleSpinBox(); self.thr_spin.setRange(0.005, 1.0)
        self.thr_spin.setSingleStep(0.005); self.thr_spin.setDecimals(3)
        self.thr_spin.setKeyboardTracking(False)
        self.thr_spin.valueChanged.connect(lambda _: self._threshold_changed())
        self.cb_only_this = QCheckBox("nur für dieses Bild")
        self.cb_only_this.setToolTip("Aus: Methode und Schwelle gelten für den ganzen Ordner.")
        form.addRow("Kontur:", self.method_combo)
        form.addRow("OD-Schwelle:", self.thr_spin)
        form.addRow("", self.cb_only_this)
        self.i0_label = QLabel("–"); form.addRow("Hintergrund I₀:", self.i0_label)
        rl.addLayout(form)
        row = QHBoxLayout()
        self.b_roi_reset = QPushButton("ROI entfernen"); self.b_roi_reset.clicked.connect(lambda: self.set_roi(None))
        self.b_rerun = QPushButton("Cellpose neu"); self.b_rerun.clicked.connect(self.rerun_cellpose)
        row.addWidget(self.b_roi_reset); row.addWidget(self.b_rerun)
        rl.addLayout(row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.itemSelectionChanged.connect(self._table_selection)
        rl.addWidget(QLabel("Zellen in diesem Bild:"))
        rl.addWidget(self.table, 1)
        self.detail = QLabel(""); self.detail.setWordWrap(True)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        rl.addWidget(self.detail)
        row2 = QHBoxLayout()
        self.b_discard = QPushButton("Verwerfen (X)"); self.b_discard.clicked.connect(self.toggle_discard)
        self.b_delete = QPushButton("Gezeichnete löschen"); self.b_delete.clicked.connect(self.delete_manual)
        row2.addWidget(self.b_discard); row2.addWidget(self.b_delete)
        rl.addLayout(row2)
        self.b_done = QPushButton("✔ Bild geprüft (Enter)"); self.b_done.setProperty("class", "primary")
        self.b_done.clicked.connect(self.toggle_reviewed)
        rl.addWidget(self.b_done)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.list); split.addWidget(centre); split.addWidget(right)
        split.setSizes([260, 900, 380])
        split.setStretchFactor(1, 1)
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        bl.addWidget(tb); bl.addWidget(split, 1)
        self.setCentralWidget(body)
        self._set_enabled(False)

    def _build_shortcuts(self) -> None:
        keys = {
            Qt.Key_Right: lambda: self.go(+1), Qt.Key_Left: lambda: self.go(-1),
            Qt.Key_1: lambda: self.set_tool(TOOL_SELECT), Qt.Key_2: lambda: self.set_tool(TOOL_DRAW),
            Qt.Key_3: lambda: self.set_tool(TOOL_ROI),
            Qt.Key_X: self.toggle_discard, Qt.Key_Delete: self.toggle_discard,
            Qt.Key_Backspace: self.toggle_discard,
            Qt.Key_D: self.cycle_view, Qt.Key_O: lambda: self.cb_contours.toggle(),
            Qt.Key_Return: self.toggle_reviewed, Qt.Key_Enter: self.toggle_reviewed,
            Qt.Key_F: lambda: self.view.fit(), Qt.Key_Escape: lambda: self.select_cell(0),
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence.Undo, self, activated=self.undo)
        QShortcut(QKeySequence.Open, self, activated=self.choose_folder)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.method_combo, self.thr_spin, self.cb_only_this, self.b_roi_reset,
                  self.b_rerun, self.b_discard, self.b_delete, self.b_done):
            w.setEnabled(on)

    # ── folder / images ─────────────────────────────────────
    def choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Bildordner öffnen",
                                             str(self.folder or Path.home()))
        if d:
            self.open_folder(Path(d))

    def open_folder(self, folder: Path) -> None:
        folder = Path(folder).resolve()
        images = list_images(folder)
        if not images:
            QMessageBox.information(self, "NKReview", f"Keine Bilder in\n{folder}")
            return
        self.folder, self.images = folder, images
        self.settings = Settings.load(folder)
        self.settings.save(folder)            # the folder always documents what was used
        self.failed.clear(); self.running.clear()
        try:
            _last_folder_file().write_text(json.dumps({"folder": str(folder)}), "utf-8")
        except OSError:
            pass
        self.setWindowTitle(f"NKReview {__version__} — {folder.name}")
        self.list.blockSignals(True)
        self.list.clear()
        for p in images:
            self.list.addItem(QListWidgetItem(p.relative_to(folder).as_posix()))
        self.list.blockSignals(False)
        for i in range(len(images)):
            self._refresh_list_item(i)
        self.worker.stain_vectors = self.settings.stain_vectors
        self.worker.set_queue(folder, images, self.settings.cellpose)
        self.list.setCurrentRow(0)

    def _status_of(self, i: int) -> str:
        p = self.images[i]
        if str(p) in self.failed:
            return ST_ERR
        if str(p) in self.running:
            return ST_RUN
        if Review.load(self.folder, p).reviewed:
            return ST_DONE
        return ST_SEG if has_cellpose_cache(self.folder, p, self.settings.cellpose) else ST_WAIT

    def _refresh_list_item(self, i: int) -> None:
        it = self.list.item(i)
        st = self._status_of(i)
        it.setText(f"{st}  {self.images[i].relative_to(self.folder).as_posix()}")
        if st == ST_ERR:
            it.setToolTip(self.failed[str(self.images[i])])

    def _list_row(self, row: int) -> None:
        if 0 <= row < len(self.images) and row != self.idx:
            self.load(row)

    def go(self, step: int) -> None:
        if self.images:
            self.list.setCurrentRow(max(0, min(len(self.images) - 1, self.idx + step)))

    def load(self, i: int) -> None:
        self.idx = i
        p = self.images[i]
        self.img = load_image(p)
        self.review = Review.load(self.folder, p)
        self.undo_stack.clear()
        self.selected = 0
        self.worker.prioritize(p)
        self._set_enabled(True)
        self._recompute(fit=True)

    # ── analysis / redraw ───────────────────────────────────
    def _recompute(self, fit: bool = False) -> None:
        if self.img is None:
            return
        p = self.images[self.idx]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = analyze(self.folder, p, self.img, self.settings, self.review)
        finally:
            QApplication.restoreOverrideCursor()
        self._render_base(fit)
        self.view.set_roi(self.review.roi)
        self.view.set_cells(self.result.labels, self.result.rows, self.selected)
        self._sync_controls()
        self._fill_table()
        self._update_detail()
        self._update_status()

    def _render_base(self, fit: bool = False) -> None:
        if self.view_mode == "rgb" or self.result is None:
            rgb = self.img
        else:
            ch = self.result.stains[..., 1] if self.view_mode == "dab" else self.result.tod
            v = np.clip(ch / OD_DISPLAY_MAX, 0, 1)
            if self.view_mode == "dab":
                g = (255 * (1 - v)).astype(np.uint8)
                rgb = np.stack([g, g, g], axis=-1)
            else:
                from matplotlib import colormaps
                rgb = (colormaps["magma"](1 - v)[..., :3] * 255).astype(np.uint8)
        self.view.set_pixmap(rgb, fit=fit)

    def _sync_controls(self) -> None:
        r = self.result
        own = self.review.method is not None or self.review.threshold is not None
        for w, fn in ((self.method_combo, lambda: self.method_combo.setCurrentIndex(
                          self.method_combo.findData(self.review.method or self.settings.method))),
                      (self.thr_spin, lambda: self.thr_spin.setValue(r.threshold)),
                      (self.cb_only_this, lambda: self.cb_only_this.setChecked(own))):
            w.blockSignals(True); fn(); w.blockSignals(False)
        i0 = r.background
        self.i0_label.setText(f"R {i0.i0[0]:.0f} · G {i0.i0[1]:.0f} · B {i0.i0[2]:.0f}  ({'ROI' if i0.source == 'roi' else 'auto'})")
        self.banner.setText(r.note)
        self.b_done.setText("↺ Prüfung zurücknehmen (Enter)" if self.review.reviewed else "✔ Bild geprüft (Enter)")

    def _fill_table(self) -> None:
        rows = self.result.rows
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        muted = QColor("#8A8F99")
        for i, r in enumerate(rows):
            hint = []
            if r["manuell"]: hint.append("gezeichnet")
            if r["randzelle"]: hint.append("Bildrand")
            if r["ring_warnung"]: hint.append(f"I₀ lokal {r['ring_i0_abweichung_pct']:.1f}%")
            if r["verworfen"]: hint.append("verworfen")
            vals = {**r, "hinweis": ", ".join(hint)}
            for c, (key, _) in enumerate(COLUMNS):
                v = vals[key]
                text = f"{v:.4f}" if key.endswith("mean") else f"{v:.1f}" if key.endswith("iod") else str(v)
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, r["zelle"])
                if r["verworfen"]:
                    it.setForeground(muted)
                    f = it.font(); f.setStrikeOut(c < len(COLUMNS) - 1); it.setFont(f)
                self.table.setItem(i, c, it)
            if r["zelle"] == self.selected:
                self.table.selectRow(i)
        if not any(r["zelle"] == self.selected for r in rows):
            self.table.clearSelection()
        self.table.blockSignals(False)

    def _row(self, cid: int) -> dict | None:
        return next((r for r in self.result.rows if r["zelle"] == cid), None) if self.result else None

    def _update_detail(self) -> None:
        r = self._row(self.selected)
        self.b_discard.setEnabled(r is not None)
        self.b_delete.setEnabled(bool(r and r["manuell"]))
        if r is None:
            n = sum(not x["verworfen"] for x in self.result.rows) if self.result else 0
            self.detail.setText(f"{n} Zelle(n) werden gezählt. Klick auf eine Zelle für Details.")
            return
        self.b_discard.setText("Zurückholen (X)" if r["verworfen"] else "Verwerfen (X)")
        ring = "–" if r["ring_i0_abweichung_pct"] is None else f"{r['ring_i0_abweichung_pct']:.1f} %"
        self.detail.setText(
            f"<b>Zelle #{r['zelle']}</b> · {r['kontur']} · {r['flaeche_px']} px<br>"
            f"DAB-OD mean <b>{r['dab_od_mean']:.4f}</b> ± {r['dab_od_sd']:.4f} (SD) · "
            f"median {r['dab_od_median']:.4f} · p95 {r['dab_od_p95']:.4f} · max {r['dab_od_max']:.4f}<br>"
            f"DAB-IOD {r['dab_iod']:.1f} · Gesamt-OD mean {r['gesamt_od_mean']:.4f} · "
            f"H-Kanal {r['kanal_h_od_mean']:.4f}<br>"
            f"Ring-I₀ weicht {ring} vom Bild-I₀ ab")

    def _update_status(self) -> None:
        if not self.images:
            self.status.setText("Ordner öffnen, um zu beginnen (⌘O)")
            return
        done = sum(Review.load(self.folder, p).reviewed for p in self.images)
        pend = self.worker.pending() + len(self.running)
        cp = f"Cellpose: {pend} offen" if pend else "Cellpose: fertig"
        self.status.setText(f"Bild {self.idx + 1}/{len(self.images)} · {done} geprüft · {cp}")

    # ── review edits ────────────────────────────────────────
    def _edit(self, fn) -> None:
        self.undo_stack.append(copy.deepcopy(self.review))
        fn(self.review)
        self.review.save(self.folder, self.images[self.idx])
        self._recompute()
        self._refresh_list_item(self.idx)

    def undo(self) -> None:
        if self.undo_stack:
            self.review = self.undo_stack.pop()
            self.review.save(self.folder, self.images[self.idx])
            self._recompute()
            self._refresh_list_item(self.idx)

    def select_cell(self, cid: int) -> None:
        self.selected = cid
        if self.result:
            self.view.restyle(self.result.rows, cid)
            self._fill_table()
            self._update_detail()

    def _table_selection(self) -> None:
        items = self.table.selectedItems()
        if items:
            cid = items[0].data(Qt.UserRole)
            self.select_cell(cid)
            r = self._row(cid)
            if r:
                self.view.centerOn(r["x"], r["y"])

    def toggle_discard(self) -> None:
        r = self._row(self.selected)
        if not r:
            return
        def fn(rv: Review):
            if r["zelle"] in rv.discarded:
                rv.discarded.remove(r["zelle"])
            else:
                rv.discarded.append(r["zelle"])
        self._edit(fn)

    def add_manual(self, pts: list) -> None:
        def fn(rv: Review):
            rv.manual.append([[round(x, 1), round(y, 1)] for x, y in pts])
        self._edit(fn)
        self.select_cell(MANUAL_ID_BASE + len(self.review.manual) - 1)

    def delete_manual(self) -> None:
        r = self._row(self.selected)
        if not (r and r["manuell"]):
            return
        k = r["zelle"] - MANUAL_ID_BASE
        def fn(rv: Review):
            rv.manual.pop(k)
            # later drawn cells shift down one id
            rv.discarded = [d if d < r["zelle"] else d - 1 for d in rv.discarded
                            if d != r["zelle"]]
        self.selected = 0
        self._edit(fn)

    def set_roi(self, roi) -> None:
        self._edit(lambda rv: setattr(rv, "roi", [round(v) for v in roi] if roi else None))
        self.set_tool(TOOL_SELECT)

    def toggle_reviewed(self) -> None:
        if self.result is None:
            return
        self._edit(lambda rv: setattr(rv, "reviewed", not rv.reviewed))
        self._update_status()

    def _method_changed(self) -> None:
        m = self.method_combo.currentData()
        if self.cb_only_this.isChecked():
            self._edit(lambda rv: setattr(rv, "method", m))
        else:
            self.settings.method = m
            self.settings.save(self.folder)
            self._recompute()

    def _threshold_changed(self) -> None:
        t = round(self.thr_spin.value(), 4)
        if self.cb_only_this.isChecked():
            self._edit(lambda rv: setattr(rv, "threshold", t))
        else:
            self.settings.threshold = t
            self.settings.save(self.folder)
            self._recompute()

    def rerun_cellpose(self) -> None:
        p = self.images[self.idx]
        self.worker.force(self.folder, p, self.settings.cellpose)

    # ── tools / view ────────────────────────────────────────
    def set_tool(self, tool: str) -> None:
        for b in self.tool_group.buttons():
            b.setChecked(b.property("tool") == tool)
        self.view.set_tool(tool)

    def set_view_mode(self, mode: str) -> None:
        self.view_mode = mode
        if self.img is not None:
            self._render_base()

    def cycle_view(self) -> None:
        keys = [k for k, _ in VIEWS]
        self.view_combo.setCurrentIndex((keys.index(self.view_mode) + 1) % len(keys))

    # ── worker callbacks ────────────────────────────────────
    def _index_of(self, path: str) -> int:
        try:
            return [str(p) for p in self.images].index(path)
        except ValueError:
            return -1

    def _cp_started(self, path: str) -> None:
        self.running.add(path)
        i = self._index_of(path)
        if i >= 0:
            self._refresh_list_item(i)
        self._update_status()

    def _cp_finished(self, path: str) -> None:
        self.running.discard(path)
        self.failed.pop(path, None)
        i = self._index_of(path)
        if i >= 0:
            self._refresh_list_item(i)
            if i == self.idx:
                self._recompute()
        self._update_status()

    def _cp_failed(self, path: str, msg: str) -> None:
        self.running.discard(path)
        self.failed[path] = msg
        i = self._index_of(path)
        if i >= 0:
            self._refresh_list_item(i)
        self._update_status()

    # ── export ──────────────────────────────────────────────
    def export(self) -> None:
        if not self.folder:
            return
        missing = [p for p in self.images if not has_cellpose_cache(self.folder, p, self.settings.cellpose)]
        if missing:
            ans = QMessageBox.question(
                self, "Export",
                f"{len(missing)} Bild(er) sind noch nicht durch Cellpose gelaufen.\n"
                "Trotzdem exportieren? Diese Bilder werden dann nur mit der OD-Schwelle "
                "gemessen (Spalte „kontur“ = threshold).")
            if ans != QMessageBox.Yes:
                return
        from ..export import export_folder
        dlg = QProgressDialog("Exportiere …", None, 0, len(self.images), self)
        dlg.setWindowModality(Qt.WindowModal); dlg.setMinimumDuration(0)

        def prog(i, n, name):
            dlg.setValue(i); dlg.setLabelText(f"Exportiere {name} ({i + 1}/{n})")
            QApplication.processEvents()
        try:
            info = export_folder(self.folder, prog)
        except Exception as e:  # noqa: BLE001
            dlg.close()
            QMessageBox.critical(self, "Export fehlgeschlagen", f"{type(e).__name__}: {e}")
            return
        dlg.setValue(len(self.images))
        notes = "\n".join(info["notes"][:15])
        QMessageBox.information(
            self, "Export fertig",
            f"{info['n_cells']} Zellen aus {info['n_images']} Bildern.\n\n{info['csv']}"
            + (f"\n\nHinweise:\n{notes}" if notes else ""))

    def closeEvent(self, e):
        self.worker.shutdown()
        super().closeEvent(e)


def unhide_qt_plugins() -> int:
    """macOS flags files inside dot-folders (.venv) as hidden, and Qt's plugin
    loader skips hidden files → no 'cocoa' platform plugin. Clear the flag on
    PySide6's own plugins. (Same fix as in ErysReview.)"""
    if sys.platform != "darwin":
        return 0
    import os
    import stat
    import PySide6
    n = 0
    for f in (Path(PySide6.__file__).parent / "Qt" / "plugins").rglob("*"):
        try:
            st = os.lstat(f)
            if st.st_flags & stat.UF_HIDDEN:
                os.chflags(f, st.st_flags & ~stat.UF_HIDDEN, follow_symlinks=False)
                n += 1
        except OSError:
            pass
    return n


def make_app(argv=None) -> QApplication:
    unhide_qt_plugins()
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setStyle("Fusion")
    apply_look(app)
    return app


def main() -> int:
    app = make_app()
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 and Path(sys.argv[1]).is_dir() else None
    win = MainWindow(folder)
    win.show()
    return app.exec()
