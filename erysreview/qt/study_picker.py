"""'Studie öffnen' (mockup v3, Q-Studien): one card per study with folder,
progress per proband ID and model status; actions for the selected study.
Replaces the Tk profile manager. Returns the chosen ProfileData or None."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from ..cellpose_runner import ModelUnavailable, list_images, resolve_model
from ..grouping import group_images
from ..profile import (
    DEFAULT_PROFILE_NAME, ProfileData, delete_profile, duplicate_profile,
    ensure_default_profile, list_profiles, load_profile, rename_profile, save_profile,
)
from ..study import study_progress


def _progress_of(p: ProfileData):
    """(progress | None, folder_ok). Only for folders that are reachable —
    a study on an unplugged stick must not stall the dialog."""
    if not p.import_folder:
        return None, False
    folder = Path(p.import_folder)
    if not folder.is_dir():
        return None, False
    try:
        images = list_images(folder, exclude={folder / "outputs"})
        return study_progress(folder, group_images(images, p)), True
    except Exception:  # noqa: BLE001
        return None, True


class _Card(QFrame):
    def __init__(self, picker: "StudyPicker", p: ProfileData, is_last: bool):
        super().__init__()
        self.picker, self.profile = picker, p
        P, S = picker.P, picker.S
        self.setObjectName("studyCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(170)
        v = QVBoxLayout(self); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(6)
        top = QHBoxLayout()
        name = QLabel(p.name); name.setStyleSheet("font-size: 17px; font-weight: 600;")
        top.addWidget(name, 1)
        if is_last:
            tag = QLabel(S["qt_last"])
            tag.setStyleSheet(f"color: {P['accent_primary']}; font-size: 11px;")
            top.addWidget(tag)
        v.addLayout(top)
        prog, folder_ok = _progress_of(p)
        if not p.import_folder:
            folder_txt, col = S["qt_no_folder"], P["fg_muted"]
        elif not folder_ok:
            folder_txt, col = f"{S['qt_folder_missing']}: {p.import_folder}", P["accent_warn"]
        else:
            folder_txt, col = p.import_folder, P["fg_secondary"]
        fl = QLabel(folder_txt); fl.setWordWrap(True); fl.setStyleSheet(f"color: {col}; font-size: 12px;")
        v.addWidget(fl)
        if prog is not None:
            big = QLabel(f'<span style="font-size:22px;font-weight:700;color:{P["state_finished"]};">'
                         f'{prog.done}</span>&nbsp;<span style="color:{P["fg_secondary"]};">'
                         f'/ {S["study_menu_progress"].format(done="", total=prog.total).lstrip(" /")}</span>')
            v.addWidget(big)
            bar = QProgressBar(); bar.setRange(0, max(1, prog.total)); bar.setValue(prog.done)
            bar.setTextVisible(False); v.addWidget(bar)
            rest = QLabel(S["study_menu_rest"].format(wip=prog.wip, open=prog.open))
            rest.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
            v.addWidget(rest)
        try:
            resolve_model(p.cellpose.model, picker.registry)
            m_txt, m_col = f"{p.cellpose.model} ✓", P["state_finished"]
        except ModelUnavailable:
            m_txt, m_col = f"{p.cellpose.model} ⚠", P["accent_warn"]
        ml = QLabel(m_txt); ml.setStyleSheet(f"color: {m_col}; font-size: 12px;")
        v.addStretch(1); v.addWidget(ml)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        self.style().unpolish(self); self.style().polish(self)

    def mousePressEvent(self, _e):
        self.picker.select(self)

    def mouseDoubleClickEvent(self, _e):
        self.picker.select(self)
        self.picker.open_selected()


class StudyPicker(QDialog):
    def __init__(self, parent, P: dict, S: dict, settings):
        super().__init__(parent)
        self.P, self.S, self.settings = P, S, settings
        self.registry = settings.cellpose_models
        self.chosen: ProfileData | None = None
        self._selected: _Card | None = None
        self.setWindowTitle(S["qt_studies_title"])
        self.resize(1080, 720)
        v = QVBoxLayout(self); v.setContentsMargins(40, 32, 40, 24); v.setSpacing(18)
        head = QHBoxLayout()
        col = QVBoxLayout()
        t = QLabel(S["qt_studies_title"]); t.setStyleSheet("font-size: 26px; font-weight: 700;")
        sub = QLabel(S["qt_studies_sub"]); sub.setStyleSheet(f"color: {P['fg_secondary']};")
        col.addWidget(t); col.addWidget(sub)
        head.addLayout(col, 1)
        imp = QPushButton(S["qt_import_pkg"]); imp.setEnabled(False); imp.setToolTip(S["qt_later"])
        new = QPushButton(S["qt_new_study"]); new.setProperty("class", "primary")
        new.clicked.connect(self._new)
        head.addWidget(imp); head.addWidget(new)
        v.addLayout(head)

        self.area = QScrollArea(); self.area.setWidgetResizable(True); self.area.setFrameShape(QFrame.NoFrame)
        v.addWidget(self.area, 1)

        cap = QLabel(S["qt_selected_study"].upper()); cap.setObjectName("caption")
        v.addWidget(cap)
        acts = QHBoxLayout(); acts.setSpacing(8)
        self.btn_open = QPushButton(S["qt_open"]); self.btn_open.setProperty("class", "primary")
        self.btn_open.clicked.connect(self.open_selected)
        self.btn_dup = QPushButton(S["pm_duplicate"]); self.btn_dup.clicked.connect(self._duplicate)
        self.btn_ren = QPushButton(S["qt_rename"]); self.btn_ren.clicked.connect(self._rename)
        self.btn_del = QPushButton(S["pm_delete"]); self.btn_del.clicked.connect(self._delete)
        self.btn_del.setStyleSheet(f"color: {P['accent_error']};")
        dflt = QPushButton(S["pm_default"]); dflt.clicked.connect(self._default)
        for b in (self.btn_open, self.btn_dup, self.btn_ren, self.btn_del):
            acts.addWidget(b)
        acts.addStretch(1); acts.addWidget(dflt)
        v.addLayout(acts)
        self._fill()

    # ── cards ──
    def _fill(self, select_name: str | None = None) -> None:
        holder = QWidget()
        grid = QGridLayout(holder); grid.setSpacing(16); grid.setContentsMargins(0, 0, 0, 0)
        self.cards: list[_Card] = []
        last = select_name or self.settings.last_profile_name
        names = list_profiles()
        for i, name in enumerate(names):
            p = load_profile(name)
            if p is None:
                continue
            card = _Card(self, p, name == self.settings.last_profile_name)
            grid.addWidget(card, i // 3, i % 3)
            self.cards.append(card)
        grid.setRowStretch(len(names) // 3 + 1, 1)
        self.area.setWidget(holder)
        self._selected = None
        target = next((c for c in self.cards if c.profile.name == last), None)
        if target is None and self.cards:
            target = self.cards[0]
        if target is not None:
            self.select(target)
        self._update_buttons()

    def select(self, card: _Card) -> None:
        if self._selected is not None:
            self._selected.set_selected(False)
        self._selected = card
        card.set_selected(True)
        self._update_buttons()

    def _update_buttons(self) -> None:
        has = self._selected is not None
        for b in (self.btn_open, self.btn_dup, self.btn_ren, self.btn_del):
            b.setEnabled(has)

    # ── actions ──
    def open_selected(self) -> None:
        if self._selected is not None:
            self.chosen = self._selected.profile
            self.accept()

    def _ask_name(self, prompt: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, self.S["qt_studies_title"], prompt, text=default)
        name = name.strip()
        if not ok or not name:
            return None
        if name in list_profiles():
            QMessageBox.information(self, self.S["qt_studies_title"], self.S["qt_name_taken"])
            return None
        return name

    def _new(self) -> None:
        name = self._ask_name(self.S["pm_new_name_prompt"])
        if name:
            p = ProfileData(name=name)
            save_profile(p)
            self._fill(select_name=name)

    def _duplicate(self) -> None:
        src = self._selected.profile
        name = self._ask_name(self.S["pm_dup_name_prompt"], f"{src.name} 2")
        if name:
            duplicate_profile(src, name)
            self._fill(select_name=name)

    def _rename(self) -> None:
        p = self._selected.profile
        old = p.name
        name = self._ask_name(self.S["qt_rename_prompt"], old)
        if name:
            rename_profile(p, name)
            if self.settings.last_profile_name == old:
                self.settings.last_profile_name = name
                self.settings.save()
            self._fill(select_name=name)

    def _delete(self) -> None:
        p = self._selected.profile
        if QMessageBox.question(self, self.S["qt_studies_title"],
                                self.S["pm_confirm_delete"].format(name=p.name)) != QMessageBox.Yes:
            return
        delete_profile(p.name)
        self._fill()

    def _default(self) -> None:
        self.chosen = load_profile(DEFAULT_PROFILE_NAME) or ensure_default_profile()
        self.accept()


def pick_study(parent, P, S, settings) -> ProfileData | None:
    dlg = StudyPicker(parent, P, S, settings)
    return dlg.chosen if dlg.exec() == QDialog.Accepted else None
