"""The one settings window (mockup v3): study pages that travel in packages,
and pages for this computer only.

Study pages edit a working copy of the profile. Nothing reaches the study
until "Speichern …", which first shows what each change does (profile_diff)
in a sheet. Computer pages (language, toolbar, model files, …) are saved as
soon as they change — they cannot invalidate any data.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import nameparser
from ..cellpose_runner import BUILTIN_MODEL, ModelUnavailable, known_builtins, list_images, resolve_model
from ..grouping import group_images
from ..persistence import TypeDef
from ..profile import LIMITS, ProfileData, list_profiles, load_profile
from ..profile_diff import diff_profiles, profile_diff
from ..theme import COLOR_SCHEMES, FONT_SCALE_CHOICES, OUTLINE_ORDER, apply_scheme_to_types

STUDY_PAGES = [("images", "qt_page_images"), ("seg", "qt_page_seg"), ("types", "qt_page_types"),
               ("size", "qt_page_size"), ("display", "qt_page_display"), ("export", "qt_page_export")]
LOCAL_PAGES = [("general", "qt_page_general"), ("edit", "qt_page_edit"), ("keys", "qt_page_keys"),
               ("models", "qt_page_models")]

FIELD_LABELS = {
    "cellpose.model": "settings_cp_model", "cellpose.diameter": "settings_cp_diameter",
    "cellpose.flow_threshold": "settings_cp_flow", "cellpose.cellprob_threshold": "settings_cp_cellprob",
    "use_simple_parser": "pe_use_simple", "delimiters": "pe_delimiters", "field_roles": "pe_roles",
    "image_pattern": "pe_image_pattern", "target_cells": "pe_target_cells",
    "margin_n": "settings_margin_n", "size_filter.enabled": "pe_size_enabled",
    "size_filter.min_factor": "pe_size_min", "size_filter.max_factor": "pe_size_max",
    "name": "pe_name", "import_folder": "pe_import_folder",
    "images_per_subject": "pe_images_per_subject", "stats_enabled": "pe_stats_enabled",
    "overview_xlsx": "pe_overview_xlsx", "overlay_png": "pe_overlay_png",
    "copy_originals_to_outputs": "pe_copy_originals", "use_gpu": "qt_gpu",
    "overlay_alpha": "pe_overlay_alpha", "type_outlines_enabled": "pe_outlines_enabled",
    "show_margin_overlay": "settings_show_margin_overlay", "cell_types": "qt_page_types",
}
ROLE_KEYS = {nameparser.ROLE_SUBJECT: "pe_role_subject", nameparser.ROLE_TIMEPOINT: "pe_role_timepoint",
             nameparser.ROLE_IMAGE_NUM: "pe_role_image_num", nameparser.ROLE_IGNORE: "pe_role_ignore"}
ROLE_COLORS = {nameparser.ROLE_SUBJECT: "accent_primary", nameparser.ROLE_TIMEPOINT: "state_finished",
               nameparser.ROLE_IMAGE_NUM: "accent_warn", nameparser.ROLE_IGNORE: "fg_muted"}
DELIMS = [("_", "pe_delim_underscore"), (" ", "pe_delim_space"), ("-", "pe_delim_dash"), (".", "pe_delim_dot")]


def label(S: dict, key: str) -> str:
    return S[key].rstrip(": ").strip()


def fmt_value(S: dict, v) -> str:
    if isinstance(v, bool):
        return "✓" if v else "–"
    if isinstance(v, float):
        return f"{v:g}".replace(".", S["qt_decimal_sep"])
    if isinstance(v, list):
        return " ".join(repr(x) if isinstance(x, str) and len(x) == 1 else str(x) for x in v) or "–"
    return str(v) if v not in (None, "") else "–"


def models_using(name: str) -> list[str]:
    out = []
    for pn in list_profiles():
        p = load_profile(pn)
        if p is not None and (p.cellpose.model or BUILTIN_MODEL) == name:
            out.append(pn)
    return out


# ─────────────────────────────────────────────
# Building blocks
# ─────────────────────────────────────────────
def _card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("settingsCard")
    v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(10)
    return f, v


def _segmented(options: list[tuple[str, str]], current: str, on_pick: Callable[[str], None]) -> QFrame:
    fr = QFrame(); fr.setObjectName("segment")
    h = QHBoxLayout(fr); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
    grp = QButtonGroup(fr); grp.setExclusive(True)
    for i, (value, text) in enumerate(options):
        b = QPushButton(text); b.setCheckable(True); b.setChecked(value == current)
        b.setProperty("first", i == 0); b.setProperty("last", i == len(options) - 1)
        b.setMinimumHeight(30)
        b.clicked.connect(lambda _c=False, v=value: on_pick(v))
        grp.addButton(b); h.addWidget(b)
    fr.setSizePolicy(fr.sizePolicy().horizontalPolicy(), fr.sizePolicy().verticalPolicy())
    fr.setMaximumWidth(120 * len(options))
    return fr


def _dspin(lo: float, hi: float, step: float, value: float, decimals: int = 2) -> QDoubleSpinBox:
    s = QDoubleSpinBox(); s.setRange(lo, hi); s.setSingleStep(step); s.setDecimals(decimals)
    s.setValue(value); s.setMaximumWidth(140)
    return s


class SettingsDialog(QDialog):
    def __init__(self, parent, P: dict, S: dict, settings, profile: ProfileData,
                 folder: Path | None, images: list[Path],
                 on_study_saved: Callable[[ProfileData, str], None],
                 on_local_changed: Callable[[str], None],
                 on_switch_study: Callable[[], None],
                 start_page: str = "images"):
        super().__init__(parent)
        self.P, self.S, self.settings = P, S, settings
        self.orig = copy.deepcopy(profile)
        self.work = copy.deepcopy(profile)
        self.folder, self.images = folder, images
        self._on_study_saved = on_study_saved
        self._on_local_changed = on_local_changed
        self._on_switch_study = on_switch_study
        self._folder_images: list[Path] | None = None
        self.setWindowTitle(S["settings_title"])
        self.resize(1100, 740)

        root = QHBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        root.addWidget(self._build_nav())
        right = QWidget(); rv = QVBoxLayout(right); rv.setContentsMargins(0, 0, 0, 0); rv.setSpacing(0)
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        rv.addWidget(self._build_footer())
        root.addWidget(right, 1)
        self._pages: dict[str, int] = {}
        self._build_pages()
        self.show_page(start_page)
        self._changed()

    # ── navigation ──
    def _build_nav(self) -> QWidget:
        S = self.S
        nav = QFrame(); nav.setObjectName("settingsNav"); nav.setFixedWidth(270)
        v = QVBoxLayout(nav); v.setContentsMargins(10, 16, 10, 16); v.setSpacing(2)
        cap = QLabel(S["qt_nav_study"].upper()); cap.setObjectName("caption"); v.addWidget(cap)
        self.study_btn = QPushButton(f"●  {self.work.name}   ▾"); self.study_btn.setObjectName("studySwitch")
        self.study_btn.setToolTip(S["study_menu_switch"])
        self.study_btn.clicked.connect(self._switch_study)
        v.addWidget(self.study_btn)
        b1 = QLabel(S["qt_badge_travels"]); b1.setObjectName("badgeStudy"); v.addWidget(b1)
        self.nav_group = QButtonGroup(self); self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, lk in STUDY_PAGES:
            v.addWidget(self._nav_button(key, S[lk]))
        sep = QFrame(); sep.setObjectName("sep"); sep.setFixedHeight(1); v.addSpacing(10); v.addWidget(sep); v.addSpacing(10)
        cap2 = QLabel(S["qt_nav_local"].upper()); cap2.setObjectName("caption"); v.addWidget(cap2)
        b2 = QLabel(S["qt_badge_local"]); b2.setObjectName("badgeLocal"); v.addWidget(b2)
        for key, lk in LOCAL_PAGES:
            v.addWidget(self._nav_button(key, S[lk]))
        v.addStretch(1)
        return nav

    def _nav_button(self, key: str, text: str) -> QPushButton:
        # "&" would be eaten as a mnemonic marker ("Bilder & Gruppierung").
        b = QPushButton(text.replace("&", "&&")); b.setObjectName("navItem"); b.setCheckable(True)
        b.clicked.connect(lambda _c=False, k=key: self.show_page(k))
        self.nav_group.addButton(b)
        self.nav_buttons[key] = b
        return b

    def show_page(self, key: str) -> None:
        if key in self._pages:
            self.stack.setCurrentIndex(self._pages[key])
            self.nav_buttons[key].setChecked(True)

    # ── footer ──
    def _build_footer(self) -> QWidget:
        f = QFrame(); f.setObjectName("settingsFooter")
        h = QHBoxLayout(f); h.setContentsMargins(28, 12, 28, 12); h.setSpacing(10)
        self.footer_dot = QLabel("●"); self.footer_dot.setStyleSheet(f"color: {self.P['accent_warn']};")
        self.footer_text = QLabel("")
        self.btn_discard = QPushButton(self.S["qt_discard"]); self.btn_discard.clicked.connect(self._discard)
        self.btn_save = QPushButton(self.S["qt_save_dots"]); self.btn_save.setProperty("class", "primary")
        self.btn_save.clicked.connect(self._save)
        close = QPushButton(self.S["qt_close"]); close.clicked.connect(self.close)
        h.addWidget(self.footer_dot); h.addWidget(self.footer_text, 1)
        h.addWidget(self.btn_discard); h.addWidget(self.btn_save); h.addWidget(close)
        return f

    def _changed(self) -> None:
        n = len(diff_profiles(self.orig, self.work))
        self.footer_text.setText(self.S["qt_n_changes"].format(n=n, name=self.orig.name) if n
                                 else self.S["qt_no_changes"])
        self.footer_dot.setVisible(bool(n))
        self.btn_discard.setEnabled(bool(n))
        self.btn_save.setEnabled(bool(n))

    def _discard(self) -> None:
        self.work = copy.deepcopy(self.orig)
        current = self.stack.currentIndex()
        self._build_pages()
        self.stack.setCurrentIndex(current)
        self._changed()

    def _save(self) -> None:
        diff = profile_diff(self.orig, self.work, self.folder, self.images)
        if not diff:
            return
        sheet = SaveSheet(self, self.P, self.S, self.settings, self.orig.name, diff)
        if sheet.exec() != QDialog.Accepted:
            return
        saved = copy.deepcopy(self.work)
        self._on_study_saved(saved, sheet.choice)
        self.orig = copy.deepcopy(saved)
        self.study_btn.setText(f"●  {saved.name}   ▾")
        self._changed()

    def _switch_study(self) -> None:
        if not self._confirm_leave():
            return
        self.reject()
        self._on_switch_study()

    def _confirm_leave(self) -> bool:
        if not diff_profiles(self.orig, self.work):
            return True
        return QMessageBox.question(self, self.S["settings_title"], self.S["note_discard_msg"]) == QMessageBox.Yes

    def closeEvent(self, e):
        if self._confirm_leave():
            e.accept()
        else:
            e.ignore()

    def reject(self):
        if self._confirm_leave():
            super().reject()

    # ── pages ──
    def _build_pages(self) -> None:
        while self.stack.count():
            w = self.stack.widget(0); self.stack.removeWidget(w); w.deleteLater()
        self._pages.clear()
        builders = {"images": self._page_images, "seg": self._page_seg, "types": self._page_types,
                    "size": self._page_size, "display": self._page_display, "export": self._page_export,
                    "general": self._page_general, "edit": self._page_edit, "keys": self._page_keys,
                    "models": self._page_models}
        subs = {"images": "qt_page_images_sub", "seg": "qt_page_seg_sub", "types": "qt_page_types_sub",
                "size": "qt_page_size_sub", "display": "qt_page_display_sub", "export": "qt_page_export_sub",
                "general": "qt_page_local_sub", "edit": "qt_page_local_sub", "keys": "qt_page_keys_sub",
                "models": "qt_page_models_sub"}
        titles = dict(STUDY_PAGES + LOCAL_PAGES)
        for key, build in builders.items():
            page = QWidget(); v = QVBoxLayout(page); v.setContentsMargins(32, 24, 32, 24); v.setSpacing(14)
            t = QLabel(self.S[titles[key]]); t.setStyleSheet("font-size: 21px; font-weight: 700;")
            sub = QLabel(self.S[subs[key]]); sub.setWordWrap(True)
            sub.setStyleSheet(f"color: {self.P['fg_secondary']};")
            v.addWidget(t); v.addWidget(sub)
            build(v)
            v.addStretch(1)
            scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(page)
            self._pages[key] = self.stack.addWidget(scroll)

    def _set(self, setter: Callable[[], None]) -> None:
        setter()
        self._changed()

    # Studie · Bilder & Gruppierung
    def _page_images(self, v: QVBoxLayout) -> None:
        S, w = self.S, self.work
        card, cv = _card()
        grid = QGridLayout(); grid.setHorizontalSpacing(14); grid.setVerticalSpacing(10)
        grid.addWidget(QLabel(label(S, "pe_name")), 0, 0)
        name = QLineEdit(w.name); name.setMaximumWidth(320)
        name.textEdited.connect(lambda t: self._set(lambda: setattr(w, "name", t.strip() or self.orig.name)))
        grid.addWidget(name, 0, 1)
        grid.addWidget(QLabel(label(S, "pe_import_folder")), 1, 0)
        row = QHBoxLayout()
        self.folder_label = QLabel(w.import_folder or "–"); self.folder_label.setWordWrap(True)
        choose = QPushButton(S["pe_choose_folder"]); choose.clicked.connect(self._choose_folder)
        row.addWidget(self.folder_label, 1); row.addWidget(choose)
        grid.addLayout(row, 1, 1)
        cv.addLayout(grid)
        v.addWidget(card)

        card, cv = _card()
        head = QHBoxLayout()
        hl = QLabel(S["qt_read_names"]); hl.setStyleSheet("font-weight: 600;")
        head.addWidget(hl, 1)
        head.addWidget(_segmented([("simple", S["qt_parse_simple"]), ("regex", S["qt_parse_regex"])],
                                  "simple" if w.use_simple_parser else "regex", self._set_parser))
        cv.addLayout(head)
        self.parser_box = QVBoxLayout(); cv.addLayout(self.parser_box)
        self.parse_check = QLabel(""); self.parse_check.setWordWrap(True); self.parse_check.setObjectName("checkLine")
        cv.addWidget(self.parse_check)
        v.addWidget(card)
        self._fill_parser()

    def _choose_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.S["pe_import_folder"],
                                                self.work.import_folder or "")
        if path:
            self.work.import_folder = path
            self.folder_label.setText(path)
            self._folder_images = None
            self._fill_parser()
            self._changed()

    def _set_parser(self, mode: str) -> None:
        self.work.use_simple_parser = mode == "simple"
        self._fill_parser()
        self._changed()

    def _clear(self, layout) -> None:
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._clear(it.layout())

    def _fill_parser(self) -> None:
        S, w = self.S, self.work
        self._clear(self.parser_box)
        if w.use_simple_parser:
            row = QHBoxLayout(); row.addWidget(QLabel(label(S, "pe_delimiters")))
            for d, key in DELIMS:
                cb = QCheckBox(S[key].strip()); cb.setChecked(d in w.delimiters)
                cb.toggled.connect(lambda on, d=d: self._toggle_delim(d, on))
                row.addWidget(cb)
            row.addStretch(1)
            self.parser_box.addLayout(row)
            samples = nameparser.sample_filenames(w.import_folder)
            if samples:
                sample = samples[0]
                cap = QLabel(S["qt_click_role"].format(name=sample))
                cap.setStyleSheet(f"color: {self.P['fg_secondary']};"); cap.setWordWrap(True)
                self.parser_box.addWidget(cap)
                tokens = nameparser.tokenize(sample, w.delimiters)
                roles = list(w.field_roles) + [nameparser.ROLE_IGNORE] * max(0, len(tokens) - len(w.field_roles))
                chips = QHBoxLayout(); chips.setSpacing(8)
                for i, tok in enumerate(tokens):
                    chips.addWidget(self._token_chip(i, tok, roles[i]))
                chips.addStretch(1)
                self.parser_box.addLayout(chips)
        else:
            pat = QLineEdit(w.image_pattern)
            pat.textEdited.connect(lambda t: (setattr(w, "image_pattern", t), self._update_check(), self._changed()))
            self.parser_box.addWidget(pat)
            hint = QLabel(S["pe_pattern_help"]); hint.setWordWrap(True)
            hint.setStyleSheet(f"color: {self.P['fg_secondary']}; font-size: 12px;")
            self.parser_box.addWidget(hint)
        self._update_check()

    def _token_chip(self, i: int, tok: str, role: str) -> QPushButton:
        col = self.P[ROLE_COLORS[role]]
        b = QPushButton(f"{tok}\n{label(self.S, ROLE_KEYS[role])} ▾")
        b.setObjectName("tokenChip")
        b.setStyleSheet(f"QPushButton#tokenChip {{ border: 2px solid {col}; border-radius: 10px;"
                        f" padding: 6px 12px; text-align: left; min-height: 40px; }}")
        menu = QMenu(b)
        for r, key in ROLE_KEYS.items():
            a = menu.addAction(label(self.S, key))
            a.triggered.connect(lambda _c=False, r=r: self._set_role(i, r))
        b.setMenu(menu)
        return b

    def _set_role(self, i: int, role: str) -> None:
        roles = list(self.work.field_roles)
        while len(roles) <= i:
            roles.append(nameparser.ROLE_IGNORE)
        roles[i] = role
        self.work.field_roles = roles
        self._fill_parser()
        self._changed()

    def _toggle_delim(self, d: str, on: bool) -> None:
        ds = [x for x in self.work.delimiters if x != d] + ([d] if on else [])
        self.work.delimiters = ds or ["_"]
        self._fill_parser()
        self._changed()

    def _update_check(self) -> None:
        S, w = self.S, self.work
        if not w.import_folder or not Path(w.import_folder).is_dir():
            self.parse_check.setText(S["qt_parse_none"])
            return
        if self._folder_images is None:
            f = Path(w.import_folder)
            try:
                self._folder_images = list_images(f, exclude={f / "outputs"})
            except Exception:  # noqa: BLE001
                self._folder_images = []
        imgs = self._folder_images
        if w.use_simple_parser:
            ok_roles, _why = nameparser.validate_roles(w.field_roles)
            if not ok_roles:
                self.parse_check.setText("⚠ " + label(S, "pe_roles"))
                self.parse_check.setProperty("bad", True)
                self.style().unpolish(self.parse_check); self.style().polish(self.parse_check)
                return
        try:
            g = group_images(imgs, w)
        except Exception as e:  # noqa: BLE001 — e.g. a broken regex while typing
            self.parse_check.setText(f"⚠ {e}")
            return
        ung = sum(1 for i in g.flat if i.is_ungrouped)
        subs = {i.subject for i in g.flat if not i.is_ungrouped}
        tps = {i.timepoint for i in g.flat if not i.is_ungrouped}
        self.parse_check.setText(("✓ " if ung == 0 else "⚠ ") + S["qt_parse_check"].format(
            ok=len(imgs) - ung, n=len(imgs), subs=len(subs), tps=len(tps), ung=ung))
        self.parse_check.setProperty("bad", ung > 0)
        self.style().unpolish(self.parse_check); self.style().polish(self.parse_check)

    # Studie · Segmentierung
    def _page_seg(self, v: QVBoxLayout) -> None:
        S, w = self.S, self.work
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{label(S, 'settings_cp_model')}</b>"))
        combo = QComboBox(); combo.setMaximumWidth(340)
        names = list(dict.fromkeys(list(known_builtins()) + list(self.settings.cellpose_models)
                                   + [w.cellpose.model or BUILTIN_MODEL]))
        combo.addItems(names)
        combo.setCurrentText(w.cellpose.model or BUILTIN_MODEL)
        cv.addWidget(combo)
        self.model_status = QLabel(""); self.model_status.setWordWrap(True)
        assign = QPushButton(S["qt_assign_file"]); assign.clicked.connect(lambda: self.show_page("models"))
        status_row = QHBoxLayout(); status_row.addWidget(self.model_status, 1); status_row.addWidget(assign)
        self.model_assign_btn = assign
        cv.addLayout(status_row)

        def on_model(name):
            w.cellpose.model = name
            self._update_model_status()
            self._changed()
        combo.currentTextChanged.connect(on_model)
        v.addWidget(card)
        self._update_model_status()

        card, cv = _card()
        grid = QGridLayout(); grid.setHorizontalSpacing(24)
        lo, hi = LIMITS["diameter"]
        d = _dspin(lo, hi, 1, w.cellpose.diameter, 1)
        lo2, hi2 = LIMITS["flow_threshold"]
        fl = _dspin(lo2, hi2, 0.1, w.cellpose.flow_threshold)
        lo3, hi3 = LIMITS["cellprob_threshold"]
        cp = _dspin(lo3, hi3, 0.5, w.cellpose.cellprob_threshold, 1)
        for col, (key, spin, attr) in enumerate((("settings_cp_diameter", d, "diameter"),
                                                 ("settings_cp_flow", fl, "flow_threshold"),
                                                 ("settings_cp_cellprob", cp, "cellprob_threshold"))):
            grid.addWidget(QLabel(f"<b>{label(S, key)}</b>"), 0, col, Qt.AlignLeft)
            grid.addWidget(spin, 1, col, Qt.AlignLeft)
            spin.valueChanged.connect(lambda val, a=attr: self._set(lambda: setattr(w.cellpose, a, float(val))))
        cv.addLayout(grid)
        hint = QLabel(S["qt_seg_hint"]); hint.setStyleSheet(f"color: {self.P['fg_secondary']};"); hint.setWordWrap(True)
        cv.addWidget(hint)
        v.addWidget(card)

        card, cv = _card()
        gpu = QCheckBox(S["qt_gpu"]); gpu.setChecked(w.use_gpu)
        gpu.toggled.connect(lambda on: self._set(lambda: setattr(w, "use_gpu", bool(on))))
        cv.addWidget(gpu)
        v.addWidget(card)

    def _update_model_status(self) -> None:
        name = self.work.cellpose.model or BUILTIN_MODEL
        try:
            resolve_model(name, self.settings.cellpose_models)
            self.model_status.setText("✓ " + self.S["qt_model_ok"])
            self.model_status.setStyleSheet(f"color: {self.P['state_finished']};")
            self.model_assign_btn.hide()
        except ModelUnavailable:
            self.model_status.setText("⚠ " + self.S["qt_model_unassigned"].format(name=name))
            self.model_status.setStyleSheet(f"color: {self.P['accent_warn']};")
            self.model_assign_btn.show()

    # Studie · Zelltypen
    def _page_types(self, v: QVBoxLayout) -> None:
        S = self.S
        top = QHBoxLayout(); top.addStretch(1)
        scheme = QComboBox()
        scheme.addItem(S["pe_color_scheme"], None)
        lang = "de" if S["qt_decimal_sep"] == "," else "en"
        for key, sch in COLOR_SCHEMES.items():
            scheme.addItem(sch[f"label_{lang}"], key)
        scheme.activated.connect(lambda i: self._apply_scheme(scheme.itemData(i)))
        top.addWidget(scheme)
        v.addLayout(top)
        card, cv = _card()
        self.types_grid = QGridLayout(); self.types_grid.setHorizontalSpacing(10); self.types_grid.setVerticalSpacing(8)
        cv.addLayout(self.types_grid)
        add = QPushButton(S["qt_types_add"]); add.clicked.connect(self._add_type)
        cv.addWidget(add, 0, Qt.AlignLeft)
        v.addWidget(card)
        note = QLabel(S["qt_types_chip_note"]); note.setStyleSheet(f"color: {self.P['fg_secondary']};")
        note.setWordWrap(True)
        v.addWidget(note)
        self._fill_types()

    def _fill_types(self) -> None:
        S, g = self.S, self.types_grid
        self._clear(g)
        for c, key in enumerate(("qt_col_color", "settings_type_name", "qt_col_key", "pe_type_outline")):
            h = QLabel(label(S, key).upper()); h.setObjectName("caption"); g.addWidget(h, 0, c)
        outline_labels = {"": "pe_outline_none", "solid": "pe_outline_solid", "dashed": "pe_outline_dashed",
                          "dotted": "pe_outline_dotted", "dashdot": "pe_outline_dashdot"}
        for r, t in enumerate(self.work.cell_types, start=1):
            col = QPushButton(); col.setFixedSize(44, 28)
            col.setStyleSheet(f"background: {t.color}; border: 1px solid {self.P['sash']}; border-radius: 6px;")
            col.setAccessibleName(S["settings_color_pick"])
            col.clicked.connect(lambda _c=False, t=t: self._pick_color(t))
            name = QLineEdit(t.name or (S.get(t.i18n_key, "") if t.i18n_key else "") or t.key)
            name.textEdited.connect(lambda txt, t=t: self._set(lambda: setattr(t, "name", txt)))
            hk = QLineEdit(t.hotkey); hk.setMaxLength(1); hk.setMaximumWidth(50)
            hk.textEdited.connect(lambda txt, t=t: self._set(lambda: setattr(t, "hotkey", txt)))
            ol = QComboBox()
            for o in OUTLINE_ORDER:
                ol.addItem(S[outline_labels[o]], o)
            ol.setCurrentIndex(max(0, OUTLINE_ORDER.index(t.outline) if t.outline in OUTLINE_ORDER else 0))
            ol.activated.connect(lambda i, t=t, ol=ol: self._set(lambda: setattr(t, "outline", ol.itemData(i))))
            rm = QPushButton(S["qt_remove"]); rm.setStyleSheet(f"color: {self.P['accent_error']}; border: none;")
            rm.setEnabled(t.key != "normal")
            if t.key == "normal":
                rm.setToolTip(S["settings_type_normal_protected"])
            rm.clicked.connect(lambda _c=False, t=t: self._remove_type(t))
            for c, wdg in enumerate((col, name, hk, ol, rm)):
                g.addWidget(wdg, r, c)
        g.setColumnStretch(1, 1)

    def _pick_color(self, t: TypeDef) -> None:
        c = QColorDialog.getColor(QColor(t.color), self, self.S["settings_color_pick"])
        if c.isValid():
            t.color = c.name().upper()
            self._fill_types(); self._changed()

    def _add_type(self) -> None:
        taken = {t.key for t in self.work.cell_types}
        name, ok = QInputDialog.getText(self, self.S["qt_page_types"], label(self.S, "settings_type_name"),
                                        text=self.S["qt_new_type"])
        if not ok or not name.strip():
            return
        import re
        base = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "type"
        key, i = base, 2
        while key in taken:
            key, i = f"{base}_{i}", i + 1
        self.work.cell_types.append(TypeDef(key=key, name=name.strip(), color="#8291AB",
                                            hotkey=str(min(9, len(self.work.cell_types) + 1))))
        self._fill_types(); self._changed()

    def _remove_type(self, t: TypeDef) -> None:
        self.work.cell_types = [x for x in self.work.cell_types if x is not t]
        if self.work.active_type_key == t.key:
            self.work.active_type_key = self.work.default_type_key()
        self._fill_types(); self._changed()

    def _apply_scheme(self, key) -> None:
        if key:
            apply_scheme_to_types(self.work.cell_types, key)
            self._fill_types(); self._changed()

    # Studie · Größenfilter & Bildrand
    def _page_size(self, v: QVBoxLayout) -> None:
        S, w = self.S, self.work
        card, cv = _card()
        en = QCheckBox(label(S, "pe_size_enabled")); en.setChecked(w.size_filter.enabled)
        en.toggled.connect(lambda on: self._set(lambda: setattr(w.size_filter, "enabled", bool(on))))
        cv.addWidget(en)
        hint = QLabel(S["pe_size_hint"]); hint.setWordWrap(True); hint.setStyleSheet(f"color: {self.P['fg_secondary']};")
        cv.addWidget(hint)
        grid = QGridLayout(); grid.setHorizontalSpacing(24)
        lo, hi = LIMITS["size_min_factor"]; mn = _dspin(lo, hi, 0.05, w.size_filter.min_factor)
        lo2, hi2 = LIMITS["size_max_factor"]; mx = _dspin(lo2, hi2, 0.05, w.size_filter.max_factor)
        grid.addWidget(QLabel(f"<b>{label(S, 'pe_size_min')}</b>"), 0, 0, Qt.AlignLeft)
        grid.addWidget(mn, 1, 0, Qt.AlignLeft)
        grid.addWidget(QLabel(f"<b>{label(S, 'pe_size_max')}</b>"), 0, 1, Qt.AlignLeft)
        grid.addWidget(mx, 1, 1, Qt.AlignLeft)
        mn.valueChanged.connect(lambda val: self._set(lambda: setattr(w.size_filter, "min_factor", float(val))))
        mx.valueChanged.connect(lambda val: self._set(lambda: setattr(w.size_filter, "max_factor", float(val))))
        cv.addLayout(grid)
        v.addWidget(card)
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{label(S, 'settings_margin_n')}</b>"))
        m = QSpinBox(); m.setRange(0, 500); m.setValue(w.margin_n); m.setMaximumWidth(140)
        m.valueChanged.connect(lambda val: self._set(lambda: setattr(w, "margin_n", int(val))))
        cv.addWidget(m)
        h2 = QLabel(S["settings_margin_help"]); h2.setWordWrap(True); h2.setStyleSheet(f"color: {self.P['fg_secondary']};")
        cv.addWidget(h2)
        v.addWidget(card)

    # Studie · Darstellung
    def _page_display(self, v: QVBoxLayout) -> None:
        S, w = self.S, self.work
        card, cv = _card()
        cv.addWidget(QLabel(f"<b>{label(S, 'pe_overlay_alpha')}</b>"))
        row = QHBoxLayout()
        lo, hi = LIMITS["overlay_alpha"]
        sl = QSlider(Qt.Horizontal); sl.setRange(int(lo * 100), int(hi * 100)); sl.setValue(int(round(w.overlay_alpha * 100)))
        pct = QLabel(f"{sl.value()} %"); pct.setMinimumWidth(50)
        sl.valueChanged.connect(lambda val: (pct.setText(f"{val} %"),
                                             self._set(lambda: setattr(w, "overlay_alpha", val / 100.0))))
        row.addWidget(sl, 1); row.addWidget(pct)
        cv.addLayout(row)
        hint = QLabel(S["pe_overlay_alpha_hint"]); hint.setWordWrap(True); hint.setStyleSheet(f"color: {self.P['fg_secondary']};")
        cv.addWidget(hint)
        v.addWidget(card)
        card, cv = _card()
        ol = QCheckBox(label(S, "pe_outlines_enabled")); ol.setChecked(w.type_outlines_enabled)
        ol.toggled.connect(lambda on: self._set(lambda: setattr(w, "type_outlines_enabled", bool(on))))
        sm = QCheckBox(label(S, "settings_show_margin_overlay")); sm.setChecked(w.show_margin_overlay)
        sm.toggled.connect(lambda on: self._set(lambda: setattr(w, "show_margin_overlay", bool(on))))
        cv.addWidget(ol); cv.addWidget(sm)
        v.addWidget(card)

    # Studie · Export & Statistik
    def _page_export(self, v: QVBoxLayout) -> None:
        S, w = self.S, self.work
        card, cv = _card()
        grid = QGridLayout(); grid.setHorizontalSpacing(24)
        t = QSpinBox(); t.setRange(1, 100000); t.setValue(w.target_cells); t.setMaximumWidth(160)
        n = QSpinBox(); n.setRange(1, 1000); n.setValue(w.images_per_subject); n.setMaximumWidth(160)
        grid.addWidget(QLabel(f"<b>{label(S, 'pe_target_cells')}</b>"), 0, 0, Qt.AlignLeft)
        grid.addWidget(t, 1, 0, Qt.AlignLeft)
        grid.addWidget(QLabel(f"<b>{label(S, 'pe_images_per_subject')}</b>"), 0, 1, Qt.AlignLeft)
        grid.addWidget(n, 1, 1, Qt.AlignLeft)
        t.valueChanged.connect(lambda val: self._set(lambda: setattr(w, "target_cells", int(val))))
        n.valueChanged.connect(lambda val: self._set(lambda: setattr(w, "images_per_subject", int(val))))
        cv.addLayout(grid)
        v.addWidget(card)
        card, cv = _card()
        for attr, key in (("overview_xlsx", "pe_overview_xlsx"), ("overlay_png", "pe_overlay_png"),
                          ("copy_originals_to_outputs", "pe_copy_originals"), ("stats_enabled", "pe_stats_enabled")):
            cb = QCheckBox(S[key]); cb.setChecked(bool(getattr(w, attr)))
            cb.toggled.connect(lambda on, a=attr: self._set(lambda: setattr(w, a, bool(on))))
            cv.addWidget(cb)
        v.addWidget(card)

    # Rechner · Allgemein (saved at once)
    def _local(self, field: str, value) -> None:
        setattr(self.settings, field, value)
        self.settings.save()
        self._on_local_changed(field)

    def _page_general(self, v: QVBoxLayout) -> None:
        S, st = self.S, self.settings
        card, cv = _card()
        grid = QGridLayout(); grid.setHorizontalSpacing(18); grid.setVerticalSpacing(14)
        rows = [
            ("settings_language", _segmented([("de", "Deutsch"), ("en", "English")], st.language,
                                             lambda val: self._local("language", val))),
            ("settings_theme", _segmented([("light", S["settings_theme_light"]), ("dark", S["settings_theme_dark"])],
                                          st.theme, lambda val: self._local("theme", val))),
            ("settings_font_scale", _segmented([(f, S[f"settings_font_{int(f * 100)}"]) for f in FONT_SCALE_CHOICES],
                                               st.font_scale, lambda val: self._local("font_scale", val))),
        ]
        for r, (key, wdg) in enumerate(rows):
            grid.addWidget(QLabel(label(S, key)), r, 0); grid.addWidget(wdg, r, 1, Qt.AlignLeft)
        grid.addWidget(QLabel(label(S, "settings_reviewer")), len(rows), 0)
        rev = QLineEdit(st.reviewer_name); rev.setMaximumWidth(260)
        rev.editingFinished.connect(lambda: self._local("reviewer_name", rev.text().strip()))
        grid.addWidget(rev, len(rows), 1)
        cv.addLayout(grid)
        v.addWidget(card)
        row = QHBoxLayout()
        note = QLabel(S["qt_restart_note"]); note.setStyleSheet(f"color: {self.P['fg_secondary']};")
        note.setWordWrap(True)
        restart = QPushButton(S["qt_restart_now"]); restart.setProperty("class", "primary")
        restart.clicked.connect(lambda: self._on_local_changed("restart"))
        row.addWidget(note, 1); row.addWidget(restart)
        v.addLayout(row)

    # Rechner · Bearbeiten
    def _page_edit(self, v: QVBoxLayout) -> None:
        S, st = self.S, self.settings
        card, cv = _card()
        ov = QCheckBox(S["settings_overlay_autoshow"]); ov.setChecked(st.overlay_autoshow)
        ov.toggled.connect(lambda on: self._local("overlay_autoshow", bool(on)))
        cv.addWidget(ov)
        v.addWidget(card)
        for title, field, opts in (
                ("settings_toolbar", "toolbar_layout",
                 [("grouped", "settings_toolbar_grouped"), ("two_row", "settings_toolbar_two_row")]),
                ("settings_resegment", "resegment_default",
                 [("later", "settings_reseg_later"), ("ask", "settings_reseg_ask"), ("batch", "settings_reseg_batch")])):
            card, cv = _card()
            cv.addWidget(QLabel(f"<b>{label(S, title)}</b>"))
            grp = QButtonGroup(card)
            for value, key in opts:
                rb = QRadioButton(S[key]); rb.setChecked(getattr(st, field) == value)
                rb.toggled.connect(lambda on, f=field, val=value: on and self._local(f, val))
                grp.addButton(rb); cv.addWidget(rb)
            v.addWidget(card)

    # Rechner · Tastenkürzel
    def _page_keys(self, v: QVBoxLayout) -> None:
        from .. import shortcuts as sc
        S = self.S
        top = QHBoxLayout(); top.addStretch(1)
        reset = QPushButton(S["qt_keys_reset"]); reset.clicked.connect(self._keys_reset)
        top.addWidget(reset)
        v.addLayout(top)
        self.keys_msg = QLabel(""); self.keys_msg.setWordWrap(True)
        self.keys_msg.setStyleSheet(f"color: {self.P['accent_error']}; font-weight: 600;")
        v.addWidget(self.keys_msg)
        card, cv = _card()
        self.keys_grid = QGridLayout(); self.keys_grid.setHorizontalSpacing(10); self.keys_grid.setVerticalSpacing(6)
        cv.addLayout(self.keys_grid)
        v.addWidget(card)
        self._keys = sc.effective(self.settings.shortcuts)
        self._fill_keys()

    def _fill_keys(self) -> None:
        from .. import shortcuts as sc
        S, g = self.S, self.keys_grid
        self._clear(g)
        for c, key in enumerate(("qt_keys_action", "qt_keys_first", "qt_keys_second")):
            h = QLabel(label(S, key).upper()); h.setObjectName("caption"); g.addWidget(h, 0, c)
        self.key_buttons: dict[tuple[str, int], "KeyCaptureButton"] = {}
        for r, a in enumerate(sc.ACTIONS, start=1):
            g.addWidget(QLabel(label(S, a.label_key)), r, 0)
            for slot in (0, 1):
                cur = self._keys[a.id][slot] if slot < len(self._keys[a.id]) else ""
                b = KeyCaptureButton(S, cur, lambda bind, aid=a.id, sl=slot: self._assign(aid, sl, bind))
                g.addWidget(b, r, 1 + slot)
                self.key_buttons[(a.id, slot)] = b
        g.setColumnStretch(0, 1)

    def _assign(self, aid: str, slot: int, binding: str) -> bool:
        """Bind (or clear, binding "") one slot. Refuses a key another action owns."""
        from .. import shortcuts as sc
        S = self.S
        if binding:
            binding = sc.normalize(binding)
            owner = sc.owner_of(self._keys, binding, except_id=aid)
            if owner is not None:
                self.keys_msg.setText(S["qt_keys_taken"].format(
                    key=KeyCaptureButton.display(S, binding), action=label(S, sc.BY_ID[owner].label_key)))
                return False
        cur = list(self._keys[aid])
        while len(cur) <= slot:
            cur.append("")
        cur[slot] = binding
        self._keys[aid] = [b for b in dict.fromkeys(cur) if b]
        self.keys_msg.setText("")
        self._local("shortcuts", sc.overrides_from(self._keys))
        self._fill_keys()
        return True

    def _keys_reset(self) -> None:
        from .. import shortcuts as sc
        self._keys = sc.effective({})
        self.keys_msg.setText("")
        self._local("shortcuts", {})
        self._fill_keys()

    # Rechner · Modelldateien
    def _page_models(self, v: QVBoxLayout) -> None:
        top = QHBoxLayout(); top.addStretch(1)
        add = QPushButton(self.S["models_add"]); add.setProperty("class", "primary")
        add.clicked.connect(self._add_model)
        top.addWidget(add)
        v.addLayout(top)
        card, cv = _card()
        self.models_grid = QGridLayout(); self.models_grid.setHorizontalSpacing(14); self.models_grid.setVerticalSpacing(8)
        cv.addLayout(self.models_grid)
        v.addWidget(card)
        self._fill_models()

    def _fill_models(self) -> None:
        S, g, P = self.S, self.models_grid, self.P
        self._clear(g)
        for c, key in enumerate(("models_col_name", "models_col_file", "qt_used_by")):
            h = QLabel(label(S, key).upper()); h.setObjectName("caption"); g.addWidget(h, 0, c)
        names = list(dict.fromkeys(list(known_builtins()) + list(self.settings.cellpose_models)))
        wanted = self.work.cellpose.model or BUILTIN_MODEL
        if wanted not in names:
            names.append(wanted)
        for r, name in enumerate(names, start=1):
            g.addWidget(QLabel(f"<b>{name}</b>"), r, 0)
            if name in known_builtins():
                f = QLabel(S["models_builtin"]); f.setStyleSheet(f"color: {P['fg_secondary']};")
                act = None
            elif name in self.settings.cellpose_models:
                path = self.settings.cellpose_models[name]
                ok = Path(path).exists()
                f = QLabel(path if ok else f"⚠ {S['models_file_missing']}: {path}")
                f.setStyleSheet(f"color: {P['fg'] if ok else P['accent_warn']};")
                act = QPushButton(S["models_remove"]); act.setStyleSheet(f"color: {P['accent_error']}; border: none;")
                act.clicked.connect(lambda _c=False, n=name: self._remove_model(n))
            else:
                f = QLabel("⚠ " + S["qt_model_unassigned"].format(name=name))
                f.setStyleSheet(f"color: {P['accent_warn']};")
                act = QPushButton(S["qt_assign_file"])
                act.clicked.connect(lambda _c=False, n=name: self._assign_model(n))
            f.setWordWrap(True)
            g.addWidget(f, r, 1)
            used = QLabel(", ".join(models_using(name)) or "–"); used.setStyleSheet(f"color: {P['fg_secondary']};")
            g.addWidget(used, r, 2)
            if act is not None:
                g.addWidget(act, r, 3)
        g.setColumnStretch(1, 1)

    def _pick_model_file(self) -> str | None:
        path, _ = QFileDialog.getOpenFileName(self, self.S["models_pick_file"])
        return path or None

    def _assign_model(self, name: str) -> None:
        path = self._pick_model_file()
        if path:
            reg = dict(self.settings.cellpose_models); reg[name] = path
            self._local("cellpose_models", reg)
            self._fill_models(); self._update_model_status()

    def _add_model(self) -> None:
        path = self._pick_model_file()
        if not path:
            return
        name, ok = QInputDialog.getText(self, self.S["qt_page_models"], self.S["models_name_prompt"],
                                        text=Path(path).stem)
        name = name.strip()
        if not ok or not name:
            return
        if name in known_builtins() or name in self.settings.cellpose_models:
            QMessageBox.information(self, self.S["qt_page_models"], self.S["models_name_reserved"])
            return
        reg = dict(self.settings.cellpose_models); reg[name] = path
        self._local("cellpose_models", reg)
        self._fill_models()

    def _remove_model(self, name: str) -> None:
        reg = {k: v for k, v in self.settings.cellpose_models.items() if k != name}
        self._local("cellpose_models", reg)
        self._fill_models(); self._update_model_status()


# ─────────────────────────────────────────────
# Key capture
# ─────────────────────────────────────────────
class KeyCaptureButton(QPushButton):
    """Shows a binding; click it, then press a key combination or a mouse side
    button to rebind. Backspace/Delete clears; clicking again cancels."""

    def __init__(self, S: dict, binding: str, on_bind: Callable[[str], bool]):
        super().__init__(self.display(S, binding))
        self.S, self.binding, self._on_bind = S, binding, on_bind
        self.capturing = False
        self.setObjectName("keyCapture")
        self.setMinimumWidth(170)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setToolTip(S["qt_page_keys_sub"])
        self.clicked.connect(self._toggle)

    @staticmethod
    def display(S: dict, binding: str) -> str:
        from PySide6.QtGui import QKeySequence
        from .. import shortcuts as sc
        if not binding:
            return S["qt_keys_none"]
        if binding == sc.MOUSE_BACK:
            return S["qt_mouse_back"]
        if binding == sc.MOUSE_FORWARD:
            return S["qt_mouse_forward"]
        return QKeySequence(binding).toString(QKeySequence.NativeText) or binding

    def _toggle(self) -> None:
        self.capturing = not self.capturing
        self.setProperty("capturing", self.capturing)
        self.style().unpolish(self); self.style().polish(self)
        self.setText(self.S["qt_keys_press"] if self.capturing else self.display(self.S, self.binding))
        if self.capturing:
            self.setFocus()

    def _finish(self, binding: str) -> None:
        self.capturing = False
        self.setProperty("capturing", False)
        self.style().unpolish(self); self.style().polish(self)
        if self._on_bind(binding):
            self.binding = binding
        self.setText(self.display(self.S, self.binding))

    def event(self, e):
        # Tab/Backtab would move focus before keyPressEvent sees them.
        from PySide6.QtCore import QEvent
        if self.capturing and e.type() == QEvent.KeyPress:
            self.keyPressEvent(e)
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if not self.capturing:
            return super().keyPressEvent(e)
        from PySide6.QtGui import QKeySequence
        k = e.key()
        if k in (Qt.Key_Shift, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_unknown):
            return                                   # wait for the real key
        if k in (Qt.Key_Backspace, Qt.Key_Delete) and e.modifiers() == Qt.NoModifier:
            self._finish("")
            return
        self._finish(QKeySequence(e.keyCombination()).toString(QKeySequence.PortableText))

    def mousePressEvent(self, e):
        from .. import shortcuts as sc
        if self.capturing and e.button() in (Qt.BackButton, Qt.ForwardButton):
            self._finish(sc.MOUSE_BACK if e.button() == Qt.BackButton else sc.MOUSE_FORWARD)
            return
        super().mousePressEvent(e)

    def focusOutEvent(self, e):
        if self.capturing:
            self._toggle()                           # clicking elsewhere cancels
        super().focusOutEvent(e)


# ─────────────────────────────────────────────
# Save sheet with consequences
# ─────────────────────────────────────────────
class SaveSheet(QDialog):
    def __init__(self, parent, P: dict, S: dict, settings, study_name: str, diff):
        super().__init__(parent)
        self.setWindowFlag(Qt.Sheet, True)        # a macOS sheet under the title bar
        self.setWindowModality(Qt.WindowModal)
        self.setMinimumWidth(620)
        self.choice = "later"
        v = QVBoxLayout(self); v.setContentsMargins(26, 22, 26, 22); v.setSpacing(10)
        t = QLabel(S["qt_save_title"].format(name=study_name)); t.setStyleSheet("font-size: 17px; font-weight: 700;")
        sub = QLabel(S["qt_save_sub"]); sub.setStyleSheet(f"color: {P['fg_secondary']};")
        v.addWidget(t); v.addWidget(sub)
        badge_col = {"resegment": ("accent_error", 0.12), "grouping": ("accent_warn", 0.14),
                     "counting": ("accent_warn", 0.14), "study": ("accent_primary", 0.10),
                     "export": ("accent_primary", 0.10), "display": ("state_finished", 0.12)}
        from .style import _mix
        imp = diff.impact
        for kind, changes in diff.by_kind():
            card, cv = _card()
            col, a = badge_col[kind]
            head = QHBoxLayout()
            lines = [f"<b>{label(S, FIELD_LABELS.get(c.field, c.field))}</b>: "
                     f"{fmt_value(S, c.old)} → {fmt_value(S, c.new)}" for c in changes]
            body = QLabel("<br>".join(lines)); body.setTextFormat(Qt.RichText); body.setWordWrap(True)
            head.addWidget(body, 1)
            badge = QLabel(S[f"qt_kind_{kind}"])
            badge.setStyleSheet(f"color: {P[col]}; background: {_mix(P['panel_bg'], P[col], a)};"
                                f" border-radius: 9px; padding: 2px 8px; font-size: 11px;")
            head.addWidget(badge, 0, Qt.AlignTop)
            cv.addLayout(head)
            text = {"resegment": S["qt_imp_resegment"].format(n=imp.n_with_masks),
                    "grouping": S["qt_imp_grouping"].format(moved=imp.n_moved, before=imp.n_ungrouped_before,
                                                            after=imp.n_ungrouped_after),
                    "counting": S["qt_imp_counting"], "display": S["qt_imp_display"],
                    "export": S["qt_imp_export"], "study": S["qt_imp_study"]}[kind]
            il = QLabel(text); il.setWordWrap(True); il.setStyleSheet(f"color: {P['fg_secondary']};")
            cv.addWidget(il)
            v.addWidget(card)
        if "resegment" in diff.kinds and imp.n_with_masks:
            card, cv = _card()
            cv.addWidget(QLabel(f"<b>{S['qt_masks_legend']}</b>"))
            later = QRadioButton(f"{S['qt_masks_later']}  {S['qt_default_hint']}")
            batch = QRadioButton(S["qt_masks_batch"])
            if settings.resegment_default == "batch":
                batch.setChecked(True); self.choice = "batch"
            else:
                later.setChecked(True)
            later.toggled.connect(lambda on: on and setattr(self, "choice", "later"))
            batch.toggled.connect(lambda on: on and setattr(self, "choice", "batch"))
            cv.addWidget(later); cv.addWidget(batch)
            v.addWidget(card)
        row = QHBoxLayout(); row.addStretch(1)
        back = QPushButton(S["qt_back"]); back.clicked.connect(self.reject)
        ok = QPushButton(S["settings_save"]); ok.setProperty("class", "primary"); ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(back); row.addWidget(ok)
        v.addLayout(row)
