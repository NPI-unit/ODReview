"""Qt main window (Phase 1): toolbar A/C, sidebar, image view, status bar,
remark box; open a folder, step through images, edit cells, undo, autosave,
Cellpose for one image. State and workflows are the AppController's — this
module only wires widgets to it. Dialog-heavy features (study settings,
batch, export, packages, statistics) follow in Phases 2–4 and say so.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from .. import __version__
from ..cellpose_runner import ModelUnavailable, has_cache
from ..controller import AppController, format_status, status_counts
from ..i18n import get_strings
from ..persistence import (
    AppSettings, TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE, TOOLS_ORDER,
)
from ..profile import ensure_default_profile, load_profile, save_profile
from .settings import SettingsDialog
from .study_picker import pick_study
from ..study import study_progress
from ..theme import palette
from .canvas import QtEryCanvas
from .notebox import QtNoteBox
from .sidebar import QtSidebar
from .study_card import StudyCard
from .style import build_qss
from .tasks import run_task_modal
from .toolbar import QtToolbar


class _ActivityFilter(QObject):
    """Counts any mouse or key input as 'the user is working' (time tracking)."""

    def __init__(self, ctl: AppController):
        super().__init__()
        self.ctl = ctl

    def eventFilter(self, _obj, ev):
        if ev.type() in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.ctl.mark_activity()
        return False


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None):
        super().__init__()
        self.settings = settings or AppSettings.load()
        self.S = get_strings(self.settings.language)
        self.P = palette(self.settings.theme)
        profile = None
        if self.settings.last_profile_name:
            profile = load_profile(self.settings.last_profile_name)
        self._pick_at_start = profile is None
        if profile is None:
            profile = ensure_default_profile()   # replaced by the picker once shown
        if profile.type_by_key(profile.active_type_key) is None:
            profile.active_type_key = profile.default_type_key()
        self.ctl = AppController(self.settings, profile)
        self.ctl.activate_profile(profile)

        self.setWindowTitle(self.S["title_with_profile"].format(profile=profile.name))
        self.resize(max(1100, self.settings.window_w), max(700, self.settings.window_h))
        self._build()
        self._build_menu()
        self._bind_keys()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self.ctl.tick)
        self._tick.start(int(self.ctl.TIME_TICK_S * 1000))
        self._profile_save = QTimer(self)
        self._profile_save.setSingleShot(True)
        self._profile_save.timeout.connect(lambda: save_profile(self.ctl.profile))

        self._set_tool(self.settings.active_tool)
        if self._pick_at_start:
            QTimer.singleShot(50, self.switch_study)
        elif profile.import_folder and Path(profile.import_folder).is_dir():
            QTimer.singleShot(50, lambda: self.open_folder(Path(profile.import_folder), True))

    # ── layout ──
    def _build(self) -> None:
        S, P = self.S, self.P
        central = QWidget(); self.setCentralWidget(central)
        v = QVBoxLayout(central); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        self.toolbar = QtToolbar(P, S, self.settings, self.ctl.profile, {
            "tool": self._set_tool, "type": self._on_type, "resegment": self.resegment,
            "overlay": self.toggle_overlay, "reset_zoom": lambda: self.canvas.reset_zoom(),
            "undo": self.undo, "info": self._later, "export": self._later,
            "study": self._show_study_card,
        })
        v.addWidget(self.toolbar)

        split = QSplitter(Qt.Horizontal)
        self.sidebar = QtSidebar(P, S, self.ctl.profile, on_select=self.show_image)
        self.sidebar.setMinimumWidth(220)
        center = QWidget()
        cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0)
        self.canvas = QtEryCanvas(self.settings, self.ctl.profile, self._save_review,
                                  status_callback=self._set_status,
                                  note_callback=self._on_cell_note,
                                  on_overlay_autoshown=self._on_overlay_autoshown,
                                  on_edit_overlay_hidden=lambda: self._set_status(
                                      S["overlay_hidden_edit"]))
        cl.addWidget(self.canvas)
        self.canvas.set_overlay_visible(self.settings.overlay_visible)
        self.notebox = QtNoteBox(self.canvas, S, self._on_note_saved, self._on_note_closed)
        self.empty_btn = QPushButton(S["btn_open_folder"], center)
        self.empty_btn.setProperty("class", "primary")
        self.empty_btn.setMinimumSize(240, 52)
        self.empty_btn.clicked.connect(self.ask_open_folder)
        split.addWidget(self.sidebar); split.addWidget(center)
        split.setStretchFactor(1, 1)
        split.setSizes([self.settings.sidebar_width, 1000])
        self.splitter = split
        v.addWidget(split, 1)

        self.status_label = QLabel(S["status_ready"])
        self.saved_label = QLabel("")
        self.saved_label.setMinimumWidth(110)     # "gespeichert" must not clip
        self.tool_label = QLabel("")
        self.statusBar().addWidget(self.status_label, 1)
        self.statusBar().addPermanentWidget(self.saved_label)
        self.statusBar().addPermanentWidget(self.tool_label)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        c = self.empty_btn.parentWidget()
        self.empty_btn.move((c.width() - self.empty_btn.width()) // 2,
                            (c.height() - self.empty_btn.height()) // 2)

    def _build_menu(self) -> None:
        S = self.S
        mb = self.menuBar()
        m_file = mb.addMenu(S["menu_file"])
        self._act(m_file, S["qt_studies_title"] + " …", self.switch_study, "Ctrl+Shift+O")
        self._act(m_file, S["menu_open_folder"], self.ask_open_folder, QKeySequence.Open)
        # "Ctrl" is ⌘ on macOS; QKeySequence.Preferences is empty on some platforms.
        prefs = self._act(m_file, S["settings_title"] + " …", lambda: self.open_settings(),
                          ["Ctrl+,"])
        prefs.setMenuRole(QAction.PreferencesRole)      # macOS: app menu › Einstellungen
        self.m_recent = m_file.addMenu(S["menu_recent_folders"])
        self._fill_recent()
        self._act(m_file, S["menu_refresh"], self.refresh, ["F5", "Ctrl+R"])
        m_file.addSeparator()
        self._act(m_file, S["menu_quit"], self.close, QKeySequence.Quit)
        m_edit = mb.addMenu(S["qt_menu_edit"])
        self._act(m_edit, S["tool_undo"], self.undo, QKeySequence.Undo)
        m_view = mb.addMenu(S["qt_menu_view"])
        self._act(m_view, S["tool_overlay"], self.toggle_overlay)
        self._act(m_view, S["tool_reset_zoom"], lambda: self.canvas.reset_zoom())
        m_help = mb.addMenu(S["menu_help"])
        self._act(m_help, S["menu_help_hotkeys"],
                  lambda: QMessageBox.information(self, S["help_hotkeys_title"], S["help_hotkeys"]))
        self._act(m_help, S["menu_help_about"],
                  lambda: QMessageBox.about(self, S["menu_help_about"], f"Erys v{__version__} (Qt)"))

    def _act(self, menu, text, fn, keys=None) -> QAction:
        a = QAction(text, self)
        if keys is not None:
            if isinstance(keys, str):
                keys = [keys]           # a bare string would be read as a key list
            a.setShortcuts([QKeySequence(k) for k in keys] if isinstance(keys, list) else keys)
        a.triggered.connect(lambda _c=False: fn())
        menu.addAction(a)
        return a

    def _bind_keys(self) -> None:
        """Bare-key shortcuts. Window-wide, but a focused text field (the remark
        box) gets the key first, so typing never switches tools (K9)."""
        def key(seq, fn):
            QShortcut(QKeySequence(seq), self, activated=fn, context=Qt.WindowShortcut)
        for i, tool in enumerate(TOOLS_ORDER, start=1):
            key(str(i), lambda t=tool: self._set_tool(t))
        key("Left", lambda: self.step(-1))
        key("Right", lambda: self.step(+1))
        key("F", self.toggle_finished)
        key("O", self.toggle_overlay)
        key("Escape", lambda: self._set_tool(TOOL_CURSOR))
        key("R", self.resegment)
        key("Z", self.undo)
        key("E", self.cycle_eval)
        key("0", lambda: self.canvas.reset_zoom())
        key("Home", lambda: self.canvas.reset_zoom())

    # ── folders ──
    def ask_open_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, self.S["menu_open_folder"])
        if path:
            self.ctl.remember_folder(path)
            self._fill_recent()
            self.open_folder(Path(path), restore_index=False)

    def open_folder(self, path: Path, restore_index: bool) -> None:
        start = self.ctl.open_folder(path, restore_index)
        if start is None:
            QMessageBox.information(self, self.S["title"], self.S["err_no_images"].format(path=path))
            return
        self.empty_btn.hide()
        self.sidebar.populate(path, self.ctl.images, self.ctl.grouped)
        self.show_image(start)

    def _fill_recent(self) -> None:
        self.m_recent.clear()
        recents = list(self.ctl.profile.recent_folders or [])
        if not recents:
            a = self.m_recent.addAction(self.S["menu_recent_empty"]); a.setEnabled(False)
            return
        for p in recents:
            a = self.m_recent.addAction(Path(p).name or p)
            a.triggered.connect(lambda _c=False, p=p: self._open_recent(p))

    def _open_recent(self, p: str) -> None:
        if not Path(p).is_dir():
            self.ctl.forget_recent_folder(p)
            self._fill_recent()
            QMessageBox.information(self, self.S["title"], self.S["err_no_images"].format(path=p))
            return
        self.ctl.remember_folder(p)
        self._fill_recent()
        self.open_folder(Path(p), restore_index=True)

    def refresh(self) -> None:
        if self.ctl.folder is None:
            return
        if not self.ctl.rescan():
            QMessageBox.information(self, self.S["title"],
                                    self.S["err_no_images"].format(path=self.ctl.folder))
            return
        self.sidebar.populate(self.ctl.folder, self.ctl.images, self.ctl.grouped)
        self.show_image(self.ctl.idx)

    # ── images ──
    def show_image(self, idx: int) -> None:
        if not self.ctl.has_images or not self.notebox.request_close():
            return
        target = max(0, min(idx, len(self.ctl.images) - 1))
        path = self.ctl.images[target]
        try:
            loaded = self.ctl.load_image_at(target)
        except Exception as e:  # noqa: BLE001 — shown to the user
            QMessageBox.warning(self, self.S["title"],
                                self.S["err_load_image"].format(path=path, err=e))
            return
        self.sidebar.set_idx(self.ctl.idx)
        if loaded is None:
            return
        self.toolbar.set_param_mismatch(
            self.S["status_not_segmented"] if loaded.unsegmented
            else (self.S["status_param_mismatch"] if loaded.mismatch else None))
        self.canvas.set_image(loaded.image, loaded.masks, loaded.review)
        self.canvas.reset_zoom()
        self._update_status()
        self._update_tool_label()

    def step(self, delta: int) -> None:
        if self.ctl.has_images:
            self.show_image(self.ctl.idx + delta)

    # ── edits ──
    def _save_review(self) -> None:
        if not self.ctl.has_images:
            return
        try:
            self.ctl.save_review()
            self.saved_label.setText(self.S["status_saved"])
            QTimer.singleShot(1500, lambda: self.saved_label.setText(""))
        except Exception as e:  # noqa: BLE001
            self.saved_label.setText(f"! {e}")
        self.sidebar.refresh_group_of(self.ctl.idx)
        self._update_status()

    def undo(self) -> None:
        if self.canvas.undo():
            self._update_status()

    def toggle_finished(self) -> None:
        if self.ctl.has_images:
            self.ctl.toggle_finished()
            self._save_review()

    def cycle_eval(self) -> None:
        if not self.ctl.has_images:
            return
        msg = {"in": self.S["eval_toggled_in"], "out": self.S["eval_toggled_out"],
               "auto": self.S["eval_toggled_auto"]}[self.ctl.cycle_eval()]
        self._save_review()
        self._set_status(msg)

    def toggle_overlay(self) -> None:
        self.settings.overlay_visible = not self.settings.overlay_visible
        self.canvas.set_overlay_visible(self.settings.overlay_visible)
        self.settings.save()

    def _on_overlay_autoshown(self) -> None:
        self.settings.save()
        self._set_status(self.S["overlay_auto_shown"])

    # ── tools ──
    def _set_tool(self, key: str) -> None:
        if not self.ctl.set_active_tool(key):
            return
        self.toolbar.set_active_tool(key)
        self.canvas.set_tool_cursor(key)
        self._update_tool_label()

    def _on_type(self, type_key: str) -> None:
        self.ctl.set_active_type(type_key)
        self._profile_save.start(200)       # debounced: this is a hot path
        self._update_tool_label()

    def _update_tool_label(self) -> None:
        S, key = self.S, self.settings.active_tool
        names = {TOOL_ADD: "tool_add", TOOL_REMOVE: "tool_remove", TOOL_TYPE: "tool_type",
                 TOOL_NOTE: "tool_note", TOOL_CURSOR: "tool_cursor"}
        text = f"{S['status_tool']}: {S[names.get(key, 'tool_cursor')]}"
        if key == TOOL_TYPE:
            td = self.ctl.profile.type_by_key(self.ctl.profile.active_type_key)
            text += f"  ·  {S['status_tool_type']}: {(td.name or td.key) if td else ''}"
        self.tool_label.setText(text)

    # ── remarks ──
    def _on_cell_note(self, cid: int | None) -> None:
        if not self.ctl.has_images:
            return
        rv = self.ctl.review
        if cid is None:
            self.notebox.show_for_image(rv.note)
        else:
            self.notebox.show_for_cell(cid, rv.notes.get(str(cid), ""))
        if self.settings.active_tool == TOOL_CURSOR and self.notebox.visible:
            self._set_tool(TOOL_NOTE)

    def _on_note_saved(self, target: str | None, text: str) -> None:
        if self.ctl.has_images and self.ctl.set_note(target, text):
            if target is not None:
                self.canvas.set_cell_note_marker(int(target), bool(text))
            self._save_review()

    def _on_note_closed(self) -> None:
        if self.settings.active_tool == TOOL_NOTE:
            self._set_tool(TOOL_CURSOR)

    # ── Cellpose ──
    def resegment(self) -> None:
        if not self.ctl.has_images:
            return
        try:
            self.ctl.check_model()
        except ModelUnavailable as e:
            QMessageBox.warning(self, self.S["model_missing_title"],
                                self.S["model_missing_msg"].format(name=e.name))
            return
        path = self.ctl.current_path
        if has_cache(self.ctl.folder, path):
            if QMessageBox.question(
                    self, self.S["confirm_resegment_title"],
                    self.S["confirm_resegment_msg"].format(name=path.name)) != QMessageBox.Yes:
                return
            self.ctl.discard_for_resegment(path)
        try:
            run_task_modal(self, self.ctl.segment_task(path), self.S["cp_running"],
                           self.S["cp_running_image"].format(name=path.name))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, self.S["title"], self.S["err_cellpose"].format(err=e))
            return
        self.sidebar.refresh_group_of(self.ctl.idx)
        self.show_image(self.ctl.idx)

    # ── study card ──
    def _show_study_card(self, button) -> None:
        S = self.S
        prog = (study_progress(self.ctl.folder, self.ctl.grouped)
                if self.ctl.folder is not None and self.ctl.grouped is not None else None)
        try:
            self.ctl.check_model()
            model_ok = True
        except ModelUnavailable:
            model_ok = False
        card = StudyCard(self, self.P, S, self.ctl.profile.name,
                         str(self.ctl.folder) if self.ctl.folder else None, prog, model_ok,
                         self.ctl.profile.cellpose.model,
                         [(S["study_menu_edit"], lambda: self.open_settings("images")),
                          (S["study_menu_switch"], self.switch_study)])
        card.adjustSize()
        # Left of the button by the shadow margin, but never off-screen: with
        # the window at the screen's left edge that put the card at x < 0.
        pos = button.mapToGlobal(QPoint(-16, button.height() - 4))
        area = (button.screen() or QApplication.primaryScreen()).availableGeometry()
        pos.setX(max(area.left(), min(pos.x(), area.right() - card.width())))
        pos.setY(max(area.top(), min(pos.y(), area.bottom() - card.height())))
        card.move(pos)
        card.show()

    # ── studies & settings (Phase 2) ──
    def open_settings(self, page: str = "images") -> None:
        dlg = SettingsDialog(self, self.P, self.S, self.settings, self.ctl.profile,
                             self.ctl.folder, self.ctl.images,
                             on_study_saved=self._apply_profile_edit,
                             on_local_changed=self._on_local_setting,
                             on_switch_study=lambda: QTimer.singleShot(0, self.switch_study),
                             start_page=page)
        dlg.exec()

    def _apply_profile_edit(self, new, choice: str) -> None:
        old_name = self.ctl.profile.name
        change = self.ctl.apply_edited_profile(new)
        if new.name != old_name:
            from ..profile import delete_profile
            delete_profile(old_name)                # renamed in the settings
            self.ctl.activate_profile(new)
        self._profile_changed(reopen_folder=False)
        if change.margin_changed and self.canvas.image is not None:
            self.canvas.set_margin_n(new.margin_n)
        if new.import_folder and str(self.ctl.folder or "") != new.import_folder \
                and Path(new.import_folder).is_dir():
            self.open_folder(Path(new.import_folder), restore_index=False)
        if choice == "batch":
            self._later()

    def _on_local_setting(self, field: str) -> None:
        if field == "toolbar_layout":
            self.toolbar.rebuild()
            self.toolbar.set_active_tool(self.settings.active_tool)
            if self.ctl.has_images:
                self.show_image(self.ctl.idx)       # restores the parameter banner
        elif field == "cellpose_models" and self.ctl.has_images:
            self.show_image(self.ctl.idx)

    def switch_study(self) -> None:
        chosen = pick_study(self, self.P, self.S, self.settings)
        if chosen is None:
            return
        if chosen.type_by_key(chosen.active_type_key) is None:
            chosen.active_type_key = chosen.default_type_key()
        self.ctl.flush_time()
        self.ctl.activate_profile(chosen)
        self._profile_changed(reopen_folder=True)

    def _profile_changed(self, reopen_folder: bool) -> None:
        """Push the (new or edited) study to every widget."""
        p = self.ctl.profile
        self.setWindowTitle(self.S["title_with_profile"].format(profile=p.name))
        self.canvas.set_profile(p)
        self.toolbar.set_profile(p)
        self.sidebar.set_profile(p)
        if reopen_folder:
            self.ctl.folder, self.ctl.images, self.ctl.grouped = None, [], None
            self.canvas.clear()
            self.sidebar.populate(None, [], None)
            self.empty_btn.show()
            self._fill_recent()
            if p.import_folder and Path(p.import_folder).is_dir():
                self.open_folder(Path(p.import_folder), restore_index=True)
            else:
                self._update_status()
            return
        if self.ctl.has_images:
            self.sidebar.populate(self.ctl.folder, self.ctl.images, self.ctl.grouped)
            self.show_image(self.ctl.idx)

    # ── status / misc ──
    def _update_status(self) -> None:
        if not self.ctl.has_images:
            self._set_status(self.S["status_ready"])
            return
        c = status_counts(self.canvas.cell_infos, self.ctl.review, self.ctl.profile,
                          self.canvas.border_ids, self.canvas.duplicate_ids)
        self._set_status(format_status(self.S, self.ctl.idx, len(self.ctl.images),
                                       self.ctl.current_path.name, c, self.ctl.review.is_finished))

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _later(self) -> None:
        QMessageBox.information(self, self.S["title"], self.S["qt_later"])

    def closeEvent(self, e) -> None:
        if not self.notebox.request_close():
            e.ignore()
            return
        self.settings.window_w, self.settings.window_h = self.width(), self.height()
        sizes = self.splitter.sizes()
        if sizes and sizes[0] > 50:
            self.settings.sidebar_width = int(sizes[0])
        self._profile_save.stop()
        self.ctl.close()
        e.accept()


def unhide_qt_plugins() -> int:
    """Clear the macOS 'hidden' file flag on PySide6's Qt plugins.

    macOS (seen on 27) flags everything inside dot-folders such as `.venv` as
    hidden. Python does not care, but Qt's plugin loader skips hidden files —
    so it finds no platform plugin ("Could not find the Qt platform plugin
    'cocoa' in ''") and aborts before a window exists. Only our own plugin
    files are touched, and only the flag. Returns how many were changed."""
    if sys.platform != "darwin":
        return 0
    import os
    import stat
    import PySide6
    root = Path(PySide6.__file__).parent / "Qt" / "plugins"
    n = 0
    for f in root.rglob("*"):
        try:
            st = os.lstat(f)
            if st.st_flags & stat.UF_HIDDEN:
                os.chflags(f, st.st_flags & ~stat.UF_HIDDEN, follow_symlinks=False)
                n += 1
        except OSError:
            pass
    return n


def make_app(argv=None) -> QApplication:
    unhide_qt_plugins()             # must run before the QApplication exists
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setStyle("Fusion")           # the same drawing on macOS and (later) Windows
    settings = AppSettings.load()
    f = QFont(app.font())
    f.setPointSizeF(f.pointSizeF() * float(settings.font_scale or 1.0))
    app.setFont(f)
    app.setStyleSheet(build_qss(palette(settings.theme)))
    return app


def main() -> int:
    app = make_app()
    win = MainWindow()
    app._activity = _ActivityFilter(win.ctl)            # keep a reference
    app.installEventFilter(app._activity)
    win.show()
    return app.exec()
