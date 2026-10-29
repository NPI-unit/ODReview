"""Main window: images of a study · image view · cell panel.

Flow: pick or create a study → Cellpose runs in the background (the image on
screen first) → go through the images with ←/→, check each cell's contour and
I₀ fields, discard or draw cells → Export writes the study's tables.
"""

from __future__ import annotations

import copy
import datetime
import gc
import json
import sys
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QProgressDialog, QPushButton, QScrollArea, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import __version__
from ..analysis import IMAGE_KEY, STATUS_KEPT, ImageResult, analyze
from ..segment import METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, cached_cellpose, has_cellpose_cache
from ..state import I0_PER_IMAGE, MANUAL_ID_BASE, Review, app_support_dir, load_image
from ..study import (
    LEVEL_OBJECTIVE, META_ROLES, ROLE_LABELS, ROLE_PROBAND, TARGET_NUCLEUS, ImageInfo, Study, is_counted,
    level_value, list_studies, proband_progress,
)
from .batch import mag_order
from .cellpanel import CellPanel, Fold, chip, edit_note, state_of
from .style import apply_look
from .studies import StudyPicker, _clear_layout
from .view import LEGEND, TOOL_DRAW, TOOL_I0, TOOL_NOTE, TOOL_SELECT, ImageView, legend_icon
from .splitter import PaneSplitter
from .worker import CellposeWorker

METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD edge)",
                 METHOD_CELLPOSE: "Model contour only",
                 METHOD_THRESHOLD: "OD threshold only (backup)"}
VIEWS = (("rgb", "Original"), ("gray", "Grey OD"), ("dab", "DAB channel"))
OD_DISPLAY_MAX = 0.4          # fixed scale so images stay comparable

COLUMNS = [("cell", "#"), ("status", "Status"), ("od", "OD"), ("i0", "I₀"),
           ("area_px", "Area"), ("note", "")]

ST_WAIT, ST_RUN, ST_SEG, ST_DONE, ST_ERR = "○", "◐", "●", "✔", "✕"
LIST_FILTERS = {"all": "All", "open": "Open", "warn": "⚠ I₀", "confirmed": "✔ Confirmed",
                "discarded": "Discarded"}
FILTERS = {"all": "All", "unsegmented": "○ Not segmented", "in_progress": "● In progress",
           "finished": "✔ Finished", "with_notes": "❝ With notes"}
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


class SortItem(QTableWidgetItem):
    """Table cell that sorts by its key (numbers as numbers), not by its text."""
    def __lt__(self, other):
        a, b = self.data(Qt.UserRole + 1), other.data(Qt.UserRole + 1)
        try:
            return a < b
        except TypeError:
            return str(a) < str(b)


class MainWindow(QMainWindow):
    def __init__(self, study: Study | None = None):
        super().__init__()
        self.resize(1500, 920)
        self.study: Study | None = None
        self.images: list[ImageInfo] = []
        self.visible: list[int] = []            # indices into images, in tree order
        self.marker = ALL_MARKERS
        self.filter_mode = "all"
        self.mag_filter = ""                    # "" = every magnification
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
        self.unsegmented = False
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
        m_file = self.menuBar().addMenu("File")
        m_file.addAction("Choose study …", self.choose_study)
        m_file.addAction("Segmentation …", self.open_batch, QKeySequence("Ctrl+Shift+S"))
        m_file.addSeparator()
        m_file.addAction("Export …", self.export)
        tb = QFrame(objectName="toolbar")
        tl = QHBoxLayout(tb); tl.setContentsMargins(10, 6, 10, 6)
        self.b_study = QPushButton("Choose study"); self.b_study.setObjectName("study")
        self.b_study.clicked.connect(self.choose_study)
        tl.addWidget(self.b_study)
        seg = QFrame(objectName="segment"); sl = QHBoxLayout(seg)
        sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        self.tool_group = QButtonGroup(self)
        for i, (tool, text) in enumerate(((TOOL_SELECT, "1  Select"), (TOOL_DRAW, "2  Draw contour"),
                                          (TOOL_I0, "3  I₀ fields"), (TOOL_NOTE, "4  Note"))):
            b = QPushButton(text); b.setCheckable(True)
            b.setProperty("first", i == 0); b.setProperty("last", i == 3)
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
        tl.addWidget(QLabel("View (D):")); tl.addWidget(self.view_combo)
        self.cb_contours = QCheckBox("Contours (O)"); self.cb_contours.setChecked(True)
        self.cb_contours.toggled.connect(lambda on: self.view.set_contours_visible(on))
        self.cb_fields = QCheckBox("I₀ fields (I)"); self.cb_fields.setChecked(True)
        self.cb_fields.toggled.connect(lambda on: self.view.set_fields_visible(on))
        tl.addWidget(self.cb_contours); tl.addWidget(self.cb_fields)
        tl.addStretch(1)
        self.status = QLabel(""); self.status.setObjectName("caption")
        tl.addWidget(self.status)
        b_br = QPushButton("☀︎"); b_br.setToolTip("Brightness & contrast – display only (B)")
        b_br.setProperty("class", "icon"); b_br.clicked.connect(self.adjust_display)
        tl.addWidget(b_br)
        b_set = QPushButton("⚙"); b_set.setToolTip("Study settings (⌘,)")
        b_set.setProperty("class", "icon"); b_set.clicked.connect(self.open_settings)
        tl.addWidget(b_set)
        b_help = QPushButton("?"); b_help.setToolTip("How the OD is measured (F1)")
        b_help.setProperty("class", "icon"); b_help.clicked.connect(self.show_help)
        tl.addWidget(b_help)
        b_exp = QPushButton("⤓ Export", objectName="export"); b_exp.clicked.connect(self.export)
        tl.addWidget(b_exp)

        # left: marker filter + image tree
        left = QWidget(objectName="sidebar"); ll = QVBoxLayout(left); ll.setContentsMargins(8, 8, 8, 8)
        self.filter_btn = QPushButton(objectName="filterButton")   # marker + status in one menu, one tick each
        self.filter_btn.setMenu(QMenu(self.filter_btn))
        ll.addWidget(self.filter_btn)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.currentItemChanged.connect(self._tree_changed)
        ll.addWidget(self.tree, 1)
        legend = QLabel("○ not segmented · ◐ running · ● segmented · ✔ reviewed · ⊘ not counted (E)", objectName="caption")
        ll.addWidget(legend)

        # centre
        self.view = ImageView()
        self.view.cellClicked.connect(self.select_cell)
        self.view.noteClicked.connect(self.edit_note)
        self.view.markDrawn.connect(self.add_mark)
        self.view.markClicked.connect(self.edit_mark)
        self.view.polygonDrawn.connect(self.add_manual)
        self.view.i0Clicked.connect(self.place_i0_field)
        self.view.i0Moved.connect(self.move_i0_field)
        self.view.menuRequested.connect(self._image_menu)
        self.banner = QLabel("", objectName="banner"); self.banner.setWordWrap(True)
        self.footer = QLabel("", objectName="caption")
        centre = QWidget(); cl = QVBoxLayout(centre); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(self.view, 1); cl.addWidget(self._build_legend())
        cl.addWidget(self.banner); cl.addWidget(self.footer)

        # right: fixed head (image, progress, tabs) · Nucleus | List | Notes · fixed foot
        right = QWidget(objectName="reviewPane")
        rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        head = QFrame(objectName="paneHead"); hv = QVBoxLayout(head)
        hv.setContentsMargins(14, 12, 14, 10); hv.setSpacing(6)
        hr = QHBoxLayout()
        self.img_name = QLabel(""); f = self.img_name.font(); f.setBold(True); self.img_name.setFont(f)
        self.img_done = QLabel("")
        hr.addWidget(self.img_name, 1); hr.addWidget(self.img_done)
        hv.addLayout(hr)
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.setFixedHeight(6)
        hv.addWidget(self.progress)
        self.summary = QLabel(""); self.summary.setWordWrap(True)
        hv.addWidget(self.summary)
        tabw = QFrame(objectName="segment"); tl2 = QHBoxLayout(tabw)
        tl2.setContentsMargins(0, 0, 0, 0); tl2.setSpacing(0)
        self.tab_group = QButtonGroup(self); self.tabs: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate((("cell", "Cell"), ("list", "List"), ("notes", "Notes"))):
            b = QPushButton(text); b.setCheckable(True)
            b.setProperty("first", i == 0); b.setProperty("last", i == 2)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            self.tab_group.addButton(b); tl2.addWidget(b, 1); self.tabs[key] = b
        hv.addWidget(tabw)
        rv.addWidget(head)

        def page() -> tuple[QScrollArea, QVBoxLayout]:
            w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(14, 12, 14, 12); lay.setSpacing(10)
            sc = QScrollArea(); sc.setWidgetResizable(True); sc.setWidget(w); sc.setFrameShape(QFrame.NoFrame)
            return sc, lay

        self.pages = QStackedWidget()
        # Cell: result and why, its buttons, then details
        sc, lay = page()
        self.panel = CellPanel(more_open=bool(_recall("more_open")), on_more=lambda on: _remember(more_open=on),
                               dist_open=_recall("dist_open") is not False, on_dist=lambda on: _remember(dist_open=on),
                               i0_open=_recall("i0_open") is not False, on_i0=lambda on: _remember(i0_open=on))
        lay.addWidget(self.panel.head)
        acts = QGridLayout(); acts.setSpacing(8)
        self.b_confirm = QPushButton("✔ Confirm (C)"); self.b_confirm.clicked.connect(self.toggle_confirm)
        self.b_confirm.setToolTip("Confirm this counted cell and jump to the next open one. C again undoes it.")
        self.b_confirm.setStyleSheet("QPushButton { border: 2px solid #1B7A4D; color: #1B7A4D; font-weight: 600; }")
        self.b_discard = QPushButton("Discard (X)"); self.b_discard.clicked.connect(self.toggle_discard)
        self.b_note = QPushButton("Note (N)"); self.b_note.clicked.connect(lambda: self.edit_note(self.selected))
        self.b_redraw = QPushButton("New I₀ fields (R)"); self.b_redraw.clicked.connect(self.redraw_fields)
        self.b_delete = QPushButton("Delete drawn contour"); self.b_delete.clicked.connect(self.delete_manual)
        acts.addWidget(self.b_confirm, 0, 0); acts.addWidget(self.b_discard, 0, 1)
        acts.addWidget(self.b_note, 1, 0); acts.addWidget(self.b_redraw, 1, 1)
        acts.addWidget(self.b_delete, 2, 0, 1, 2)
        acts.setColumnStretch(0, 7); acts.setColumnStretch(1, 5)
        lay.addLayout(acts)
        lay.addWidget(self.panel.details); lay.addStretch(1)
        self.pages.addWidget(sc)
        # List: filter chips, sortable table
        sc, lay = page()
        crow = QHBoxLayout(); crow.setSpacing(6)
        self.list_filter = "all"; self.list_chips: dict[str, QPushButton] = {}
        for key in LIST_FILTERS:
            b = QPushButton(); b.setCheckable(True); b.setProperty("class", "chip")
            b.clicked.connect(lambda _=False, k=key: self._set_list_filter(k))
            crow.addWidget(b); self.list_chips[key] = b
        crow.addStretch(1)
        lay.addLayout(crow)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMinimumHeight(420)
        self.table.itemSelectionChanged.connect(self._table_selection)
        lay.addWidget(self.table, 1)
        lay.addWidget(QLabel("Click a row: select it and zoom to it. Click a column head to sort.", objectName="caption"))
        self.pages.addWidget(sc)
        # Notes: the image's note, its comment circles, the cells' notes
        sc, lay = page()
        self.notes_box = QWidget(); self.notes_l = QVBoxLayout(self.notes_box)
        self.notes_l.setContentsMargins(0, 0, 0, 0); self.notes_l.setSpacing(8)
        lay.addWidget(self.notes_box)
        nrow = QHBoxLayout()
        b_in = QPushButton("+ Image note"); b_in.clicked.connect(lambda: self.edit_note(0))
        b_circ = QPushButton("◯ Draw circle (4)"); b_circ.clicked.connect(lambda: self.set_tool(TOOL_NOTE))
        nrow.addWidget(b_in); nrow.addWidget(b_circ)
        lay.addLayout(nrow)
        lay.addWidget(QLabel("Click an entry to jump to it. Notes stay in the review file and are not exported.",
                             objectName="caption", wordWrap=True))
        lay.addStretch(1)
        self.pages.addWidget(sc)
        rv.addWidget(self.pages, 1)

        foot = QFrame(objectName="paneFoot"); fv = QVBoxLayout(foot)
        fv.setContentsMargins(14, 10, 14, 12); fv.setSpacing(8)
        frow = QHBoxLayout()
        self.b_confirm_all = QPushButton("Confirm all open"); self.b_confirm_all.clicked.connect(self.confirm_all_open)
        self.b_confirm_all.setToolTip("Confirms every open cell without an I₀ warning – those need a look, one by one.")
        self.b_more = QToolButton(text="⋯"); self.b_more.setPopupMode(QToolButton.InstantPopup)
        self.b_more.setToolTip("More for this image")
        menu = QMenu(self.b_more)
        menu.addAction("Segment this image (again)", self.rerun_cellpose)
        menu.addAction("Contour method and OD threshold …", self._contour_dialog)
        self.b_more.setMenu(menu)
        frow.addWidget(self.b_confirm_all, 1); frow.addWidget(self.b_more)
        fv.addLayout(frow)
        self.b_done = QPushButton("✔ Image reviewed (Enter)"); self.b_done.setProperty("class", "primary")
        self.b_done.clicked.connect(self.toggle_reviewed)
        fv.addWidget(self.b_done)
        rv.addWidget(foot)

        # contour method / threshold: rarely changed, so in a small dialog from ⋯
        self.contour_dlg = QDialog(self); self.contour_dlg.setWindowTitle("Contour method and OD threshold")
        form = QFormLayout(self.contour_dlg)
        self.method_combo = QComboBox()
        for k, v in METHOD_LABELS.items():
            self.method_combo.addItem(v, k)
        self.method_combo.activated.connect(lambda _: self._method_changed())
        self.thr_spin = QDoubleSpinBox(); self.thr_spin.setRange(0.005, 1.0)
        self.thr_spin.setSingleStep(0.005); self.thr_spin.setDecimals(3)
        self.thr_spin.setKeyboardTracking(False)
        self.thr_spin.valueChanged.connect(lambda _: self._threshold_changed())
        self.cb_only_this = QCheckBox("this image only")
        self.cb_only_this.setToolTip("Off: method and threshold apply to the whole study.")
        form.addRow("Contour:", self.method_combo)
        form.addRow("OD threshold:", self.thr_spin)
        form.addRow("", self.cb_only_this)
        right.setMinimumWidth(400)
        self.show_tab(_recall("tab") or "cell", remember=False)

        # Side panels can be dragged shut; the arrow on their divider brings them back.
        split = PaneSplitter(); split.setObjectName("paneSplit")
        left.setMinimumWidth(200); centre.setMinimumWidth(320)
        split.addWidget(left); split.addWidget(centre); split.addWidget(right)
        split.setSizes([290, 800, 450])
        split.setStretchFactor(1, 1)
        self.split = split
        body = QWidget(); bl = QVBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        bl.addWidget(tb); bl.addWidget(split, 1)
        self.setCentralWidget(body)
        self._set_enabled(False)
        self.panel.show_cell(None)

    def _build_legend(self) -> QWidget:
        """What the contour colours mean – always under the image."""
        w = QWidget(); g = QGridLayout(w); g.setContentsMargins(4, 4, 4, 0)
        g.setHorizontalSpacing(4); g.setVerticalSpacing(2)
        self._legend_icons = {}
        for i, (kind, text) in enumerate(LEGEND):             # two rows, so nothing is cut off
            ic = QLabel(); self._legend_icons[kind] = ic
            t = QLabel(text, objectName="caption")
            if kind == "field":
                self._legend_field = t
            row, col = divmod(i, 4)
            g.addWidget(ic, row, 2 * col); g.addWidget(t, row, 2 * col + 1)
        g.setColumnStretch(8, 1)
        self.apply_display_prefs()
        return w

    def apply_display_prefs(self) -> None:
        """Fill of the kept cells: an app-wide display choice (app.json), not a study setting."""
        on = _recall("fill_kept")
        on = True if on is None else bool(on)
        alpha = float(_recall("fill_alpha") or 0.35)
        self.view.set_fill(on, alpha)
        for kind, ic in self._legend_icons.items():
            ic.setPixmap(legend_icon(kind, alpha if (kind == "keep" and on) else 0.0))

    def _build_shortcuts(self) -> None:
        keys = {
            Qt.Key_Right: lambda: self.go(+1), Qt.Key_Left: lambda: self.go(-1),
            Qt.Key_1: lambda: self.set_tool(TOOL_SELECT), Qt.Key_2: lambda: self.set_tool(TOOL_DRAW),
            Qt.Key_3: lambda: self.set_tool(TOOL_I0), Qt.Key_4: lambda: self.set_tool(TOOL_NOTE),
            Qt.Key_C: self.toggle_confirm, Qt.Key_N: lambda: self.edit_note(self.selected),
            Qt.Key_X: self.toggle_discard, Qt.Key_Delete: self.toggle_discard,
            Qt.Key_Backspace: self.toggle_discard, Qt.Key_R: self.redraw_fields,
            Qt.Key_D: self.cycle_view, Qt.Key_O: lambda: self.cb_contours.toggle(),
            Qt.Key_I: lambda: self.cb_fields.toggle(),
            Qt.Key_Return: self.toggle_reviewed, Qt.Key_Enter: self.toggle_reviewed,
            Qt.Key_F: lambda: self.view.fit(), Qt.Key_Escape: lambda: self.view.cancel_field_drag() or self.select_cell(0),
            Qt.Key_B: self.adjust_display, Qt.Key_E: self.toggle_counted,
        }
        for key, fn in keys.items():
            QShortcut(QKeySequence(key), self, activated=fn)
        QShortcut(QKeySequence.Undo, self, activated=self.undo)
        QShortcut(QKeySequence(Qt.Key_F1), self, activated=self.show_help)
        QShortcut(QKeySequence.Preferences, self, activated=self.open_settings)

    def _set_enabled(self, on: bool) -> None:
        for w in (self.method_combo, self.thr_spin, self.cb_only_this, self.b_more,
                  self.b_discard, self.b_redraw, self.b_delete, self.b_done, self.b_confirm,
                  self.b_confirm_all, self.b_note):
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
        self.panel.noun = "Nucleus" if study.target == TARGET_NUCLEUS else "Cell"
        self.tabs["cell"].setText(self.panel.noun)
        self._legend_field.setText("I₀ fields 1–3 of this image" if study.settings.i0_scope == I0_PER_IMAGE
                                   else "I₀ fields 1–3 of the selected")
        self.images = study.images()
        self.failed.clear(); self.running.clear()
        self.idx = -1
        _remember(study=study.file)
        self.setWindowTitle(f"ODReview {__version__} — {study.name}")
        self._build_tree()
        self.worker.stain_vectors = study.settings.stain_vectors
        self.worker.set_queue([], study.settings.cellpose)   # nothing runs until File › Segmentation …
        self._update_study_button()
        if self.visible:
            self.go_to(self.visible[0])
        else:
            self.img = None; self.result = None
            self._set_enabled(False)
            self.banner.setText("This study has no images – check the folders in its settings.")

    def _update_study_button(self) -> None:
        done, n = proband_progress(self.images)
        markers = ", ".join(self.study.markers(self.images)) or "–"
        self.b_study.setText(f"{self.study.name}   ▾")
        self.b_study.setToolTip(f"{markers} · {done}/{n} subjects done – click to switch study")

    def _set_filter(self, marker: str | None = None, mode: str | None = None, mag: str | None = None) -> None:
        if mag is not None:
            self.mag_filter = mag
        if marker is not None:
            self.marker = marker
        if mode is not None:
            self.filter_mode = mode
        self._build_tree()
        if self.visible and self.idx not in self.visible:
            self.go_to(self.visible[0])

    def _counted(self, i: int) -> bool:
        info = self.images[i]
        return is_counted(info, Review.load(info.folder, info.path), self.study.settings)

    def toggle_counted(self) -> None:
        """E: this image counts toward the results – or not (⊘, never exported)."""
        if self.idx < 0:
            return
        now = self._counted(self.idx)
        self._edit(lambda rv: setattr(rv, "counted", not now))

    def _build_filter_menu(self, per_marker: dict, counts: dict, per_mag: dict) -> None:
        menu = self.filter_btn.menu(); menu.clear()
        if len(per_marker) > 1:
            menu.addSection("Marker")
            for m, n in [(ALL_MARKERS, sum(per_marker.values()))] + sorted(per_marker.items()):
                a = menu.addAction(f"{m or 'All markers'} ({n})"); a.setCheckable(True); a.setChecked(m == self.marker)
                a.triggered.connect(lambda _=False, mk=m: self._set_filter(marker=mk))
        menu.addSection("Show")
        for key, label in FILTERS.items():
            a = menu.addAction(f"{label} ({counts[key]})"); a.setCheckable(True); a.setChecked(key == self.filter_mode)
            a.triggered.connect(lambda _=False, k=key: self._set_filter(mode=k))
        if len(per_mag) > 1:
            menu.addSeparator()
            menu.addSection("Magnification")
            for m, n in [("", sum(per_mag.values()))] + sorted(per_mag.items(), key=lambda kv: mag_order(kv[0])):
                a = menu.addAction(f"{m or 'All'} ({n})"); a.setCheckable(True); a.setChecked(m == self.mag_filter)
                a.triggered.connect(lambda _=False, mg=m: self._set_filter(mag=mg))
        shown = f"{FILTERS[self.filter_mode]} ({counts[self.filter_mode]})"
        bits = [b for b in (self.marker, self.mag_filter) if b]
        self.filter_btn.setText(" · ".join(bits + [shown]))

    def _filter_state(self, i: int) -> tuple[str, bool]:
        """('unsegmented' | 'in_progress' | 'finished', has a note or comment) of image i."""
        st = self._status_of(i)
        info = self.images[i]
        rv = Review.load(info.folder, info.path)
        noted = bool(rv.note or rv.notes or rv.marks)
        if st == ST_DONE:
            return "finished", noted
        return ("in_progress" if st == ST_SEG else "unsegmented"), noted

    def _build_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear(); self._items.clear(); self.visible = []
        mode = self.filter_mode
        markers = self.study.markers(self.images)
        if self.marker not in markers:
            self.marker = ALL_MARKERS
        per_marker = {m: sum(info.marker == m for info in self.images) for m in markers}
        counts = dict.fromkeys(FILTERS, 0)
        per_mag: dict = {}
        levels = [lv for lv in self.study.level_order
                  if any(level_value(self.study, info, lv) for info in self.images)]   # skip empty levels
        shown = []
        for i, info in enumerate(self.images):
            if self.marker and info.marker != self.marker:
                continue
            obj = info.objective or "unknown"
            per_mag[obj] = per_mag.get(obj, 0) + 1
            if self.mag_filter and obj != self.mag_filter:
                continue
            state, noted = self._filter_state(i)
            counts["all"] += 1; counts[state] += 1; counts["with_notes"] += noted
            if mode != "all" and not (noted if mode == "with_notes" else state == mode):
                continue
            shown.append(i)
        self._build_filter_menu(per_marker, counts, per_mag)
        heads: dict = {}
        key = lambda i: tuple(level_value(self.study, self.images[i], lv) for lv in levels)
        order = lambda i: tuple(mag_order(v) if lv == LEVEL_OBJECTIVE else v.lower()
                                for lv, v in zip(levels, key(i)))
        for i in sorted(shown, key=lambda i: (order(i), not self._counted(i), self.images[i].marker,
                                              self.images[i].path.name)):
            parent = None
            for depth, (lv, v) in enumerate(zip(levels, key(i))):
                path = key(i)[:depth + 1]
                if path not in heads:
                    text = f"Subject {v}" if lv == ROLE_PROBAND else v
                    h = QTreeWidgetItem([text, ""])
                    f = h.font(0); f.setBold(depth < len(levels) - 1 or len(levels) == 1); h.setFont(0, f)
                    h.setFlags(h.flags() & ~Qt.ItemIsSelectable)
                    (parent.addChild(h) if parent else self.tree.addTopLevelItem(h))
                    h.setExpanded(True)
                    heads[path] = h
                parent = heads[path]
            it = QTreeWidgetItem(["", ""])
            it.setData(0, Qt.UserRole, i)
            (parent.addChild(it) if parent else self.tree.addTopLevelItem(it))
            self._items[i] = it
            self.visible.append(i)
            self._refresh_item(i)
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
        rv = Review.load(info.folder, info.path)
        sun = "  ☀︎" if rv.display else ""
        noted = "  ❝" if rv.note or rv.notes or rv.marks else ""
        off = "⊘ " if not is_counted(info, rv, self.study.settings) else ""
        it.setText(0, f"{off}{st}  {info.path.name}{sun}{noted}")
        it.setForeground(0, QColor("#8A8F99") if off else QColor(self.palette().text().color()))
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
        self._set_enabled(True)
        m = info.meta
        bits = [f"{ROLE_LABELS[k].lower()}: {m[k]}" for k in META_ROLES if m.get(k)]
        self.footer.setText(f"{info.path.name}   ·   " + "  ·  ".join(bits))
        self._recompute(fit=True)

    # ── analysis / redraw ───────────────────────────────────
    def _recompute(self, fit: bool = False) -> None:
        if self.img is None:
            return
        info = self.images[self.idx]
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            cp = cached_cellpose(info.folder, info.path, self.study.settings.cellpose)
            # not segmented yet: no contours at all (no OD-threshold stand-in)
            self.unsegmented = cp is None
            if cp is None:
                cp = np.zeros(self.img.shape[:2], np.int32)
            self.result = analyze(info.folder, info.path, self.img, self.study.settings, self.review, cp)
        finally:
            QApplication.restoreOverrideCursor()
        if self.selected and self._row(self.selected) is None:
            self.selected = 0
        if not self.selected and self.result.rows:
            self.selected = self.result.rows[0]["cell"]
        self._render_base(fit)
        self.view.set_cells(self.result.labels, self.result.rows, self.selected)
        self.view.set_marks(self.review.marks)
        self._fill_comments()
        self._draw_fields()
        self._sync_controls()
        self._fill_table()
        self._update_detail()
        self._update_status()

    def _draw_fields(self) -> None:
        cm = self.result.cells.get(self.selected)
        r = self._row(self.selected)
        if self._per_image():                      # one set for the whole image: always shown
            pts = self.result.image_fields or []
        else:
            pts = cm.fields if cm is not None and r and r["status"] != "discarded" else []
        self.view.set_fields(pts, self.study.settings.i0_field_size)

    def _render_base(self, fit: bool = False) -> None:
        if self.view_mode == "rgb" or self.result is None:
            rgb = self.img.astype(np.uint8)
            if self.review.display:                       # brightness/contrast/gamma: view only
                from .. import display_adjust as da
                adj = da.apply(rgb.astype(np.float32) / 255.0, self.review.display)
                rgb = (adj * 255.0 + 0.5).astype(np.uint8)
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
        self.banner.setText("Not segmented yet – File › Segmentation …, or ⋯ › Segment this image"
                            if self.unsegmented else r.note)
        self.b_done.setText("↺ Undo reviewed (Enter)" if self.review.reviewed else "✔ Image reviewed (Enter)")

    def _fill_table(self) -> None:
        rows = self.result.rows
        n = {k: 0 for k in ("confirmed", "kept", "warn", "excluded", "discarded")}
        for r in rows:
            n[state_of(r)] += 1
        label = {"confirmed": "confirmed", "kept": "open", "warn": "I₀ warning", "excluded": "no background",
                 "discarded": "discarded"}
        todo = n["confirmed"] + n["kept"] + n["warn"]
        self.img_name.setText(self.images[self.idx].path.name if self.idx >= 0 else "")
        self.img_done.setText(f"{n['confirmed']} / {todo} confirmed" if todo else "")
        self.progress.setRange(0, max(todo, 1)); self.progress.setValue(n["confirmed"])
        self.summary.setText("  ".join(chip(f"{n[k]} {label[k]}", k) for k in n if n[k]))
        self.b_confirm_all.setEnabled(n["kept"] > 0)
        self.tabs["list"].setText(f"List {len(rows)}")
        counts = {"all": len(rows), "open": n["kept"], "warn": n["warn"], "confirmed": n["confirmed"],
                  "discarded": n["discarded"]}
        for key, b in self.list_chips.items():
            b.setText(f"{LIST_FILTERS[key]} {counts[key]}"); b.setChecked(key == self.list_filter)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        colors = {"discarded": QColor("#8A8F99"), "excluded": QColor("#975E0C"), "warn": QColor("#975E0C"),
                  "confirmed": QColor("#1B7A4D")}
        shown = {"open": "kept"}
        for i, r in enumerate(rows):
            hint = []
            if r["manual"]: hint.append("drawn")
            if r["edge_cell"]: hint.append("image edge")
            if r["debris_suspect"]: hint.append("Debris?")
            if r.get("positive") is not None: hint.append("pos" if r["positive"] else "neg")
            if r["reason"] and r["status"] != "discarded": hint.append(r["reason"])
            if r.get("has_note"): hint.insert(0, "❝")
            state = state_of(r)
            vals = {**r, "note": ", ".join(hint), "status": {"kept": "open", "warn": "I₀ warning"}.get(state, state)}
            for c, (key, _) in enumerate(COLUMNS):
                v = vals[key]
                if v is None:
                    text = "–"
                elif key == "od":
                    text = f"{v:.4f}"
                elif key == "i0":
                    text = f"{v:.1f}"
                else:
                    text = str(v)
                it = SortItem(text)
                it.setData(Qt.UserRole, r["cell"])
                it.setData(Qt.UserRole + 1, v if isinstance(v, (int, float)) else text)
                if state in colors:
                    it.setForeground(colors[state])
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        want = shown.get(self.list_filter, self.list_filter)
        self.table.clearSelection()
        for row in range(self.table.rowCount()):
            cid = self.table.item(row, 0).data(Qt.UserRole)
            r = self._row(cid)
            self.table.setRowHidden(row, want != "all" and state_of(r) != want)
            if cid == self.selected:
                self.table.selectRow(row)
        self.table.blockSignals(False)

    def _set_list_filter(self, key: str) -> None:
        self.list_filter = key
        if self.result:
            self._fill_table()

    def show_tab(self, key: str, remember: bool = True) -> None:
        self.pages.setCurrentIndex(list(self.tabs).index(key))
        self.tabs[key].setChecked(True)
        if remember:
            _remember(tab=key)

    def _contour_dialog(self) -> None:
        self.contour_dlg.exec()

    def _row(self, cid: int) -> dict | None:
        return next((r for r in self.result.rows if r["cell"] == cid), None) if self.result else None

    def _update_detail(self) -> None:
        r = self._row(self.selected)
        self.b_discard.setEnabled(r is not None)
        self.b_confirm.setEnabled(bool(r and r["status"] == STATUS_KEPT))
        self.b_confirm.setText("Undo confirm (C)" if r and r.get("confirmed") else "✔ Confirm (C)")
        self.b_note.setEnabled(r is not None)
        self.b_redraw.setEnabled(r is not None or self._per_image())
        self.b_redraw.setText("New image I₀ fields (R)" if self._per_image() else "New I₀ fields (R)")
        self.b_delete.setVisible(bool(r and r["manual"]))      # only a contour drawn by hand
        if r is None:
            self.panel.show_cell(None)
            return
        self.b_discard.setText("Restore (X)" if r["status"] == "discarded" else "Discard (X)")
        self.panel.show_cell(r, self.result.cells.get(r["cell"]), self.result.gray,
                             self.result.labels, self.study.settings,
                             note=self.review.notes.get(str(r["cell"]), ""))

    def _update_status(self) -> None:
        if not self.images:
            self.status.setText("")
            return
        pend = self.worker.pending() + len(self.running)
        cp = f"Segmentation: {pend} to go" if pend else ""
        pos = self.visible.index(self.idx) + 1 if self.idx in self.visible else 0
        rows = self.result.rows if self.result else []
        kept = sum(r["status"] == STATUS_KEPT for r in rows)
        conf = sum(bool(r.get("confirmed")) for r in rows)
        nouns = "nuclei" if self.study and self.study.target == TARGET_NUCLEUS else "cells"
        self.status.setText(f"Image {pos}/{len(self.visible)} · {conf}/{kept} {nouns} confirmed" + (f" · {cp}" if cp else ""))

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
                self.view.zoom_to(r["x"], r["y"], 300)

    def toggle_discard(self) -> None:
        r = self._row(self.selected)
        if not r:
            return
        def fn(rv: Review):
            if r["cell"] in rv.discarded:
                rv.discarded.remove(r["cell"])
            else:
                rv.discarded.append(r["cell"])
                if r["cell"] in rv.confirmed:
                    rv.confirmed.remove(r["cell"])
        self._edit(fn)

    def toggle_confirm(self) -> None:
        """C: confirm the selected counted cell (green) and go to the next open one; C again undoes."""
        r = self._row(self.selected)
        if not r or r["status"] != STATUS_KEPT:
            return
        cid, undo = r["cell"], bool(r.get("confirmed"))
        def fn(rv: Review):
            if undo:
                rv.confirmed.remove(cid)
            else:
                rv.confirmed.append(cid)
        self._edit(fn)
        if not undo:
            rows = self.result.rows
            i = next((k for k, x in enumerate(rows) if x["cell"] == cid), -1)
            nxt = next((x for x in rows[i + 1:] + rows[:i] if x["status"] == STATUS_KEPT and not x.get("confirmed")), None)
            if nxt:
                self.select_cell(nxt["cell"])
                self.view.centerOn(nxt["x"], nxt["y"])

    def _open_cells(self, with_warning: bool = True) -> list[int]:
        return [r["cell"] for r in self.result.rows if r["status"] == STATUS_KEPT and not r.get("confirmed")
                and (with_warning or not r.get("i0_warning"))]

    def confirm_all_open(self) -> None:
        """Every open cell without an I₀ warning – those need a look, one by one."""
        open_ = self._open_cells(with_warning=False) if self.result else []
        if open_:
            self._edit(lambda rv: rv.confirmed.extend(c for c in open_ if c not in rv.confirmed))

    def add_mark(self, x: float, y: float, r: float) -> None:
        """Tool 4 next to the cells: a comment circle anywhere in the image."""
        if self.result is None:
            return
        text = edit_note(self, f"Comment {len(self.review.marks) + 1}", "")
        if text:
            mark = {"x": round(x, 1), "y": round(y, 1), "r": round(r, 1), "text": text}
            self._edit(lambda rv: rv.marks.append(mark))

    def edit_mark(self, k: int) -> None:
        old = self.review.marks[k]["text"]
        text = edit_note(self, f"Comment {k + 1}", old)
        if text is None or text == old:
            return
        def fn(rv: Review):
            if text:
                rv.marks[k] = {**rv.marks[k], "text": text}
            else:
                del rv.marks[k]                     # empty or Delete: the circle goes too
        self._edit(fn)

    def _fill_comments(self) -> None:
        """Notes tab: the image's note, its comment circles, the cells' notes – a click jumps there."""
        _clear_layout(self.notes_l)
        rv = self.review
        cell_notes = [(int(k), t) for k, t in rv.notes.items() if t.strip()]
        def section(title: str) -> None:
            self.notes_l.addWidget(QLabel(f"<b>{title}</b>", objectName="caption"))
        def entry(text: str, tip: str, on_click) -> None:
            b = QPushButton(text); b.setToolTip(tip); b.clicked.connect(on_click)
            b.setStyleSheet("text-align: left; padding: 6px 8px;")
            self.notes_l.addWidget(b)
        if rv.note:
            section("This image")
            entry(f"❝  {rv.note.splitlines()[0][:70]}", rv.note, lambda: self.edit_note(0))
        if rv.marks:
            section("Circles in the image")
            for k, m in enumerate(rv.marks):
                first = m["text"].splitlines()[0][:60] if m["text"] else ""
                entry(f"{k + 1}  ·  {first}", m["text"],
                      lambda _=False, m=m: self.view.zoom_to(m["x"], m["y"], 4 * m["r"] + 80))
        if cell_notes:
            section(f"{self.panel.noun}s" if self.panel.noun == "Cell" else "Nuclei")
            for cid, t in sorted(cell_notes):
                entry(f"#{cid}  ·  {t.splitlines()[0][:60]}", t, lambda _=False, c=cid: self._jump_to(c))
        if not (rv.note or rv.marks or cell_notes):
            self.notes_l.addWidget(QLabel("No notes yet.", objectName="caption"))
        n = bool(rv.note) + len(rv.marks) + len(cell_notes)
        self.tabs["notes"].setText(f"Notes {n}" if n else "Notes")

    def _jump_to(self, cid: int) -> None:
        r = self._row(cid)
        if r:
            self.select_cell(cid)
            self.view.zoom_to(r["x"], r["y"], 300)
            self.show_tab("cell")

    def edit_note(self, cid: int) -> None:
        """N / tool 4: the remark of a cell, or of the whole image when no cell is hit."""
        if self.result is None:
            return
        r = self._row(cid) if cid else None
        title = f"Note · {self.panel.noun} #{cid}" if r else "Note · this image"
        old = self.review.notes.get(str(cid), "") if r else self.review.note
        text = edit_note(self, title, old)
        if text is None or text == old:
            return
        def fn(rv: Review):
            if not r:
                rv.note = text
            elif text:
                rv.notes[str(cid)] = text
            else:
                rv.notes.pop(str(cid), None)
        if r:
            self.selected = cid
        self._edit(fn)

    def _per_image(self) -> bool:
        return self.study is not None and self.study.settings.i0_scope == I0_PER_IMAGE

    def redraw_fields(self) -> None:
        r = self._row(self.selected)
        if not r and not self._per_image():
            return
        key = IMAGE_KEY if self._per_image() else str(r["cell"])
        def fn(rv: Review):
            rv.i0_fields.pop(key, None)
            rv.i0_seed[key] = int(rv.i0_seed.get(key, 0)) + 1
        self._edit(fn)

    def _i0_target(self) -> tuple[list, str] | None:
        """The I₀ fields tool 3 edits: the image's, or the selected cell's (None: no cell selected)."""
        if self._per_image():
            return [list(p) for p in (self.result.image_fields or [])], IMAGE_KEY
        r = self._row(self.selected)
        if not r:
            QMessageBox.information(self, "I₀ field", "Select a cell first (tool 1), then set its fields.")
            return None
        cm = self.result.cells.get(r["cell"])
        return [list(p) for p in (cm.fields if cm else [])], str(r["cell"])

    def place_i0_field(self, x: float, y: float) -> None:
        """Tool 3, click next to the fields: add one while fewer than set. Existing ones are dragged."""
        t = self._i0_target()
        if t is None:
            return
        fields, key = t
        if len(fields) >= self.study.settings.i0_fields:
            return
        fields.append([round(x, 1), round(y, 1)])
        self._edit(lambda rv: rv.i0_fields.__setitem__(key, fields))

    def move_i0_field(self, k: int, x: float, y: float) -> None:
        """Tool 3: field k dragged to x, y."""
        t = self._i0_target()
        if t is None or not 0 <= k < len(t[0]):
            return
        fields, key = t
        fields[k] = [round(x, 1), round(y, 1)]
        self._edit(lambda rv: rv.i0_fields.__setitem__(key, fields))

    def add_manual(self, pts: list) -> None:
        def fn(rv: Review):
            rv.manual.append([[round(x, 1), round(y, 1)] for x, y in pts])
        self.selected = MANUAL_ID_BASE + len(self.review.manual)
        self._edit(fn)
        self.set_tool(TOOL_SELECT)

    def delete_manual(self) -> None:
        r = self._row(self.selected)
        if not (r and r["manual"]):
            return
        k = r["cell"] - MANUAL_ID_BASE
        cid = r["cell"]
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
        open_ = self._open_cells(with_warning=False)
        warned = len(self._open_cells()) - len(open_)
        if not self.review.reviewed and (open_ or warned):
            nouns = "nuclei" if self.panel.noun == "Nucleus" else "cells"
            box = QMessageBox(QMessageBox.Question, "Image reviewed",
                              f"{len(open_) + warned} counted {nouns} are not confirmed yet"
                              + (f" ({warned} with an I₀ warning – these stay open unless you confirm them one by one)"
                                 if warned else "") + ". Only confirmed ones enter summary.csv.", parent=self)
            b_all = box.addButton("Confirm all and finish", QMessageBox.AcceptRole)
            b_keep = box.addButton("Finish, leave them open", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() == b_all:
                def fn(rv: Review):
                    rv.confirmed.extend(c for c in open_ if c not in rv.confirmed)
                    rv.reviewed = True
                self._edit(fn)
                self._update_study_button()
                return
            if box.clickedButton() != b_keep:
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

    def open_batch(self) -> None:
        if self.study is None:
            return
        from .batch import BatchDialog
        dlg = BatchDialog(self, self.study, self.images, self.worker)
        dlg.finished.connect(lambda *_: self._build_tree())
        dlg.open()

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

    # ── brightness & contrast (display only) ────────────────
    def _image_menu(self) -> None:
        if self.img is None:
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("☀︎  Brightness & contrast …", self.adjust_display)
        reset = m.addAction("Reset brightness", self.reset_display)
        reset.setEnabled(bool(self.review.display))
        m.exec(QCursor.pos())

    def adjust_display(self) -> None:
        if self.img is None:
            return
        from .brightness import BrightnessDialog
        cur = self.images[self.idx]
        same = [i for i in self.images if (i.proband, i.timepoint) == (cur.proband, cur.timepoint)]
        label = f"subject {cur.proband} · {cur.timepoint}"
        dlg = BrightnessDialog(self, cur, same, label, list(self.images))
        if dlg.exec() == QDialog.Accepted:
            self._after_display(dlg.applied)

    def reset_display(self) -> None:
        if self.img is None:
            return
        cur = self.images[self.idx]
        self.review.display = None
        self.review.save(cur.folder, cur.path)
        self._after_display({str(cur.path): None})

    def _after_display(self, applied: dict) -> None:
        """Files are written; the open image's review in memory takes the same
        value (else its next save would undo it), then view and list follow."""
        cur = self.images[self.idx]
        if str(cur.path) in applied:
            self.review.display = applied[str(cur.path)]
            self._render_base()
        for i, info in enumerate(self.images):
            if str(info.path) in applied:
                self._refresh_item(i)
        n = len(applied)
        self.banner.setText(f"Brightness changed for {n} image{'s' if n != 1 else ''} – display only, "
                            "measurements unchanged.")

    def open_settings(self) -> None:
        if self.study is None:
            return
        from .studies import StudySettings
        dlg = StudySettings(self, self.study)
        dlg.exec()
        self.apply_display_prefs()
        if getattr(dlg, "switch_requested", False):
            self.choose_study()
        elif dlg.saved is not None:
            self.open_study(dlg.saved)

    def show_help(self) -> None:
        from .help import HelpWindow
        if getattr(self, "_help", None) is None:
            self._help = HelpWindow(self)
        self._help.show(); self._help.raise_(); self._help.activateWindow()

    # ── export ──────────────────────────────────────────────
    def export(self) -> None:
        if not self.study:
            return
        dlg = ExportDialog(self, self.study, self.images)
        if dlg.exec() != QDialog.Accepted:
            return
        from ..export import export_study
        prog = QProgressDialog("Exporting …", None, 0, len(self.images), self)
        prog.setWindowModality(Qt.WindowModal); prog.setMinimumDuration(0)

        def step(i, n, name):
            prog.setValue(i); prog.setLabelText(f"Exporting {name} ({min(i + 1, n)}/{n})")
            QApplication.processEvents()
        try:
            info = export_study(self.study, dlg.only_reviewed.isChecked(), dlg.with_excluded.isChecked(),
                                step, self.images)
        except Exception as e:  # noqa: BLE001
            prog.close()
            QMessageBox.critical(self, "Export failed", f"{type(e).__name__}: {e}")
            return
        prog.setValue(len(self.images))
        notes = "\n".join(info["notes"][:15])
        QMessageBox.information(
            self, "Export done",
            f"{info['n_cells']} kept cells ({info['n_confirmed']} confirmed – only these enter summary.csv) "
            f"from {info['n_images']} images"
            + (f" ({info['skipped']} not reviewed, skipped)" if info["skipped"] else "")
            + f".\n\n{info['folder']}" + (f"\n\nNotes:\n{notes}" if notes else ""))

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
        head = QLabel(f"<b>{study.name}</b> – to <code>outputs/nkreview_&lt;time&gt;/</code> in the study's first folder")
        head.setWordWrap(True); l.addWidget(head)
        l.addWidget(QLabel("• cells.csv – one row per cell (long format for R)\n"
                           "• summary.csv – subject × group × timepoint × marker\n"
                           "• settings.json – the study with every parameter\n"
                           "• overlays/ – PNG per image · nk_export.xlsx – both tables"))
        self.only_reviewed = QCheckBox("reviewed images only"); self.only_reviewed.setChecked(True)
        self.with_excluded = QCheckBox("include discarded and excluded cells (flagged)")
        l.addWidget(self.only_reviewed); l.addWidget(self.with_excluded)
        no_cp = sum(not has_cellpose_cache(i.folder, i.path, study.settings.cellpose) for i in images)
        not_rev = sum(not Review.load(i.folder, i.path).reviewed for i in images)
        warn = []
        if no_cp:
            warn.append(f"{no_cp} image(s) not segmented yet (would be measured with the OD threshold only)")
        if not_rev:
            warn.append(f"{not_rev} image(s) not reviewed yet")
        if warn:
            w = QLabel("Before exporting:  " + " · ".join(warn), objectName="banner"); w.setWordWrap(True)
            l.addWidget(w)
        l.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        b_c = QPushButton("Cancel"); b_c.clicked.connect(self.reject)
        b_ok = QPushButton("Export", objectName="export"); b_ok.clicked.connect(self.accept)
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
    # English UI: decimal point in every number field, whatever the system language.
    from PySide6.QtCore import QLocale
    QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
    from PySide6.QtGui import QIcon
    icon = Path(__file__).resolve().parent.parent.parent / "assets" / "icon.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    apply_look(app, _recall("theme") or "system")   # Settings › Display › Appearance
    return app


def gc_on_gui_thread(app) -> None:
    """Python's cyclic GC runs in whichever thread happens to allocate. In the
    segmentation thread it destroyed a closed settings dialog – Qt widgets
    deleted off the GUI thread, SIGBUS (0.9.0, first InstanSeg import). So
    automatic collection is off and timers collect here instead."""
    gc.disable()
    young = QTimer(app); young.timeout.connect(lambda: gc.collect(1)); young.start(1000)
    full = QTimer(app); full.timeout.connect(gc.collect); full.start(30000)   # ~50 ms with torch loaded


def log_path() -> Path:
    return app_support_dir() / "odreview.log"


def log_to_file() -> None:
    """Started from ODReview.app there is no terminal: print and tracebacks go to odreview.log."""
    if sys.stderr is not None and sys.stderr.isatty():
        return                                            # run.command: keep them in the terminal
    path = log_path()
    if path.exists() and path.stat().st_size > 5_000_000:
        path.unlink()                                     # start over instead of growing forever
    f = open(path, "a", buffering=1, encoding="utf-8")
    f.write(f"\n── ODReview {__version__} started {datetime.datetime.now():%Y-%m-%d %H:%M:%S} ──\n")
    sys.stdout = sys.stderr = f


def show_errors(app) -> None:
    """An unexpected exception is logged and shown once – not one box per repeat."""
    import traceback
    shown: set = set()

    def hook(kind, value, tb):
        traceback.print_exception(kind, value, tb)        # → odreview.log or the terminal
        key = f"{kind.__name__}: {value}"
        if key in shown or app.thread() is not QThread.currentThread():   # a box only from the GUI thread
            return
        shown.add(key)
        QMessageBox.warning(QApplication.activeWindow(), "Something went wrong",
                            f"{key}\n\nThe app keeps running. Details are in\n{log_path()}")
    sys.excepthook = hook


def main() -> int:
    log_to_file()
    app = make_app()
    show_errors(app)
    gc_on_gui_thread(app)
    win = MainWindow()
    win.show()
    return app.exec()
