"""'Studie öffnen' (mockup v3, Q-Studien): one card per study with folder,
progress per proband ID and model status; actions for the selected study.
Replaces the Tk profile manager. Returns the chosen ProfileData or None.

Counting progress reads every review file of a study — slow with many
studies on a slow stick. So the window opens at once with names, folders and
the last known numbers (cached in study_progress.json), counts on a worker
thread, and fills each card in as its study is done. Opening a study never
waits for the count."""

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
    ensure_default_profile, list_profiles, load_profile, name_is_free, rename_profile, save_profile,
)
from ..persistence import app_support_dir, read_json, write_json_atomic
from ..study import StudyProgress, study_progress
from ..tasks import Task
from .tasks import start_task

CACHE_FILE = "study_progress.json"


def _cache_path() -> Path:
    return app_support_dir() / CACHE_FILE


def load_progress_cache() -> dict:
    data = read_json(_cache_path())
    return data if isinstance(data, dict) else {}


def _cached(cache: dict, p: ProfileData) -> StudyProgress | None:
    e = cache.get(p.name)
    if not isinstance(e, dict) or e.get("folder") != p.import_folder:
        return None
    try:
        return StudyProgress(total=int(e["total"]), done=int(e["done"]), wip=int(e["wip"]), open=int(e["open"]))
    except (KeyError, TypeError, ValueError):
        return None


def _progress_of(p: ProfileData):
    """(progress | None, folder_ok). Only for folders that are reachable —
    a study on an unplugged stick must not stall the dialog."""
    if not p.import_folder:
        return None, False
    folder = Path(p.import_folder)
    if not folder.is_dir():
        return None, False
    try:
        from ..persistence import main_reviews_only
        images = list_images(folder, exclude={folder / "outputs"})
        with main_reviews_only():
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
        self.folder_label = QLabel(p.import_folder or S["qt_no_folder"]); self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet(f"color: {P['fg_secondary'] if p.import_folder else P['fg_muted']};"
                                        f" font-size: 12px;")
        v.addWidget(self.folder_label)
        self.big = QLabel(""); v.addWidget(self.big)
        self.bar = QProgressBar(); self.bar.setTextVisible(False); v.addWidget(self.bar)
        self.rest = QLabel(""); self.rest.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        v.addWidget(self.rest)
        self.counted = False
        cached = _cached(picker.cache, p)
        if p.rater:
            tag = QLabel(S["ab_tag"].format(base=p.rater_base, rater=p.rater) + ("  🔒" if p.blind else ""))
            tag.setStyleSheet(f"color: {P['accent_primary']}; font-size: 12px; font-weight: 600;")
            v.addWidget(tag)
            self.big.hide(); self.bar.hide(); self.rest.hide()
            self.counted = True
        elif not p.import_folder:
            self.set_progress(None, False)
        elif cached is not None:
            self.set_progress(cached, True, pending=True)
        else:
            self.big.hide()
            self.bar.setRange(0, 0)              # busy
            self.rest.setText(S["qt_progress_counting"])
        try:
            resolve_model(p.cellpose.model, picker.registry)
            m_txt, m_col = f"{p.cellpose.model} ✓", P["state_finished"]
        except ModelUnavailable:
            m_txt, m_col = f"{p.cellpose.model} ⚠", P["accent_warn"]
        ml = QLabel(m_txt); ml.setStyleSheet(f"color: {m_col}; font-size: 12px;")
        v.addStretch(1); v.addWidget(ml)

    def set_progress(self, prog: StudyProgress | None, folder_ok: bool, pending: bool = False) -> None:
        """Show a study's progress; `pending` = the last known numbers while
        the fresh count still runs."""
        P, S, p = self.picker.P, self.picker.S, self.profile
        if not pending:
            self.counted = True
        if p.import_folder and not folder_ok:
            self.folder_label.setText(f"{S['qt_folder_missing']}: {p.import_folder}")
            self.folder_label.setStyleSheet(f"color: {P['accent_warn']}; font-size: 12px;")
        if prog is None:
            self.big.hide(); self.bar.hide()
            self.rest.setVisible(bool(p.import_folder) and folder_ok)
            self.rest.setText(S["qt_progress_unknown"])
            return
        self.big.setText(f'<span style="font-size:22px;font-weight:700;color:{P["state_finished"]};">'
                         f'{prog.done}</span>&nbsp;<span style="color:{P["fg_secondary"]};">'
                         f'/ {S["study_menu_progress"].format(done="", total=prog.total).lstrip(" /")}</span>')
        self.big.show(); self.bar.show(); self.rest.show()
        self.bar.setRange(0, max(1, prog.total)); self.bar.setValue(prog.done)
        txt = S["study_menu_rest"].format(wip=prog.wip, open=prog.open)
        self.rest.setText(txt + (f"  ·  {S['qt_progress_updating']}" if pending else ""))

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
        self.compare_with: str | None = None    # open the agreement window afterwards
        self.segment_now = False                # a new study asked to segment right away
        self._selected: _Card | None = None
        self.cache = load_progress_cache()
        self._count_task = None
        self._alive = True
        self.setWindowTitle(S["qt_studies_title"])
        self.resize(1080, 720)
        v = QVBoxLayout(self); v.setContentsMargins(40, 32, 40, 24); v.setSpacing(18)
        head = QHBoxLayout()
        col = QVBoxLayout()
        t = QLabel(S["qt_studies_title"]); t.setStyleSheet("font-size: 26px; font-weight: 700;")
        sub = QLabel(S["qt_studies_sub"]); sub.setStyleSheet(f"color: {P['fg_secondary']};")
        col.addWidget(t); col.addWidget(sub)
        head.addLayout(col, 1)
        imp = QPushButton(S["qt_import_pkg"]); imp.clicked.connect(self._import)
        ab = QPushButton(S["ab_button"]); ab.setToolTip(S["ab_sub"]); ab.clicked.connect(self._abgleich)
        head.addWidget(ab)
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
        self._count_progress()
        self._selected = None
        target = next((c for c in self.cards if c.profile.name == last), None)
        if target is None and self.cards:
            target = self.cards[0]
        if target is not None:
            self.select(target)
        self._update_buttons()

    # ── counting in the background ──
    def _count_progress(self) -> None:
        if self._count_task is not None:
            self._count_task.cancel()
        todo = [c.profile for c in self.cards if c.profile.import_folder and not c.counted
                and not c.profile.rater]
        if not todo:
            return

        def work(progress, cancelled):
            for prof in todo:
                if cancelled():
                    return None
                prog, ok = _progress_of(prof)
                progress(None, (prof.name, prof.import_folder, prog, ok))
            return None

        def on_progress(_v, payload):
            name, folder, prog, ok = payload
            if prog is not None:
                self.cache[name] = {"folder": folder, "total": prog.total, "done": prog.done,
                                    "wip": prog.wip, "open": prog.open}
            if not self._alive:
                return
            card = next((c for c in self.cards if c.profile.name == name), None)
            if card is not None:
                card.set_progress(prog, ok)

        def on_done(_r, _e):
            try:
                write_json_atomic(_cache_path(), self.cache)
            except OSError:
                pass

        # The relay hangs on the main window, not on this dialog: the count
        # may outlive the dialog and must still end (and quit its thread).
        self._count_task = start_task(self.parent() or self, Task(work=work), on_progress, on_done)

    def done(self, r) -> None:          # accept / reject / close
        self._alive = False
        if self._count_task is not None:
            self._count_task.cancel()
        super().done(r)

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
        if name in list_profiles() or not name_is_free(name):
            QMessageBox.information(self, self.S["qt_studies_title"], self.S["qt_name_taken"])
            return None
        return name

    def _new(self) -> None:
        """The 'Neue Studie' assistant; a study made there opens at once."""
        from .new_study import NewStudyWizard
        win = self.parent()
        on_import = (lambda: (self.reject(), win.package_import())) if hasattr(win, "package_import") else None
        wiz = NewStudyWizard(self, self.P, self.S, self.settings, on_import=on_import)
        if wiz.exec() == QDialog.Accepted and wiz.result_profile is not None:
            self.chosen = wiz.result_profile
            self.segment_now = wiz.segment_now
            self.accept()

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

    def _abgleich(self) -> None:
        from .abgleich import AbgleichDialog
        cur = self._selected.profile.name if self._selected else None
        dlg = AbgleichDialog(self, self.P, self.S, self.settings, current=cur)
        if dlg.exec() == QDialog.Accepted and dlg.result_profile is not None:
            self.chosen = dlg.result_profile
            self.compare_with = dlg.compare_with
            self.accept()

    def _import(self) -> None:
        """Import a package; the main window then opens the package's study."""
        win = self.parent()
        if hasattr(win, "package_import"):
            self.reject()
            win.package_import()

    def _default(self) -> None:
        self.chosen = load_profile(DEFAULT_PROFILE_NAME) or ensure_default_profile()
        self.accept()


def pick_study(parent, P, S, settings) -> tuple[ProfileData | None, bool, str | None]:
    """(chosen study or None, segment it now?, rater to compare with)."""
    dlg = StudyPicker(parent, P, S, settings)
    if dlg.exec() != QDialog.Accepted:
        return None, False, None
    return dlg.chosen, dlg.segment_now, dlg.compare_with
