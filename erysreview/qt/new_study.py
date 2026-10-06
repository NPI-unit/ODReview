"""'Neue Studie' — the assistant (mockup v5): three steps and a summary.

1 Ordner & Name   name, image folder (count, size, stick?), template study
2 Dateinamen      delimiters and roles guessed from the real files, a sample
                  with the unassigned files first, one-click fix
3 Zellen & Modell cell types and Cellpose values from the template, a test
                  run on one image that measures the cell diameter
  Fertig          summary with jumps back; segment now (in the background)
                  or later

Everything the assistant does not ask about comes from the template and is
changed later under Einstellungen › Studie. The logic lives in study_setup.py.
"""

from __future__ import annotations

import copy
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QRadioButton, QScrollArea, QSpinBox,
    QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import nameparser
from .. import study_setup as setup
from ..cellpose_runner import BUILTIN_MODEL, known_builtins
from ..package import fmt_bytes
from ..persistence import TypeDef
from ..profile import LIMITS, ProfileData, list_profiles, load_profile, save_profile
from ..tasks import Task
from .settings import DELIMS, ROLE_COLORS, ROLE_KEYS, _dspin, label
from .tasks import start_task

STEPS = ["wiz_step_folder", "wiz_step_names", "wiz_step_cells", "wiz_step_done"]


def _card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("settingsCard")
    v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
    return f, v


def _muted(text: str, P: dict, size: int = 12) -> QLabel:
    lab = QLabel(text); lab.setWordWrap(True)
    lab.setStyleSheet(f"color: {P['fg_secondary']}; font-size: {size}px;")
    return lab


def _choice(text: str, desc: str, P: dict) -> tuple[QFrame, QRadioButton]:
    fr = QFrame(); fr.setObjectName("choiceCard")
    v = QVBoxLayout(fr); v.setContentsMargins(12, 10, 12, 10); v.setSpacing(3)
    rb = QRadioButton(text); rb.setStyleSheet("font-weight: 600;")
    v.addWidget(rb)
    d = _muted(desc, P); d.setContentsMargins(26, 0, 0, 0)
    v.addWidget(d)
    rb.toggled.connect(lambda on: (fr.setProperty("on", on), fr.style().unpolish(fr), fr.style().polish(fr)))
    return fr, rb


class NewStudyWizard(QDialog):
    def __init__(self, parent, P: dict, S: dict, settings, on_import=None):
        super().__init__(parent)
        self.P, self.S, self.settings = P, S, settings
        self._on_import = on_import
        self.result_profile: ProfileData | None = None
        self.segment_now = True
        self.scan: setup.FolderScan | None = None
        self.folder: Path | None = None
        self.delims: list[str] = ["_"]
        self.roles: list[str] = []
        self.auto_delims = True
        self.work = ProfileData(cell_types=[])  # cells & model, from the template (step 3)
        self._template_name: str | None = None
        self._probe = None                     # (n_cells, seconds, diameter|None, thumbnail)
        self._probe_task = None
        self.setWindowTitle(S["wiz_title"])
        self.resize(1100, 720)

        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_nav())
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        rv.addWidget(self._build_footer())
        root.addWidget(right, 1)
        for i, build in enumerate((self._page_folder, self._page_names, self._page_cells, self._page_done)):
            self.stack.addWidget(self._page(i, build))
        self.step = 0
        self._reached = 0
        self._go(0)

    # ── frame ──
    def _build_nav(self) -> QWidget:
        S = self.S
        nav = QFrame(); nav.setObjectName("settingsNav"); nav.setFixedWidth(240)
        v = QVBoxLayout(nav); v.setContentsMargins(12, 20, 12, 20); v.setSpacing(4)
        cap = QLabel(S["wiz_title"].upper()); cap.setObjectName("caption"); v.addWidget(cap)
        v.addSpacing(6)
        self.nav_buttons: list[QPushButton] = []
        grp = QButtonGroup(self); grp.setExclusive(True)
        for i, key in enumerate(STEPS):
            b = QPushButton(); b.setObjectName("navItem"); b.setCheckable(True)
            b.clicked.connect(lambda _c=False, i=i: self._go(i))
            grp.addButton(b); v.addWidget(b)
            self.nav_buttons.append(b)
        v.addStretch(1)
        v.addWidget(_muted(S["wiz_later_hint"], self.P))
        return nav

    def _build_footer(self) -> QWidget:
        S = self.S
        f = QFrame(); f.setObjectName("settingsFooter")
        h = QHBoxLayout(f); h.setContentsMargins(28, 12, 28, 12); h.setSpacing(10)
        self.footer_text = QLabel(""); self.footer_text.setStyleSheet(f"color: {self.P['fg_secondary']};")
        cancel = QPushButton(S["settings_cancel"]); cancel.setFlat(True); cancel.clicked.connect(self.reject)
        self.btn_back = QPushButton("← " + S["wiz_back"]); self.btn_back.clicked.connect(lambda: self._go(self.step - 1))
        self.btn_next = QPushButton(); self.btn_next.setProperty("class", "primary")
        self.btn_next.setMinimumWidth(150)
        self.btn_next.clicked.connect(self._next)
        h.addWidget(self.footer_text, 1); h.addWidget(cancel); h.addWidget(self.btn_back); h.addWidget(self.btn_next)
        return f

    def _page(self, i: int, build) -> QWidget:
        S = self.S
        page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(28, 24, 28, 18); v.setSpacing(12)
        t = QLabel(S[f"wiz_h{i}"]); t.setStyleSheet("font-size: 21px; font-weight: 700;")
        v.addWidget(t); v.addWidget(_muted(S[f"wiz_sub{i}"], self.P, 13))
        build(v)
        v.addStretch(1)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        return scroll

    def _go(self, i: int) -> None:
        i = max(0, min(i, len(STEPS) - 1))
        if i > self._reached:
            i = self._reached
        self.step = i
        if i == 1:
            self._enter_names()
        elif i == 2:
            self._enter_cells()
        elif i == 3:
            self._enter_done()
        self.stack.setCurrentIndex(i)
        self._update()

    def _next(self) -> None:
        if not self._valid(self.step):
            return
        if self.step == len(STEPS) - 1:
            self._finish()
            return
        self._reached = max(self._reached, self.step + 1)
        self._go(self.step + 1)

    def _valid(self, i: int) -> bool:
        if i == 0:
            name = self.name_edit.text().strip()
            return bool(name) and name not in list_profiles() and self.scan is not None and self.scan.count > 0
        if i == 1:
            ok, _ = nameparser.validate_roles(self.roles)
            return ok and self.scan is not None and setup.read_result(self.scan.images, self.delims, self.roles).ok > 0
        return True

    def _update(self) -> None:
        S = self.S
        for i, b in enumerate(self.nav_buttons):
            mark = "✓" if i < self._reached and i != self.step else str(i + 1)
            if i == len(STEPS) - 1:
                mark = "✓" if i == self.step else "·"
            b.setText(f"{mark}   {S[STEPS[i]].replace('&', '&&')}")
            b.setChecked(i == self.step)
            b.setEnabled(i <= self._reached)
        self.btn_back.setVisible(self.step > 0)
        last = self.step == len(STEPS) - 1
        self.btn_next.setText(S["wiz_create"] if last else S["wiz_next"] + "  →")
        self.btn_next.setEnabled(self._valid(self.step))
        if self.step < 3:
            self.footer_text.setText(S["wiz_step_of"].format(i=self.step + 1, n=3))
        else:
            self.footer_text.setText(S["wiz_footer_done"])
        if self.step == 1 and self.scan is not None:
            r = setup.read_result(self.scan.images, self.delims, self.roles)
            if r.ok:
                self.footer_text.setText(S["wiz_footer_names"].format(subs=r.subjects, tps=len(r.timepoints),
                                                                     list=", ".join(r.timepoints[:4])))

    # ── step 1: folder & name ──
    def _page_folder(self, v: QVBoxLayout) -> None:
        S, P = self.S, self.P
        card, cv = _card()
        g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(10)
        g.addWidget(QLabel(S["wiz_name"]), 0, 0)
        self.name_edit = QLineEdit(); self.name_edit.setPlaceholderText(S["wiz_name_placeholder"])
        self.name_edit.setMaximumWidth(320)
        self.name_edit.textChanged.connect(lambda _t: self._name_changed())
        g.addWidget(self.name_edit, 0, 1)
        self.name_msg = QLabel(""); self.name_msg.setStyleSheet(f"color: {P['accent_warn']}; font-size: 12px;")
        g.addWidget(self.name_msg, 1, 1)
        g.addWidget(QLabel(label(S, "pe_import_folder")), 2, 0)
        row = QHBoxLayout()
        self.folder_label = QLabel("–"); self.folder_label.setWordWrap(True)
        choose = QPushButton(S["pe_choose_folder"]); choose.clicked.connect(self._choose_folder)
        row.addWidget(self.folder_label, 1); row.addWidget(choose)
        g.addLayout(row, 2, 1)
        g.setColumnStretch(1, 1)
        cv.addLayout(g)
        v.addWidget(card)
        self.scan_line = QLabel(""); self.scan_line.setObjectName("checkLine"); self.scan_line.setWordWrap(True)
        self.scan_line.hide()
        v.addWidget(self.scan_line)

        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{S['wiz_template_q']}</b>"))
        row = QHBoxLayout(); row.setSpacing(10)
        others = [n for n in list_profiles()]
        last = self.settings.last_profile_name if self.settings.last_profile_name in others else \
            (others[0] if others else None)
        f1, self.rb_template = _choice(S["wiz_template_from"], S["wiz_template_from_desc"], P)
        f2, self.rb_defaults = _choice(S["wiz_template_defaults"], S["wiz_template_defaults_desc"], P)
        row.addWidget(f1, 1); row.addWidget(f2, 1)
        cv.addLayout(row)
        pick = QHBoxLayout()
        pick.addWidget(QLabel(S["wiz_template"]))
        self.template_combo = QComboBox(); self.template_combo.addItems(others); self.template_combo.setMinimumWidth(260)
        if last:
            self.template_combo.setCurrentText(last)
        pick.addWidget(self.template_combo); pick.addStretch(1)
        cv.addLayout(pick)
        grp = QButtonGroup(card); grp.addButton(self.rb_template); grp.addButton(self.rb_defaults)
        (self.rb_template if others else self.rb_defaults).setChecked(True)
        self.rb_template.setEnabled(bool(others))
        self.rb_template.toggled.connect(lambda on: self.template_combo.setEnabled(on))
        self.template_combo.setEnabled(self.rb_template.isChecked())
        v.addWidget(card)
        if self._on_import is not None:
            imp = QPushButton(S["wiz_got_package"]); imp.setFlat(True)
            imp.setStyleSheet(f"color: {P['accent_primary']}; text-align: left; border: none;")
            imp.clicked.connect(self._import)
            v.addWidget(imp, 0, Qt.AlignLeft)

    def _name_changed(self) -> None:
        name = self.name_edit.text().strip()
        self.name_msg.setText(self.S["qt_name_taken"] if name and name in list_profiles() else "")
        self._update()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.S["pe_import_folder"])
        if path:
            self.set_folder(Path(path))

    def set_folder(self, folder: Path) -> None:
        S = self.S
        self.folder = folder
        self.folder_label.setText(str(folder))
        try:
            self.scan = setup.scan_folder(folder)
        except Exception as e:  # noqa: BLE001
            self.scan = None
            self.scan_line.setText(f"⚠ {e}")
        if self.scan is not None:
            n = self.scan.count
            text = S["wiz_scan"].format(n=n, size=fmt_bytes(self.scan.total_bytes, S["qt_decimal_sep"]))
            if self.scan.removable:
                text += " · " + S["wiz_scan_removable"]
            self.scan_line.setText(("✓ " if n else "⚠ ") + (text if n else S["wiz_scan_empty"]))
            self.scan_line.setProperty("bad", n == 0)
            # a fresh folder means a fresh reading of its names
            names = [p.name for p in self.scan.images]
            self.delims = setup.detect_delimiters(names[:200])
            self.roles = setup.guess_roles(names[:200], self.delims)
            self.auto_delims = True
            if not self.name_edit.text().strip():
                self.name_edit.setText(folder.name)
        self.scan_line.style().unpolish(self.scan_line); self.scan_line.style().polish(self.scan_line)
        self.scan_line.show()
        self._reached = 0
        self._update()

    def _import(self) -> None:
        self.reject()
        self._on_import()

    def template(self) -> ProfileData | None:
        if self.rb_template.isChecked() and self.template_combo.currentText():
            return load_profile(self.template_combo.currentText())
        return None

    # ── step 2: file names ──
    def _page_names(self, v: QVBoxLayout) -> None:
        S = self.S
        card, cv = _card()
        self.delim_row = QHBoxLayout(); cv.addLayout(self.delim_row)
        self.role_hint = _muted(S["wiz_click_roles"], self.P, 13); cv.addWidget(self.role_hint)
        self.chip_row = QHBoxLayout(); self.chip_row.setSpacing(8); cv.addLayout(self.chip_row)
        v.addWidget(card)
        card, cv = _card()
        head = QHBoxLayout()
        hl = QLabel(f"<b>{S['wiz_sample']}</b>"); head.addWidget(hl, 1)
        self.sample_count = _muted("", self.P); head.addWidget(self.sample_count)
        cv.addLayout(head)
        self.sample = QTreeWidget(); self.sample.setRootIsDecorated(False); self.sample.setColumnCount(5)
        self.sample.setHeaderLabels([S["wiz_col_file"], label(S, "pe_role_subject"), label(S, "pe_role_timepoint"),
                                     label(S, "pe_role_image_num"), ""])
        self.sample.setColumnWidth(0, 330); self.sample.setMinimumHeight(150)
        cv.addWidget(self.sample)
        v.addWidget(card)
        row = QHBoxLayout()
        self.read_line = QLabel(""); self.read_line.setObjectName("checkLine"); self.read_line.setWordWrap(True)
        row.addWidget(self.read_line, 1)
        self.fix_btn = QPushButton(""); self.fix_btn.clicked.connect(self._apply_fix)
        row.addWidget(self.fix_btn)
        v.addLayout(row)

    def _clear(self, layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()

    def _enter_names(self) -> None:
        S, P = self.S, self.P
        self._clear(self.delim_row)
        self.delim_row.addWidget(QLabel(label(S, "pe_delimiters")))
        for d, key in DELIMS:
            cb = QCheckBox(S[key].strip()); cb.setChecked(d in self.delims)
            cb.toggled.connect(lambda on, d=d: self._toggle_delim(d, on))
            self.delim_row.addWidget(cb)
        self.delim_row.addStretch(1)
        if self.auto_delims:
            auto = QLabel("✦ " + S["wiz_auto"]); auto.setStyleSheet(f"color: {P['accent_primary']}; font-size: 12px;")
            self.delim_row.addWidget(auto)
        self._clear(self.chip_row)
        names = [p.name for p in self.scan.images] if self.scan else []
        example = setup.example_name(names, self.delims)
        if example:
            self.role_hint.setText(S["wiz_click_roles_example"].format(name=example))
            toks = nameparser.tokenize(example, self.delims)
            roles = list(self.roles) + [nameparser.ROLE_IGNORE] * max(0, len(toks) - len(self.roles))
            self.roles = roles[:len(toks)]
            for i, tok in enumerate(toks):
                self.chip_row.addWidget(self._chip(i, tok, self.roles[i]))
        self.chip_row.addStretch(1)
        self._refresh_check()

    def _chip(self, i: int, tok: str, role: str) -> QPushButton:
        col = self.P[ROLE_COLORS[role]]
        b = QPushButton(f"{tok}\n{label(self.S, ROLE_KEYS[role])} ▾")
        b.setObjectName("tokenChip")
        b.setStyleSheet(f"QPushButton#tokenChip {{ border: 2px solid {col}; border-radius: 10px;"
                        f" padding: 6px 12px; text-align: left; min-height: 40px; }}")
        menu = QMenu(b)
        for r, key in ROLE_KEYS.items():
            a = menu.addAction(label(self.S, key))
            a.triggered.connect(lambda _c=False, r=r: self.set_role(i, r))
        b.setMenu(menu)
        return b

    def set_role(self, i: int, role: str) -> None:
        roles = list(self.roles)
        while len(roles) <= i:
            roles.append(nameparser.ROLE_IGNORE)
        if role in (nameparser.ROLE_SUBJECT, nameparser.ROLE_TIMEPOINT, nameparser.ROLE_IMAGE_NUM):
            roles = [nameparser.ROLE_IGNORE if r == role else r for r in roles]   # one part per role
        roles[i] = role
        self.roles = roles
        self._enter_names()
        self._update()

    def _toggle_delim(self, d: str, on: bool) -> None:
        ds = [x for x in self.delims if x != d] + ([d] if on else [])
        self.delims = ds or ["_"]
        self.auto_delims = False
        names = [p.name for p in self.scan.images[:200]] if self.scan else []
        self.roles = setup.guess_roles(names, self.delims)
        self._enter_names()
        self._update()

    def _refresh_check(self) -> None:
        S, P = self.S, self.P
        if self.scan is None:
            return
        imgs = self.scan.images
        self.sample.clear()
        for row in setup.preview_rows(imgs, self.delims, self.roles):
            it = QTreeWidgetItem([row.name, row.subject, row.timepoint, row.image_num,
                                  "✓" if row.ok else "⚠ " + S["wiz_unassigned"]])
            it.setForeground(4, QColor(P["state_finished"] if row.ok else P["accent_warn"]))
            self.sample.addTopLevelItem(it)
        self.sample_count.setText(S["wiz_sample_of"].format(k=self.sample.topLevelItemCount(), n=len(imgs)))
        ok_roles, why = nameparser.validate_roles(self.roles)
        self.fix_btn.hide()
        self._fix = None
        if not ok_roles:
            self.read_line.setText("⚠ " + S.get(f"wiz_roles_{why}", label(S, "pe_roles")))
            bad = True
        else:
            r = setup.read_result(imgs, self.delims, self.roles)
            bad = r.unassigned > 0
            text = S["wiz_read"].format(ok=r.ok, n=r.n, subs=r.subjects, tps=len(r.timepoints), ung=r.unassigned)
            if bad:
                d = setup.suggest_delimiter(imgs, self.delims, self.roles)
                if d is not None:
                    self._fix = d
                    shown = S["pe_delim_space"].strip() if d == " " else f"„{d}“"
                    self.fix_btn.setText(S["wiz_fix_delim"].format(d=shown))
                    self.fix_btn.show()
            self.read_line.setText(("⚠ " if bad else "✓ ") + text)
        self.read_line.setProperty("bad", bad)
        self.read_line.style().unpolish(self.read_line); self.read_line.style().polish(self.read_line)

    def _apply_fix(self) -> None:
        if self._fix:
            self.delims = self.delims + [self._fix]
            self.auto_delims = False
            self._enter_names()
            self._update()

    # ── step 3: cells & model ──
    def _page_cells(self, v: QVBoxLayout) -> None:
        S = self.S
        row = QHBoxLayout(); row.setSpacing(14)
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{S['qt_page_types']}</b>"))
        self.types_box = QVBoxLayout(); self.types_box.setSpacing(6); cv.addLayout(self.types_box)
        add = QPushButton("+ " + S["wiz_add_type"]); add.clicked.connect(self._add_type)
        cv.addWidget(add)
        self.types_src = _muted("", self.P); cv.addWidget(self.types_src)
        cv.addStretch(1)
        row.addWidget(card, 1)
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{S['qt_page_seg']}</b>"))
        g = QGridLayout(); g.setHorizontalSpacing(12); g.setVerticalSpacing(10)
        g.addWidget(QLabel(label(S, "settings_cp_model")), 0, 0)
        self.model_combo = QComboBox(); self.model_combo.setMinimumWidth(220)
        self.model_combo.currentTextChanged.connect(lambda t: setattr(self.work.cellpose, "model", t))
        g.addWidget(self.model_combo, 0, 1)
        g.addWidget(QLabel(label(S, "settings_cp_diameter")), 1, 0)
        lo, hi = LIMITS["diameter"]
        self.diam = _dspin(lo, hi, 1, 30.0, 1)
        self.diam.valueChanged.connect(lambda val: setattr(self.work.cellpose, "diameter", float(val)))
        g.addWidget(self.diam, 1, 1)
        tl = QLabel(S["wiz_target"]); tl.setToolTip(S["pe_target_cells"])
        g.addWidget(tl, 2, 0)
        self.target = QSpinBox(); self.target.setRange(1, 100000); self.target.setMaximumWidth(140)
        self.target.valueChanged.connect(lambda val: setattr(self.work, "target_cells", int(val)))
        g.addWidget(self.target, 2, 1)
        g.setColumnStretch(1, 1)
        cv.addLayout(g)
        probe = QFrame(); probe.setObjectName("probeBox")
        ph = QHBoxLayout(probe); ph.setContentsMargins(10, 10, 10, 10); ph.setSpacing(12)
        self.thumb = QLabel(); self.thumb.setFixedSize(150, 112); self.thumb.setObjectName("probeThumb")
        self.thumb.setAlignment(Qt.AlignCenter)
        ph.addWidget(self.thumb)
        pv = QVBoxLayout(); pv.setSpacing(6)
        pv.addWidget(QLabel(f"<b>{S['wiz_probe']}</b>"))
        self.probe_info = _muted(S["wiz_probe_hint"], self.P); pv.addWidget(self.probe_info)
        prow = QHBoxLayout()
        self.probe_btn = QPushButton(S["wiz_probe_run"]); self.probe_btn.clicked.connect(self.run_probe)
        self.take_diam = QPushButton(""); self.take_diam.hide(); self.take_diam.clicked.connect(self._take_diameter)
        prow.addWidget(self.probe_btn); prow.addWidget(self.take_diam); prow.addStretch(1)
        pv.addLayout(prow); pv.addStretch(1)
        ph.addLayout(pv, 1)
        cv.addWidget(probe)
        cv.addStretch(1)
        row.addWidget(card, 1)
        v.addLayout(row)
        v.addWidget(_muted(S["wiz_cells_rest"], self.P))
        self._probe_idx = 0

    def _enter_cells(self) -> None:
        S = self.S
        tpl = self.template()
        name = tpl.name if tpl is not None else None
        if name != self._template_name or not self.work.cell_types:
            # deep copy: the defaults' TypeDefs are module-level objects
            self.work = copy.deepcopy(tpl if tpl is not None else ProfileData())
            self._template_name = name
        self.types_src.setText(S["wiz_from_template"].format(name=name) if name else S["wiz_from_defaults"])
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        names = list(dict.fromkeys(list(known_builtins()) + list(self.settings.cellpose_models)
                                   + [self.work.cellpose.model or BUILTIN_MODEL]))
        self.model_combo.addItems(names)
        self.model_combo.setCurrentText(self.work.cellpose.model or BUILTIN_MODEL)
        self.model_combo.blockSignals(False)
        self.diam.blockSignals(True); self.diam.setValue(self.work.cellpose.diameter); self.diam.blockSignals(False)
        self.target.blockSignals(True); self.target.setValue(self.work.target_cells); self.target.blockSignals(False)
        self._fill_types()

    def _fill_types(self) -> None:
        S = self.S
        self._clear_layout(self.types_box)
        for td in self.work.cell_types:
            row = QHBoxLayout(); row.setSpacing(8)
            sw = QPushButton(); sw.setFixedSize(22, 22)
            sw.setStyleSheet(f"background: {td.color}; border: 1px solid {self.P['border']}; border-radius: 5px;"
                             f" min-height: 0; padding: 0;")
            sw.setToolTip(S["wiz_pick_color"])
            sw.clicked.connect(lambda _c=False, td=td: self._pick_color(td))
            name = QLineEdit(td.name or (S.get(td.i18n_key, td.key) if td.i18n_key else td.key))
            name.textEdited.connect(lambda t, td=td: (setattr(td, "name", t), setattr(td, "i18n_key", None)))
            rm = QPushButton("✕"); rm.setFixedWidth(34); rm.setToolTip(S["pm_delete"])
            rm.setEnabled(len(self.work.cell_types) > 1)
            rm.clicked.connect(lambda _c=False, td=td: self._remove_type(td))
            row.addWidget(sw); row.addWidget(name, 1); row.addWidget(rm)
            self.types_box.addLayout(row)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear_layout(it.layout())

    def _pick_color(self, td: TypeDef) -> None:
        c = QColorDialog.getColor(QColor(td.color), self, self.S["wiz_pick_color"])
        if c.isValid():
            td.color = c.name().upper()
            self._fill_types()

    def _add_type(self) -> None:
        keys = {t.key for t in self.work.cell_types}
        n = 1
        while f"typ{n}" in keys:
            n += 1
        palette_cycle = ["#3B7DDD", "#D9534F", "#8E44AD", "#E67E22", "#16A085", "#C0392B"]
        self.work.cell_types.append(TypeDef(key=f"typ{n}", name=f"{self.S['wiz_type']} {n}",
                                            color=palette_cycle[len(keys) % len(palette_cycle)],
                                            hotkey=str(min(9, len(keys) + 1))))
        self._fill_types()

    def _remove_type(self, td: TypeDef) -> None:
        if len(self.work.cell_types) > 1:
            self.work.cell_types = [t for t in self.work.cell_types if t is not td]
            self._fill_types()

    def run_probe(self) -> None:
        """Segment one image with the current values — on a worker thread,
        nothing is saved. Shows count, time and the measured diameter."""
        S = self.S
        if self.scan is None or not self.scan.images or self._probe_task is not None:
            return
        path = self.scan.images[self._probe_idx % len(self.scan.images)]
        self._probe_idx += 1
        params = copy.deepcopy(self.work.cellpose)
        gpu, registry = self.work.use_gpu, self.settings.cellpose_models

        def work(_progress, _cancelled):
            from ..cellpose_runner import load_image, run_cellpose
            img = load_image(path)
            t0 = time.monotonic()
            masks = run_cellpose(img, params, use_gpu=gpu, model_registry=registry)
            return path, img, masks, time.monotonic() - t0

        self.probe_btn.setEnabled(False)
        self.probe_info.setText(S["wiz_probe_running"].format(name=path.name))
        self._probe_task = start_task(self.parent() or self, Task(work=work), lambda *_: None, self._probe_done)

    def _probe_done(self, result, error) -> None:
        S = self.S
        self._probe_task = None
        try:
            self.probe_btn.setEnabled(True)
        except RuntimeError:            # the assistant was closed meanwhile
            return
        self.probe_btn.setText(S["wiz_probe_other"])
        if error is not None:
            self.probe_info.setText("⚠ " + S["wiz_probe_failed"].format(err=error))
            return
        path, img, masks, secs = result
        self.show_probe(path, img, masks, secs)

    def show_probe(self, path: Path, img, masks, secs: float) -> None:
        S = self.S
        n = int(np.asarray(masks).max())
        d = setup.measured_diameter(masks)
        self._probe = (n, secs, d)
        dec = S["qt_decimal_sep"]
        txt = S["wiz_probe_result"].format(name=path.name, n=n, s=f"{secs:.0f}")
        if d is not None:
            txt += " · " + S["wiz_probe_diam"].format(d=f"{d:.0f}")
            self.take_diam.setText(S["wiz_take_diam"].format(d=f"{d:.0f}".replace(".", dec)))
            self.take_diam.setVisible(abs(d - self.work.cellpose.diameter) >= 2)
        self.probe_info.setText(txt)
        self.thumb.setPixmap(self._thumbnail(img, masks))

    def _take_diameter(self) -> None:
        if self._probe and self._probe[2]:
            self.diam.setValue(round(self._probe[2]))
            self.take_diam.hide()

    def _thumbnail(self, img, masks) -> QPixmap:
        a = np.asarray(img)
        if a.ndim == 3:
            a = a[..., :3].mean(axis=2)
        a = a.astype(np.float32)
        lo, hi = np.percentile(a, (1, 99)) if a.size else (0, 1)
        g = np.clip((a - lo) / max(1e-6, hi - lo) * 255, 0, 255).astype(np.uint8)
        rgb = np.stack([g, g, g], axis=-1)
        m = np.asarray(masks)
        edge = np.zeros(m.shape, bool)
        edge[1:, :] |= m[1:, :] != m[:-1, :]
        edge[:, 1:] |= m[:, 1:] != m[:, :-1]
        edge &= m > 0
        col = QColor(self.P["accent_primary"])
        rgb[edge] = (col.red(), col.green(), col.blue())
        rgb = np.ascontiguousarray(rgb)
        h, w = rgb.shape[:2]
        qi = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(qi).scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ── done ──
    def _page_done(self, v: QVBoxLayout) -> None:
        S, P = self.S, self.P
        card, cv = _card()
        head = QHBoxLayout()
        self.sum_name = QLabel(""); self.sum_name.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.sum_tpl = _muted("", P)
        head.addWidget(self.sum_name, 1); head.addWidget(self.sum_tpl)
        cv.addLayout(head)
        self.sum_rows: dict[str, QLabel] = {}
        for key, step in (("wiz_sum_images", 0), ("wiz_sum_groups", 1), ("wiz_sum_types", 2), ("wiz_sum_seg", 2)):
            sep = QFrame(); sep.setObjectName("sep"); sep.setFixedHeight(1); cv.addWidget(sep)
            row = QHBoxLayout()
            k = QLabel(S[key]); k.setFixedWidth(150); k.setStyleSheet(f"color: {P['fg_secondary']};")
            val = QLabel(""); val.setWordWrap(True)
            edit = QPushButton(S["wiz_change"]); edit.setFlat(True)
            edit.setStyleSheet(f"color: {P['accent_primary']}; border: none; font-size: 12px;")
            edit.clicked.connect(lambda _c=False, s=step: self._go(s))
            row.addWidget(k); row.addWidget(val, 1); row.addWidget(edit)
            cv.addLayout(row)
            self.sum_rows[key] = val
        v.addWidget(card)
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{S['wiz_segment_q']}</b>"))
        row = QHBoxLayout(); row.setSpacing(10)
        f1, self.rb_now = _choice(S["wiz_segment_now"], "", P)
        f2, self.rb_later = _choice(S["wiz_segment_later"], S["wiz_segment_later_desc"], P)
        self.now_desc = f1.findChildren(QLabel)[-1]
        row.addWidget(f1, 1); row.addWidget(f2, 1)
        cv.addLayout(row)
        grp = QButtonGroup(card); grp.addButton(self.rb_now); grp.addButton(self.rb_later)
        self.rb_now.setChecked(True)
        v.addWidget(card)

    def _enter_done(self) -> None:
        S = self.S
        name = self.name_edit.text().strip()
        tpl = self.template()
        self.sum_name.setText(name)
        self.sum_tpl.setText(S["wiz_template_is"].format(name=tpl.name) if tpl else S["wiz_from_defaults"])
        n = self.scan.count if self.scan else 0
        self.sum_rows["wiz_sum_images"].setText(S["wiz_sum_images_v"].format(n=n, folder=self.folder or "–"))
        r = setup.read_result(self.scan.images, self.delims, self.roles) if self.scan else None
        if r is not None:
            self.sum_rows["wiz_sum_groups"].setText(S["wiz_sum_groups_v"].format(
                subs=r.subjects, tps=len(r.timepoints), ung=r.unassigned))
        types = [t.name or (S.get(t.i18n_key, t.key) if t.i18n_key else t.key) for t in self.work.cell_types]
        self.sum_rows["wiz_sum_types"].setText(S["wiz_sum_types_v"].format(n=len(types), list=", ".join(types)))
        dec = S["qt_decimal_sep"]
        self.sum_rows["wiz_sum_seg"].setText(S["wiz_sum_seg_v"].format(
            model=self.work.cellpose.model or BUILTIN_MODEL,
            d=f"{self.work.cellpose.diameter:g}".replace(".", dec), t=self.work.target_cells))
        est = ""
        if self._probe:
            est = " " + S["wiz_minutes"].format(m=max(1, round(self._probe[1] * n / 60)))
        self.now_desc.setText(S["wiz_segment_now_desc"].format(n=n) + est)

    def _finish(self) -> None:
        p = setup.build_profile(self.name_edit.text(), str(self.folder), self.work if self.work.cell_types else None,
                                self.delims, self.roles)
        save_profile(p)
        self.result_profile = p
        self.segment_now = self.rb_now.isChecked()
        self.accept()

    def done(self, r) -> None:
        if self._probe_task is not None:
            self._probe_task.cancel()
        super().done(r)
