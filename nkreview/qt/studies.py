"""Study overview and the 'New study' assistant (mockup screens 2 and 3)."""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..segment import METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD
from ..state import GRAY_MEAN, GRAY_WEIGHTED, I0_ABSOLUTE, I0_RELATIVE, IMG_EXTS, Settings, list_images
from ..study import (
    CANDIDATE_DELIMS, META_ROLES, ROLE_GROUP, ROLE_IGNORE, ROLE_LABELS, ROLE_PROBAND, ROLE_TIMEPOINT,
    ROLES, TARGET_CELL, TARGET_LABELS, TARGET_NUCLEUS, Study, Template, detect_delimiters,
    guess_roles, list_studies, list_templates, parse_name, progress, proband_progress,
    template_file_for, tokenize,
)

DELIM_LABELS = {" ": "space", "_": "_", "-": "-", ".": "."}
METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD edge)", METHOD_CELLPOSE: "Cellpose only",
                 METHOD_THRESHOLD: "OD threshold only"}
TREE_MAX_FILES = 300            # per folder in the folder tree; the rest is summarised


def i0_rule(s: Settings) -> str:
    if s.i0_check == I0_RELATIVE:
        return f"± {s.i0_rel_pct:g} % of the image's free background"
    return f"valid {s.i0_min:g}–{s.i0_max:g}"


def fit_to_screen(w: QWidget, width: int, height: int) -> None:
    """Resize to (width, height) but never beyond 90 % of the screen (laptops)."""
    scr = (w.screen() or QGuiApplication.primaryScreen())
    if scr is not None:
        g = scr.availableGeometry()
        width, height = min(width, int(g.width() * 0.9)), min(height, int(g.height() * 0.9))
    w.resize(width, height)


def _clear_layout(lay) -> None:
    """Empty a layout now. deleteLater() alone leaves the old widgets on screen
    (still parented, out of the layout) until the event loop runs."""
    while lay.count():
        w = lay.takeAt(0).widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()


def _card(title: str, value: str, sub: str = "") -> QFrame:
    f = QFrame(objectName="metricCard")
    l = QVBoxLayout(f); l.setContentsMargins(12, 10, 12, 10); l.setSpacing(2)
    l.addWidget(QLabel(title, objectName="caption"))
    v = QLabel(f"<b>{value}</b>"); l.addWidget(v)
    if sub:
        s = QLabel(sub, objectName="caption"); s.setWordWrap(True); l.addWidget(s)
    return f


def _scroll(w: QWidget) -> QScrollArea:
    a = QScrollArea(); a.setWidgetResizable(True); a.setFrameShape(QFrame.NoFrame); a.setWidget(w)
    return a


# ─────────────────────────────────────────────
# Folder tree
# ─────────────────────────────────────────────
def _fill_tree(parent: QTreeWidgetItem, folder: Path) -> int:
    """Add folder contents below `parent`; returns the number of images found.
    Hidden folders (.nk_cache) and outputs/ are shown greyed as 'skipped'."""
    try:
        entries = sorted(os.scandir(folder), key=lambda e: (not e.is_dir(), e.name.lower()))
    except OSError as e:
        it = QTreeWidgetItem([f"cannot read: {e.strerror}", ""]); parent.addChild(it)
        return 0
    grey = QColor("#8A8F99")
    n_img, files_shown, others = 0, 0, 0
    for e in entries:
        if e.name.startswith(".") and not e.is_dir():
            continue
        if e.is_dir():
            skipped = e.name.startswith(".") or e.name == "outputs"
            it = QTreeWidgetItem([f"📁 {e.name}", ""])
            parent.addChild(it)
            if skipped:
                it.setText(1, "skipped")
                for c in (0, 1):
                    it.setForeground(c, grey)
                continue
            k = _fill_tree(it, Path(e.path))
            it.setText(1, f"{k} image{'s' if k != 1 else ''}")
            n_img += k
        elif Path(e.name).suffix.lower() in IMG_EXTS:
            n_img += 1
            if files_shown < TREE_MAX_FILES:
                parent.addChild(QTreeWidgetItem([f"🖼 {e.name}", ""]))
                files_shown += 1
        else:
            others += 1
    direct = sum(1 for e in entries if not e.is_dir() and Path(e.name).suffix.lower() in IMG_EXTS)
    if direct > TREE_MAX_FILES:
        it = QTreeWidgetItem([f"… {direct - TREE_MAX_FILES} more images", ""]); it.setForeground(0, grey)
        parent.addChild(it)
    if others:
        it = QTreeWidgetItem([f"{others} other file{'s' if others != 1 else ''} (not images)", ""])
        it.setForeground(0, grey); parent.addChild(it)
    return n_img


# ─────────────────────────────────────────────
# Overview
# ─────────────────────────────────────────────
class StudyPicker(QDialog):
    """Pick a study to work on (or make a new one). `self.chosen` after accept."""

    def __init__(self, parent=None, current: Study | None = None):
        super().__init__(parent)
        self.setWindowTitle("Studies")
        fit_to_screen(self, 1100, 700)
        self.chosen: Study | None = None
        self._studies: list[Study] = []
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        left = QWidget(objectName="sidebar"); ll = QVBoxLayout(left); ll.setContentsMargins(14, 14, 14, 14)
        top = QHBoxLayout(); t = QLabel("<b>Studies</b>"); f = t.font(); f.setPointSizeF(f.pointSizeF() * 1.3); t.setFont(f)
        b_new = QPushButton("+ New study"); b_new.setProperty("class", "primary"); b_new.clicked.connect(self.new_study)
        top.addWidget(t); top.addStretch(1); top.addWidget(b_new)
        ll.addLayout(top)
        self.list = QListWidget(); self.list.currentRowChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda _: self.open_selected())
        ll.addWidget(self.list, 1)
        left.setFixedWidth(320)
        lay.addWidget(left)

        right = QWidget(); self.rl = QVBoxLayout(right); self.rl.setContentsMargins(22, 18, 22, 18); self.rl.setSpacing(12)
        head = QHBoxLayout()
        self.h_name = QLabel(""); f = self.h_name.font(); f.setPointSizeF(f.pointSizeF() * 1.5); f.setBold(True); self.h_name.setFont(f)
        self.h_sub = QLabel("", objectName="caption")
        hv = QVBoxLayout(); hv.addWidget(self.h_name); hv.addWidget(self.h_sub)
        self.b_tpl = QPushButton("Save as template"); self.b_tpl.clicked.connect(self.save_template)
        self.b_edit = QPushButton("Settings"); self.b_edit.clicked.connect(self.edit_study)
        self.b_open = QPushButton("Open review"); self.b_open.setProperty("class", "primary")
        self.b_open.clicked.connect(self.open_selected)
        head.addLayout(hv, 1); head.addWidget(self.b_tpl); head.addWidget(self.b_edit); head.addWidget(self.b_open)
        self.rl.addLayout(head)
        self.cards = QGridLayout(); self.cards.setSpacing(10)
        self.rl.addLayout(self.cards)
        self.rl.addWidget(QLabel("<b>Progress</b>  – reviewed / all images per subject × timepoint × marker"))
        self.grid = QTableWidget(); self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rl.addWidget(self.grid, 1)
        self.rl.addWidget(QLabel("<b>Folders</b>"))
        self.folders = QLabel(""); self.folders.setWordWrap(True); self.folders.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.rl.addWidget(self.folders)
        lay.addWidget(right, 1)
        self.reload(current)

    def reload(self, select: Study | None = None) -> None:
        self._studies = list_studies()
        self.list.blockSignals(True); self.list.clear()
        row = 0
        for i, st in enumerate(self._studies):
            imgs = st.images()
            done, n = proband_progress(imgs)
            markers = ", ".join(st.markers(imgs)) or "–"
            self.list.addItem(QListWidgetItem(
                f"{st.name}\n{TARGET_LABELS.get(st.target, st.target)} · {markers} · {done}/{n} subjects done"))
            if select is not None and st.file == select.file:
                row = i
        self.list.blockSignals(False)
        has = bool(self._studies)
        for b in (self.b_open, self.b_edit, self.b_tpl):
            b.setEnabled(has)
        if has:
            self.list.setCurrentRow(row); self._show(row)
        else:
            self.h_name.setText("No study yet")
            self.h_sub.setText("Create a new study: choose folders, map the file names, set the measurement.")

    def _current(self) -> Study | None:
        r = self.list.currentRow()
        return self._studies[r] if 0 <= r < len(self._studies) else None

    def _show(self, _row: int) -> None:
        st = self._current()
        if st is None:
            return
        imgs = st.images()
        s = st.settings
        self.h_name.setText(st.name)
        self.h_sub.setText(f"{len(st.folders)} folder(s) · {len(imgs)} images · created {st.created_at[:10]}")
        _clear_layout(self.cards)
        gray = "(R+G+B)/3" if s.gray_mode == GRAY_MEAN else "0.299·R + 0.587·G + 0.114·B"
        for i, c in enumerate((
            _card("Measurement", f"Grey OD, {TARGET_LABELS.get(st.target, st.target)}", f"−log₁₀(Ī / I₀) · grey = {gray}"),
            _card("Background I₀", f"{s.i0_fields} fields of {s.i0_field_size ** 2} px per cell",
                  f"ring {s.i0_ring_inner}–{s.i0_ring_outer} px · {i0_rule(s)}"),
            _card("Contour", METHOD_LABELS.get(s.method, s.method),
                  f"Cellpose on {'DAB map' if s.cellpose.input == 'dab' else 'RGB'} · OD threshold {s.threshold:g}"),
            _card("Stain", s.stain_preset,
                  (f"positive above OD {s.pos_threshold:g}" if s.pos_threshold > 0 else "no positive cut-off")
                  + (f" · min. area {s.min_cell_area} px" if s.min_cell_area else "")),
        )):
            self.cards.addWidget(c, 0, i)
        prog = progress(imgs)
        probands = sorted({k[0] for k in prog})
        cols = sorted({(k[1], k[2]) for k in prog})
        self.grid.clear()
        self.grid.setRowCount(len(probands)); self.grid.setColumnCount(len(cols))
        self.grid.setHorizontalHeaderLabels([f"{tp} · {mk or '–'}" for tp, mk in cols])
        self.grid.setVerticalHeaderLabels([f"{p}" + (f" · {st.groups[p]}" if p in st.groups else "") for p in probands])
        for r, p in enumerate(probands):
            for c, (tp, mk) in enumerate(cols):
                v = prog.get((p, tp, mk))
                it = QTableWidgetItem("" if v is None else f"{v[0]} / {v[1]}")
                it.setTextAlignment(Qt.AlignCenter)
                if v is not None:
                    if v[0] == v[1]:
                        it.setBackground(QColor("#1B7A4D")); it.setForeground(QColor("#FFFFFF"))
                    elif v[0]:
                        it.setBackground(QColor("#F4E8D2")); it.setForeground(QColor("#975E0C"))
                    else:
                        it.setBackground(QColor("#EEF0F4"))
                self.grid.setItem(r, c, it)
        lines = []
        for f in st.folders:
            if Path(f).is_dir():
                lines.append(f"{f}  —  {len(list_images(Path(f)))} images")
            else:
                lines.append(f"{f}  —  not found")
        self.folders.setText("\n".join(lines) or "–")

    def new_study(self) -> None:
        w = StudyWizard(self)
        if w.exec() == QDialog.Accepted:
            self.reload(w.study)

    def edit_study(self) -> None:
        st = self._current()
        if st is None:
            return
        w = StudyWizard(self, st)
        if w.exec() == QDialog.Accepted:
            self.reload(w.study)

    def save_template(self) -> None:
        st = self._current()
        if st is None:
            return
        name, ok = QInputDialog.getText(
            self, "Save as template",
            "Template name (target, file-name scheme and every measurement setting –\n"
            "not the study name, folders or groups):", text=st.name)
        name = name.strip()
        if not ok or not name:
            return
        if template_file_for(name).exists() and QMessageBox.question(
                self, "Replace template", f"A template “{name}” already exists. Replace it?") != QMessageBox.Yes:
            return
        Template.from_study(st, name, f"from study “{st.name}”").save()
        QMessageBox.information(self, "Template saved", f"“{name}” is now available in “New study”.")

    def open_selected(self) -> None:
        st = self._current()
        if st is not None:
            self.chosen = st
            self.accept()


# ─────────────────────────────────────────────
# Assistant
# ─────────────────────────────────────────────
class StudyWizard(QDialog):
    STEPS = ["1  Folders and target", "2  File names", "3  Measurement", "4  Done"]

    def __init__(self, parent=None, study: Study | None = None):
        super().__init__(parent)
        self.editing = study is not None
        self.study = copy.deepcopy(study) if study else Study(name="")
        self.setWindowTitle("Study settings" if self.editing else "New study")
        fit_to_screen(self, 900, 640)
        self._names: list[str] = []
        self._template: Template | None = None
        self._measure_key = None
        lay = QVBoxLayout(self); lay.setContentsMargins(18, 14, 18, 12); lay.setSpacing(10)
        steps = QHBoxLayout(); self.step_labels = []
        for s in self.STEPS:
            l = QLabel(s); steps.addWidget(l, 1); self.step_labels.append(l)
        lay.addLayout(steps)
        self.stack = QStackedWidget(); lay.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_folders())
        self.stack.addWidget(self._page_names())
        self.stack.addWidget(_scroll(self._page_measure()))
        self.stack.addWidget(self._page_done())
        nav = QHBoxLayout()
        b_cancel = QPushButton("Cancel"); b_cancel.clicked.connect(self.reject)
        self.b_back = QPushButton("Back"); self.b_back.clicked.connect(lambda: self._go(-1))
        self.b_next = QPushButton("Next"); self.b_next.setProperty("class", "primary"); self.b_next.clicked.connect(lambda: self._go(+1))
        nav.addWidget(b_cancel); nav.addStretch(1); nav.addWidget(self.b_back); nav.addWidget(self.b_next)
        lay.addLayout(nav)
        self._refresh_tree()
        self._go(0)

    # ── page 1 ───────────────────────────────────────────
    def _page_folders(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        form = QFormLayout(); form.setVerticalSpacing(6)
        self.t_combo = QComboBox()
        self.t_combo.addItem("none – set everything yourself", None)
        for t in list_templates():
            self.t_combo.addItem(t.name, t)
        self.t_combo.currentIndexChanged.connect(self._template_changed)
        self.t_desc = QLabel("", objectName="caption"); self.t_desc.setWordWrap(True)
        if not self.editing:
            form.addRow("Template:", self.t_combo)
            form.addRow("", self.t_desc)
        self.e_name = QLineEdit(self.study.name); self.e_name.setPlaceholderText("NK methylation 2026")
        form.addRow("Study name:", self.e_name)
        l.addLayout(form)

        split = QSplitter(Qt.Horizontal)
        lw = QWidget(); fl = QVBoxLayout(lw); fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(QLabel("<b>Image folders</b> – sub-folders included"))
        self.f_list = QListWidget()
        for f in self.study.folders:
            self.f_list.addItem(f)
        self.f_list.currentRowChanged.connect(lambda _: self._refresh_tree())
        fl.addWidget(self.f_list, 1)
        row = QHBoxLayout()
        b_add = QPushButton("Add folder"); b_add.clicked.connect(self._add_folder)
        b_rm = QPushButton("Remove"); b_rm.clicked.connect(self._remove_folder)
        row.addWidget(b_add); row.addWidget(b_rm); row.addStretch(1)
        fl.addLayout(row)
        rw = QWidget(); tl = QVBoxLayout(rw); tl.setContentsMargins(0, 0, 0, 0)
        self.tree_head = QLabel("<b>Contents</b>")
        tl.addWidget(self.tree_head)
        self.tree = QTreeWidget(); self.tree.setHeaderLabels(["Name", "Images"])
        self.tree.setTextElideMode(Qt.ElideMiddle)     # keep the image number visible
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        tl.addWidget(self.tree, 1)
        split.addWidget(lw); split.addWidget(rw); split.setSizes([320, 520])
        l.addWidget(split, 1)

        l.addWidget(QLabel("<b>What is measured?</b>"))
        self.r_cell = QRadioButton("Whole cell (e.g. NK) – Cellpose sees the colour image")
        self.r_nuc = QRadioButton("Nucleus (e.g. C2C12 Ki67) – Cellpose sees the DAB map, debris flag on")
        g = QButtonGroup(w); g.addButton(self.r_cell); g.addButton(self.r_nuc)
        (self.r_nuc if self.study.target == TARGET_NUCLEUS else self.r_cell).setChecked(True)
        l.addWidget(self.r_cell); l.addWidget(self.r_nuc)
        return w

    def _refresh_tree(self) -> None:
        """Show every added folder as a tree: sub-folders, images, other files."""
        self.tree.clear()
        total = 0
        for i in range(self.f_list.count()):
            path = Path(self.f_list.item(i).text())
            root = QTreeWidgetItem([f"📂 {path}", ""])
            self.tree.addTopLevelItem(root)
            if not path.is_dir():
                root.setText(1, "not found")
                continue
            n = _fill_tree(root, path)
            root.setText(1, f"{n} image{'s' if n != 1 else ''}")
            total += n
            root.setExpanded(True)
            for k in range(root.childCount()):        # open one level of sub-folders
                if root.child(k).childCount():
                    root.child(k).setExpanded(self.f_list.count() == 1)
        self.tree_head.setText(f"<b>Contents</b> – {total} image{'s' if total != 1 else ''} in "
                               f"{self.f_list.count()} folder{'s' if self.f_list.count() != 1 else ''}"
                               if self.f_list.count() else "<b>Contents</b> – add a folder")

    def _template_changed(self) -> None:
        t = self.t_combo.currentData()
        self._template = t
        if t is None:
            self.t_desc.setText("")
            return
        t.apply_to(self.study)
        (self.r_nuc if t.target == TARGET_NUCLEUS else self.r_cell).setChecked(True)
        self.t_desc.setText(f"{TARGET_LABELS.get(t.target, t.target)} · file-name scheme and measurement "
                            f"settings are taken over. {t.description}")

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose image folder", str(Path.home()))
        if d and not self.f_list.findItems(d, Qt.MatchExactly):
            self.f_list.addItem(d)
            self._refresh_tree()

    def _remove_folder(self) -> None:
        self.f_list.takeItem(self.f_list.currentRow())
        self._refresh_tree()

    # ── page 2 ───────────────────────────────────────────
    def _page_names(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(8)
        l.addWidget(QLabel("<b>What is in the file name?</b>  Give each part a role – the preview shows how every file is read."))
        drow = QHBoxLayout(); drow.addWidget(QLabel("Delimiters:"))
        self.d_boxes = {}
        for d in CANDIDATE_DELIMS:
            cb = QCheckBox(DELIM_LABELS[d]); cb.toggled.connect(self._delims_changed)
            self.d_boxes[d] = cb; drow.addWidget(cb)
        drow.addWidget(QLabel("  dates (2026-08-27, 27.08.2026) always stay one part", objectName="caption"))
        drow.addStretch(1)
        l.addLayout(drow)
        self.tok_row = QHBoxLayout(); self.tok_row.setSpacing(6)
        tok_box = QWidget(); tok_box.setLayout(self.tok_row)
        tok_scroll = QScrollArea(); tok_scroll.setWidgetResizable(True); tok_scroll.setFrameShape(QFrame.NoFrame)
        tok_scroll.setWidget(tok_box); tok_scroll.setFixedHeight(86)
        tok_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        l.addWidget(tok_scroll)
        self.tok_combos: list[QComboBox] = []
        self.preview = QTableWidget(0, 1 + len(META_ROLES))
        self.preview.setHorizontalHeaderLabels(["File"] + [ROLE_LABELS[r] for r in META_ROLES])
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.preview.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        l.addWidget(self.preview, 1)
        self.n_hint = QLabel(""); self.n_hint.setWordWrap(True); self.n_hint.setObjectName("banner")
        l.addWidget(self.n_hint)
        return w

    def _enter_names(self) -> None:
        self._names = []
        for i in range(self.f_list.count()):
            self._names += [p.name for p in list_images(Path(self.f_list.item(i).text()))]
        keep = (self.editing or self._template is not None) and self.study.roles
        delims = self.study.delimiters if keep else detect_delimiters(self._names)
        for d, cb in self.d_boxes.items():
            cb.blockSignals(True); cb.setChecked(d in delims); cb.blockSignals(False)
        roles = self.study.roles if keep else guess_roles(self._names, delims)
        self._build_tokens(delims, roles)

    def _delims(self) -> list[str]:
        return [d for d, cb in self.d_boxes.items() if cb.isChecked()]

    def _delims_changed(self) -> None:
        d = self._delims()
        self._build_tokens(d, guess_roles(self._names, d))

    def _build_tokens(self, delims: list[str], roles: list[str]) -> None:
        _clear_layout(self.tok_row)
        self.tok_combos = []
        sample = self._names[0] if self._names else ""
        for i, t in enumerate(tokenize(sample, delims)):
            box = QFrame(objectName="metricCard"); bl = QVBoxLayout(box); bl.setContentsMargins(8, 4, 8, 4); bl.setSpacing(2)
            lab = QLabel(f"<b>{t}</b>"); lab.setTextFormat(Qt.RichText)
            cb = QComboBox()
            for r in ROLES:
                cb.addItem(ROLE_LABELS[r], r)
            cb.setCurrentIndex(ROLES.index(roles[i]) if i < len(roles) and roles[i] in ROLES else ROLES.index(ROLE_IGNORE))
            cb.currentIndexChanged.connect(self._refresh_preview)
            bl.addWidget(lab); bl.addWidget(cb)
            self.tok_row.addWidget(box); self.tok_combos.append(cb)
        self.tok_row.addStretch(1)
        self._refresh_preview()

    def _roles(self) -> list[str]:
        return [cb.currentData() for cb in self.tok_combos]

    def _refresh_preview(self) -> None:
        delims, roles = self._delims(), self._roles()
        n = len(roles)
        rows = sorted(self._names, key=lambda x: len(tokenize(x, delims)) == n)   # odd ones first (False < True)
        self.preview.setRowCount(min(len(rows), 300))
        for r, name in enumerate(rows[:300]):
            m = parse_name(name, delims, roles)
            bad = len(tokenize(name, delims)) != n
            for c, v in enumerate([name] + [m[k] for k in META_ROLES]):
                it = QTableWidgetItem(v)
                if bad:
                    it.setForeground(QColor("#B83A3A"))
                self.preview.setItem(r, c, it)
        odd = sum(len(tokenize(x, delims)) != n for x in self._names)
        hints = []
        if odd:
            hints.append(f"{odd} file(s) have a different number of parts (red, at the top) – check the delimiters.")
        if ROLE_PROBAND not in roles:
            hints.append("No part is “Subject” – all images go under “no subject”.")
        if ROLE_TIMEPOINT not in roles:
            hints.append("No part is “Timepoint” – all images go under “no timepoint”.")
        if ROLE_GROUP not in roles:
            hints.append("No part is “Group” – you can enter groups per subject in the next step.")
        self.n_hint.setText("\n".join(hints))

    # ── page 3 ───────────────────────────────────────────
    def _page_measure(self) -> QWidget:
        w = QWidget(); grid = QGridLayout(w); grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(24); grid.setVerticalSpacing(8)
        s = self.study.settings

        self.m_gray = QComboBox(); self.m_gray.addItem("(R+G+B)/3 – ImageJ default", GRAY_MEAN)
        self.m_gray.addItem("weighted 0.299/0.587/0.114", GRAY_WEIGHTED)
        self.m_fields = QSpinBox(); self.m_fields.setRange(1, 10)
        self.m_fsize = QSpinBox(); self.m_fsize.setRange(3, 31); self.m_fsize.setSingleStep(2); self.m_fsize.setSuffix(" px side")
        self.m_rin = QSpinBox(); self.m_rin.setRange(1, 200); self.m_rin.setSuffix(" px")
        self.m_rout = QSpinBox(); self.m_rout.setRange(2, 400); self.m_rout.setSuffix(" px")
        self.m_check = QComboBox(); self.m_check.addItem("fixed range", I0_ABSOLUTE)
        self.m_check.addItem("relative to the image's free background", I0_RELATIVE)
        self.m_rel = QDoubleSpinBox(); self.m_rel.setRange(0.1, 50); self.m_rel.setSuffix(" %")
        self.m_imin = QDoubleSpinBox(); self.m_imin.setRange(0, 255)
        self.m_imax = QDoubleSpinBox(); self.m_imax.setRange(0, 255)
        self.m_method = QComboBox()
        for k, v in METHOD_LABELS.items():
            self.m_method.addItem(v, k)
        self.m_thr = QDoubleSpinBox(); self.m_thr.setDecimals(3); self.m_thr.setRange(0.005, 1); self.m_thr.setSingleStep(0.005)
        self.m_input = QComboBox(); self.m_input.addItem("colour image (RGB)", "rgb"); self.m_input.addItem("DAB map", "dab")
        self.m_debris = QDoubleSpinBox(); self.m_debris.setDecimals(3); self.m_debris.setRange(0, 1); self.m_debris.setSingleStep(0.005)
        self.m_debris.setSpecialValueText("off")
        self.m_minarea = QSpinBox(); self.m_minarea.setRange(0, 100000); self.m_minarea.setSuffix(" px")
        self.m_minarea.setSpecialValueText("off")
        self.m_pos = QDoubleSpinBox(); self.m_pos.setDecimals(3); self.m_pos.setRange(0, 2); self.m_pos.setSingleStep(0.005)
        self.m_pos.setSpecialValueText("off")
        self.m_check.currentIndexChanged.connect(self._check_mode)

        left = QFormLayout(); left.setVerticalSpacing(6)
        left.addRow(QLabel("<b>Grey value</b>"))
        left.addRow("RGB → grey:", self.m_gray)
        left.addRow(QLabel("<b>Background I₀ per cell</b>"))
        left.addRow("Fields:", self.m_fields)
        left.addRow("Field size:", self.m_fsize)
        left.addRow("Distance from contour:", self.m_rin)
        left.addRow("… up to:", self.m_rout)
        left.addRow("I₀ check:", self.m_check)
        left.addRow("Allowed deviation:", self.m_rel)
        left.addRow("I₀ valid from:", self.m_imin)
        left.addRow("… to:", self.m_imax)
        right = QFormLayout(); right.setVerticalSpacing(6)
        right.addRow(QLabel("<b>Contour</b>"))
        right.addRow("Method:", self.m_method)
        right.addRow("OD threshold:", self.m_thr)
        right.addRow("Cellpose sees:", self.m_input)
        right.addRow("Min. area:", self.m_minarea)
        right.addRow("Debris flag from H OD:", self.m_debris)
        right.addRow(QLabel("<b>Analysis</b>"))
        right.addRow("Positive above OD:", self.m_pos)
        grid.addLayout(left, 0, 0); grid.addLayout(right, 0, 1)

        gw = QWidget(); gl = QVBoxLayout(gw); gl.setContentsMargins(0, 6, 0, 0)
        gl.addWidget(QLabel("<b>Group per subject</b> (only needed if the group is not in the file name)"))
        self.g_table = QTableWidget(0, 2); self.g_table.setHorizontalHeaderLabels(["Subject", "Group"])
        self.g_table.verticalHeader().setVisible(False)
        self.g_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.g_table.setMinimumHeight(110)
        gl.addWidget(self.g_table)
        grid.addWidget(gw, 1, 0, 1, 2)
        grid.setRowStretch(1, 1)
        self._fill_measure(s)
        return w

    def _check_mode(self) -> None:
        rel = self.m_check.currentData() == I0_RELATIVE
        self.m_rel.setEnabled(rel); self.m_imin.setEnabled(not rel); self.m_imax.setEnabled(not rel)

    def _fill_measure(self, s: Settings) -> None:
        self.m_gray.setCurrentIndex(0 if s.gray_mode == GRAY_MEAN else 1)
        self.m_fields.setValue(s.i0_fields); self.m_fsize.setValue(s.i0_field_size)
        self.m_rin.setValue(s.i0_ring_inner); self.m_rout.setValue(s.i0_ring_outer)
        self.m_check.setCurrentIndex(self.m_check.findData(s.i0_check))
        self.m_rel.setValue(s.i0_rel_pct); self.m_imin.setValue(s.i0_min); self.m_imax.setValue(s.i0_max)
        self.m_method.setCurrentIndex(self.m_method.findData(s.method)); self.m_thr.setValue(s.threshold)
        self.m_input.setCurrentIndex(self.m_input.findData(s.cellpose.input))
        self.m_debris.setValue(s.debris_h); self.m_minarea.setValue(s.min_cell_area)
        self.m_pos.setValue(s.pos_threshold)
        self._check_mode()

    def _enter_measure(self) -> None:
        nucleus = self.r_nuc.isChecked()
        key = (nucleus, self._template.name if self._template else None)
        if key != self._measure_key:
            # Fill the form once per target/template, so edits survive Back/Next.
            base = copy.deepcopy(self.study)
            fresh = not self.editing and self._template is None
            if fresh or (base.target == TARGET_NUCLEUS) != nucleus:
                base.target = TARGET_NUCLEUS if nucleus else TARGET_CELL
                base.apply_target()
            self._fill_measure(base.settings)
            self._measure_key = key
        delims, roles = self._delims(), self._roles()
        probands = sorted({parse_name(n, delims, roles)["proband"] for n in self._names} - {""})
        self.g_table.setRowCount(len(probands))
        for r, p in enumerate(probands):
            a = QTableWidgetItem(p); a.setFlags(a.flags() & ~Qt.ItemIsEditable)
            self.g_table.setItem(r, 0, a)
            self.g_table.setItem(r, 1, QTableWidgetItem(self.study.groups.get(p, "")))
        self.g_table.setEnabled(ROLE_GROUP not in roles)

    # ── page 4 ───────────────────────────────────────────
    def _page_done(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        self.summary = QLabel(""); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.RichText)
        l.addWidget(self.summary); l.addStretch(1)
        return w

    def _collect(self) -> None:
        st = self.study
        st.name = self.e_name.text().strip()
        st.folders = [self.f_list.item(i).text() for i in range(self.f_list.count())]
        st.target = TARGET_NUCLEUS if self.r_nuc.isChecked() else TARGET_CELL
        st.delimiters, st.roles = self._delims(), self._roles()
        s = st.settings
        s.gray_mode = self.m_gray.currentData()
        s.i0_fields, s.i0_field_size = self.m_fields.value(), self.m_fsize.value()
        s.i0_ring_inner, s.i0_ring_outer = self.m_rin.value(), self.m_rout.value()
        s.i0_min, s.i0_max = self.m_imin.value(), self.m_imax.value()
        s.method, s.threshold = self.m_method.currentData(), round(self.m_thr.value(), 4)
        s.cellpose.input = self.m_input.currentData()
        s.debris_h = round(self.m_debris.value(), 4)
        s.i0_check, s.i0_rel_pct = self.m_check.currentData(), round(self.m_rel.value(), 2)
        s.min_cell_area, s.pos_threshold = self.m_minarea.value(), round(self.m_pos.value(), 4)
        st.groups = {}
        for r in range(self.g_table.rowCount()):
            g = (self.g_table.item(r, 1).text() if self.g_table.item(r, 1) else "").strip()
            if g:
                st.groups[self.g_table.item(r, 0).text()] = g

    def _enter_done(self) -> None:
        self._collect()
        st, s = self.study, self.study.settings
        imgs = st.images()
        self.summary.setText(
            f"<h3>{st.name}</h3>"
            f"<p>{len(st.folders)} folder(s) · {len(imgs)} images · {len({i.proband for i in imgs})} subjects · "
            f"markers: {', '.join(st.markers(imgs)) or '–'}</p>"
            f"<p>Measured: <b>{TARGET_LABELS[st.target]}</b>, OD = −log₁₀(Ī / I₀) from the grey value.<br>"
            f"I₀: {s.i0_fields} fields of {s.i0_field_size}×{s.i0_field_size} px, {s.i0_ring_inner}–{s.i0_ring_outer} px "
            f"from the contour, {i0_rule(s)}.<br>"
            f"Contour: {METHOD_LABELS[s.method]}, Cellpose on {'DAB map' if s.cellpose.input == 'dab' else 'RGB'}.</p>"
            "<p>Cellpose starts in the background as soon as the review is open.</p>")

    # ── navigation ───────────────────────────────────────
    def _go(self, step: int) -> None:
        i = self.stack.currentIndex()
        if step > 0:
            if i == 0:
                if not self.e_name.text().strip():
                    QMessageBox.information(self, self.windowTitle(), "Give the study a name.")
                    return
                if not self.f_list.count():
                    QMessageBox.information(self, self.windowTitle(), "Add at least one image folder.")
                    return
            if i == len(self.STEPS) - 1:
                self._collect()
                self.study.save()
                self.accept()
                return
        j = max(0, min(len(self.STEPS) - 1, i + step))
        if step > 0 and j == 1:
            self._enter_names()
        if step > 0 and j == 2:
            self._enter_measure()
        if step > 0 and j == 3:
            self._enter_done()
        self.stack.setCurrentIndex(j)
        for k, lab in enumerate(self.step_labels):
            lab.setStyleSheet("font-weight:700;" if k == j else "color:#616B7C;")
        self.b_back.setEnabled(j > 0)
        self.b_next.setText("Save study" if j == len(self.STEPS) - 1 else "Next")
