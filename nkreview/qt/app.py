"""Main window: images of a study · image view · cell panel.

Flow: pick or create a study → Cellpose runs in the background (the image on
screen first) → go through the images with ←/→, check each cell's contour and
I₀ fields, discard or draw cells → Export writes the study's tables.
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
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMessageBox,
    QProgressDialog, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..analysis import STATUS_KEPT, ImageResult, analyze
from ..segment import METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, has_cellpose_cache
from ..state import MANUAL_ID_BASE, Review, app_support_dir, load_image
from ..study import ImageInfo, Study, list_studies, proband_progress
from .cellpanel import CellPanel
from .style import apply_look
from .studies import StudyPicker, _clear_layout
from .view import TOOL_DRAW, TOOL_I0, TOOL_SELECT, ImageView
from .worker import CellposeWorker

METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD-Rand)",
                 METHOD_CELLPOSE: "Nur Cellpose",
                 METHOD_THRESHOLD: "Nur OD-Schwelle (Backup)"}
VIEWS = (("rgb", "Original"), ("gray", "Grau-OD"), ("dab", "DAB-Kanal"))
OD_DISPLAY_MAX = 0.4          # fixed scale so images stay comparable

COLUMNS = [("zelle", "#"), ("status", "Status"), ("od_fiji", "OD"), ("i0", "I₀"),
           ("flaeche_px", "Fläche"), ("hinweis", "")]

ST_WAIT, ST_RUN, ST_SEG, ST_DONE, ST_ERR = "○", "◐", "●", "✔", "✕"
ALL_MARKERS = ""


def _app_file() -> Path:
    return app_support_dir() / "app.json"


def _remember(**kw) -> None:
    try:
        d = json.loads(_app_file().read_text("utf-8"))
    except (OSError, ValueError):
        d = {}
    d.update(kw)
    try:
        _app_file().write_text(json.dumps(d), "utf-8")
    except OSError:
        pass


def _recall(key: str):
    try:
        return json.loads(_app_file().read_text("utf-8")).get(key)
    except (OSError, ValueError):
        return None


class MainWindow(QMainWindow):
    def __init__(self, study: Study | None = None):
        super().__init__()
        self.resize(1500, 920)
        self.study: Study | None = None
        self.images: list[ImageInfo] = []
        self.visible: list[int] = []            # indices into images, in tree order
        self.marker = ALL_MARKERS
        self.idx = -1
        self.img: np.ndarray | None = None
        self.review = Review()
        self.result: ImageResult | None = None
        self.selected = 0
        self.view_mode = "rgb"
        self.undo_stack: list[Review] = []
        self.running: set[str] = set()
        self.failed: dict[str, str] = {}
        self._items: dict[int, QTreeWidgetItem] = {}

        self.worker = CellposeWorker()
        self.worker.started.connect(self._cp_started)
        self.worker.finished.connect(self._cp_finished)
        self.worker.failed.connect(self._cp_failed)
        self.worker.idle.connect(self._update_status)

        self._build_ui()
        self._build_shortcuts()
        QTimer.singleShot(0, lambda: self._startup(study))

    def _startup(self, study: Study | None) -> None:
        if study is None:
            last = _recall("study")
            study = next((s for s in list_studies() if s.file == last), None)
        if study is None:
            self.choose_study()
        else:
            self.open_study(study)

    # ── UI ──────────────────────────────────────────────────
    def _build_ui(self) -> None:
        tb = QFrame(objectName="toolbar")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 6, 10, 6)
        self.b_study = QPushButton("Studie wählen"); self.b_study.setObjectName("study")
        self.b_study.clicked.connect(self.choose_study)
        tl.addWidget(self.b_study)
        seg = QFrame(objectName="segment"); sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        self.tool_group = QButtonGroup(self)
        for i, (tool, text) in enumerate(((TOOL_SELECT, "1  Auswählen"), (TOOL_DRAW, "2  Kontur zeichnen"),
                                          (TOOL_I0, "3  I₀-Feld setzen"))):
            b = QPushButton(text); b.setCheckable(True)
            b.setProperty("first", i == 0); b.setProperty("last", i == 2)
            b.setProperty("tool", tool)
            b.clicked.connect(lambda _=False, t=tool: self.set_tool(t))
            self.tool_group.addButton(b); sl.addWidget(b)
        self.tool_group.buttons()[0].setChecked(True)
        tl.addWidget(seg)
        tl.addSpacing(10)
        self.view_combo = QComboBox()
        for key, text in VIEWS:
            self.view_combo.addItem(text, key)
        self.view_combo.currentIndexChanged.connect(lambda _: self.set_view_mode(self.view_combo.currentData()))
        tl.addWidget(QLabel("Ansicht (D):")); tl.addWidget(self.view_combo)
        self.cb_contours = QCheckBox("Konturen (O)"); self.cb_contours.setChecked(True)
        self.cb_contours.toggled.connect(lambda on: self.view.set_contours_visible(on))
        self.cb_fields = QCheckBox("I₀-Felder (I)"); self.cb_fields.setChecked(True)
        self.cb_fields.toggled.connect(lambda on: self.view.set_fields_visible(on))
        tl.addWidget(self.cb_contours); tl.addWidget(self.cb_fields)
        tl.addStretch(1)
        self.status = QLabel(""); self.status.setObjectName("caption")
        tl.addWidget(self.status)
        b_exp = QPushButton("⤓ Export", objectName="export"); b_exp.clicked.connect(self.export)
        tl.addWidget(b_exp)

        # left: marker filter + image tree
        left = QWidget(objectName="sidebar"); ll = QVBoxLayout(left); ll.setContentsMargins(8, 8, 8, 8)
        self.chip_row = QHBoxLayout(); self.chip_row.setSpacing(6)
        chips = QWidget(); chips.setLayout(self.chip_row)
        ll.addWidget(chips)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.currentItemChanged.connect(self._tree_changed)
        ll.addWidget(self.tree, 1)
        legend = QLabel("○ wartet · ◐ Cellpose · ● segmentiert · ✔ geprüft", objectName="caption")
        ll.addWidget(legend)

        # centre
        self.view = ImageView()
        self.view.cellClicked.connect(self.select_cell)
        self.view.polygonDrawn.connect(self.add_manual)
        self.view.i0Clicked.connect(self.place_i0_field)
        self.banner = QLabel("", objectName="banner"); self.banner.setWordWrap(True)
        self.footer = QLabel("", objectName="caption")
        centre = QWidget(); cl = QVBoxLayout(centre); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.view, 1); cl.addWidget(self.banner); cl.addWidget(self.footer)

        # right: contour settings, cells, cell panel
        right_inner = QWidget(); rl = QVBoxLayout(right_inner); rl.setContentsMargins(10, 8, 10, 8); rl.setSpacing(8)
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
        self.cb_only_this.setToolTip("Aus: Methode und Schwelle gelten für die ganze Studie.")
        thr_row = QHBoxLayout(); thr_row.addWidget(self.thr_spin); thr_row.addWidget(self.cb_only_this)
        form.addRow("Kontur:", self.method_combo)
        form.addRow("OD-Schwelle:", thr_row)
        rl.addLayout(form)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMaximumHeight(170)
        self.table.itemSelectionChanged.connect(self._table_selection)
        rl.addWidget(self.table)
        self.panel = CellPanel(); rl.addWidget(self.panel)
        row2 = QHBoxLayout()
        self.b_discard = QPushButton("Verwerfen (X)"); self.b_discard.clicked.connect(self.toggle_discard)
        self.b_redraw = QPushButton("I₀-Felder neu losen (R)"); self.b_redraw.clicked.connect(self.redraw_fields)
        self.b_delete = QPushButton("Gezeichnete löschen"); self.b_delete.clicked.connect(self.delete_manual)
        row2.addWidget(self.b_discard); row2.addWidget(self.b_redraw); row2.addWidget(self.b_delete)
        rl.addLayout(row2)
        row3 = QHBoxLayout()
        self.b_rerun = QPushButton("Cellpose neu"); self.b_rerun.clicked.connect(self.rerun_cellpose)
        self.b_done = QPushButton("✔ Bild geprüft (Enter)"); self.b_done.setProperty("class", "primary")
        self.b_done.clicked.connect(self.toggle_reviewed)
        row3.addWidget(self.b_rerun); row3.addWidget(self.b_done, 1)
        rl.addLayout(row3)
        rl.addStretch(1)
        right = QScrollArea(); right.setWidgetResizable(True); right.setWidget(right_inner)
        right.setFrameShape(QFrame.NoFrame); right.setMinimumWidth(430)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(left); split.addWidget(centre); split.addWidget(right)
        split.setSizes([290, 800, 450])
        split.setStretchFactor(1, 1)
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        bl.addWidget(tb); bl.addWidget(split, 1)
        self.setCentralWidget(body)
        self._set_enabled(False)
        self.panel.show_cell(None)

    def _build_shortcuts(self) -> None:
        keys = {
            Qt.Key_Right: lambda: self.go(+1), Qt.Key_Left: lambda: self.go(-1),
            Qt.Key_1: lambda: self.set_tool(TOOL_SELECT), Qt.Key_2: lambda: self.set_tool(TOOL_DRAW),
            Qt.Key_3: lambda: self.set_tool(TOOL_I0),
            Qt.Key_X: self.toggle_discard, Qt.Key_Delete: self.toggle_discard,
            Qt.Key_Backspace: self.toggle_discard, Qt.Key_R: self.redraw_fields,
            Qt.Key_D: self.cycle_view, Qt.Key_O: lambda: self.cb_contours.toggle(),
            Qt.Key_I: lambda: self.cb_fields.toggle(),
            Qt.Key_Return: self.toggle_reviewed, Qt.Key_Enter: self.toggle_reviewed,
            Qt.Key_F: lambda: self.view.fit(), Qt.Key_Escape: lambda: self.select_cell(0),
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence.Undo, self, activated=self.undo)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.method_combo, self.thr_spin, self.cb_only_this, self.b_rerun,
                  self.b_discard, self.b_redraw, self.b_delete, self.b_done):
            w.setEnabled(on)

    # ── study ───────────────────────────────────────────────
    def choose_study(self) -> None:
        dlg = StudyPicker(self, self.study)
        if dlg.exec() == QDialog.Accepted and dlg.chosen is not None:
            self.open_study(dlg.chosen)
        elif self.study is not None:
            # settings may have been edited in the picker
            fresh = next((s for s in list_studies() if s.file == self.study.file), None)
            if fresh is not None and fresh.to_dict() != self.study.to_dict():
                self.open_study(fresh)

    def open_study(self, study: Study) -> None:
        self.study = study
        self.images = study.images()
        self.failed.clear(); self.running.clear()
        self.idx = -1
        _remember(study=study.file)
        self.setWindowTitle(f"NKReview {__version__} — {study.name}")
        self._build_chips()
        self._build_tree()
        self.worker.stain_vectors = study.settings.stain_vectors
        self.worker.set_queue([(i.folder, i.path) for i in self.images], study.settings.cellpose)
        self._update_study_button()
        if self.visible:
            self.go_to(self.visible[0])
        else:
            self.img = None; self.result = None
            self._set_enabled(False)
            self.banner.setText("Diese Studie hat keine Bilder – Ordner in den Einstellungen prüfen.")

    def _update_study_button(self) -> None:
        done, n = proband_progress(self.images)
        markers = ", ".join(self.study.markers(self.images)) or "–"
        self.b_study.setText(f"{self.study.name}   ·   {markers}   ·   {done}/{n} Probanden")

    def _build_chips(self) -> None:
        _clear_layout(self.chip_row)
        markers = self.study.markers(self.images)
        if self.marker not in markers:
            self.marker = ALL_MARKERS
        for m in markers + [ALL_MARKERS]:
            b = QPushButton(m or "alle"); b.setCheckable(True); b.setProperty("class", "chip")
            b.setChecked(m == self.marker)
            b.clicked.connect(lambda _=False, mk=m: self._set_marker(mk))
            self.chip_row.addWidget(b)
        self.chip_row.addStretch(1)

    def _set_marker(self, m: str) -> None:
        self.marker = m
        self._build_chips()
        self._build_tree()
        if self.visible and self.idx not in self.visible:
            self.go_to(self.visible[0])

    def _build_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear(); self.visible = []
        groups: dict = {}
        for i, info in enumerate(self.images):
            if self.marker and info.marker != self.marker:
                continue
            groups.setdefault((info.timepoint, info.proband), []).append(i)
        for (tp, prob), idxs in sorted(groups.items(), key=lambda kv: (kv[0][0].lower(), kv[0][1].lower())):
            grp = self.images[idxs[0]].meta.get("gruppe", "")
            head = QTreeWidgetItem([f"{tp}  ·  Proband {prob}" + (f"  ·  {grp}" if grp else ""), ""])
            f = head.font(0); f.setBold(True); head.setFont(0, f)
            head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
            self.tree.addTopLevelItem(head)
            for i in sorted(idxs, key=lambda k: (self.images[k].marker, self.images[k].path.name)):
                it = QTreeWidgetItem(["", ""])
                it.setData(0, Qt.UserRole, i)
                head.addChild(it)
                self._items[i] = it
                self.visible.append(i)
                self._refresh_item(i)
            head.setExpanded(True)
        self.tree.blockSignals(False)
        if self.idx in self._items:
            self.tree.setCurrentItem(self._items[self.idx])

    def _status_of(self, i: int) -> str:
        info = self.images[i]
        if str(info.path) in self.failed:
            return ST_ERR
        if str(info.path) in self.running:
            return ST_RUN
        if Review.load(info.folder, info.path).reviewed:
            return ST_DONE
        return ST_SEG if has_cellpose_cache(info.folder, info.path, self.study.settings.cellpose) else ST_WAIT

    def _refresh_item(self, i: int) -> None:
        it = self._items.get(i)
        if it is None:
            return
        info = self.images[i]
        st = self._status_of(i)
        it.setText(0, f"{st}  {info.path.name}")
        it.setToolTip(0, self.failed.get(str(info.path), str(info.path)))
        it.setText(1, info.marker)

    def _tree_changed(self, cur, _prev) -> None:
        if cur is None:
            return
        i = cur.data(0, Qt.UserRole)
        if i is not None and i != self.idx:
            self.load(int(i))

    def go(self, step: int) -> None:
        if not self.visible:
            return
        pos = self.visible.index(self.idx) if self.idx in self.visible else -1
        self.go_to(self.visible[max(0, min(len(self.visible) - 1, pos + step))])

    def go_to(self, i: int) -> None:
        if i in self._items:
            self.tree.setCurrentItem(self._items[i])
        else:
            self.load(i)

    def load(self, i: int) -> None:
        self.idx = i
        info = self.images[i]
        self.img = load_image(info.path)
        self.review = Review.load(info.folder, info.path)
        self.undo_stack.clear()
        self.selected = 0
        self.worker.prioritize(info.path)
        self._set_enabled(True)
        m = info.meta
        bits = [f"{k}: {m[k]}" for k in ("proband", "gruppe", "zeitpunkt", "zelltyp", "marker", "bildnr") if m.get(k)]
        self.footer.setText(f"{info.path.name}   ·   " + "  ·  ".join(bits))
        self._recompute(fit=True)

    # ── analysis / redraw ───────────────────────────────────
    def _recompute(self, fit: bool = False) -> None:
        if self.img is None:
            return
        info = self.images[self.idx]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.result = analyze(info.folder, info.path, self.img, self.study.settings, self.review)
        finally:
            QApplication.restoreOverrideCursor()
        if self.selected and self._row(self.selected) is None:
            self.selected = 0
        if not self.selected and self.result.rows:
            self.selected = self.result.rows[0]["zelle"]
        self._render_base(fit)
        self.view.set_cells(self.result.labels, self.result.rows, self.selected)
        self._draw_fields()
        self._sync_controls()
        self._fill_table()
        self._update_detail()
        self._update_status()

    def _draw_fields(self) -> None:
        fields = {cid: cm.fields for cid, cm in self.result.cells.items()
                  if self._row(cid) and self._row(cid)["status"] != "verworfen"}
        self.view.set_fields(fields, self.study.settings.i0_field_size, self.selected)

    def _render_base(self, fit: bool = False) -> None:
        if self.view_mode == "rgb" or self.result is None:
            rgb = self.img.astype(np.uint8)
        else:
            if self.view_mode == "dab":
                ch = self.result.stains[..., 1]
            else:
                i0 = float(np.median(self.result.gray))
                ch = -np.log10(np.clip(self.result.gray, 1, None) / i0)
            v = np.clip(ch / OD_DISPLAY_MAX, 0, 1)
            from matplotlib import colormaps
            if self.view_mode == "dab":
                g = (255 * (1 - v)).astype(np.uint8)
                rgb = np.stack([g, g, g], axis=-1)
            else:
                rgb = (colormaps["magma_r"](v)[..., :3] * 255).astype(np.uint8)
        self.view.set_pixmap(rgb, fit=fit)

    def _sync_controls(self) -> None:
        r = self.result
        own = self.review.method is not None or self.review.threshold is not None
        for w, fn in ((self.method_combo, lambda: self.method_combo.setCurrentIndex(
                          self.method_combo.findData(self.review.method or self.study.settings.method))),
                      (self.thr_spin, lambda: self.thr_spin.setValue(r.threshold)),
                      (self.cb_only_this, lambda: self.cb_only_this.setChecked(own))):
            w.blockSignals(True); fn(); w.blockSignals(False)
        self.banner.setText(r.note)
        self.b_done.setText("↺ Prüfung zurücknehmen (Enter)" if self.review.reviewed else "✔ Bild geprüft (Enter)")

    def _fill_table(self) -> None:
        rows = self.result.rows
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        colors = {"verworfen": QColor("#8A8F99"), "ausgeschlossen": QColor("#975E0C")}
        for i, r in enumerate(rows):
            hint = []
            if r["manuell"]: hint.append("gezeichnet")
            if r["randzelle"]: hint.append("Bildrand")
            if r["debris_verdacht"]: hint.append("Debris?")
            if r["grund"] and r["status"] == "ausgeschlossen": hint.append(r["grund"])
            vals = {**r, "hinweis": ", ".join(hint)}
            for c, (key, _) in enumerate(COLUMNS):
                v = vals[key]
                if v is None:
                    text = "–"
                elif key == "od_fiji":
                    text = f"{v:.4f}"
                elif key == "i0":
                    text = f"{v:.1f}"
                else:
                    text = str(v)
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, r["zelle"])
                if r["status"] in colors:
                    it.setForeground(colors[r["status"]])
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
        self.b_redraw.setEnabled(r is not None)
        self.b_delete.setEnabled(bool(r and r["manuell"]))
        if r is None:
            self.panel.show_cell(None)
            return
        self.b_discard.setText("Zurückholen (X)" if r["status"] == "verworfen" else "Verwerfen (X)")
        self.panel.show_cell(r, self.result.cells.get(r["zelle"]), self.result.gray,
                             self.result.labels, self.study.settings)

    def _update_status(self) -> None:
        if not self.images:
            self.status.setText("")
            return
        pend = self.worker.pending() + len(self.running)
        cp = f"Cellpose: {pend} offen" if pend else "Cellpose: fertig"
        pos = self.visible.index(self.idx) + 1 if self.idx in self.visible else 0
        kept = sum(r["status"] == STATUS_KEPT for r in self.result.rows) if self.result else 0
        self.status.setText(f"Bild {pos}/{len(self.visible)} · {kept} Zelle(n) gewertet · {cp}")

    # ── review edits ────────────────────────────────────────
    def _edit(self, fn) -> None:
        info = self.images[self.idx]
        self.undo_stack.append(copy.deepcopy(self.review))
        fn(self.review)
        self.review.save(info.folder, info.path)
        self._recompute()
        self._refresh_item(self.idx)

    def undo(self) -> None:
        if self.undo_stack:
            info = self.images[self.idx]
            self.review = self.undo_stack.pop()
            self.review.save(info.folder, info.path)
            self._recompute()
            self._refresh_item(self.idx)

    def select_cell(self, cid: int) -> None:
        self.selected = cid
        if self.result:
            self.view.restyle(self.result.rows, cid)
            self._draw_fields()
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

    def redraw_fields(self) -> None:
        r = self._row(self.selected)
        if not r:
            return
        key = str(r["zelle"])
        def fn(rv: Review):
            rv.i0_fields.pop(key, None)
            rv.i0_seed[key] = int(rv.i0_seed.get(key, 0)) + 1
        self._edit(fn)

    def place_i0_field(self, x: float, y: float) -> None:
        """Tool 3: move the selected cell's nearest I₀ field to the click."""
        r = self._row(self.selected)
        if not r:
            QMessageBox.information(self, "I₀-Feld", "Erst eine Zelle auswählen (Werkzeug 1), dann ihre Felder setzen.")
            return
        cm = self.result.cells.get(r["zelle"])
        fields = [list(p) for p in (cm.fields if cm else [])]
        n = self.study.settings.i0_fields
        if len(fields) < n:
            fields.append([round(x, 1), round(y, 1)])
        else:
            k = min(range(len(fields)), key=lambda j: (fields[j][0] - x) ** 2 + (fields[j][1] - y) ** 2)
            fields[k] = [round(x, 1), round(y, 1)]
        key = str(r["zelle"])
        self._edit(lambda rv: rv.i0_fields.__setitem__(key, fields))

    def add_manual(self, pts: list) -> None:
        def fn(rv: Review):
            rv.manual.append([[round(x, 1), round(y, 1)] for x, y in pts])
        self.selected = MANUAL_ID_BASE + len(self.review.manual)
        self._edit(fn)
        self.set_tool(TOOL_SELECT)

    def delete_manual(self) -> None:
        r = self._row(self.selected)
        if not (r and r["manuell"]):
            return
        k = r["zelle"] - MANUAL_ID_BASE
        cid = r["zelle"]
        def fn(rv: Review):
            rv.manual.pop(k)
            # later drawn cells shift down one id
            rv.discarded = [d if d < cid else d - 1 for d in rv.discarded if d != cid]
            for store in (rv.i0_fields, rv.i0_seed):
                moved = {}
                for key, v in store.items():
                    c = int(key)
                    if c == cid:
                        continue
                    moved[str(c - 1 if c > cid else c)] = v
                store.clear(); store.update(moved)
        self.selected = 0
        self._edit(fn)

    def toggle_reviewed(self) -> None:
        if self.result is None:
            return
        self._edit(lambda rv: setattr(rv, "reviewed", not rv.reviewed))
        self._update_study_button()

    def _save_study(self) -> None:
        self.study.save()

    def _method_changed(self) -> None:
        m = self.method_combo.currentData()
        if self.cb_only_this.isChecked():
            self._edit(lambda rv: setattr(rv, "method", m))
        else:
            self.study.settings.method = m
            self._save_study()
            self._recompute()

    def _threshold_changed(self) -> None:
        t = round(self.thr_spin.value(), 4)
        if self.cb_only_this.isChecked():
            self._edit(lambda rv: setattr(rv, "threshold", t))
        else:
            self.study.settings.threshold = t
            self._save_study()
            self._recompute()

    def rerun_cellpose(self) -> None:
        info = self.images[self.idx]
        self.worker.force(info.folder, info.path, self.study.settings.cellpose)

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
        for i, info in enumerate(self.images):
            if str(info.path) == path:
                return i
        return -1

    def _cp_started(self, path: str) -> None:
        self.running.add(path)
        self._refresh_item(self._index_of(path))
        self._update_status()

    def _cp_finished(self, path: str) -> None:
        self.running.discard(path)
        self.failed.pop(path, None)
        i = self._index_of(path)
        if i >= 0:
            self._refresh_item(i)
            if i == self.idx:
                self._recompute()
        self._update_status()

    def _cp_failed(self, path: str, msg: str) -> None:
        self.running.discard(path)
        self.failed[path] = msg
        self._refresh_item(self._index_of(path))
        self._update_status()

    # ── export ──────────────────────────────────────────────
    def export(self) -> None:
        if not self.study:
            return
        dlg = ExportDialog(self, self.study, self.images)
        if dlg.exec() != QDialog.Accepted:
            return
        from ..export import export_study
        prog = QProgressDialog("Exportiere …", None, 0, len(self.images), self)
        prog.setWindowModality(Qt.WindowModal); prog.setMinimumDuration(0)

        def step(i, n, name):
            prog.setValue(i); prog.setLabelText(f"Exportiere {name} ({min(i + 1, n)}/{n})")
            QApplication.processEvents()
        try:
            info = export_study(self.study, dlg.only_reviewed.isChecked(), dlg.with_excluded.isChecked(),
                                step, self.images)
        except Exception as e:  # noqa: BLE001
            prog.close()
            QMessageBox.critical(self, "Export fehlgeschlagen", f"{type(e).__name__}: {e}")
            return
        prog.setValue(len(self.images))
        notes = "\n".join(info["notes"][:15])
        QMessageBox.information(
            self, "Export fertig",
            f"{info['n_cells']} gewertete Zellen aus {info['n_images']} Bildern"
            + (f" ({info['skipped']} ungeprüfte übersprungen)" if info["skipped"] else "")
            + f".\n\n{info['folder']}" + (f"\n\nHinweise:\n{notes}" if notes else ""))

    def closeEvent(self, e):
        self.worker.shutdown()
        super().closeEvent(e)


class ExportDialog(QDialog):
    """What goes into the export, plus what is still open (mockup screen 4)."""

    def __init__(self, parent, study: Study, images: list[ImageInfo]):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.resize(640, 360)
        l = QVBoxLayout(self); l.setSpacing(10)
        head = QLabel(f"<b>{study.name}</b> – nach <code>outputs/nkreview_&lt;Zeit&gt;/</code> im ersten Ordner der Studie")
        head.setWordWrap(True); l.addWidget(head)
        l.addWidget(QLabel("• zellen.csv – 1 Zeile pro Zelle (Long-Format für R)\n"
                           "• zusammenfassung.csv – Proband × Zeitpunkt × Marker\n"
                           "• einstellungen.json – Studie mit allen Parametern\n"
                           "• overlays/ – PNG je Bild · nk_export.xlsx – beide Tabellen"))
        self.only_reviewed = QCheckBox("nur geprüfte Bilder"); self.only_reviewed.setChecked(True)
        self.with_excluded = QCheckBox("verworfene und ausgeschlossene Zellen mitschreiben (markiert)")
        l.addWidget(self.only_reviewed); l.addWidget(self.with_excluded)
        no_cp = sum(not has_cellpose_cache(i.folder, i.path, study.settings.cellpose) for i in images)
        not_rev = sum(not Review.load(i.folder, i.path).reviewed for i in images)
        warn = []
        if no_cp:
            warn.append(f"{no_cp} Bild(er) ohne Cellpose (würden nur mit der OD-Schwelle gemessen)")
        if not_rev:
            warn.append(f"{not_rev} Bild(er) noch nicht geprüft")
        if warn:
            w = QLabel("Vor dem Export:  " + " · ".join(warn), objectName="banner"); w.setWordWrap(True)
            l.addWidget(w)
        l.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        b_c = QPushButton("Abbrechen"); b_c.clicked.connect(self.reject)
        b_ok = QPushButton("Exportieren", objectName="export"); b_ok.clicked.connect(self.accept)
        row.addWidget(b_c); row.addWidget(b_ok)
        l.addLayout(row)


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
    win = MainWindow()
    win.show()
    return app.exec()
