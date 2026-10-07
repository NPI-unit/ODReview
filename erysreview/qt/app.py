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
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
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
from .analysis import (
    AreasWindow, DashboardWindow, PackageCreateDialog, StatsWindow, import_package_flow,
)
from .processing import BatchDialog, EvalSelectDialog, ExportDialog, ImageInfoDialog
from .settings import SettingsDialog
from .study_picker import pick_study
from ..study import study_progress
from ..theme import palette
from .canvas import QtEryCanvas
from .notebox import QtNoteBox
from .sidebar import QtSidebar
from .study_card import StudyCard
from .style import apply_look
from .tasks import run_task_modal
from .toolbar import QtToolbar


class _ActivityFilter(QObject):
    """Counts any mouse or key input as 'the user is working' (time tracking)."""

    def __init__(self, ctl: AppController, window=None):
        super().__init__()
        self.ctl = ctl
        self.window = window

    def eventFilter(self, _obj, ev):
        if ev.type() in (QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.KeyPress, QEvent.Wheel):
            self.ctl.mark_activity()
        if ev.type() == QEvent.MouseButtonPress and self.window is not None \
                and ev.button() in (Qt.BackButton, Qt.ForwardButton) \
                and QApplication.activeWindow() is self.window:
            # Mouse side buttons, if the user bound them (Rechner › Tastenkürzel).
            from .. import shortcuts as sc
            b = sc.MOUSE_BACK if ev.button() == Qt.BackButton else sc.MOUSE_FORWARD
            aid = self.window._mouse_map.get(b)
            if aid:
                self.window.run_action(aid)
                return True
        return False


class MainWindow(QMainWindow):
    def __init__(self, settings: AppSettings | None = None, controller: AppController | None = None):
        """`controller`: take over a running session (folder, image, edits)
        from a window being replaced — how language, theme and text size
        change without a restart (restyle)."""
        super().__init__()
        self.settings = settings or AppSettings.load()
        self.S = get_strings(self.settings.language)
        self.P = palette(self.settings.theme)
        self._handover = False
        self._settings_dlg = None
        self._pending_restyle = None
        if controller is not None:
            self.ctl = controller
            profile = controller.profile
            self._pick_at_start = False
        else:
            from ..profile import migrate_default_name
            migrate_default_name(self.settings)       # 'TRIBAL (Default)' → 'Standard', once
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
        if controller is None:
            QTimer.singleShot(3000, self._maybe_check_updates)
        hints = QApplication.instance().styleHints()
        hints.colorSchemeChanged.connect(self._on_system_scheme)
        if controller is not None:
            if self.ctl.has_images:
                self.empty_btn.hide()
                self.sidebar.populate(self.ctl.folder, self.ctl.images, self.ctl.grouped)
                QTimer.singleShot(0, lambda: self.show_image(self.ctl.idx))
        elif self._pick_at_start:
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
            "undo": self.undo, "info": self.show_info, "export": self.export,
            "study": self._show_study_card,
            "abgleich": self.toggle_split, "stats": lambda: self.open_interrater(self.split.rater),
        })
        v.addWidget(self.toolbar)

        split = QSplitter(Qt.Horizontal)
        self.sidebar = QtSidebar(P, S, self.ctl.profile, on_select=self.show_image,
                                 on_subject_eval=self.choose_counted,
                                 on_subject_status=self.set_subject_status,
                                 on_rerun=self.rerun,
                                 on_adjust=self.adjust_display,
                                 on_reset_display=self.reset_display)
        self.sidebar.setMinimumWidth(220)
        center = QWidget()
        cl = QVBoxLayout(center); cl.setContentsMargins(0, 0, 0, 0)
        self.canvas = QtEryCanvas(self.settings, self.ctl.profile, self._save_review,
                                  status_callback=self._set_status,
                                  note_callback=self._on_cell_note,
                                  on_overlay_autoshown=self._on_overlay_autoshown,
                                  on_edit_overlay_hidden=lambda: self._set_status(
                                      S["overlay_hidden_edit"]),
                                  context_callback=self._image_menu)
        # Comparison (Abgleich): bar on top, A | B views, decision panel right.
        from .split import SplitController
        self.views = QSplitter(Qt.Horizontal)
        self.views.addWidget(self.canvas)
        self.canvas_b = None
        self.split = SplitController(self)
        cl.addWidget(self.split.bar)
        body = QWidget(); bl = QHBoxLayout(body); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(0)
        bl.addWidget(self.views, 1); bl.addWidget(self.split.panel)
        cl.addWidget(body, 1)
        self.canvas.set_overlay_visible(self.settings.overlay_visible)
        self.canvas.grid_n = self.settings.grid_n
        self.notebox = QtNoteBox(self.canvas, S, self._on_note_saved, self._on_note_closed,
                                 settings=self.settings)
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
        self._qactions: dict[str, QAction] = {}
        mb = self.menuBar()
        m_file = mb.addMenu(S["menu_file"])
        self._act(m_file, S["qt_studies_title"] + " …", self.switch_study, "open_study")
        self._act(m_file, S["menu_open_folder"], self.ask_open_folder, "open_folder")
        prefs = self._act(m_file, S["settings_title"] + " …", lambda: self.open_settings(), "settings")
        prefs.setMenuRole(QAction.PreferencesRole)      # macOS: app menu › Einstellungen
        self.m_recent = m_file.addMenu(S["menu_recent_folders"])
        self._fill_recent()
        self._act(m_file, S["menu_refresh"], self.refresh, "refresh")
        m_file.addSeparator()
        self._act(m_file, S["batch_title"] + " …", self.open_batch, "batch")
        self._act(m_file, S["menu_export"], self.export, "export")
        m_file.addSeparator()
        self._act(m_file, S["menu_package_create"], self.package_create)
        self._act(m_file, S["menu_package_import"], self.package_import)
        self._act(m_file, S["bk_menu"], self.open_backups)
        m_file.addSeparator()
        quit_ = self._act(m_file, S["menu_quit"], self.close, "quit")
        quit_.setMenuRole(QAction.QuitRole)
        m_edit = mb.addMenu(S["qt_menu_edit"])
        self._act(m_edit, S["tool_undo"], self.undo, "undo")
        m_view = mb.addMenu(S["qt_menu_view"])
        self._act(m_view, S["tool_overlay"], self.toggle_overlay, "overlay")
        self._act(m_view, S["tool_reset_zoom"], lambda: self.canvas.reset_zoom(), "reset_zoom")
        m_view.addSeparator()
        self.m_grid = m_view.addMenu(S["qt_grid"])
        self._grid_actions = {}
        for n in self.canvas.GRID_CHOICES:
            a = QAction(S["qt_grid_off"] if n == 0 else f"{n} × {n}", self)
            a.setCheckable(True)
            a.triggered.connect(lambda _c=False, n=n: self.set_grid(n))
            self.m_grid.addAction(a)
            self._grid_actions[n] = a
        self.m_grid.addSeparator()
        self._act(self.m_grid, S["qt_grid_next"], self.cycle_grid, "grid")
        self._sync_grid_menu()
        m_an = mb.addMenu(S["menu_analysis"])
        self.act_stats = self._act(m_an, S["menu_stats"], self.open_stats)
        self._act(m_an, S["menu_dashboard"], self.open_dashboard)
        self._act(m_an, S["menu_overview_xlsx"], self.overview_xlsx)
        self._act(m_an, S["menu_areas"], self.open_areas)
        self._act(m_an, S["ir_menu"], self.open_interrater)
        m_an.addSeparator()
        self._act(m_an, S["menu_training_export"], self.training_export)
        m_help = mb.addMenu(S["menu_help"])
        self._act(m_help, S["menu_help_hotkeys"], lambda: self.open_settings("keys"))
        self._act(m_help, S["menu_check_updates"], lambda: self.check_updates(silent=False))
        self._act(m_help, S["menu_help_about"],
                  lambda: QMessageBox.about(self, S["menu_help_about"], f"Erys v{__version__} (Qt)"))

    def _act(self, menu, text, fn, action_id: str | None = None) -> QAction:
        """A menu entry. With `action_id` its shortcuts come from the shortcut
        registry (shortcuts.py) and follow the user's own bindings."""
        a = QAction(text, self)
        a.triggered.connect(lambda _c=False: fn())
        menu.addAction(a)
        if action_id:
            self._qactions[action_id] = a
        return a

    def _handlers(self) -> dict:
        return {
            "tool_add": lambda: self._set_tool(TOOL_ADD), "tool_remove": lambda: self._set_tool(TOOL_REMOVE),
            "tool_type": lambda: self._set_tool(TOOL_TYPE), "tool_note": lambda: self._set_tool(TOOL_NOTE),
            "tool_cursor": lambda: self._set_tool(TOOL_CURSOR),
            "prev_image": lambda: self.step(-1), "next_image": lambda: self.step(+1),
            "toggle_finished": self.toggle_finished, "cycle_eval": self.cycle_eval,
            "resegment": self.resegment, "info": self.show_info,
            "next_diff": lambda: self.split.step(+1), "prev_diff": lambda: self.split.step(-1),
            "take_b": lambda: self.split.take_b(), "keep_a": lambda: self.split.keep_a(),
            "split_layout": self._toggle_split_layout,
        }

    def _bind_keys(self) -> None:
        self._shortcut_objs: list[QShortcut] = []
        self._mouse_map: dict[str, str] = {}
        self._apply_shortcuts()

    def _apply_shortcuts(self) -> None:
        """(Re)bind every action from the registry plus this computer's own
        bindings. Window shortcuts yield to a focused text field (the remark
        box), so typing never switches tools (K9). Bindings that collide (a
        hand-edited settings file) fall back to the defaults, and the user is told."""
        from .. import shortcuts as sc
        mapping, reset = sc.resolve(self.settings.shortcuts)
        if reset:
            self.settings.shortcuts = sc.overrides_from(mapping)
            self.settings.save()
            QTimer.singleShot(0, lambda: self._set_status(self.S["qt_keys_reset_start"].format(
                list=", ".join(self.S[sc.BY_ID[a].label_key] for a in reset))))
        for obj in self._shortcut_objs:
            obj.setEnabled(False); obj.deleteLater()
        self._shortcut_objs = []
        self._mouse_map = {}
        handlers = self._handlers()
        for action in sc.ACTIONS:
            keys = [b for b in mapping[action.id] if b not in sc.MOUSE_BINDINGS]
            for b in mapping[action.id]:
                if b in sc.MOUSE_BINDINGS:
                    self._mouse_map[b] = action.id
            if action.id in self._qactions:
                self._qactions[action.id].setShortcuts([QKeySequence(k) for k in keys])
            elif action.id in handlers:
                for k in keys:
                    self._shortcut_objs.append(QShortcut(QKeySequence(k), self, activated=handlers[action.id],
                                                         context=Qt.WindowShortcut))
        self._mapping = mapping

    def _toggle_split_layout(self) -> None:
        if self.split.active:
            rows = self.views.orientation() == Qt.Horizontal
            self.split.set_layout("rows" if rows else "side")
            for b in self.split.layout_group.buttons():
                b.setChecked(b.text() == self.S["sp_rows" if rows else "sp_side"])

    def run_action(self, action_id: str) -> None:
        if action_id in self._qactions:
            self._qactions[action_id].trigger()
        elif action_id in self._handlers():
            self._handlers()[action_id]()

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
        self._update_abgleich_ui()

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
        self._show_cellpose_state(loaded)
        self.canvas.set_image(loaded.image, loaded.masks, loaded.review)
        self.canvas.reset_zoom()
        self._update_status()
        self._update_tool_label()
        if self.split.active:
            self.split.load_image()

    def _show_cellpose_state(self, loaded) -> None:
        """The Cellpose button carries the image's state (mockup v4): quiet,
        orange 'old values' with the reason in the tooltip, or blue 'Rechnen'."""
        S = self.S
        self._stale_now = False
        if loaded.unsegmented:
            self.toolbar.set_cellpose_state("unsegmented", S["qt_tip_unseg"])
        elif loaded.mismatch:
            from ..sidebar_model import stale_reason
            why = stale_reason(self.ctl.folder, loaded.path, self.ctl.profile.cellpose)
            names = {"model": "settings_cp_model", "diameter": "settings_cp_diameter",
                     "flow_threshold": "settings_cp_flow", "cellprob_threshold": "settings_cp_cellprob"}
            def num(v):
                if v is None:
                    return "?"
                return f"{v:g}".replace(".", S["qt_decimal_sep"]) if isinstance(v, (int, float)) else str(v)
            label = lambda f: S[names[f]].split(" (")[0]          # "Diameter (px)" -> "Diameter"
            old = ", ".join(f"{label(f)} {num(h)}" for f, h, _w in why) or "?"
            new = ", ".join(f"{num(w)}" for _f, _h, w in why) or "?"
            self.toolbar.set_cellpose_state("stale", S["qt_tip_stale"].format(old=old, new=new))
            self._stale_now = True
        else:
            self.toolbar.set_cellpose_state("ok", S["qt_tip_ok"])

    def rerun(self, paths) -> None:
        """Re-segment images whose masks were made with old values — after the
        user confirmed that their reviews are reset (new cells, new ids)."""
        if not paths or self._no_compute_in_rating():
            return
        try:
            self.ctl.check_model()
        except ModelUnavailable as e:
            QMessageBox.warning(self, self.S["model_missing_title"],
                                self.S["model_missing_msg"].format(name=e.name))
            return
        if QMessageBox.question(self, self.S["qt_rerun_title"],
                                self.S["qt_rerun_msg"].format(n=len(paths))) != QMessageBox.Yes:
            return
        self.ctl.flush_time()
        by_name = {p.name: p for p in self.ctl.images}
        dlg = BatchDialog(self, self.P, self.S, self.ctl.folder, self.ctl.images, self.ctl.grouped,
                          self.ctl.profile, self.settings.cellpose_models,
                          rerun=[by_name.get(p.name, p) for p in paths],
                          discard=lambda info: self.ctl.discard_image(info.path))
        dlg.exec()
        if dlg.ran:
            self.ctl.reload_review()
            self.sidebar.refresh_all()
            self.show_image(self.ctl.idx)

    def step(self, delta: int) -> None:
        """Next / previous image in the list's order (⊘ at the end of each
        proband, filter applied); folder order if the image is not listed."""
        if not self.ctl.has_images:
            return
        order = self.sidebar.order()
        if self.ctl.idx in order:
            pos = order.index(self.ctl.idx) + delta
            if 0 <= pos < len(order):
                self.show_image(order[pos])
            return
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
        if self.split.active:
            self.split.refresh()

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

    # ── comparison in the main window (split.py) & backups of A ──
    def ensure_canvas_b(self) -> None:
        if self.canvas_b is None:
            self.canvas_b = QtEryCanvas(self.settings, self.ctl.profile, lambda: None)
            self.canvas_b.read_only = True
            self.canvas_b.grid_n = self.settings.grid_n
            self.views.addWidget(self.canvas_b)
        self.canvas_b.set_profile(self.ctl.profile)

    def _update_abgleich_ui(self) -> None:
        avail = self.split.available()
        if not avail and self.split.active:
            self.split.deactivate()
        self.toolbar.set_abgleich(avail, self.split.active)

    def toggle_split(self, on: bool) -> None:
        if on:
            from .. import interrater as ir
            raters = ir.list_raters(self.ctl.folder) if self.ctl.folder else []
            rater = None
            if len(raters) > 1:
                from PySide6.QtGui import QCursor
                from PySide6.QtWidgets import QMenu
                m = QMenu(self); m.addSection(self.S["sp_with_whom"])
                acts = {m.addAction(r): r for r in raters}
                chosen = m.exec(QCursor.pos())
                if chosen is None:
                    self.toolbar.set_abgleich(True, False)
                    return
                rater = acts[chosen]
            if not self.split.activate(rater):
                self.toolbar.set_abgleich(self.split.available(), False)
        else:
            self.split.deactivate()
        self.toolbar.set_abgleich(self.split.available(), self.split.active)

    def backup_now(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        from .. import backups
        if self.ctl.folder is None:
            return
        name, ok = QInputDialog.getText(self, self.S["bk_title"], self.S["bk_name_prompt"],
                                        text=self.S["bk_default_name"])
        if ok and name.strip():
            self.ctl.flush_time()
            backups.create(self.ctl.folder, name.strip(), "manual")
            self._set_status(self.S["bk_saved"].format(name=name.strip()))

    def open_backups(self) -> None:
        from .backups_dialog import BackupsDialog
        if self.ctl.folder is None:
            return
        self.ctl.flush_time()
        dlg = BackupsDialog(self, self.P, self.S, self.ctl.folder)
        dlg.exec()
        if dlg.restored:
            self.ctl.reload_review()
            self.sidebar.refresh_all()
            self.show_image(self.ctl.idx)

    # ── brightness & contrast (display only, display_adjust.py) ──
    def _image_menu(self) -> None:
        """Right click (no drag) on the image: the menu for the image in view."""
        if not self.ctl.has_images:
            return
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("☀︎  " + self.S["br_menu"], lambda: self.adjust_display([self.ctl.idx]))
        reset = m.addAction(self.S["br_reset"], lambda: self.reset_display([self.ctl.idx]))
        reset.setEnabled(bool(self.ctl.review.display))
        m.exec(QCursor.pos())

    def adjust_display(self, idxs: list[int]) -> None:
        if not self.ctl.has_images or not idxs:
            return
        from .brightness import BrightnessDialog
        imgs = self.ctl.images
        sel = [imgs[i] for i in idxs]
        key = self.sidebar._group_of_idx.get(idxs[0])
        group = [imgs[i] for i in self.sidebar.group_indices(*key)] if key else sel
        label = f"{key[0]} · {self.S['pe_role_subject']} {key[1]}" if key else ""
        self.ctl.flush_time()                      # the dialog reads the files
        dlg = BrightnessDialog(self, self.P, self.S, self.ctl.folder, sel, group, label, list(imgs))
        if dlg.exec() == QDialog.Accepted:
            self._after_display(dlg.applied)

    def reset_display(self, idxs: list[int]) -> None:
        from ..persistence import ReviewState
        self.ctl.flush_time()
        done = {}
        for i in idxs:
            p = self.ctl.images[i]
            rv = ReviewState.load(self.ctl.folder, p.stem)
            if rv.display:
                rv.display = None
                rv.save(self.ctl.folder, p.stem)
            done[p] = None
        self._after_display(done)

    def _after_display(self, applied: dict) -> None:
        """Files are written; the open image's review in memory gets the same
        value (else the next save of it would undo the change), and the view
        and the ☀︎ markers follow."""
        cur = self.ctl.current_path
        if cur in applied:
            self.ctl.review.display = applied[cur]
            self.canvas.set_display(applied[cur])
        self.sidebar.refresh_all()
        self.sidebar.set_idx(self.ctl.idx)
        self._set_status(self.S["br_done"].format(n=len(applied)))

    # ── helper grid (exploratory) ──
    def set_grid(self, n: int) -> None:
        self.settings.grid_n = n
        self.settings.save()
        self.canvas.set_grid(n)
        self._sync_grid_menu()

    def cycle_grid(self) -> None:
        ch = list(self.canvas.GRID_CHOICES)
        cur = self.settings.grid_n if self.settings.grid_n in ch else 0
        self.set_grid(ch[(ch.index(cur) + 1) % len(ch)])
        self._set_status(self.S["qt_grid"] + ": " + (self.S["qt_grid_off"] if not self.settings.grid_n
                                                     else f"{self.settings.grid_n} × {self.settings.grid_n}"))

    def _sync_grid_menu(self) -> None:
        for n, a in self._grid_actions.items():
            a.setChecked(n == (self.settings.grid_n or 0))

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
    def _no_compute_in_rating(self) -> bool:
        """A second rating works on A's masks — recomputing them would end the
        comparison (identical masks only). True = refused, user told."""
        if self.ctl.profile.rater:
            QMessageBox.information(self, self.S["ab_title"], self.S["ab_no_compute"])
            return True
        return False

    def resegment(self) -> None:
        if not self.ctl.has_images or self._no_compute_in_rating():
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
        stale = self.sidebar.stale_paths()
        card = StudyCard(self, self.P, S, self.ctl.profile.name,
                         str(self.ctl.folder) if self.ctl.folder else None, prog, model_ok,
                         self.ctl.profile.cellpose.model,
                         n_stale=len(stale), on_rerun_all=lambda: self.rerun(stale),
                         actions=
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

    # ── processing (Phase 3) ──
    def open_batch(self) -> None:
        if not self.ctl.has_images or self._no_compute_in_rating():
            return
        try:
            self.ctl.check_model()
        except ModelUnavailable as e:
            QMessageBox.warning(self, self.S["model_missing_title"],
                                self.S["model_missing_msg"].format(name=e.name))
            return
        dlg = BatchDialog(self, self.P, self.S, self.ctl.folder, self.ctl.images, self.ctl.grouped,
                          self.ctl.profile, self.settings.cellpose_models)
        dlg.exec()
        if dlg.ran:
            self.sidebar.refresh_all()
            self.show_image(self.ctl.idx)

    def batch_in_background(self):
        """Segment every not yet segmented image without blocking the window
        (a new study's 'Jetzt im Hintergrund'): the batch window runs beside
        the main window, which picks up the masks when it is done."""
        try:
            self.ctl.check_model()
        except ModelUnavailable as e:
            QMessageBox.warning(self, self.S["model_missing_title"],
                                self.S["model_missing_msg"].format(name=e.name))
            return
        dlg = BatchDialog(self, self.P, self.S, self.ctl.folder, self.ctl.images, self.ctl.grouped,
                          self.ctl.profile, self.settings.cellpose_models)
        dlg._select("unseg")
        dlg.setModal(False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)

        def after(_r):
            if dlg.ran and self.ctl.has_images:
                self.sidebar.refresh_all()
                self.show_image(self.ctl.idx)
        dlg.finished.connect(after)
        dlg.show()
        dlg._start()
        self._bg_batch = dlg
        return dlg

    def export(self) -> None:
        if self.ctl.folder is None or self.ctl.grouped is None:
            return
        self.ctl.flush_time()
        dlg = ExportDialog(self, self.P, self.S, self.ctl.folder, self.ctl.grouped)
        if dlg.exec() != QDialog.Accepted:
            return
        groups = dlg.groups
        if not groups:
            QMessageBox.information(self, self.S["export_scope_title"], self.S["export_scope_empty"])
            return
        is_subset = len(groups) != len(self.ctl.all_groups())
        try:
            out = run_task_modal(self, self.ctl.export_task(groups), self.S["menu_export"],
                                 self.S["export_running"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, self.S["title"], f"Export error: {e}")
            return
        marked = self.ctl.mark_exported_finished(groups) if dlg.mark.isChecked() else 0
        QMessageBox.information(
            self, self.S["export_done_title"],
            self.S["export_done_msg"].format(path=out) + self.S["export_done_extra"].format(
                n=len(groups), marked=marked, overview=self.S["export_no_overview"] if is_subset else ""))
        self.sidebar.refresh_all()
        self.show_image(self.ctl.idx)

    def show_info(self) -> None:
        if not self.ctl.has_images:
            return
        c = status_counts(self.canvas.cell_infos, self.ctl.review, self.ctl.profile,
                          self.canvas.border_ids, self.canvas.duplicate_ids)
        group = None
        key = self.sidebar._group_of_idx.get(self.ctl.idx)
        if key is not None and self.ctl.grouped is not None:
            from ..evaluation import group_eval
            from ..grouping import UNGROUPED_TIMEPOINT
            tp, sub = key
            images = self.ctl.grouped.tree[tp][sub]
            if tp != UNGROUPED_TIMEPOINT:
                summary, stems = group_eval(self.ctl.folder, images, self.ctl.profile)
                group = {"title": f"{tp} · {self.S['pe_role_subject']} {sub}", "summary": summary,
                         "n_total": len(images), "counted": self.ctl.current_path.stem in stems}
        ImageInfoDialog(self, self.P, self.S, self.ctl.current_path.name, c,
                        self.ctl.profile, self.ctl.review, group=group).exec()

    def choose_counted(self, tp: str, sub: str) -> None:
        images = self.ctl.grouped.tree.get(tp, {}).get(sub, []) if self.ctl.grouped else []
        if not images:
            return
        dlg = EvalSelectDialog(self, self.P, self.S, self.ctl.folder, images, self.ctl.profile,
                               f"{tp} / {sub}")
        dlg.exec()
        if dlg.changed:
            self.ctl.reload_review()
            self.sidebar.refresh_all()
            self.sidebar.set_idx(self.ctl.idx)
            self._update_status()

    def set_subject_status(self, tp: str, sub: str, done: bool) -> None:
        n = self.ctl.set_subject_status(tp, sub, done)
        self.sidebar.refresh_all()
        self.sidebar.set_idx(self.ctl.idx)
        self._update_status()
        self._set_status(self.S["status_bulk_done"].format(n=n))

    # ── packages & analysis (Phase 4) ──
    def _need_folder(self, title: str) -> bool:
        if self.ctl.folder is None or self.ctl.grouped is None:
            QMessageBox.information(self, title, self.S["stats_no_data"])
            return False
        self.ctl.flush_time()
        return True

    def package_create(self) -> None:
        if not self._need_folder(self.S["pkg_create_title"]):
            return
        dlg = PackageCreateDialog(self, self.P, self.S, self.ctl.folder, self.ctl.grouped,
                                  self.ctl.profile, self.settings.reviewer_name)
        dlg.exec()
        if dlg.reviewer_used is not None and dlg.reviewer_used != self.settings.reviewer_name:
            self.settings.reviewer_name = dlg.reviewer_used
            self.settings.save()

    def package_import(self) -> bool:
        on_compare = self._compare_package if self.ctl.folder is not None and self.ctl.grouped else None
        result = import_package_flow(self, self.P, self.S, self.ctl.profile, on_compare=on_compare)
        if result is None:
            return False
        # Activate the package's study so types and parameters match its masks.
        prof = result["profile"]
        save_profile(prof)
        self.ctl.activate_profile(prof)
        target = Path(result["target"])
        if target.is_dir():
            self.ctl.remember_folder(str(target))
        self._profile_changed(reopen_folder=True)
        return True

    def _compare_package(self, zip_path) -> None:
        from .interrater import compare_package_flow
        name = compare_package_flow(self, self.P, self.S, self.ctl.folder, Path(zip_path))
        if name:
            self.open_interrater(name)

    def open_interrater(self, rater: str | None = None) -> None:
        from .. import interrater as ir
        from .interrater import InterraterWindow
        if not self._need_folder(self.S["ir_title"]):
            return
        if not ir.list_raters(self.ctl.folder):
            QMessageBox.information(self, self.S["ir_title"], self.S["ir_none"])
            return
        p = self.ctl.profile
        base = (load_profile(p.rater_base) if p.rater else None) or p
        InterraterWindow(self, self.P, self.S, self.ctl.folder, self.ctl.grouped, p,
                         me=self.settings.reviewer_name, rater=rater, base_profile=base).exec()

    def open_stats(self) -> None:
        if not self.ctl.profile.stats_enabled:
            QMessageBox.information(self, self.S["stats_title"], self.S["pe_stats_enabled"])
            return
        if self._need_folder(self.S["stats_title"]):
            StatsWindow(self, self.P, self.S, self.ctl.folder, self.ctl.profile, self.ctl.grouped).exec()

    def open_dashboard(self) -> None:
        if self._need_folder(self.S["dash_title"]):
            DashboardWindow(self, self.P, self.S, self.ctl.folder, self.ctl.profile, self.ctl.grouped).exec()

    def open_areas(self) -> None:
        if not self._need_folder(self.S["areas_title"]):
            return
        groups = run_task_modal(self, self.ctl.areas_task(), self.S["areas_title"], self.S["areas_running"])
        AreasWindow(self, self.P, self.S, groups).exec()

    def overview_xlsx(self) -> None:
        S = self.S
        if not self._need_folder(S["menu_overview_xlsx"]):
            return
        dest, _ = QFileDialog.getSaveFileName(self, S["overview_save_as"], "Uebersicht.xlsx",
                                              f"{S['overview_filetype']} (*.xlsx)")
        if not dest:
            return
        try:
            written = run_task_modal(self, self.ctl.overview_task(Path(dest)), S["menu_overview_xlsx"],
                                     S["overview_building"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, S["menu_overview_xlsx"], S["overview_save_err"].format(err=e))
            return
        QMessageBox.information(self, S["menu_overview_xlsx"],
                                S["overview_save_ok"].format(path=written) if written else S["stats_no_data"])

    def training_export(self) -> None:
        S = self.S
        if not self._need_folder(S["menu_training_export"]):
            return
        dest = QFileDialog.getExistingDirectory(self, S["training_pick_folder"])
        if not dest:
            return
        try:
            summary = run_task_modal(self, self.ctl.training_task(Path(dest)), S["menu_training_export"],
                                     S["training_running"])
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, S["menu_training_export"], S["overview_save_err"].format(err=e))
            return
        sk = summary["skipped"]
        QMessageBox.information(self, S["menu_training_export"], S["training_done"].format(
            n=summary["n_written"], path=dest, n_unfinished=len(sk["not_finished"]),
            n_manual=len(sk["has_manual_cells"]), n_nomask=len(sk["no_mask"])))

    # ── studies & settings (Phase 2) ──
    def open_settings(self, page: str = "images", work=None) -> None:
        dlg = SettingsDialog(self, self.P, self.S, self.settings, self.ctl.profile,
                             self.ctl.folder, self.ctl.images,
                             on_study_saved=self._apply_profile_edit,
                             on_local_changed=self._on_local_setting,
                             on_switch_study=lambda: QTimer.singleShot(0, self.switch_study),
                             start_page=page, work=work)
        self._settings_dlg = dlg
        dlg.exec()
        self._settings_dlg = None
        if self._pending_restyle is not None:
            page, work = self._pending_restyle
            self._pending_restyle = None
            self.restyle(reopen=(page, work))

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
            self.open_batch()

    def _on_local_setting(self, field: str) -> None:
        if field == "restart":
            self.restart()
        elif field in ("language", "theme", "font_scale"):
            dlg = self._settings_dlg
            if dlg is not None:
                # Rebuild once the dialog is closed, then reopen it on the same
                # page with any unsaved study edits carried over.
                self._pending_restyle = (dlg.current_page(), dlg.work if dlg.has_changes() else None)
                QTimer.singleShot(0, lambda: dlg.done(0))   # not from inside the click
            else:
                self.restyle()
        elif field == "toolbar_layout":
            self.toolbar.rebuild()
            self.toolbar.set_active_tool(self.settings.active_tool)
            self._update_abgleich_ui()
            if self.ctl.has_images:
                self.show_image(self.ctl.idx)       # restores the parameter banner
        elif field == "shortcuts":
            self._apply_shortcuts()
        elif field == "cellpose_models" and self.ctl.has_images:
            self.show_image(self.ctl.idx)

    def switch_study(self) -> None:
        chosen, segment_now, compare_with = pick_study(self, self.P, self.S, self.settings)
        if chosen is None:
            return
        if chosen.type_by_key(chosen.active_type_key) is None:
            chosen.active_type_key = chosen.default_type_key()
        self.ctl.flush_time()
        self.ctl.activate_profile(chosen)
        self._profile_changed(reopen_folder=True)
        if segment_now and self.ctl.has_images:
            self.batch_in_background()
        if compare_with:
            self.open_interrater(compare_with)

    def _profile_changed(self, reopen_folder: bool) -> None:
        """Push the (new or edited) study to every widget."""
        p = self.ctl.profile
        if self.split.active:
            self.split.deactivate()
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
        text = format_status(self.S, self.ctl.idx, len(self.ctl.images),
                             self.ctl.current_path.name, c, self.ctl.review.is_finished)
        if getattr(self, "_stale_now", False):
            text += "   " + self.S["qt_status_stale"]
        self._set_status(text)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def restyle(self, reopen=None) -> "MainWindow":
        """Apply language, theme and text size now: restyle the application
        and rebuild the main window around the same session (controller —
        folder, current image and review stay). Every window built
        afterwards picks up the new strings and colours. Returns the new window."""
        app = QApplication.instance()
        if getattr(app, "_restyling", False):
            return self
        app._restyling = True               # apply_look can emit colorSchemeChanged → no nested rebuild
        try:
            return self._restyle(app, reopen)
        finally:
            app._restyling = False

    def _restyle(self, app, reopen) -> "MainWindow":
        apply_look(app, self.settings)
        self.notebox.request_close()
        self.ctl.flush_time()
        new = MainWindow(self.settings, controller=self.ctl)
        new.setGeometry(self.geometry())
        new.splitter.setSizes(self.splitter.sizes())
        act = getattr(app, "_activity", None)
        if act is not None:
            act.window = new
        self._handover = True
        new.show()
        self.close()
        self.deleteLater()
        if reopen is not None:
            # A beat later: macOS must have ended the old dialog's modal
            # session before the next one starts (else "modalSession has been
            # exited prematurely").
            page, work = reopen
            QTimer.singleShot(120, lambda: new.open_settings(page, work=work))
        return new

    def _on_system_scheme(self, *_):
        from ..theme import THEME_SYSTEM, resolve_theme
        from .style import system_scheme
        if self.settings.theme != THEME_SYSTEM or self._handover \
                or getattr(QApplication.instance(), "_restyling", False):
            return
        if system_scheme() == resolve_theme(THEME_SYSTEM):
            return
        dlg = self._settings_dlg
        if dlg is not None:
            self._pending_restyle = (dlg.current_page(), dlg.work if dlg.has_changes() else None)
            dlg.done(0)
        else:
            self.restyle()

    def restart(self) -> None:
        """Start a fresh instance and close this one (saves like a normal quit).
        Language, theme and text size are read once at startup — a restart is
        what makes every window and every text colour follow them."""
        from PySide6.QtCore import QProcess
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QDialog) and w.isVisible():
                w.done(0)
        if self.close():
            QProcess.startDetached(sys.executable, ["-m", "erysreview.qt"])
            QApplication.quit()

    # ── update check (updater.py) ──
    def check_updates(self, silent: bool) -> None:
        """Ask GitHub on a worker thread whether a newer release exists.
        silent=True (daily start-up check): only a find is reported."""
        import time as _time
        from .. import updater
        from ..tasks import simple
        from .tasks import start_task
        self.settings.last_update_check = _time.time()
        self.settings.save()

        def done(result, error):
            state, rel = result if error is None and result else ("unknown", None)
            title = self.S["menu_check_updates"]
            if state == "newer":
                notes = f"\n\n{rel.notes}" if rel.notes else ""
                QMessageBox.information(self, title, self.S["update_found"].format(
                    new=rel.version, cur=__version__, url=rel.url) + notes)
            elif silent:
                return
            elif state == "current":
                QMessageBox.information(self, title, self.S["update_none"].format(version=__version__))
            else:
                QMessageBox.information(self, title, self.S["update_unknown"].format(version=__version__))
        self._update_task = start_task(self, simple(lambda: updater.check_status(__version__)),
                                       lambda *_: None, done)

    def _maybe_check_updates(self) -> None:
        """At most once a day at start-up, only if switched on (Allgemein)."""
        import time as _time
        if self.settings.update_check_enabled and _time.time() - self.settings.last_update_check > 86400:
            self.check_updates(silent=True)

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
        if self._handover:
            save_profile(self.ctl.profile)          # the session lives on in the new window
        else:
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
    apply_look(app, AppSettings.load())     # our palette, not the system's dark/light
    from .tasks import shutdown
    if not getattr(app, "_shutdown_hooked", False):
        app.aboutToQuit.connect(shutdown)
        app._shutdown_hooked = True
    return app


def main() -> int:
    app = make_app()
    win = MainWindow()
    app._activity = _ActivityFilter(win.ctl, win)       # keep a reference
    app.installEventFilter(app._activity)
    win.show()
    return app.exec()
