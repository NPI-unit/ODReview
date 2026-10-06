"""Neue Studie › Abgleich (mockup v6, board 2): a second rating of a study.

A is a study (its folder, its reviews). B is either a new rating made here —
by me again later (intra-rater) or by someone at this computer — or a package
from someone else. B works on A's masks (the same files, never recomputed)
and in its own namespace, so it cannot see A: blind by construction. "Blind"
additionally keeps the comparison closed until B is finished."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QVBoxLayout,
)

from .. import interrater as ir
from ..cellpose_runner import has_cache, list_images
from ..profile import list_profiles, load_profile


class AbgleichDialog(QDialog):
    def __init__(self, parent, P, S, settings, current: str | None = None):
        super().__init__(parent)
        self.P, self.S, self.settings = P, S, settings
        self.result_profile = None           # the study to open afterwards
        self.compare_with: str | None = None  # open the agreement window for this rater
        self.zip_path: str | None = None
        self.setWindowTitle(S["ab_title"])
        self.setMinimumWidth(720)
        v = QVBoxLayout(self); v.setContentsMargins(26, 22, 26, 18); v.setSpacing(12)
        t = QLabel(S["ab_title"]); t.setStyleSheet("font-size: 21px; font-weight: 700;")
        sub = QLabel(S["ab_sub"]); sub.setWordWrap(True); sub.setStyleSheet(f"color: {P['fg_secondary']};")
        v.addWidget(t); v.addWidget(sub)

        card = QFrame(); card.setObjectName("settingsCard")
        g = QGridLayout(card); g.setContentsMargins(16, 14, 16, 14); g.setHorizontalSpacing(14); g.setVerticalSpacing(10)
        g.addWidget(QLabel(f"<b>{S['ab_a']}</b>"), 0, 0)
        self.base = QComboBox(); self.base.setMinimumWidth(320)
        bases = [n for n in list_profiles() if (p := load_profile(n)) is not None and not p.rater and p.import_folder]
        self.base.addItems(bases)
        if current in bases:
            self.base.setCurrentText(current)
        self.base.currentTextChanged.connect(lambda _t: self._update())
        g.addWidget(self.base, 0, 1)
        self.base_info = QLabel(""); self.base_info.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        g.addWidget(self.base_info, 1, 1)
        g.addWidget(QLabel(f"<b>{S['ab_b']}</b>"), 2, 0)
        box = QVBoxLayout()
        self.rb_self = QRadioButton(S["ab_self"]); self.rb_pkg = QRadioButton(S["ab_pkg"])
        grp = QButtonGroup(card); grp.addButton(self.rb_self); grp.addButton(self.rb_pkg)
        self.rb_self.setChecked(True)
        box.addWidget(self.rb_self)
        row = QHBoxLayout(); row.setContentsMargins(26, 0, 0, 0)
        row.addWidget(QLabel(S["ab_name"]))
        me = (settings.reviewer_name or S["ir_me"]).strip()
        self.name = QLineEdit(f"{me} 2"); self.name.setMaximumWidth(220)
        row.addWidget(self.name); row.addStretch(1)
        box.addLayout(row)
        box.addWidget(self.rb_pkg)
        row2 = QHBoxLayout(); row2.setContentsMargins(26, 0, 0, 0)
        self.pkg_label = QLabel("–"); self.pkg_btn = QPushButton(S["pkg_import_pick"])
        self.pkg_btn.clicked.connect(self._pick_zip)
        row2.addWidget(self.pkg_label, 1); row2.addWidget(self.pkg_btn)
        box.addLayout(row2)
        g.addLayout(box, 2, 1)
        g.setColumnStretch(1, 1)
        v.addWidget(card)

        self.blind = QCheckBox(S["ab_blind"]); self.blind.setChecked(True)
        v.addWidget(self.blind)
        bh = QLabel(S["ab_blind_hint"]); bh.setWordWrap(True); bh.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        v.addWidget(bh)
        rule = QLabel(S["ab_rule"]); rule.setObjectName("checkLine"); rule.setWordWrap(True)
        v.addWidget(rule)
        for rb in (self.rb_self, self.rb_pkg):
            rb.toggled.connect(lambda _on: self._update())

        foot = QHBoxLayout(); foot.addStretch(1)
        cancel = QPushButton(S["settings_cancel"]); cancel.clicked.connect(self.reject)
        self.go = QPushButton(S["ab_create"]); self.go.setProperty("class", "primary"); self.go.clicked.connect(self._create)
        foot.addWidget(cancel); foot.addWidget(self.go)
        v.addLayout(foot)
        self._update()

    def _base_profile(self):
        return load_profile(self.base.currentText()) if self.base.currentText() else None

    def _stems_with_masks(self, p) -> list[str]:
        folder = Path(p.import_folder)
        if not folder.is_dir():
            return []
        return [i.stem for i in list_images(folder, exclude={folder / "outputs"}) if has_cache(folder, i)]

    def _update(self) -> None:
        p = self._base_profile()
        n = len(self._stems_with_masks(p)) if p else 0
        self.base_info.setText(self.S["ab_base_info"].format(n=n, folder=p.import_folder if p else "–"))
        self_mode = self.rb_self.isChecked()
        self.name.setEnabled(self_mode); self.pkg_btn.setEnabled(not self_mode)
        self.blind.setEnabled(self_mode)
        ok = p is not None and n > 0 and (self.name.text().strip() if self_mode else self.zip_path)
        self.go.setEnabled(bool(ok))

    def _pick_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.S["pkg_import_pick"], "",
                                              "Erys package / ZIP (*.zip);;All files (*)")
        if path:
            self.zip_path = path
            self.pkg_label.setText(Path(path).name)
            self._update()

    def _create(self) -> None:
        S = self.S
        p = self._base_profile()
        folder = Path(p.import_folder)
        if self.rb_self.isChecked():
            name = self.name.text().strip()
            if name in ir.list_raters(folder) or name == ir.CONSENSUS:
                QMessageBox.information(self, S["ab_title"], S["qt_name_taken"])
                return
            ir.create_self_rating(folder, name, self._stems_with_masks(p), blind=self.blind.isChecked())
            self.result_profile = ir.rater_study(p, name, blind=self.blind.isChecked())
        else:
            from .interrater import compare_package_flow
            name = compare_package_flow(self, self.P, S, folder, Path(self.zip_path))
            if not name:
                return
            ir.rater_study(p, name)            # to look through B's rating later
            self.result_profile = p
            self.compare_with = name
        self.accept()
