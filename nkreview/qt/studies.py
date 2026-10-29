"""Study overview and the 'New study' assistant (mockup screens 2 and 3)."""

from __future__ import annotations

import copy
import os
from collections import Counter
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSlider,
    QSpinBox,
    QSplitter, QStackedWidget, QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..segment import METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, MODEL_INSTANSEG
from .cellpanel import Fold
from ..state import (
    GRAY_MEAN, GRAY_WEIGHTED, I0_ABSOLUTE, I0_LOCAL, I0_PER_CELL, I0_PER_IMAGE, I0_RELATIVE, IMG_EXTS, Settings,
    list_images,
)
from ..study import (
    CANDIDATE_DELIMS, ROLE_CELLTYPE, ROLE_DATE, ROLE_GROUP, ROLE_IGNORE, ROLE_IMAGE_NUM, ROLE_LABELS,
    ROLE_MARKER, ROLE_PROBAND, ROLE_TIMEPOINT,
    MAG_LABELS, MAGNIFICATIONS, ROLES, objective_rule, TARGET_CELL, TARGET_LABELS, TARGET_NUCLEUS, Study, Template,
    detect_delimiters,
    guess_roles, list_studies, list_templates, parse_name, progress, proband_progress,
    template_file_for, tokenize,
)

DELIM_LABELS = {" ": "space", "_": "_", "-": "-", ".": "."}
ROLE_COLORS = {ROLE_PROBAND: "#2F6FD0", ROLE_TIMEPOINT: "#C7700E", ROLE_MARKER: "#7B4BC4",
               ROLE_IMAGE_NUM: "#1C8A84", ROLE_GROUP: "#3D8B3D", ROLE_CELLTYPE: "#B5487A",
               ROLE_DATE: "#6B7A8F", ROLE_IGNORE: "#A0A0A0"}
METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD edge)", METHOD_CELLPOSE: "Model contour only",
                 METHOD_THRESHOLD: "OD threshold only"}
MODEL_LABELS = {"cpsam": "Cellpose-SAM (cells)", MODEL_INSTANSEG: "InstanSeg (nuclei)"}
MAG_CUSTOM = ""
TREE_MAX_FILES = 300            # per folder in the folder tree; the rest is summarised


def segmentation_text(s: Settings) -> str:
    """'InstanSeg (nuclei), high magnification' / 'Cellpose-SAM (cells) on the DAB map'."""
    if s.cellpose.model == MODEL_INSTANSEG:
        return f"{MODEL_LABELS[MODEL_INSTANSEG]}, {MAG_LABELS.get(magnification_of(s), f'{s.cellpose.pixel_size:g} µm/px')}"
    return f"{MODEL_LABELS.get(s.cellpose.model, s.cellpose.model)} on {'the DAB map' if s.cellpose.input == 'dab' else 'RGB'}"


def magnification_of(s: Settings) -> str:
    """The preset the settings match, MAG_CUSTOM if none."""
    now = {"pixel_size": s.cellpose.pixel_size, "min_cell_area": s.min_cell_area, "max_cell_area": s.max_cell_area}
    return next((k for k, v in MAGNIFICATIONS.items() if v == now), MAG_CUSTOM)


def i0_rule(s: Settings) -> str:
    if s.i0_check == I0_RELATIVE:
        return f"± {s.i0_rel_pct:g} % of the image's free background"
    if s.i0_check == I0_LOCAL:
        return f"fields agree within ± {s.i0_rel_pct:g} %"
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
class _StudyCard(QFrame):
    """One study: name, target and markers, folders, progress per subject,
    the measurement in one line. Click selects, double-click opens."""

    def __init__(self, picker: "StudyPicker", st: Study, is_last: bool):
        super().__init__(objectName="studyCard")
        self.picker, self.study = picker, st
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(200)
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(6)
        top = QHBoxLayout()
        name = QLabel(st.name); name.setStyleSheet("font-size: 17px; font-weight: 600;"); name.setWordWrap(True)
        top.addWidget(name, 1)
        if is_last:
            tag = QLabel("last opened"); tag.setStyleSheet("color: #2161D9; font-size: 11px;")
            top.addWidget(tag, 0, Qt.AlignTop)
        v.addLayout(top)
        self.kind = QLabel(TARGET_LABELS.get(st.target, st.target)); self.kind.setObjectName("caption")
        v.addWidget(self.kind)
        self.folder = QLabel(""); self.folder.setWordWrap(True); self.folder.setObjectName("caption")
        v.addWidget(self.folder)
        self.big = QLabel(""); v.addWidget(self.big)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); self.bar.setRange(0, 0); v.addWidget(self.bar)
        self.rest = QLabel("counting …"); self.rest.setObjectName("caption"); self.rest.setWordWrap(True)
        v.addWidget(self.rest)
        self.dots = QLabel(""); self.dots.setTextFormat(Qt.RichText); self.dots.setWordWrap(True)
        v.addWidget(self.dots)
        v.addStretch(1)
        s = st.settings
        meas = QLabel(f"Grey OD · I₀ {i0_rule(s)} · {segmentation_text(s)}")
        meas.setObjectName("caption"); meas.setWordWrap(True)
        v.addWidget(meas)
        self.counted = False

    def count(self) -> None:
        """Read the review state of every image (fast; done after the window shows)."""
        st = self.study
        missing = [f for f in st.folders if not Path(f).is_dir()]
        imgs = st.images()
        markers = st.markers(imgs)
        self.kind.setText(f"{TARGET_LABELS.get(st.target, st.target)} · {', '.join(markers) or 'no marker'}")
        if not st.folders:
            self.folder.setText("no folder")
        elif missing:
            self.folder.setText(f"⚠ folder not found: {missing[0]}")
            self.folder.setStyleSheet("color: #975E0C;")
        else:
            first = Path(st.folders[0])
            more = f" + {len(st.folders) - 1} more" if len(st.folders) > 1 else ""
            self.folder.setText(f"{first.name}{more} · {len(imgs)} images")
            self.folder.setToolTip("\n".join(st.folders))
        prog = progress(imgs)
        per: dict = {}
        for (subj, _tp, _mk), (done, n) in prog.items():
            a = per.setdefault(subj, [0, 0]); a[0] += done; a[1] += n
        n_sub = len(per)
        sub_done = sum(1 for d, n in per.values() if d == n)
        img_done = sum(d for d, _ in per.values())
        self.big.setText(f'<span style="font-size:22px; font-weight:700; color:#1B7A4D;">{sub_done}</span>'
                         f'&nbsp;<span style="color:#5B6476;">/ {n_sub} subjects done</span>')
        self.bar.setRange(0, max(1, len(imgs))); self.bar.setValue(img_done)
        wip = sum(1 for d, n in per.values() if 0 < d < n)
        self.rest.setText(f"{img_done} of {len(imgs)} images reviewed · {wip} subject(s) in progress, "
                          f"{n_sub - sub_done - wip} open")
        chips = []
        for subj in sorted(per)[:14]:
            d, n = per[subj]
            col, bg = (("#FFFFFF", "#1B7A4D") if d == n else ("#975E0C", "#F4E8D2") if d else ("#5B6476", "#EEF0F4"))
            chips.append(f'<span style="background:{bg}; color:{col};">&nbsp;{subj}&nbsp;</span>')
        if len(per) > 14:
            chips.append(f'<span style="color:#5B6476;">+{len(per) - 14}</span>')
        self.dots.setText(" ".join(chips))
        self.counted = True

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self); self.style().polish(self)

    def mousePressEvent(self, _e):
        self.picker.select(self)

    def mouseDoubleClickEvent(self, _e):
        self.picker.select(self)
        self.picker.open_selected()


def _last_study_file() -> str | None:
    import json
    from ..paths import app_support_dir
    try:
        return json.loads((app_support_dir() / "app.json").read_text("utf-8")).get("study")
    except (OSError, ValueError):
        return None


class StudyPicker(QDialog):
    """All studies as cards (as in ErysReview), actions for the selected one.
    `self.chosen` after accept."""

    def __init__(self, parent=None, current: Study | None = None):
        super().__init__(parent)
        self.setWindowTitle("Studies")
        fit_to_screen(self, 1080, 720)
        self.chosen: Study | None = None
        self._selected: _StudyCard | None = None
        self.cards: list[_StudyCard] = []
        v = QVBoxLayout(self); v.setContentsMargins(36, 28, 36, 22); v.setSpacing(16)
        head = QHBoxLayout()
        col = QVBoxLayout()
        t = QLabel("Studies"); t.setStyleSheet("font-size: 26px; font-weight: 700;")
        sub = QLabel("Choose a study to review, or create a new one. Double-click opens.", objectName="caption")
        col.addWidget(t); col.addWidget(sub)
        head.addLayout(col, 1)
        new = QPushButton("+ New study"); new.setProperty("class", "primary"); new.clicked.connect(self.new_study)
        head.addWidget(new, 0, Qt.AlignTop)
        v.addLayout(head)

        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setFrameShape(QFrame.NoFrame)
        v.addWidget(self.area, 1)

        v.addWidget(QLabel("SELECTED STUDY", objectName="caption"))
        acts = QHBoxLayout(); acts.setSpacing(8)
        self.btn_open = QPushButton("Open"); self.btn_open.setProperty("class", "primary")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_set = QPushButton("Settings"); self.btn_set.clicked.connect(self.edit_study)
        self.btn_tpl = QPushButton("Save as template"); self.btn_tpl.clicked.connect(self.save_template)
        self.btn_dup = QPushButton("Duplicate"); self.btn_dup.clicked.connect(self._duplicate)
        self.btn_ren = QPushButton("Rename"); self.btn_ren.clicked.connect(self._rename)
        self.btn_del = QPushButton("Delete"); self.btn_del.clicked.connect(self._delete)
        self.btn_del.setStyleSheet("color: #B83A3A;")
        for b in (self.btn_open, self.btn_set, self.btn_tpl, self.btn_dup, self.btn_ren):
            acts.addWidget(b)
        acts.addStretch(1); acts.addWidget(self.btn_del)
        v.addLayout(acts)
        self._fill(current.file if current else None)

    # ── cards ────────────────────────────────────────────
    def _fill(self, select_file: str | None = None) -> None:
        holder = QWidget()
        grid = QGridLayout(holder); grid.setSpacing(16); grid.setContentsMargins(0, 0, 0, 0)
        self.cards = []
        last = _last_study_file()
        studies = sorted(list_studies(), key=lambda s: (s.file != last, s.name.lower()))
        cols = 3
        for i, st in enumerate(studies):
            card = _StudyCard(self, st, st.file == last)
            grid.addWidget(card, i // cols, i % cols)
            self.cards.append(card)
        for c in range(cols):
            grid.setColumnStretch(c, 1)
        grid.setRowStretch(len(studies) // cols + 1, 1)
        if not studies:
            empty = QLabel("No study yet.\nCreate one with “+ New study”: choose folders, map the file names, "
                           "set the measurement – or start from a template.")
            empty.setAlignment(Qt.AlignCenter); empty.setObjectName("caption")
            grid.addWidget(empty, 0, 0, 1, cols)
        self.area.setWidget(holder)
        self._selected = None
        target = next((c for c in self.cards if c.study.file == (select_file or last)), None)
        if target is None and self.cards:
            target = self.cards[0]
        if target is not None:
            self.select(target)
        self._update_buttons()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._count_next)       # counts after the window is up

    def _count_next(self) -> None:
        card = next((c for c in self.cards if not c.counted), None)
        if card is None:
            return
        try:
            card.count()
        except Exception:  # noqa: BLE001 — an unreadable folder must not break the list
            card.counted = True
            card.rest.setText("could not be counted")
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._count_next)

    def select(self, card: _StudyCard) -> None:
        if self._selected is not None:
            self._selected.set_selected(False)
        self._selected = card
        card.set_selected(True)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = self._selected is not None
        for b in (self.btn_open, self.btn_set, self.btn_tpl, self.btn_dup, self.btn_ren, self.btn_del):
            b.setEnabled(has)

    def _current(self) -> Study | None:
        return self._selected.study if self._selected else None

    # ── actions ──────────────────────────────────────────
    def open_selected(self) -> None:
        st = self._current()
        if st is not None:
            self.chosen = st
            self.accept()

    def new_study(self) -> None:
        w = StudyWizard(self)
        if w.exec() == QDialog.Accepted:
            self._fill(w.study.file)

    def edit_study(self) -> None:
        st = self._current()
        if st is None:
            return
        w = StudySettings(self, st)
        w.exec()
        self._fill((w.saved or st).file)

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

    def _ask_name(self, title: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, "Study name:", text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if any(s.name == name for s in list_studies()):
            QMessageBox.information(self, title, f"A study “{name}” already exists.")
            return None
        return name

    def _duplicate(self) -> None:
        st = self._current()
        name = self._ask_name("Duplicate study", f"{st.name} 2") if st else None
        if not name:
            return
        dup = copy.deepcopy(st); dup.name, dup.file, dup.created_at = name, "", ""
        dup.save()
        self._fill(dup.file)

    def _rename(self) -> None:
        st = self._current()
        name = self._ask_name("Rename study", st.name) if st else None
        if not name:
            return
        st.name = name
        st.save()                     # same file, new name
        self._fill(st.file)

    def _delete(self) -> None:
        st = self._current()
        if st is None:
            return
        if QMessageBox.question(
                self, "Delete study",
                f"Delete the study “{st.name}”?\n\nOnly the study entry is removed. Images, Cellpose results, "
                "reviews (in each folder's .nk_cache) and exports stay where they are.") != QMessageBox.Yes:
            return
        try:
            st.path.unlink()
        except OSError as e:
            QMessageBox.warning(self, "Delete study", f"Could not delete: {e}")
        self._fill()


# ─────────────────────────────────────────────
# Assistant
# ─────────────────────────────────────────────
class StudyForm:
    """The study's fields, shared by the 'New study' assistant and the settings
    window. Builders return widgets; `_collect(study)` writes them back.
    Needs on self: study, editing, _template, _names, _measure_key."""

    def _form_changed(self) -> None:          # settings window counts changes here
        pass

    # ── name, folders, target ────────────────────────────
    def _name_edit(self) -> QLineEdit:
        self.e_name = QLineEdit(self.study.name); self.e_name.setPlaceholderText("NK methylation 2026")
        self.e_name.textChanged.connect(lambda _: self._form_changed())
        return self.e_name

    def _folders_box(self) -> QWidget:
        split = QSplitter(Qt.Horizontal); split.setChildrenCollapsible(False)
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
        split.addWidget(lw); split.addWidget(rw); split.setSizes([300, 480])
        split.setMinimumHeight(240)
        return split

    def _target_box(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(4)
        self.r_cell = QRadioButton("Whole cell (e.g. NK) – Cellpose-SAM on the colour image")
        self.r_nuc = QRadioButton("Nucleus (e.g. C2C12 Ki67) – InstanSeg finds pale and dark nuclei, debris flag on")
        g = QButtonGroup(w); g.addButton(self.r_cell); g.addButton(self.r_nuc)
        (self.r_nuc if self.study.target == TARGET_NUCLEUS else self.r_cell).setChecked(True)
        self.r_nuc.toggled.connect(lambda _: self._target_toggled())
        l.addWidget(self.r_cell); l.addWidget(self.r_nuc)
        return w

    def _target_toggled(self) -> None:
        self._form_changed()

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

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose image folder", str(Path.home()))
        if d and not self.f_list.findItems(d, Qt.MatchExactly):
            self.f_list.addItem(d)
            self._refresh_tree()
            self._folders_changed()

    def _remove_folder(self) -> None:
        self.f_list.takeItem(self.f_list.currentRow())
        self._refresh_tree()
        self._folders_changed()

    def _folders_changed(self) -> None:
        self._form_changed()

    # ── file names ───────────────────────────────────────
    def _names_box(self) -> QWidget:
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
        self.role_hint = QLabel("", objectName="caption")
        l.addWidget(self.role_hint)
        self.chip_row = QHBoxLayout(); self.chip_row.setSpacing(8)
        l.addLayout(self.chip_row)
        self._role_list: list[str] = []
        head = QHBoxLayout(); head.addWidget(QLabel("<b>Sample</b>")); head.addStretch(1)
        self.sample_count = QLabel("", objectName="caption"); head.addWidget(self.sample_count)
        l.addLayout(head)
        self.preview = QTableWidget(0, 1)
        self.preview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.preview.verticalHeader().setVisible(False)
        self.preview.setFixedHeight(150)
        l.addWidget(self.preview)
        row = QHBoxLayout()
        self.read_line = QLabel(""); self.read_line.setWordWrap(True)
        row.addWidget(self.read_line, 1)
        self.fix_btn = QPushButton(""); self.fix_btn.clicked.connect(self._apply_fix); self.fix_btn.hide()
        row.addWidget(self.fix_btn)
        l.addLayout(row)
        l.addStretch(1)
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
        """One chip per part of a typical name, as in ErysReview: click → menu of roles."""
        _clear_layout(self.chip_row)
        widths = Counter(len(tokenize(n, delims)) for n in self._names[:500])
        common = widths.most_common(1)[0][0] if widths else 0
        sample = next((n for n in self._names if len(tokenize(n, delims)) == common), "")
        toks = tokenize(sample, delims) if sample else []
        self._role_list = (list(roles) + [ROLE_IGNORE] * len(toks))[:len(toks)]
        self.role_hint.setText(f"Click a part to say what it means – example: {sample}" if sample else "")
        for i, tok in enumerate(toks):
            role = self._role_list[i]
            col = ROLE_COLORS[role]
            b = QPushButton(f"{tok}\n{ROLE_LABELS[role]}"); b.setObjectName("tokenChip")
            b.setStyleSheet(f"QPushButton#tokenChip {{ border: 2px solid {col}; border-radius: 10px;"
                            f" padding: 6px 12px; text-align: left; min-height: 40px; }}")
            menu = QMenu(b)
            for r in ROLES:
                menu.addAction(ROLE_LABELS[r]).triggered.connect(lambda _=False, i=i, r=r: self._set_role(i, r))
            b.setMenu(menu)
            self.chip_row.addWidget(b)
        self.chip_row.addStretch(1)
        self._refresh_preview()

    def _set_role(self, i: int, role: str) -> None:
        roles = [ROLE_IGNORE if (r == role and role != ROLE_IGNORE) else r for r in self._role_list]   # one part per role
        roles[i] = role
        self._build_tokens(self._delims(), roles)

    def _roles(self) -> list[str]:
        return list(self._role_list)

    def _unassigned(self, name: str, delims: list[str], roles: list[str]) -> bool:
        """Read wrong: another number of parts, or no subject where a part is the subject."""
        return (len(tokenize(name, delims)) != len(roles)
                or (ROLE_PROBAND in roles and not parse_name(name, delims, roles)[ROLE_PROBAND]))

    def _read_count(self, delims: list[str], roles: list[str]) -> int:
        return sum(not self._unassigned(n, delims, roles) for n in self._names)

    def _refresh_preview(self) -> None:
        delims, roles = self._delims(), self._roles()
        shown = [r for r in roles if r != ROLE_IGNORE]
        good = [n for n in self._names if not self._unassigned(n, delims, roles)]
        bad = [n for n in self._names if self._unassigned(n, delims, roles)]
        take_bad = bad[:1] if good else bad[:4]          # the odd ones first, so a problem shows at a glance
        rows = take_bad + good[:4 - len(take_bad)]
        self.preview.clear()
        self.preview.setColumnCount(len(shown) + 2); self.preview.setRowCount(len(rows))
        self.preview.setHorizontalHeaderLabels(["File"] + [ROLE_LABELS[r] for r in shown] + [""])
        hh = self.preview.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents); hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for r, name in enumerate(rows):
            ok = name in good
            m = parse_name(name, delims, roles)
            for c, v in enumerate([name] + [m[k] if ok else "—" for k in shown]):
                self.preview.setItem(r, c, QTableWidgetItem(v))
            mark = QTableWidgetItem("✓" if ok else "⚠ not assigned")
            mark.setForeground(QColor("#1B7A4D" if ok else "#975E0C"))
            self.preview.setItem(r, len(shown) + 1, mark)
        self.sample_count.setText(f"{len(rows)} of {len(self._names)}")
        self.fix_btn.hide(); self._fix = None
        if ROLE_PROBAND not in roles:
            text, warn = "⚠ Choose a part as “Subject”.", True
        else:
            parts = [f"{len(good)} of {len(self._names)} files read"]
            for role in shown:
                if role == ROLE_IMAGE_NUM:
                    continue
                k = len({parse_name(n, delims, roles)[role] for n in good} - {""})
                parts.append(f"{k} {ROLE_LABELS[role].lower()}{'s' if k != 1 else ''}")
            if bad:
                parts.append(f"{len(bad)} not assigned")
                base = len(good)
                gains = {d: self._read_count(delims + [d], roles) - base for d in CANDIDATE_DELIMS if d not in delims}
                d = max(gains, key=gains.get, default=None)
                if d is not None and gains[d] > 0:
                    self._fix = d
                    self.fix_btn.setText(f"Add delimiter „{DELIM_LABELS[d]}“"); self.fix_btn.show()
            text, warn = ("⚠ " if bad else "✓ ") + " · ".join(parts), bool(bad)
        self.read_line.setText(text)
        self.read_line.setStyleSheet(f"color: {'#975E0C' if warn else '#1B7A4D'};")
        self._fill_groups()
        self._form_changed()

    def _apply_fix(self) -> None:
        if self._fix:
            cb = self.d_boxes[self._fix]
            cb.blockSignals(True); cb.setChecked(True); cb.blockSignals(False)
            self._build_tokens(self._delims(), self._roles())

    # ── measurement ──────────────────────────────────────
    def _build_measure_widgets(self) -> None:
        self.m_gray = QComboBox(); self.m_gray.addItem("(R+G+B)/3 – ImageJ default", GRAY_MEAN)
        self.m_gray.addItem("weighted 0.299/0.587/0.114", GRAY_WEIGHTED)
        self.m_scope = QComboBox()
        self.m_scope.addItem("per cell – fields next to each cell", I0_PER_CELL)
        self.m_scope.addItem("per image – one set of fields for all cells", I0_PER_IMAGE)
        self.m_scope.currentIndexChanged.connect(lambda _: self._scope_mode())
        self.m_scope_hint = QLabel("Only for evenly lit or flat-field corrected images. With vignetting the "
                                   "OD of a cell can be off by 10–25 % (pilot images); use “per cell” there.",
                                   objectName="banner")
        self.m_scope_hint.setWordWrap(True)
        self.m_fields = QSpinBox(); self.m_fields.setRange(1, 10)
        self.m_fsize = QSpinBox(); self.m_fsize.setRange(3, 31); self.m_fsize.setSingleStep(2); self.m_fsize.setSuffix(" px side")
        self.m_rin = QSpinBox(); self.m_rin.setRange(1, 200); self.m_rin.setSuffix(" px")
        self.m_rout = QSpinBox(); self.m_rout.setRange(2, 400); self.m_rout.setSuffix(" px")
        self.m_check = QComboBox(); self.m_check.addItem("fixed range", I0_ABSOLUTE)
        self.m_check.addItem("relative to the image's free background", I0_RELATIVE)
        self.m_check.addItem("fields agree with each other (local, for uneven illumination)", I0_LOCAL)
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
        self.m_maxarea = QSpinBox(); self.m_maxarea.setRange(0, 1000000); self.m_maxarea.setSuffix(" px")
        self.m_maxarea.setSpecialValueText("off")
        self.m_model = QComboBox()
        for k, v in MODEL_LABELS.items():
            self.m_model.addItem(v, k)
        self.m_mag = QComboBox()
        for k, v in MAG_LABELS.items():
            p = MAGNIFICATIONS[k]
            self.m_mag.addItem(f"{v} – {p['pixel_size']:g} µm/px, {p['min_cell_area']}–{p['max_cell_area']} px", k)
        self.m_mag.addItem("custom", MAG_CUSTOM)
        self.m_px = QDoubleSpinBox(); self.m_px.setDecimals(3); self.m_px.setRange(0, 2); self.m_px.setSingleStep(0.01)
        self.m_px.setSuffix(" µm/px"); self.m_px.setSpecialValueText("unknown")
        self.m_pos = QDoubleSpinBox(); self.m_pos.setDecimals(3); self.m_pos.setRange(0, 2); self.m_pos.setSingleStep(0.005)
        self.m_pos.setSpecialValueText("off")
        self.m_high = QDoubleSpinBox(); self.m_high.setDecimals(2); self.m_high.setRange(0.01, 3); self.m_high.setSingleStep(0.05)
        self.m_diam = QDoubleSpinBox(); self.m_diam.setRange(5, 300); self.m_diam.setDecimals(0); self.m_diam.setSuffix(" px")
        self.m_flow = QDoubleSpinBox(); self.m_flow.setRange(0, 3); self.m_flow.setSingleStep(0.05)
        self.m_prob = QDoubleSpinBox(); self.m_prob.setRange(-6, 6); self.m_prob.setSingleStep(0.25)
        self.m_check.currentIndexChanged.connect(self._check_mode)
        self.m_model.currentIndexChanged.connect(lambda _: self._model_mode())
        self.m_mag.activated.connect(lambda _: self._mag_chosen())
        for w in (self.m_minarea, self.m_maxarea, self.m_px):
            w.valueChanged.connect(lambda _: self._sync_mag())
        for w in (self.m_gray, self.m_check, self.m_method, self.m_input, self.m_model, self.m_scope):
            w.currentIndexChanged.connect(lambda _: self._form_changed())
        for w in (self.m_fields, self.m_fsize, self.m_rin, self.m_rout, self.m_rel, self.m_imin, self.m_imax,
                  self.m_thr, self.m_debris, self.m_minarea, self.m_maxarea, self.m_px, self.m_pos, self.m_high,
                  self.m_diam, self.m_flow, self.m_prob):
            w.valueChanged.connect(lambda _: self._form_changed())
        self._fill_measure(self.study.settings)

    @staticmethod
    def _form(rows: list) -> QWidget:
        w = QWidget(); f = QFormLayout(w); f.setContentsMargins(0, 0, 0, 0); f.setVerticalSpacing(6)
        for r in rows:
            if isinstance(r, str):
                f.addRow(QLabel(f"<b>{r}</b>"))
            else:
                f.addRow(*r)
        return w

    def _form_grey_i0(self) -> QWidget:
        w = self._form(["Grey value", ("RGB → grey:", self.m_gray), "Background I₀",
                           ("I₀ from:", self.m_scope), ("", self.m_scope_hint),
                           ("Fields:", self.m_fields), ("Field size:", self.m_fsize),
                           ("Distance from contour:", self.m_rin), ("… up to:", self.m_rout),
                           ("I₀ check:", self.m_check), ("Allowed deviation:", self.m_rel),
                           ("I₀ valid from:", self.m_imin), ("… to:", self.m_imax)])
        self._scope_mode()
        return w

    def _form_contour(self) -> QWidget:
        return self._form(["Contour", ("Model:", self.m_model), ("Magnification:", self.m_mag),
                           ("Pixel size:", self.m_px), ("Method:", self.m_method), ("OD threshold:", self.m_thr),
                           ("Cellpose sees:", self.m_input), ("Min. area:", self.m_minarea),
                           ("Max. area:", self.m_maxarea), ("Debris flag from H OD:", self.m_debris)])

    def _form_cellpose(self) -> QWidget:
        self.cp_box = self._form(["Cellpose (advanced)", ("Diameter:", self.m_diam), ("Flow threshold:", self.m_flow),
                                  ("Cell probability:", self.m_prob)])
        self._model_mode()
        return self.cp_box

    def _model_mode(self) -> None:
        """Magnification and pixel size belong to InstanSeg; input and the advanced fields to Cellpose."""
        inst = self.m_model.currentData() == MODEL_INSTANSEG
        self.m_mag.setEnabled(inst); self.m_px.setEnabled(inst); self.m_input.setEnabled(not inst)
        if hasattr(self, "cp_box"):
            self.cp_box.setEnabled(not inst)

    def _mag_chosen(self) -> None:
        if self.m_mag.currentData() == MAG_CUSTOM:
            return
        p = MAGNIFICATIONS[self.m_mag.currentData()]
        self.m_px.setValue(p["pixel_size"]); self.m_minarea.setValue(p["min_cell_area"])
        self.m_maxarea.setValue(p["max_cell_area"])

    def _sync_mag(self) -> None:
        """Show the preset the three fields match, or 'custom'."""
        s = Settings()
        s.cellpose.pixel_size = round(self.m_px.value(), 3)
        s.min_cell_area, s.max_cell_area = self.m_minarea.value(), self.m_maxarea.value()
        self.m_mag.blockSignals(True)
        self.m_mag.setCurrentIndex(self.m_mag.findData(magnification_of(s)))
        self.m_mag.blockSignals(False)

    def _form_analysis(self) -> QWidget:
        return self._form(["Analysis", ("Positive above OD:", self.m_pos), ("Share of pixels above OD:", self.m_high)])

    def _groups_box(self) -> QWidget:
        gw = QWidget(); gl = QVBoxLayout(gw); gl.setContentsMargins(0, 0, 0, 0)
        gl.addWidget(QLabel("<b>Group per subject</b> (only needed if the group is not in the file name)"))
        self.g_table = QTableWidget(0, 2); self.g_table.setHorizontalHeaderLabels(["Subject", "Group"])
        self.g_table.verticalHeader().setVisible(False)
        self.g_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.g_table.setMinimumHeight(110)
        self.g_table.itemChanged.connect(lambda _: self._form_changed())
        gl.addWidget(self.g_table)
        return gw

    def _scope_mode(self) -> None:
        """Per image the fields lie in the free background, not in a ring around the contour."""
        ring = self.m_scope.currentData() != I0_PER_IMAGE
        for w, show in ((self.m_rin, ring), (self.m_rout, ring), (self.m_scope_hint, not ring)):
            w.setEnabled(show)
            form = w.parentWidget().layout() if w.parentWidget() else None
            if isinstance(form, QFormLayout):
                form.setRowVisible(w, show)

    def _check_mode(self) -> None:
        mode = self.m_check.currentData()
        self.m_rel.setEnabled(mode != I0_ABSOLUTE)
        self.m_imin.setEnabled(mode == I0_ABSOLUTE); self.m_imax.setEnabled(mode == I0_ABSOLUTE)

    def _fill_measure(self, s: Settings) -> None:
        self.m_gray.setCurrentIndex(0 if s.gray_mode == GRAY_MEAN else 1)
        self.m_scope.setCurrentIndex(max(0, self.m_scope.findData(s.i0_scope)))
        self.m_fields.setValue(s.i0_fields); self.m_fsize.setValue(s.i0_field_size)
        self.m_rin.setValue(s.i0_ring_inner); self.m_rout.setValue(s.i0_ring_outer)
        self.m_check.setCurrentIndex(self.m_check.findData(s.i0_check))
        self.m_rel.setValue(s.i0_rel_pct); self.m_imin.setValue(s.i0_min); self.m_imax.setValue(s.i0_max)
        self.m_method.setCurrentIndex(self.m_method.findData(s.method)); self.m_thr.setValue(s.threshold)
        self.m_input.setCurrentIndex(self.m_input.findData(s.cellpose.input))
        self.m_debris.setValue(s.debris_h); self.m_minarea.setValue(s.min_cell_area)
        self.m_maxarea.setValue(s.max_cell_area); self.m_px.setValue(s.cellpose.pixel_size)
        idx = self.m_model.findData(s.cellpose.model)
        if idx < 0:                              # a model name from a hand-edited file: show it as it is
            self.m_model.addItem(s.cellpose.model, s.cellpose.model); idx = self.m_model.count() - 1
        self.m_model.setCurrentIndex(idx)
        self._sync_mag()
        self.m_pos.setValue(s.pos_threshold); self.m_high.setValue(s.od_high)
        self.m_diam.setValue(s.cellpose.diameter); self.m_flow.setValue(s.cellpose.flow_threshold)
        self.m_prob.setValue(s.cellpose.cellprob_threshold)
        self._check_mode()

    def _fill_groups(self) -> None:
        if not hasattr(self, "g_table"):
            return
        current = {}
        for r in range(self.g_table.rowCount()):
            a, b = self.g_table.item(r, 0), self.g_table.item(r, 1)
            if a and b and b.text().strip():
                current[a.text()] = b.text().strip()
        groups = {**self.study.groups, **current}
        delims, roles = self._delims(), self._roles()
        probands = sorted({parse_name(n, delims, roles)["proband"] for n in self._names} - {""})
        self.g_table.blockSignals(True)
        self.g_table.setRowCount(len(probands))
        for r, p in enumerate(probands):
            a = QTableWidgetItem(p); a.setFlags(a.flags() & ~Qt.ItemIsEditable)
            self.g_table.setItem(r, 0, a)
            self.g_table.setItem(r, 1, QTableWidgetItem(groups.get(p, "")))
        self.g_table.blockSignals(False)
        self.g_table.setEnabled(ROLE_GROUP not in roles)

    def _collect(self, st: Study) -> None:
        st.name = self.e_name.text().strip()
        st.folders = [self.f_list.item(i).text() for i in range(self.f_list.count())]
        target = TARGET_NUCLEUS if self.r_nuc.isChecked() else TARGET_CELL
        if target != st.target:
            # model, magnification preset, max area: target defaults the form does not show
            st.target = target
            st.apply_target()
        st.delimiters, st.roles = self._delims(), self._roles()
        s = st.settings
        s.gray_mode = self.m_gray.currentData()
        s.i0_scope = self.m_scope.currentData()
        s.i0_fields, s.i0_field_size = self.m_fields.value(), self.m_fsize.value()
        s.i0_ring_inner, s.i0_ring_outer = self.m_rin.value(), self.m_rout.value()
        s.i0_min, s.i0_max = self.m_imin.value(), self.m_imax.value()
        s.method, s.threshold = self.m_method.currentData(), round(self.m_thr.value(), 4)
        s.cellpose.input = self.m_input.currentData()
        s.cellpose.model = self.m_model.currentData()
        s.cellpose.pixel_size = round(self.m_px.value(), 3)
        s.max_cell_area = self.m_maxarea.value()
        s.cellpose.diameter = float(self.m_diam.value())
        s.cellpose.flow_threshold = round(self.m_flow.value(), 3)
        s.cellpose.cellprob_threshold = round(self.m_prob.value(), 3)
        s.debris_h = round(self.m_debris.value(), 4)
        s.i0_check, s.i0_rel_pct = self.m_check.currentData(), round(self.m_rel.value(), 2)
        s.min_cell_area, s.pos_threshold = self.m_minarea.value(), round(self.m_pos.value(), 4)
        s.od_high = round(self.m_high.value(), 3)
        if hasattr(self, "lv_list"):
            st.level_order = [self.lv_list.item(i).data(Qt.UserRole) for i in range(self.lv_list.count())]
        for obj, w in getattr(self, "_obj_w", {}).items():
            rule = {"count": w["count"].isChecked(), "segment": w["segment"].isChecked()}
            for k in ("min_d", "max_d"):                      # 0 = off: not stored, as an untouched tile
                if w[k].value():
                    rule[k] = round(w[k].value(), 1)
            s.objectives[obj] = rule
        st.groups = {}
        for r in range(self.g_table.rowCount()):
            a, b = self.g_table.item(r, 0), self.g_table.item(r, 1)
            g = (b.text() if b else "").strip()
            if a and g:
                st.groups[a.text()] = g


class StudyWizard(StudyForm, QDialog):
    """'New study' in four steps."""
    STEPS = ["1  Folders and target", "2  File names", "3  Measurement", "4  Done"]

    def __init__(self, parent=None, study: Study | None = None):
        QDialog.__init__(self, parent)
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
        self._build_measure_widgets()
        self.stack.addWidget(self._page_folders())
        self.stack.addWidget(self._names_box())
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
        form.addRow("Study name:", self._name_edit())
        l.addLayout(form)
        l.addWidget(self._folders_box(), 1)
        l.addWidget(QLabel("<b>What is measured?</b>"))
        l.addWidget(self._target_box())
        return w

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

    def _page_measure(self) -> QWidget:
        w = QWidget(); grid = QGridLayout(w); grid.setContentsMargins(0, 0, 8, 0)
        grid.setHorizontalSpacing(24); grid.setVerticalSpacing(10)
        grid.addWidget(self._form_grey_i0(), 0, 0)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(10)
        rl.addWidget(self._form_contour()); rl.addWidget(self._form_analysis()); rl.addStretch(1)
        grid.addWidget(right, 0, 1)
        grid.addWidget(self._groups_box(), 1, 0, 1, 2)
        grid.setRowStretch(1, 1)
        return w

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
        self._fill_groups()

    def _page_done(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w)
        self.summary = QLabel(""); self.summary.setWordWrap(True); self.summary.setTextFormat(Qt.RichText)
        l.addWidget(self.summary); l.addStretch(1)
        return w

    def _enter_done(self) -> None:
        self._collect(self.study)
        st, s = self.study, self.study.settings
        imgs = st.images()
        self.summary.setText(
            f"<h3>{st.name}</h3>"
            f"<p>{len(st.folders)} folder(s) · {len(imgs)} images · {len({i.proband for i in imgs})} subjects · "
            f"markers: {', '.join(st.markers(imgs)) or '–'}</p>"
            f"<p>Measured: <b>{TARGET_LABELS[st.target]}</b>, OD = −log₁₀(Ī / I₀) from the grey value.<br>"
            + (f"I₀: {s.i0_fields} fields of {s.i0_field_size}×{s.i0_field_size} px per image, in its free background, "
               f"{i0_rule(s)}.<br>" if s.i0_scope == I0_PER_IMAGE else
               f"I₀: {s.i0_fields} fields of {s.i0_field_size}×{s.i0_field_size} px, {s.i0_ring_inner}–{s.i0_ring_outer} px "
               f"from the contour, {i0_rule(s)}.<br>") +
            f"Contour: {METHOD_LABELS[s.method]}, {segmentation_text(s)}.</p>"
            "<p>Segmentation starts in the background as soon as the review is open.</p>")

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
            if i == 1 and ROLE_PROBAND not in self._roles():
                QMessageBox.information(self, self.windowTitle(), "Choose a part of the file name as “Subject”.")
                return
            if i == len(self.STEPS) - 1:
                self._collect(self.study)
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


# ─────────────────────────────────────────────
# Settings window (layout as in ErysReview)
# ─────────────────────────────────────────────
SETTINGS_PAGES = [
    ("images", "Images & names", "Name, image folders, how file names are read and the group of each subject."),
    ("seg", "Segmentation", "How nuclei or cells are found in the image."),
    ("i0", "Background I₀", "The brightness next to each cell that its OD is measured against."),
    ("analysis", "Analysis", "Cut-offs used in the tables."),
    ("display", "Display", "How contours look in the image. For every study, saved right away; no value changes."),
    ("templates", "Templates", "Save this study's scheme and settings for the next experiment."),
]
CHANGES_RESULTS = {"seg", "i0", "analysis"}       # marked ⚠ in the navigation


class _Tile(QFrame):
    """A settings tile: the whole card is the click target (keyboard: Tab, then Space/Return)."""

    def __init__(self, on_click):
        super().__init__(objectName="settingsTile")
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor); self.setFocusPolicy(Qt.StrongFocus)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on_click()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            self._on_click()
        else:
            super().keyPressEvent(e)


class StudySettings(StudyForm, QDialog):
    """The study's settings: navigation on the left, one page per topic, and a
    footer with the number of unsaved changes, Discard and Save."""

    def __init__(self, parent, study: Study, start_page: str = "home"):
        QDialog.__init__(self, parent)
        self.orig = copy.deepcopy(study)
        self.study = copy.deepcopy(study)
        self.saved: Study | None = None
        self.editing, self._template, self._measure_key = True, None, None
        self._names: list[str] = []
        self._ready = False
        self.setWindowTitle("Settings")
        fit_to_screen(self, 1060, 720)
        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        self.stack = QStackedWidget(); rv.addWidget(self.stack, 1)
        rv.addWidget(self._build_footer())
        root.addWidget(right, 1)
        self._build_measure_widgets()
        self._pages: dict[str, int] = {"home": self.stack.addWidget(_scroll(self._build_home()))}
        builders = {"images": self._page_images, "seg": self._page_seg, "i0": self._page_i0,
                    "analysis": self._page_analysis, "templates": self._page_templates,
                    "display": self._page_display}
        for key, title, sub in SETTINGS_PAGES:
            page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(28, 22, 28, 22); v.setSpacing(12)
            head = QHBoxLayout()
            back = QPushButton("← All settings", objectName="linkButton"); back.setCursor(Qt.PointingHandCursor)
            back.clicked.connect(lambda: self.show_page("home"))
            t = QLabel(title); t.setStyleSheet("font-size: 21px; font-weight: 700;")
            head.addWidget(back); head.addSpacing(8); head.addWidget(t); head.addStretch(1)
            v.addLayout(head)
            builders[key](v)
            v.addStretch(1)
            self._pages[key] = self.stack.addWidget(_scroll(page))
        self._refresh_tree()
        self._enter_names()
        self._fill_groups()
        self._ready = True
        self.show_page(start_page)
        self._form_changed()
        if getattr(self, "_obj_w", None):
            self._show_preview(next(iter(self._obj_w)))

    # ── frame ────────────────────────────────────────────
    def _build_home(self) -> QWidget:
        """One tile per settings page; a click opens it."""
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(36, 28, 36, 28); v.setSpacing(18)
        head = QHBoxLayout()
        t = QLabel("Settings"); t.setStyleSheet("font-size: 26px; font-weight: 700;")
        self.study_btn = QPushButton(f"●  {self.orig.name}   ▾", objectName="studySwitch")
        self.study_btn.setToolTip("Switch study"); self.study_btn.clicked.connect(self._switch_study)
        head.addWidget(t); head.addStretch(1); head.addWidget(self.study_btn)
        v.addLayout(head)
        grid = QGridLayout(); grid.setSpacing(14)
        self._tile_sub: dict[str, QLabel] = {}
        for n, (key, title, _) in enumerate(SETTINGS_PAGES):
            b = _Tile(lambda k=key: self.show_page(k)); b.setMinimumHeight(118)
            b.setAccessibleName(title)
            bl = QVBoxLayout(b); bl.setContentsMargins(18, 14, 18, 14); bl.setSpacing(6)
            tt = QLabel(title + ("   ⚠" if key in CHANGES_RESULTS else ""), objectName="tileTitle")
            sub = QLabel("", objectName="tileSub"); sub.setWordWrap(True)
            more = QLabel("Open →", objectName="tileSub")
            bl.addWidget(tt); bl.addWidget(sub); bl.addStretch(1); bl.addWidget(more)
            self._tile_sub[key] = sub
            grid.addWidget(b, n // 3, n % 3)
        v.addLayout(grid)
        v.addWidget(QLabel(f"⚠ changes the results · saved in {self.orig.path.name}", objectName="caption"))
        v.addStretch(1)
        return w

    def show_page(self, key: str) -> None:
        if key in self._pages:
            self.stack.setCurrentIndex(self._pages[key])

    def _build_footer(self) -> QWidget:
        f = QFrame(objectName="settingsFooter")
        h = QHBoxLayout(f); h.setContentsMargins(24, 10, 24, 10); h.setSpacing(10)
        self.footer_dot = QLabel("●"); self.footer_dot.setStyleSheet("color: #C77A12;")
        self.footer_text = QLabel("")
        self.btn_discard = QPushButton("Discard"); self.btn_discard.clicked.connect(self._discard)
        self.btn_save = QPushButton("Save"); self.btn_save.setProperty("class", "primary")
        self.btn_save.clicked.connect(self._save)
        close = QPushButton("Close"); close.clicked.connect(self.close)
        h.addWidget(self.footer_dot); h.addWidget(self.footer_text, 1)
        h.addWidget(self.btn_discard); h.addWidget(self.btn_save); h.addWidget(close)
        return f

    # ── pages ────────────────────────────────────────────
    @staticmethod
    def _card(v: QVBoxLayout, *widgets: QWidget) -> None:
        c = QFrame(objectName="settingsCard"); cl = QVBoxLayout(c); cl.setContentsMargins(16, 14, 16, 14); cl.setSpacing(10)
        for w in widgets:
            cl.addWidget(w)
        v.addWidget(c)

    def _page_images(self, v: QVBoxLayout) -> None:
        name = QWidget(); f = QFormLayout(name); f.setContentsMargins(0, 0, 0, 0); f.addRow("Study name:", self._name_edit())
        self._card(v, name)
        self._card(v, self._folder_rows_box())
        self._card(v, self._names_box())
        self.notread_title = QLabel("", objectName="tileTitle")
        self.notread_list = QLabel("", objectName="tileSub"); self.notread_list.setWordWrap(True)
        self.notread_card = QFrame(objectName="settingsCard"); nl = QVBoxLayout(self.notread_card)
        nl.setContentsMargins(16, 14, 16, 14); nl.addWidget(self.notread_title); nl.addWidget(self.notread_list)
        v.addWidget(self.notread_card)
        groups = self._groups_box()
        self.g_table.setShowGrid(False); self.g_table.setMinimumHeight(0); self.g_table.setMaximumHeight(220)
        self.g_fold = Fold("Condition per subject", groups)
        self._card(v, self.g_fold)
        self._card(v, self._levels_box())

    # ── folders: one row per folder, the file tree on request ──
    def _folder_rows_box(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(6)
        head = QHBoxLayout(); head.addWidget(QLabel("Folders", objectName="tileTitle")); head.addStretch(1)
        add = QPushButton("+ Add folder"); add.clicked.connect(self._add_folder); head.addWidget(add)
        l.addLayout(head)
        old = self._folders_box()                # f_list and the tree the rest of the form reads, kept hidden
        old.hide(); l.addWidget(old)
        self.f_rows = QVBoxLayout(); self.f_rows.setSpacing(0); l.addLayout(self.f_rows)
        l.addWidget(QLabel("Sub-folders included · .nk_cache skipped", objectName="tileSub"))
        self.tree.setMinimumHeight(220)
        l.addWidget(Fold("Files", self.tree))
        return w

    def _refresh_tree(self) -> None:
        StudyForm._refresh_tree(self)
        if not hasattr(self, "f_rows"):
            return
        from ..segment import image_objective
        from .batch import mag_order
        while self.f_rows.count():
            it = self.f_rows.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for k in range(self.f_list.count()):
            path = Path(self.f_list.item(k).text())
            imgs = list_images(path) if path.is_dir() else []
            mags = Counter(image_objective(p) or "unknown" for p in imgs)
            row = QFrame(objectName="folderRow"); h = QHBoxLayout(row); h.setContentsMargins(0, 8, 0, 8)
            txt = QVBoxLayout(); txt.setSpacing(3)
            name = QLabel(" › ".join(path.parts[-2:]) if path.is_dir() else f"{path} – not found")
            name.setToolTip(str(path)); txt.addWidget(name)
            chips = QHBoxLayout(); chips.setSpacing(6)
            for t in [f"{len(imgs)} images"] + [f"{m} {n}" for m, n in sorted(mags.items(), key=lambda kv: mag_order(kv[0]))]:
                chips.addWidget(QLabel(t, objectName="chip"))
            chips.addStretch(1); txt.addLayout(chips)
            h.addLayout(txt, 1)
            rm = QPushButton("✕"); rm.setToolTip("Remove folder"); rm.setAccessibleName("Remove folder")
            rm.clicked.connect(lambda _=False, i=k: (self.f_list.setCurrentRow(i), self._remove_folder()))
            h.addWidget(rm)
            self.f_rows.addWidget(row)

    def _refresh_preview(self) -> None:
        StudyForm._refresh_preview(self)
        if not hasattr(self, "notread_card"):
            return
        delims, roles = self._delims(), self._roles()
        bad = [n for n in self._names if self._unassigned(n, delims, roles)]
        self.notread_card.setVisible(bool(bad))
        self.notread_title.setText(f"Not read · {len(bad)} file{'s' if len(bad) != 1 else ''}")
        self.notread_list.setText("These names don't match the scheme – they get no subject:\n"
                                  + ", ".join(bad[:12]) + (f" … +{len(bad) - 12}" if len(bad) > 12 else ""))
        if hasattr(self, "g_fold"):
            n = self.g_table.rowCount()
            done = sum(bool(self.g_table.item(r, 1) and self.g_table.item(r, 1).text().strip()) for r in range(n))
            self.g_fold.btn.setText(f"Condition per subject · {done} of {n} set · only if not in the file name")

    # ── sidebar order ──
    def _levels_box(self) -> QWidget:
        from ..study import LEVEL_LABELS, level_value
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(0, 0, 0, 0); l.setSpacing(6)
        l.addWidget(QLabel("Sidebar order", objectName="tileTitle"))
        self.lv_list = QListWidget(); self.lv_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.lv_list.setSpacing(4); self.lv_list.setFixedHeight(4 * 34 + 8)
        imgs = self.study.images()
        for lv in self.study.level_order:
            empty = not any(level_value(self.study, i, lv) for i in imgs)
            it = QListWidgetItem(f"⋮⋮  {LEVEL_LABELS[lv]}" + ("  · empty, skipped" if empty else ""))
            it.setData(Qt.UserRole, lv)
            self.lv_list.addItem(it)
        self.lv_list.model().rowsMoved.connect(lambda *_: self._form_changed())
        l.addWidget(self.lv_list)
        l.addWidget(QLabel("Drag to reorder · empty levels are skipped", objectName="tileSub"))
        return w

    @staticmethod
    def _help_form(rows: list) -> QWidget:
        """Form whose rows carry a one-line explanation under the field: (label, widget, help)."""
        w = QWidget(); f = QFormLayout(w); f.setContentsMargins(0, 0, 0, 0)
        f.setVerticalSpacing(12); f.setHorizontalSpacing(16)
        for label, widget, help_ in rows:
            if help_:
                box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(3)
                bl.addWidget(widget)
                h = QLabel(help_, objectName="caption"); h.setWordWrap(True); bl.addWidget(h)
                f.addRow(label, box)
            else:
                f.addRow(label, widget)
        return w

    def _summary_label(self) -> QLabel:
        lab = QLabel(""); lab.setWordWrap(True); lab.setObjectName("settingsSummary")
        return lab

    def _page_seg(self, v: QVBoxLayout) -> None:
        self.seg_summary = self._summary_label(); v.addWidget(self.seg_summary)
        self._card(v, self._help_form([("What is measured", self._target_box(), "")]))
        body = QHBoxLayout(); body.setSpacing(16)
        self.sample_img = QLabel("No sample image", objectName="sampleImage"); self.sample_img.setFixedSize(380, 380)
        self.sample_img.setAlignment(Qt.AlignCenter)
        left = QVBoxLayout(); left.addWidget(self.sample_img); self.sample_cap = QLabel("", objectName="caption")
        left.addWidget(self.sample_cap); left.addStretch(1)
        body.addLayout(left)
        tiles = QGridLayout(); tiles.setSpacing(12)
        self._obj_w: dict[str, dict] = {}
        for n, (obj, px, count) in enumerate(self._objectives()):
            rule = objective_rule(self.study.settings, obj)
            c = QFrame(objectName="settingsCard"); g = QGridLayout(c); g.setContentsMargins(14, 12, 14, 12)
            head = QPushButton(obj, objectName="objectiveHead"); head.setCursor(Qt.PointingHandCursor)
            head.setToolTip("Show a sample image"); head.clicked.connect(lambda _=False, o=obj: self._show_preview(o))
            lo, hi = QDoubleSpinBox(), QDoubleSpinBox()
            for sb, val in ((lo, rule.get("min_d", 0.0)), (hi, rule.get("max_d", 0.0))):
                sb.setRange(0, 200); sb.setDecimals(1); sb.setSuffix(" µm"); sb.setSpecialValueText("off"); sb.setValue(val)
                sb.valueChanged.connect(lambda _=0.0, o=obj: (self._form_changed(), self._show_preview(o)))
            cnt, seg = QCheckBox("Count by default"), QCheckBox("Segment")
            cnt.setChecked(rule["count"]); seg.setChecked(rule["segment"])
            for cb in (cnt, seg):
                cb.toggled.connect(lambda *_: self._form_changed())
            dia = QWidget(); dl = QHBoxLayout(dia); dl.setContentsMargins(0, 0, 0, 0)
            dl.addWidget(lo); dl.addWidget(QLabel("–")); dl.addWidget(hi)
            g.addWidget(head, 0, 0, 1, 2)
            g.addWidget(QLabel("Pixel size", objectName="caption"), 1, 0)
            g.addWidget(QLabel(f"{px:.3f} µm/px · from file" if px else "unknown"), 1, 1)
            g.addWidget(QLabel("Images", objectName="caption"), 2, 0); g.addWidget(QLabel(str(count)), 2, 1)
            g.addWidget(QLabel("Nucleus Ø", objectName="caption"), 3, 0); g.addWidget(dia, 3, 1)
            g.addWidget(cnt, 4, 0, 1, 2); g.addWidget(seg, 5, 0, 1, 2)
            g.setRowStretch(6, 1); g.setVerticalSpacing(8)
            self._obj_w[obj] = {"min_d": lo, "max_d": hi, "count": cnt, "segment": seg}
            tiles.addWidget(c, n, 0)                 # stacked: no sideways scrolling
        if not self._obj_w:
            tiles.addWidget(QLabel("No objective in the image files – the study's pixel size and areas apply "
                                   "(Advanced).", objectName="caption", wordWrap=True), 0, 0)
        tw = QWidget(); tw.setLayout(tiles); body.addWidget(tw, 1)
        v.addLayout(body)
        area = QWidget(); al = QHBoxLayout(area); al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(self.m_minarea); al.addWidget(QLabel("to")); al.addWidget(self.m_maxarea); al.addStretch(1)
        adv = QWidget(); av = QVBoxLayout(adv); av.setContentsMargins(0, 0, 0, 0); av.setSpacing(10)
        av.addWidget(self._form([("Debris flag:", self.m_debris), ("Model:", self.m_model),
                                 ("Pixel size:", self.m_px), ("Area (no objective):", area),
                                 ("Method:", self.m_method), ("OD threshold:", self.m_thr),
                                 ("Cellpose sees:", self.m_input)]))
        av.addWidget(self._form_cellpose())
        self._card(v, Fold("Advanced – debris flag, model, pixel size, contour method, Cellpose", adv))
        v.addWidget(QLabel("⚠ Changes need a new run: File › Segmentation …", objectName="banner", wordWrap=True))

    def _objectives(self) -> list[tuple[str, float, int]]:
        """(objective, µm/px, images) found in the study's files, highest magnification first."""
        from ..segment import image_pixel_size
        from .batch import mag_order
        found: dict = {}
        for info in self.study.images():
            if info.objective:
                px, n = found.get(info.objective, (image_pixel_size(info.path), 0))
                found[info.objective] = (px, n + 1)
        return [(o, px, n) for o, (px, n) in sorted(found.items(), key=lambda kv: mag_order(kv[0]))]

    def _show_preview(self, obj: str) -> None:
        """A sample image of that objective with its contours: kept solid green, outside the size range dashed orange."""
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap
        from skimage.segmentation import find_boundaries
        from ..analysis import area_limits
        from ..segment import cached_cellpose
        from ..state import load_image
        info = next((i for i in self.study.images() if i.objective == obj), None)
        if info is None:
            return
        work = self._work().settings
        img = load_image(info.path)[..., :3].astype(np.uint8)
        h, w = img.shape[:2]; side = min(h, w); x0 = (w - side) // 2
        rgb = img[:, x0:x0 + side].copy()
        cp = cached_cellpose(info.folder, info.path, work.cellpose)
        if cp is not None:
            cp = cp[:, x0:x0 + side]
            lo, hi = area_limits(work, info.path)
            sizes = np.bincount(cp.ravel()); keep = (sizes >= lo) & ((hi <= 0) | (sizes <= hi)); keep[0] = False
            yy, xx = np.indices(cp.shape)
            out = find_boundaries(np.where(keep[cp], 0, cp), mode="thick")
            rgb[out & (((yy + xx) // 8) % 2 == 0)] = (230, 140, 20)
            rgb[find_boundaries(np.where(keep[cp], cp, 0), mode="thick")] = (40, 200, 90)
            n_keep, n_out = int(keep.sum()), int((sizes[1:] > 0).sum() - keep.sum())
            self.sample_cap.setText(f"{info.path.name} · {n_keep} kept · {n_out} outside size range")
        else:
            self.sample_cap.setText(f"{info.path.name} · not segmented yet")
        q = QImage(rgb.data, side, side, 3 * side, QImage.Format_RGB888).copy()
        self.sample_img.setPixmap(QPixmap.fromImage(q).scaled(380, 380, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _page_i0(self, v: QVBoxLayout) -> None:
        self.i0_summary = self._summary_label(); v.addWidget(self.i0_summary)
        self._card(v, self._help_form([
            ("I₀ from", self.m_scope, "“Per cell” measures the light right next to each cell – right for "
                                      "vignetted images. “Per image” only for evenly lit images."),
            ("", self.m_scope_hint, ""),
            ("Fields", self.m_fields, "How many background fields give one I₀ (their mean)."),
            ("Check", self.m_check, "Fixed range · relative to the image's free background · local: the "
                                    "fields must agree with each other."),
            ("Allowed deviation", self.m_rel, "Outside it a cell gets an I₀ warning; you decide and can still confirm it."),
        ]))
        adv = self._form([("RGB → grey:", self.m_gray), ("Field size:", self.m_fsize),
                          ("Distance from contour:", self.m_rin), ("… up to:", self.m_rout),
                          ("Fixed range from:", self.m_imin), ("… to:", self.m_imax)])
        self._card(v, Fold("Advanced – grey value, field size and distance, fixed range", adv))
        self._scope_mode()

    def _page_analysis(self, v: QVBoxLayout) -> None:
        self._card(v, self._form_analysis())

    def _page_display(self, v: QVBoxLayout) -> None:
        from .app import _recall, _remember
        from PySide6.QtWidgets import QApplication
        from .style import apply_look
        seg = QFrame(objectName="segment"); sl = QHBoxLayout(seg); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        grp = QButtonGroup(seg); now = _recall("theme") or "system"
        for i, (key, text) in enumerate((("system", "System"), ("light", "Light"), ("dark", "Dark"))):
            b = QPushButton(text); b.setCheckable(True); b.setChecked(key == now)
            b.setProperty("first", i == 0); b.setProperty("last", i == 2)
            b.clicked.connect(lambda _=False, k=key: (_remember(theme=k), apply_look(QApplication.instance(), k)))
            grp.addButton(b); sl.addWidget(b)
        row = QHBoxLayout(); row.addWidget(seg); row.addStretch(1)
        box = QWidget(); box.setLayout(row)
        self._card(v, QLabel("Appearance", objectName="tileTitle"), box,
                   QLabel("For the whole app on this computer · applies right away", objectName="tileSub"))
        on = _recall("fill_kept")
        fill = QCheckBox("Fill kept cells / nuclei in green"); fill.setChecked(True if on is None else bool(on))
        alpha = QSlider(Qt.Horizontal); alpha.setRange(10, 80); alpha.setSingleStep(5)
        alpha.setValue(round(float(_recall("fill_alpha") or 0.35) * 100))
        pct = QLabel(f"{alpha.value()} %")
        fill.toggled.connect(lambda c: (_remember(fill_kept=c), alpha.setEnabled(c)))
        alpha.valueChanged.connect(lambda x: (_remember(fill_alpha=x / 100), pct.setText(f"{x} %")))
        alpha.setEnabled(fill.isChecked())
        row = QHBoxLayout(); row.addWidget(QLabel("Opacity:")); row.addWidget(alpha, 1); row.addWidget(pct)
        box = QWidget(); box.setLayout(row)
        self._card(v, fill, box)

    def _page_templates(self, v: QVBoxLayout) -> None:
        b = QPushButton("Save as template …"); b.clicked.connect(self._save_template)
        self.tpl_list = QLabel(""); self.tpl_list.setWordWrap(True)
        self._card(v, QLabel("A template keeps the target, the file-name scheme and every measurement setting – "
                             "not the name, folders or groups. Saved changes are used; save first."), b)
        self._card(v, QLabel("<b>Templates</b>"), self.tpl_list)
        self._list_templates()

    def _list_templates(self) -> None:
        ts = list_templates()
        self.tpl_list.setText("\n".join(f"• {t.name}  –  {TARGET_LABELS.get(t.target, t.target)}" for t in ts) or "none yet")

    def _save_template(self) -> None:
        name, ok = QInputDialog.getText(self, "Save as template", "Template name:", text=self.orig.name)
        name = name.strip()
        if not ok or not name:
            return
        if template_file_for(name).exists() and QMessageBox.question(
                self, "Replace template", f"A template “{name}” already exists. Replace it?") != QMessageBox.Yes:
            return
        Template.from_study(self.orig, name, f"from study “{self.orig.name}”").save()
        self._list_templates()

    # ── changes ──────────────────────────────────────────
    def _target_toggled(self) -> None:
        if self._ready:      # a new target brings its defaults (model, preset, contour, debris flag)
            tmp = copy.deepcopy(self.study)
            tmp.target = TARGET_NUCLEUS if self.r_nuc.isChecked() else TARGET_CELL
            tmp.apply_target()
            t = tmp.settings
            self.m_input.setCurrentIndex(self.m_input.findData(t.cellpose.input))
            self.m_method.setCurrentIndex(self.m_method.findData(t.method))
            self.m_debris.setValue(t.debris_h)
            self.m_model.setCurrentIndex(self.m_model.findData(t.cellpose.model))
            self.m_px.setValue(t.cellpose.pixel_size)
            self.m_minarea.setValue(t.min_cell_area); self.m_maxarea.setValue(t.max_cell_area)
            self.m_check.setCurrentIndex(self.m_check.findData(t.i0_check)); self.m_rel.setValue(t.i0_rel_pct)
        self._form_changed()

    def _folders_changed(self) -> None:
        if self._ready:
            self._enter_names()
        self._form_changed()

    def _work(self) -> Study:
        w = copy.deepcopy(self.orig)
        self._collect(w)
        return w

    def _changes(self) -> list[str]:
        a, b = self.orig.to_dict(), self._work().to_dict()
        out = [k for k in a if k not in ("settings", "created_at", "app_version") and a.get(k) != b.get(k)]
        sa, sb = a["settings"], b["settings"]
        out += [k for k in sa if k != "cellpose" and sa.get(k) != sb.get(k)]
        out += [f"cellpose.{k}" for k in sa["cellpose"] if sa["cellpose"].get(k) != sb["cellpose"].get(k)]
        return out

    def _form_changed(self) -> None:
        if not getattr(self, "_ready", False):
            return
        w = self._work().settings
        if hasattr(self, "seg_summary"):
            what = TARGET_LABELS[TARGET_NUCLEUS if self.r_nuc.isChecked() else TARGET_CELL].capitalize()
            area = (" · ".join(f"{o} Ø {r.get('min_d', 0):g}–{r.get('max_d', 0) or '∞'} µm"
                               for o, r in w.objectives.items() if r.get("segment", True))
                    or (f"{w.min_cell_area}–{w.max_cell_area or '∞'} px" if w.min_cell_area or w.max_cell_area
                        else "any area"))
            self.seg_summary.setText(f"{what} · {segmentation_text(w)} · {area}"
                                     + (" · debris flag on" if w.debris_h else ""))
            per = "image" if w.i0_scope == I0_PER_IMAGE else "cell"
            self.i0_summary.setText(f"{w.i0_fields} fields of {w.i0_field_size}×{w.i0_field_size} px per {per} · "
                                    f"{i0_rule(w)}")
        if hasattr(self, "_tile_sub"):
            objs = ", ".join(f"{o} {'counted' if r['count'] else 'off'}" for o, r in w.objectives.items()
                             if r.get("segment", True))
            per = "image" if w.i0_scope == I0_PER_IMAGE else "nucleus"
            for key, text in {
                "images": f"{len(self._work().folders)} folder(s) · {', '.join(ROLE_LABELS.get(r, r) for r in self._roles() if r != ROLE_IGNORE)}",
                "seg": objs or segmentation_text(w),
                "i0": f"{w.i0_fields} fields per {per} · {i0_rule(w)}",
                "analysis": f"Positive above OD {w.pos_threshold:g}" if w.pos_threshold else "No positive/negative call",
                "display": "Contour fill and opacity",
                "templates": "Save as a template for the next study",
            }.items():
                if key in self._tile_sub:
                    self._tile_sub[key].setText(text)
        n = len(self._changes())
        self.footer_text.setText(f"{n} unsaved change{'s' if n != 1 else ''} to “{self.orig.name}”" if n
                                 else "No unsaved changes")
        self.footer_dot.setVisible(bool(n))
        self.btn_discard.setEnabled(bool(n)); self.btn_save.setEnabled(bool(n))

    def _discard(self) -> None:
        self._ready = False
        self.study = copy.deepcopy(self.orig)
        self.e_name.setText(self.orig.name)
        self.f_list.clear()
        for f in self.orig.folders:
            self.f_list.addItem(f)
        (self.r_nuc if self.orig.target == TARGET_NUCLEUS else self.r_cell).setChecked(True)
        self._fill_measure(self.orig.settings)
        for obj, w in getattr(self, "_obj_w", {}).items():
            rule = objective_rule(self.orig.settings, obj)
            w["count"].setChecked(rule["count"]); w["segment"].setChecked(rule["segment"])
            w["min_d"].setValue(rule.get("min_d", 0.0)); w["max_d"].setValue(rule.get("max_d", 0.0))
        self._refresh_tree(); self._enter_names()
        self.g_table.setRowCount(0); self._fill_groups()
        if hasattr(self, "lv_list"):                 # back to the saved sidebar order
            items = {self.lv_list.item(i).data(Qt.UserRole): self.lv_list.item(i).text() for i in range(self.lv_list.count())}
            self.lv_list.clear()
            for lv in self.orig.level_order:
                it = QListWidgetItem(items[lv]); it.setData(Qt.UserRole, lv); self.lv_list.addItem(it)
        self._ready = True
        self._form_changed()

    def _save(self) -> None:
        if not self.e_name.text().strip():
            QMessageBox.information(self, "Settings", "Give the study a name."); return
        if not self.f_list.count():
            QMessageBox.information(self, "Settings", "Add at least one image folder."); return
        changes = self._changes()
        if any(c.startswith("cellpose.") for c in changes) and QMessageBox.question(
                self, "Save", "The segmentation settings changed – run File › Segmentation … afterwards. Save?") != QMessageBox.Yes:
            return
        w = self._work()
        w.save()
        self.orig, self.study, self.saved = copy.deepcopy(w), copy.deepcopy(w), w
        self.study_btn.setText(f"●  {w.name}   ▾")
        self._form_changed()
        self.footer_text.setText("✓ Saved")

    def _confirm_leave(self) -> bool:
        if not self._changes():
            return True
        return QMessageBox.question(self, "Settings", "Discard the unsaved changes?") == QMessageBox.Yes

    def _switch_study(self) -> None:
        if self._confirm_leave():
            self.switch_requested = True
            QDialog.reject(self)

    def closeEvent(self, e):
        if self._confirm_leave():
            e.accept()
        else:
            e.ignore()

    def reject(self):
        if self._confirm_leave():
            QDialog.reject(self)
