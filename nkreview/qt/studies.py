"""Study overview and the 'Neue Studie' assistant (mockup screens 2 and 3)."""

from __future__ import annotations

import copy
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QRadioButton, QSpinBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..segment import METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD
from ..state import GRAY_MEAN, GRAY_WEIGHTED, list_images
from ..study import (
    CANDIDATE_DELIMS, META_ROLES, ROLE_GROUP, ROLE_LABELS, ROLE_PROBAND, ROLE_TIMEPOINT,
    ROLES, TARGET_CELL, TARGET_LABELS, TARGET_NUCLEUS, Study, detect_delimiters, guess_roles,
    list_studies, parse_name, progress, proband_progress, tokenize,
)

DELIM_LABELS = {" ": "Leerzeichen", "_": "_", "-": "-", ".": "."}
METHOD_LABELS = {METHOD_HYBRID: "Hybrid (Cellpose + OD-Rand)", METHOD_CELLPOSE: "Nur Cellpose",
                 METHOD_THRESHOLD: "Nur OD-Schwelle"}


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


# ─────────────────────────────────────────────
# Overview
# ─────────────────────────────────────────────
class StudyPicker(QDialog):
    """Pick a study to work on (or make a new one). `self.chosen` after accept."""

    def __init__(self, parent=None, current: Study | None = None):
        super().__init__(parent)
        self.setWindowTitle("Studien")
        self.resize(1180, 760)
        self.chosen: Study | None = None
        self._studies: list[Study] = []
        lay = QHBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(0)

        left = QWidget(objectName="sidebar"); ll = QVBoxLayout(left); ll.setContentsMargins(14, 14, 14, 14)
        top = QHBoxLayout(); t = QLabel("<b>Studien</b>"); f = t.font(); f.setPointSizeF(f.pointSizeF() * 1.3); t.setFont(f)
        b_new = QPushButton("+ Neue Studie"); b_new.setProperty("class", "primary"); b_new.clicked.connect(self.new_study)
        top.addWidget(t); top.addStretch(1); top.addWidget(b_new)
        ll.addLayout(top)
        self.list = QListWidget(); self.list.currentRowChanged.connect(self._show)
        self.list.itemDoubleClicked.connect(lambda _: self.open_selected())
        ll.addWidget(self.list, 1)
        left.setFixedWidth(340)
        lay.addWidget(left)

        right = QWidget(); self.rl = QVBoxLayout(right); self.rl.setContentsMargins(24, 20, 24, 20); self.rl.setSpacing(14)
        head = QHBoxLayout()
        self.h_name = QLabel(""); f = self.h_name.font(); f.setPointSizeF(f.pointSizeF() * 1.6); f.setBold(True); self.h_name.setFont(f)
        self.h_sub = QLabel("", objectName="caption")
        hv = QVBoxLayout(); hv.addWidget(self.h_name); hv.addWidget(self.h_sub)
        self.b_edit = QPushButton("Einstellungen"); self.b_edit.clicked.connect(self.edit_study)
        self.b_open = QPushButton("Auswertung öffnen"); self.b_open.setProperty("class", "primary")
        self.b_open.clicked.connect(self.open_selected)
        head.addLayout(hv, 1); head.addWidget(self.b_edit); head.addWidget(self.b_open)
        self.rl.addLayout(head)
        self.cards = QGridLayout(); self.cards.setSpacing(10)
        self.rl.addLayout(self.cards)
        self.rl.addWidget(QLabel("<b>Fortschritt</b>  <span>– geprüfte / alle Bilder je Proband × Zeitpunkt × Marker</span>"))
        self.grid = QTableWidget(); self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.verticalHeader().setVisible(True)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rl.addWidget(self.grid, 1)
        self.rl.addWidget(QLabel("<b>Ordner</b>"))
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
            it = QListWidgetItem(f"{st.name}\n{TARGET_LABELS.get(st.target, st.target)} · {markers} · {done}/{n} Probanden fertig")
            self.list.addItem(it)
            if select is not None and st.file == select.file:
                row = i
        self.list.blockSignals(False)
        has = bool(self._studies)
        self.b_open.setEnabled(has); self.b_edit.setEnabled(has)
        if has:
            self.list.setCurrentRow(row); self._show(row)
        else:
            self.h_name.setText("Noch keine Studie")
            self.h_sub.setText("Leg eine neue Studie an: Ordner wählen, Dateinamen zuordnen, Messung festlegen.")

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
        self.h_sub.setText(f"{len(st.folders)} Ordner · {len(imgs)} Bilder · angelegt {st.created_at[:10]}")
        _clear_layout(self.cards)
        gray = "(R+G+B)/3" if s.gray_mode == GRAY_MEAN else "0,299·R + 0,587·G + 0,114·B"
        for i, c in enumerate((
            _card("Messung", f"Grau-OD, {TARGET_LABELS.get(st.target, st.target)}", f"−log₁₀(Ī / I₀) · Grau = {gray}"),
            _card("Hintergrund I₀", f"{s.i0_fields} Felder à {s.i0_field_size ** 2} px je Zelle",
                  f"Ring {s.i0_ring_inner}–{s.i0_ring_outer} px · gültig {s.i0_min:g}–{s.i0_max:g}"),
            _card("Kontur", METHOD_LABELS.get(s.method, s.method),
                  f"Cellpose auf {'DAB-Karte' if s.cellpose.input == 'dab' else 'RGB'} · OD-Schwelle {s.threshold:g}"),
            _card("Färbung", s.stain_preset, "Treg / HRP-Green: Version 2"),
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
            n = len(list_images(Path(f))) if Path(f).is_dir() else 0
            lines.append(f"{f}  —  {n} Bilder" if Path(f).is_dir() else f"{f}  —  nicht gefunden")
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

    def open_selected(self) -> None:
        st = self._current()
        if st is not None:
            self.chosen = st
            self.accept()


# ─────────────────────────────────────────────
# Assistant
# ─────────────────────────────────────────────
class StudyWizard(QDialog):
    STEPS = ["1  Ordner und Ziel", "2  Dateinamen", "3  Messung", "4  Fertig"]

    def __init__(self, parent=None, study: Study | None = None):
        super().__init__(parent)
        self.editing = study is not None
        self.study = copy.deepcopy(study) if study else Study(name="")
        self.setWindowTitle("Studie bearbeiten" if self.editing else "Neue Studie")
        self.resize(1040, 760)
        self._names: list[str] = []
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 18, 24, 16); lay.setSpacing(12)
        steps = QHBoxLayout(); self.step_labels = []
        for s in self.STEPS:
            l = QLabel(s); steps.addWidget(l, 1); self.step_labels.append(l)
        lay.addLayout(steps)
        self.stack = QStackedWidget(); lay.addWidget(self.stack, 1)
        self.stack.addWidget(self._page_folders())
        self.stack.addWidget(self._page_names())
        self.stack.addWidget(self._page_measure())
        self.stack.addWidget(self._page_done())
        nav = QHBoxLayout()
        b_cancel = QPushButton("Abbrechen"); b_cancel.clicked.connect(self.reject)
        self.b_back = QPushButton("Zurück"); self.b_back.clicked.connect(lambda: self._go(-1))
        self.b_next = QPushButton("Weiter"); self.b_next.setProperty("class", "primary"); self.b_next.clicked.connect(lambda: self._go(+1))
        nav.addWidget(b_cancel); nav.addStretch(1); nav.addWidget(self.b_back); nav.addWidget(self.b_next)
        lay.addLayout(nav)
        self._go(0)

    # ── page 1 ───────────────────────────────────────────
    def _page_folders(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        form = QFormLayout()
        self.e_name = QLineEdit(self.study.name); self.e_name.setPlaceholderText("NK Methylierung 2026")
        form.addRow("Name der Studie:", self.e_name)
        l.addLayout(form)
        l.addWidget(QLabel("<b>Bildordner</b> – Unterordner werden mitgelesen; eine Studie kann mehrere Ordner haben."))
        self.f_list = QListWidget()
        for f in self.study.folders:
            self.f_list.addItem(f)
        l.addWidget(self.f_list, 1)
        row = QHBoxLayout()
        b_add = QPushButton("Ordner hinzufügen"); b_add.clicked.connect(self._add_folder)
        b_rm = QPushButton("Entfernen"); b_rm.clicked.connect(lambda: self.f_list.takeItem(self.f_list.currentRow()))
        row.addWidget(b_add); row.addWidget(b_rm); row.addStretch(1)
        l.addLayout(row)
        l.addWidget(QLabel("<b>Was wird gemessen?</b>"))
        self.r_cell = QRadioButton("Ganze Zelle (z. B. NK) – Cellpose sieht das Farbbild")
        self.r_nuc = QRadioButton("Zellkern (z. B. C2C12 Ki67) – Cellpose sieht die DAB-Karte, Debris-Verdacht an")
        g = QButtonGroup(w); g.addButton(self.r_cell); g.addButton(self.r_nuc)
        (self.r_nuc if self.study.target == TARGET_NUCLEUS else self.r_cell).setChecked(True)
        l.addWidget(self.r_cell); l.addWidget(self.r_nuc)
        return w

    def _add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Bildordner wählen", str(Path.home()))
        if d and not self.f_list.findItems(d, Qt.MatchExactly):
            self.f_list.addItem(d)

    # ── page 2 ───────────────────────────────────────────
    def _page_names(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setSpacing(10)
        l.addWidget(QLabel("<b>Was steht im Dateinamen?</b>  Weise jedem Teil eine Rolle zu – die Vorschau zeigt, wie jede Datei gelesen wird."))
        drow = QHBoxLayout(); drow.addWidget(QLabel("Trennzeichen:"))
        self.d_boxes = {}
        for d in CANDIDATE_DELIMS:
            cb = QCheckBox(DELIM_LABELS[d]); cb.toggled.connect(self._delims_changed)
            self.d_boxes[d] = cb; drow.addWidget(cb)
        drow.addStretch(1)
        l.addLayout(drow)
        self.tok_row = QHBoxLayout(); self.tok_row.setSpacing(8)
        tok_box = QWidget(); tok_box.setLayout(self.tok_row)
        l.addWidget(tok_box)
        self.tok_combos: list[QComboBox] = []
        self.preview = QTableWidget(0, 1 + len(META_ROLES))
        self.preview.setHorizontalHeaderLabels(["Datei"] + [ROLE_LABELS[r] for r in META_ROLES])
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
        delims = self.study.delimiters if (self.editing and self.study.roles) else detect_delimiters(self._names)
        for d, cb in self.d_boxes.items():
            cb.blockSignals(True); cb.setChecked(d in delims); cb.blockSignals(False)
        roles = self.study.roles if (self.editing and self.study.roles) else guess_roles(self._names, delims)
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
        toks = tokenize(sample, delims)
        for i, t in enumerate(toks):
            box = QFrame(objectName="metricCard"); bl = QVBoxLayout(box); bl.setContentsMargins(8, 6, 8, 6)
            lab = QLabel(f"<b>{t}</b>"); lab.setTextFormat(Qt.RichText)
            cb = QComboBox()
            for r in ROLES:
                cb.addItem(ROLE_LABELS[r], r)
            cb.setCurrentIndex(ROLES.index(roles[i]) if i < len(roles) and roles[i] in ROLES else ROLES.index("ignorieren"))
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
            vals = [name] + [m[k] for k in META_ROLES]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if bad:
                    it.setForeground(QColor("#B83A3A"))
                self.preview.setItem(r, c, it)
        odd = sum(len(tokenize(x, delims)) != n for x in self._names)
        hints = []
        if odd:
            hints.append(f"{odd} Datei(en) haben eine andere Zahl an Teilen (rot, oben) – Trennzeichen prüfen.")
        if ROLE_PROBAND not in roles:
            hints.append("Kein Teil ist „Proband“ – alle Bilder landen unter „ohne Proband“.")
        if ROLE_TIMEPOINT not in roles:
            hints.append("Kein Teil ist „Zeitpunkt“ – alle Bilder stehen unter „ohne Zeitpunkt“.")
        if ROLE_GROUP not in roles:
            hints.append("Kein Teil ist „Gruppe“ – Gruppen kannst du im nächsten Schritt je Proband eintragen.")
        self.n_hint.setText("\n".join(hints))

    # ── page 3 ───────────────────────────────────────────
    def _page_measure(self) -> QWidget:
        w = QWidget(); l = QHBoxLayout(w); l.setSpacing(24)
        s = self.study.settings
        form = QFormLayout()
        self.m_gray = QComboBox(); self.m_gray.addItem("(R+G+B)/3 – ImageJ-Standard", GRAY_MEAN)
        self.m_gray.addItem("gewichtet 0,299/0,587/0,114", GRAY_WEIGHTED)
        self.m_gray.setCurrentIndex(0 if s.gray_mode == GRAY_MEAN else 1)
        self.m_fields = QSpinBox(); self.m_fields.setRange(1, 10); self.m_fields.setValue(s.i0_fields)
        self.m_fsize = QSpinBox(); self.m_fsize.setRange(3, 31); self.m_fsize.setSingleStep(2); self.m_fsize.setValue(s.i0_field_size)
        self.m_fsize.setSuffix(" px Kante")
        self.m_rin = QSpinBox(); self.m_rin.setRange(1, 200); self.m_rin.setValue(s.i0_ring_inner); self.m_rin.setSuffix(" px")
        self.m_rout = QSpinBox(); self.m_rout.setRange(2, 400); self.m_rout.setValue(s.i0_ring_outer); self.m_rout.setSuffix(" px")
        self.m_imin = QDoubleSpinBox(); self.m_imin.setRange(0, 255); self.m_imin.setValue(s.i0_min)
        self.m_imax = QDoubleSpinBox(); self.m_imax.setRange(0, 255); self.m_imax.setValue(s.i0_max)
        self.m_method = QComboBox()
        for k, v in METHOD_LABELS.items():
            self.m_method.addItem(v, k)
        self.m_method.setCurrentIndex(self.m_method.findData(s.method))
        self.m_thr = QDoubleSpinBox(); self.m_thr.setDecimals(3); self.m_thr.setRange(0.005, 1); self.m_thr.setSingleStep(0.005); self.m_thr.setValue(s.threshold)
        self.m_input = QComboBox(); self.m_input.addItem("Farbbild (RGB)", "rgb"); self.m_input.addItem("DAB-Karte", "dab")
        self.m_debris = QDoubleSpinBox(); self.m_debris.setDecimals(3); self.m_debris.setRange(0, 1); self.m_debris.setSingleStep(0.005)
        self.m_debris.setSpecialValueText("aus")
        form.addRow("<b>Grauwert</b>", QLabel(""))
        form.addRow("RGB → Grau:", self.m_gray)
        form.addRow("<b>Hintergrund I₀ je Zelle</b>", QLabel(""))
        form.addRow("Anzahl Felder:", self.m_fields)
        form.addRow("Feldgröße:", self.m_fsize)
        form.addRow("Abstand zur Kontur ab:", self.m_rin)
        form.addRow("… bis:", self.m_rout)
        form.addRow("I₀ gültig von:", self.m_imin)
        form.addRow("… bis:", self.m_imax)
        form.addRow("<b>Kontur</b>", QLabel(""))
        form.addRow("Methode:", self.m_method)
        form.addRow("OD-Schwelle:", self.m_thr)
        form.addRow("Cellpose sieht:", self.m_input)
        form.addRow("Debris-Verdacht ab H-OD:", self.m_debris)
        l.addLayout(form, 1)
        gl = QVBoxLayout()
        gl.addWidget(QLabel("<b>Gruppe je Proband</b> (nur nötig, wenn die Gruppe nicht im Dateinamen steht)"))
        self.g_table = QTableWidget(0, 2); self.g_table.setHorizontalHeaderLabels(["Proband", "Gruppe"])
        self.g_table.verticalHeader().setVisible(False)
        self.g_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gl.addWidget(self.g_table, 1)
        l.addLayout(gl, 1)
        return w

    def _enter_measure(self) -> None:
        target_changed = (self.study.target == TARGET_NUCLEUS) != self.r_nuc.isChecked()
        if not self.editing or target_changed:
            tmp = copy.deepcopy(self.study)
            tmp.target = TARGET_NUCLEUS if self.r_nuc.isChecked() else TARGET_CELL
            tmp.apply_target()
            self.m_input.setCurrentIndex(self.m_input.findData(tmp.settings.cellpose.input))
            self.m_debris.setValue(tmp.settings.debris_h)
            self.m_method.setCurrentIndex(self.m_method.findData(tmp.settings.method))
        else:
            self.m_input.setCurrentIndex(self.m_input.findData(self.study.settings.cellpose.input))
            self.m_debris.setValue(self.study.settings.debris_h)
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
            f"<p>{len(st.folders)} Ordner · {len(imgs)} Bilder · {len({i.proband for i in imgs})} Probanden · "
            f"Marker: {', '.join(st.markers(imgs)) or '–'}</p>"
            f"<p>Gemessen wird: <b>{TARGET_LABELS[st.target]}</b>, OD = −log₁₀(Ī / I₀) aus dem Grauwert.<br>"
            f"I₀: {s.i0_fields} Felder à {s.i0_field_size}×{s.i0_field_size} px, {s.i0_ring_inner}–{s.i0_ring_outer} px neben der Kontur, "
            f"gültig {s.i0_min:g}–{s.i0_max:g}.<br>"
            f"Kontur: {METHOD_LABELS[s.method]}, Cellpose auf {'DAB-Karte' if s.cellpose.input == 'dab' else 'RGB'}.</p>"
            "<p>Cellpose startet im Hintergrund, sobald die Auswertung geöffnet ist.</p>")

    # ── navigation ───────────────────────────────────────
    def _go(self, step: int) -> None:
        i = self.stack.currentIndex()
        if step > 0:
            if i == 0:
                if not self.e_name.text().strip():
                    QMessageBox.information(self, "Neue Studie", "Gib der Studie einen Namen.")
                    return
                if not self.f_list.count():
                    QMessageBox.information(self, "Neue Studie", "Füge mindestens einen Bildordner hinzu.")
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
        self.b_next.setText("Studie speichern" if j == len(self.STEPS) - 1 else "Weiter")
