"""Main Tk application — orchestrator.

Layout is composed of three components that own their own UI + state:
    * Toolbar (`toolbar.py`) — tool palette, action buttons, type combo
    * Sidebar (`sidebar.py`) — hierarchical Treeview, filter, DetailPanel
    * EryCanvas (`canvas.py`) — matplotlib canvas with click handlers

EryApp wires them together via callbacks and holds the canonical app-level
state: folder, images, idx, profile, settings, current ReviewState.
"""

from __future__ import annotations

import dataclasses
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog


# Menu accelerator labels are display-only (the real key bindings wire both
# Command and Control — see _bind_hotkeys). Show the right one per platform:
# "Cmd" on macOS, "Ctrl" on Windows/Linux where "Cmd" is meaningless.
_ACCEL_MOD = "Cmd" if sys.platform == "darwin" else "Ctrl"

from . import __version__, tkfixes
from .canvas import EryCanvas
from .cellpose_runner import ModelUnavailable, has_cache
from .dialogs import (
    BusyDialog, CellposeSpinner, ask_overview_destination, batch_dialog,
    confirm_dialog, info_dialog, profile_editor_dialog,
    export_scope_dialog, model_registry_dialog,
    profile_manager_dialog,
    run_task_pumping, settings_dialog,
)
from .extras import (
    areas_window, dashboard_window, eval_select_dialog, package_create_dialog,
    package_import_dialog, stats_window,
)
from .controller import AppController, status_counts
from .study import study_progress
from .notebox import NoteBox
from .i18n import get_strings
from .persistence import (
    AppSettings,
    TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE, TOOLS_ORDER,
)
from .profile import ProfileData, ensure_default_profile, load_profile, save_profile
from .sidebar import Sidebar
from .theme import font, palette, set_font_scale
from .toolbar import Toolbar


def _state(name: str) -> property:
    """EryApp attribute that lives on the controller (see controller.py)."""
    return property(lambda self: getattr(self.ctl, name),
                    lambda self, value: setattr(self.ctl, name, value))


class EryApp:
    # Canonical state is the controller's; these keep the window code readable.
    profile = _state("profile")
    folder = _state("folder")
    images = _state("images")
    grouped = _state("grouped")
    idx = _state("idx")
    folder_state = _state("folder_state")
    review = _state("review")

    def __init__(self):
        # Print to stdout so the launcher console shows progress while heavy
        # imports (matplotlib, skimage) finish loading. Useful on Windows
        # where the first import takes 10-20 s and the user otherwise sees
        # nothing but a blinking cursor.
        import sys
        print("ErysReview: loading settings ...", flush=True)
        self.settings = AppSettings.load()
        # State and workflows; the profile is replaced once the user picked one.
        self.ctl = AppController(self.settings, ProfileData())
        # Must run before ANY widget exists — every font spec in the UI goes
        # through theme.font(), which reads this once (WCAG 1.4.4).
        set_font_scale(self.settings.font_scale)
        self.S = get_strings(self.settings.language)
        self.P = palette(self.settings.theme)

        # ── Create the real root FIRST and use it as the parent for the
        # profile-manager dialog. The earlier approach (transient bootstrap +
        # withdraw) renders an invisible window on Windows — the dialog
        # then has no taskbar entry and may stay behind other windows.
        # Windows: claim a distinct taskbar identity BEFORE the window exists,
        # else the taskbar shows the generic python.exe icon and groups the app
        # under "Python". No-op on macOS/Linux.
        self._set_app_user_model_id()
        print("ErysReview: opening window ...", flush=True)
        self.root = tk.Tk()
        self.root.title("ErysReview")
        # Respect the previous geometry but clamp so an off-screen value
        # from a removed second monitor can't hide the window completely.
        self.root.geometry(self._safe_geometry(
            self.settings.window_w, self.settings.window_h,
        ))
        # 980, not 900: measured, the toolbar needs 968 px to show every button
        # plus the longest parameter banner without truncating anything. A cut-off
        # warning is worse than a slightly larger minimum window.
        self.root.minsize(980, 600)
        self.root.configure(bg=self.P["bg"])
        self._apply_window_icon()

        # Show a "Loading ..." placeholder so the user sees a window
        # immediately while we ask for the profile.
        loading = tk.Label(
            self.root, text="ErysReview\n\nLoading …",
            bg=self.P["bg"], fg=self.P["fg_secondary"],
            font=font(16, "bold"), justify="center",
        )
        loading.place(relx=0.5, rely=0.5, anchor="center")
        self.root.update()

        print("ErysReview: picking profile ...", flush=True)
        self.profile: ProfileData = self._initial_profile_load()
        loading.destroy()
        self.root.title(self.S["title_with_profile"].format(profile=self.profile.name))

        # Debounce bookkeeping for profile.save() calls on hot paths.
        self._profile_save_after_id: str | None = None
        self._profile_save_debounce_ms = 200

        if self.profile.type_by_key(self.profile.active_type_key) is None:
            self.profile.active_type_key = self.profile.default_type_key()

        self._build_menu()
        self._build_layout()
        self._bind_hotkeys()
        self._refresh_tool_indicator()
        self._start_time_tracking()
        # Deferred: never let a network call sit between the user and the window.
        # Inaktiv, solange das Repo privat ist — siehe _build_menu().
        # self.root.after(3000, self._maybe_check_updates)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.profile.import_folder:
            p = Path(self.profile.import_folder)
            if p.is_dir():
                # Seed recents with the auto-loaded folder so it shows up in the menu
                self.ctl.push_recent_folder(str(p))
                self._refresh_recent_menu()
                self.root.after(100, lambda: self._open_folder(p, restore_index=True))

    # ─────────────────────────────────────────
    # Startup helpers
    # ─────────────────────────────────────────
    @staticmethod
    def _set_app_user_model_id() -> None:
        """Windows-only: set an explicit AppUserModelID so the taskbar uses our
        own icon + name instead of python.exe. Must run before the root window
        is created. Silently does nothing elsewhere or on failure."""
        import sys
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Erys.Review")
        except Exception:
            pass  # taskbar identity is cosmetic — never block startup

    def _apply_window_icon(self) -> None:
        """Set the window icon from assets/. Falls back silently if files are missing.
        Tk picks the best-sized PNG via iconphoto. On Windows we additionally try
        iconbitmap with the .ico for taskbar fidelity."""
        try:
            import sys
            assets = Path(__file__).resolve().parent.parent / "assets"
            png_paths = [assets / "icon_64.png", assets / "icon_256.png", assets / "icon.png"]
            images = []
            for p in png_paths:
                if p.exists():
                    try:
                        images.append(tk.PhotoImage(file=str(p)))
                    except tk.TclError:
                        pass
            if images:
                self.root.iconphoto(True, *images)
                self._icon_refs = images  # keep references so Tk doesn't GC them
            if sys.platform.startswith("win"):
                ico = assets / "icon.ico"
                if ico.exists():
                    try:
                        self.root.iconbitmap(default=str(ico))
                    except tk.TclError:
                        pass
        except Exception:
            pass  # icon is cosmetic — never crash the app over it

    def _initial_profile_load(self) -> ProfileData:
        """Resolve which profile to use at startup. Runs the Profile Manager
        dialog if no last-used profile name is on disk."""
        chosen: ProfileData | None = None
        from .profile import migrate_default_name
        migrate_default_name(self.settings)
        if self.settings.last_profile_name:
            chosen = load_profile(self.settings.last_profile_name)
        if chosen is None:
            chosen = profile_manager_dialog(self.root, self.S)
        if chosen is None:
            chosen = ensure_default_profile()
        self.settings.last_profile_name = chosen.name
        self.settings.save()
        return chosen

    def _safe_geometry(self, w: int, h: int) -> str:
        """Clamp geometry so a previously-saved off-screen position can't
        hide the window on a different monitor layout. Returns just the
        size — Tk centers it on the active screen."""
        try:
            sw = self.root.winfo_screenwidth() if hasattr(self, "root") else 1600
            sh = self.root.winfo_screenheight() if hasattr(self, "root") else 1000
        except tk.TclError:
            sw, sh = 1600, 1000
        # The saved size was measured on `screen_w x screen_h`. Restoring those
        # pixels verbatim on a different display is what makes the window look
        # tiny on a big external monitor: a 1512x982 laptop-sized window on a
        # 2560-wide screen leaves a third of the desktop empty, every session.
        # Carry the *proportion* across instead, then clamp as before.
        ow, oh = self.settings.screen_w, self.settings.screen_h
        if ow > 0 and oh > 0 and (ow, oh) != (sw, sh):
            w = int(w * sw / ow)
            h = int(h * sh / oh)
        elif not ow and sw >= w * 1.3:
            # Settings written before screen_w existed: the reference display is
            # unknown, but a window barely two-thirds of the screen was never a
            # deliberate choice on a monitor this size. Open near-full once; the
            # proportion is recorded on close and honoured from then on.
            w, h = int(sw * 0.92), int(sh * 0.92)
        w = max(900, min(int(w), sw - 40))
        h = max(600, min(int(h), sh - 40))
        return f"{w}x{h}"

    # ─────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────
    def _build_menu(self) -> None:
        S = self.S
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label=S["menu_open_folder"], command=self._on_open_folder, accelerator=f"{_ACCEL_MOD}+O")
        # Recent folders submenu (rebuilt on demand from profile.recent_folders)
        self.m_recent = tk.Menu(m_file, tearoff=0)
        m_file.add_cascade(label=S["menu_recent_folders"], menu=self.m_recent)
        self._refresh_recent_menu()
        m_file.add_command(label=S["menu_refresh"], command=self._on_refresh, accelerator="F5")
        m_file.add_separator()
        m_file.add_command(label=S["menu_batch"], command=self._on_batch)
        m_file.add_separator()
        m_file.add_command(label=S["menu_package_create"], command=self._on_package_create)
        m_file.add_command(label=S["menu_package_import"], command=self._on_package_import)
        m_file.add_separator()
        m_file.add_command(label=S["menu_export"], command=self._on_export, accelerator=f"{_ACCEL_MOD}+S")
        m_file.add_separator()
        m_file.add_command(label=S["menu_quit"], command=self._on_close, accelerator=f"{_ACCEL_MOD}+Q")
        menubar.add_cascade(label=S["menu_file"], menu=m_file)

        m_prof = tk.Menu(menubar, tearoff=0)
        m_prof.add_command(label=S["menu_profile_manage"], command=self._on_profile_manage)
        m_prof.add_command(label=S["menu_profile_edit"], command=self._on_profile_edit)
        menubar.add_cascade(label=S["menu_profile"], menu=m_prof)

        m_analysis = tk.Menu(menubar, tearoff=0)
        m_analysis.add_command(label=S["menu_stats"], command=self._on_stats)
        m_analysis.add_command(label=S["menu_dashboard"], command=self._on_dashboard)
        m_analysis.add_separator()
        m_analysis.add_command(label=S["menu_overview_xlsx"],
                               command=self._on_overview_xlsx)
        m_analysis.add_command(label=S["menu_areas"], command=self._on_areas)
        m_analysis.add_command(label=S["menu_training_export"],
                               command=self._on_training_export)
        menubar.add_cascade(label=S["menu_analysis"], menu=m_analysis)
        self.m_analysis = m_analysis
        self._stats_menu_index = 0  # stats(0), dashboard(1), sep(2), overview(3)
        self._apply_stats_menu_state()

        m_settings = tk.Menu(menubar, tearoff=0)
        m_settings.add_command(label=S["menu_settings_open"], command=self._on_settings)
        m_settings.add_command(label=S["menu_models"], command=self._on_model_registry)
        menubar.add_cascade(label=S["menu_settings"], menu=m_settings)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label=S["menu_help_hotkeys"], command=self._on_help_hotkeys)
        # ── Update-Prüfung: fertig gebaut, bewusst INAKTIV ──────────────
        # Sie fragt ein ÖFFENTLICHES Release-Repo ab. Solange der Quellcode
        # privat ist, antwortet GitHub unangemeldet mit 404, die Prüfung
        # meldete also immer nur "keine Information".
        # AKTIVIEREN (drei Handgriffe):
        #   1. Repo öffentlich schalten und ein Release taggen (v17.6.0);
        #      soll der Quellcode privat bleiben: separates öffentliches
        #      Release-Repo anlegen und in updater.RELEASE_REPO eintragen
        #   2. die nächste Zeile einkommentieren
        #   3. AppSettings.update_check_enabled auf True setzen (dann
        #      prüft die App zusätzlich einmal täglich still beim Start)
        # m_help.add_command(label=S["menu_check_updates"], command=self._on_check_updates)
        m_help.add_command(label=S["menu_help_about"], command=self._on_help_about)
        menubar.add_cascade(label=S["menu_help"], menu=m_help)

        tkfixes.defer_menu_commands(menubar)
        self.root.config(menu=menubar)

    def _build_layout(self) -> None:
        P = self.P

        # ─── Toolbar (component) ───
        self.toolbar = Toolbar(
            self.root, P, self.S, self.settings, self.profile,
            on_tool_change=self._set_active_tool,
            on_type_change=self._on_type_changed,
            on_resegment=self._on_resegment,
            on_toggle_overlay=self._on_toggle_overlay,
            on_reset_zoom=lambda: self.canvas.reset_zoom(),
            on_undo=self._on_undo,
            on_info=self._show_current_image_info,
            on_export=self._on_export,
            on_study=self._on_study_menu,
        )
        self.toolbar.pack(fill="x", side="top")
        tk.Frame(self.root, bg=P["border"], height=1).pack(fill="x", side="top")

        # ─── Status bar (bottom) ───
        # Same reason as the toolbar: a pinned height clips its own labels once
        # the text size grows (settings offer 125 % and 150 %). Let it size to
        # content; the padx/pady on the labels keep it from looking cramped.
        self.status = tk.Frame(self.root, bg=P["status_bg"])
        self.status.pack(fill="x", side="bottom")
        self.status_label = tk.Label(
            self.status, text=self.S["status_ready"], anchor="w",
            fg=P["fg_muted"], bg=P["status_bg"], padx=10,
            font=font(10),
        )
        self.status_label.pack(side="left", fill="both", expand=True)
        self.tool_label = tk.Label(
            self.status, text="", fg=P["accent_primary"], bg=P["status_bg"],
            padx=10, font=font(10, "bold"),
        )
        self.tool_label.pack(side="right")
        self.saved_label = tk.Label(
            self.status, text="", fg=P["accent_positive"], bg=P["status_bg"], padx=10,
            font=font(10),
        )
        self.saved_label.pack(side="right")

        # ─── Paned: sidebar | center ───
        self.paned = tk.PanedWindow(
            self.root, orient="horizontal", sashwidth=5,
            sashrelief="raised", bg=P["sash"], bd=0,
        )
        self.paned.pack(fill="both", expand=True)

        # ─── Sidebar (component) ───
        self.sidebar = Sidebar(
            self.paned, P, self.S, self.profile,
            on_open_folder=self._on_open_folder,
            on_image_select=self._show_image_at,
            on_subject_select=self._show_subject_info,
            on_subject_settings=self._on_eval_select,
            on_open_changed=self._schedule_profile_save,
            on_details_collapsed_changed=lambda _b: self._schedule_profile_save(),
            on_edit_image_note=self._on_image_note,
            on_subject_status=self._on_subject_status,
        )
        self.paned.add(self.sidebar, minsize=200, width=self.settings.sidebar_width)
        # The width YOU chose. The sash may sit further right than this when the
        # canvas cannot use the space (see _autofit_sash); that surplus is never
        # written back, or the sidebar would ratchet wider on every session.
        self._sidebar_floor = int(self.settings.sidebar_width)
        self._sash_set_by_autofit: int | None = None
        self.paned.bind("<Configure>", lambda _e: self._schedule_autofit(), add="+")
        self.paned.bind("<ButtonRelease-1>", lambda _e: self._note_manual_sash(), add="+")

        # ─── Center: canvas + empty state ───
        center = tk.Frame(self.paned, bg=P["bg"])
        self.canvas = EryCanvas(
            center, self.settings, self.profile,
            save_callback=self._save_review,
            status_callback=self._set_status_text,
            note_callback=self._on_cell_note,
            on_overlay_autoshown=self._on_overlay_autoshown,
            on_edit_overlay_hidden=lambda: self._set_status_text(
                self.S["overlay_hidden_edit"]),
        )
        self.canvas.pack(fill="both", expand=True)
        # Placed over the canvas, not packed: it floats, and `center` is the
        # frame whose coordinates the stored geometry refers to.
        self.notebox = NoteBox(center, self.settings, self.S, self._on_note_saved,
                               confirm=lambda t, m: confirm_dialog(
                                   self.root, t, m,
                                   yes_text=self.S["confirm_yes"],
                                   no_text=self.S["confirm_no"]),
                               on_closed=self._on_note_closed)
        self.canvas.set_overlay_visible(self.settings.overlay_visible)

        self.empty_btn = tk.Button(
            center, text=self.S["btn_open_folder"],
            font=font(14), padx=24, pady=14,
            command=self._on_open_folder,
        )
        self._show_empty_state(True)

        self.paned.add(center, minsize=400)

    def _bind_hotkeys(self) -> None:
        for i, tool in enumerate(TOOLS_ORDER, start=1):
            self.root.bind(str(i), lambda e, t=tool: self._set_active_tool(t))
        self.root.bind("<Left>",  lambda e: self._step_image(-1))
        self.root.bind("<Right>", lambda e: self._step_image(+1))
        self.root.bind("<Key-f>", lambda e: self._on_toggle_finished())
        self.root.bind("<Key-o>", lambda e: self._on_toggle_overlay())
        # Escape always lands in the neutral mode — the way out of any tool.
        self.root.bind("<Escape>", lambda e: self._set_active_tool(TOOL_CURSOR))
        self.root.bind("<Key-r>", lambda e: self._on_resegment())
        self.root.bind("<Key-z>", lambda e: self._on_undo())
        self.root.bind("<Key-q>", lambda e: self._on_close())
        self.root.bind("<Key-s>", lambda e: self._on_export())
        # Modifier bindings — wire both Cmd (macOS) and Ctrl (Windows/Linux)
        # so the app behaves identically across platforms.
        for mod in ("Command", "Control"):
            self.root.bind(f"<{mod}-s>", lambda e: self._on_export())
            self.root.bind(f"<{mod}-o>", lambda e: self._on_open_folder())
            self.root.bind(f"<{mod}-q>", lambda e: self._on_close())
        self.root.bind("<Home>", lambda e: self.canvas.reset_zoom())
        self.root.bind("<Key-0>", lambda e: self.canvas.reset_zoom())
        self.root.bind("<Key-e>", lambda e: self._on_toggle_eval())
        self.root.bind("<F5>", lambda e: self._on_refresh())
        for mod in ("Command", "Control"):
            self.root.bind(f"<{mod}-r>", lambda e: self._on_refresh())

    # ─────────────────────────────────────────
    # Time tracking
    # ─────────────────────────────────────────
    # Time per image: the controller counts, the window only feeds it input
    # events and a clock tick.
    def _start_time_tracking(self) -> None:
        for seq in ("<Motion>", "<Button>", "<Key>", "<MouseWheel>"):
            self.root.bind(seq, lambda _e: self.ctl.mark_activity(), add="+")
        self.root.after(int(self.ctl.TIME_TICK_S * 1000), self._time_tick)

    def _time_tick(self) -> None:
        self.ctl.tick()
        try:
            self.root.after(int(self.ctl.TIME_TICK_S * 1000), self._time_tick)
        except tk.TclError:
            pass

    def _flush_time(self) -> None:
        self.ctl.flush_time()

    # ─────────────────────────────────────────
    # Tool / type plumbing
    # ─────────────────────────────────────────
    def _set_active_tool(self, key: str) -> None:
        if not self.ctl.set_active_tool(key):
            return
        self.toolbar.set_active_tool(key)
        self._refresh_tool_indicator()
        cursors = {TOOL_ADD: "crosshair", TOOL_REMOVE: "X_cursor", TOOL_TYPE: "hand2"}
        try:
            self.canvas.canvas.get_tk_widget().configure(cursor=cursors.get(key, "arrow"))
        except tk.TclError:
            pass

    def _on_type_changed(self, type_key: str) -> None:
        """Called from Toolbar combobox when user picks a different type."""
        self.ctl.set_active_type(type_key)
        self._schedule_profile_save()
        self._refresh_tool_indicator()

    def _schedule_profile_save(self) -> None:
        if self._profile_save_after_id is not None:
            try:
                self.root.after_cancel(self._profile_save_after_id)
            except tk.TclError:
                pass
        self._profile_save_after_id = self.root.after(
            self._profile_save_debounce_ms, self._flush_profile_save,
        )

    def _flush_profile_save(self) -> None:
        self._profile_save_after_id = None
        try:
            save_profile(self.profile)
        except Exception:
            pass

    def _refresh_tool_indicator(self) -> None:
        tool_key = self.settings.active_tool
        tool_label = {
            TOOL_ADD: self.S["tool_add"],
            TOOL_REMOVE: self.S["tool_remove"],
            TOOL_TYPE: self.S["tool_type"],
        }.get(tool_key, tool_key)
        text = f"{self.S['status_tool']}: {tool_label}"
        if tool_key == TOOL_TYPE:
            td = self.profile.type_by_key(self.profile.active_type_key)
            disp = (td.name or td.key) if td else self.profile.active_type_key
            text += f"  ·  {self.S['status_tool_type']}: {disp}"
        try:
            self.tool_label.config(text=text)
        except (AttributeError, tk.TclError):
            pass

    # ─────────────────────────────────────────
    # Folder + image lifecycle
    # ─────────────────────────────────────────
    def _on_open_folder(self) -> None:
        path = filedialog.askdirectory(title=self.S["menu_open_folder"])
        if path:
            self.ctl.remember_folder(str(path))
            self._refresh_recent_menu()
            self._open_folder(Path(path), restore_index=False)

    # ─────────────────────────────────────────
    # Recent folders
    # ─────────────────────────────────────────
    def _refresh_recent_menu(self) -> None:
        try:
            self.m_recent.delete(0, "end")
        except (tk.TclError, AttributeError):
            return
        recents = list(self.profile.recent_folders or [])
        if not recents:
            self.m_recent.add_command(label=self.S["menu_recent_empty"], state="disabled")
            return
        for path_str in recents:
            label = Path(path_str).name or path_str
            self.m_recent.add_command(
                label=label,
                command=lambda p=path_str: self._open_recent(p),
            )
        tkfixes.defer_menu_commands(self.m_recent)

    def _open_recent(self, path_str: str) -> None:
        p = Path(path_str)
        if not p.is_dir():
            # Stale entry — remove and refresh
            self.ctl.forget_recent_folder(path_str)
            self._refresh_recent_menu()
            info_dialog(self.root, self.S["title"],
                        self.S["err_no_images"].format(path=p))
            return
        self.ctl.remember_folder(path_str)
        self._refresh_recent_menu()
        self._open_folder(p, restore_index=True)

    def _open_folder(self, path: Path, restore_index: bool) -> None:
        start = self.ctl.open_folder(path, restore_index)
        if start is None:
            info_dialog(self.root, self.S["title"], self.S["err_no_images"].format(path=path))
            return
        self._show_empty_state(False)
        self.sidebar.populate(path, self.images, self.grouped)
        self._show_image_at(start)

    def _autofit_sash(self) -> None:
        """Hand the sidebar the width the image provably cannot use.

        The canvas draws with aspect='equal', so an image of ratio `a` occupies
        at most `height * a` pixels of width — beyond that the extra is grey and
        nothing can be done with it *by the image*. Measured on a 2560-wide
        window: canvas 2123x833, image capped at 1333x833, 37 % of the pane grey.
        That surplus goes to the sidebar instead.

        Only ever gives, never takes: `_sidebar_floor` is the width the user
        dragged to, and the sash is never moved left of it. Narrowing it
        automatically would maximise the image at the cost of a choice the user
        made deliberately.
        """
        if self.canvas.image is None:
            return
        try:
            pane_w = self.paned.winfo_width()
            view_h = self.canvas.canvas.get_tk_widget().winfo_height()
            cur = self.paned.sash_coord(0)[0]
        except (tk.TclError, IndexError, TypeError):
            return
        if pane_w < 100 or view_h < 100:
            return                              # not laid out yet
        h, w = self.canvas.image.shape[:2]
        want = desired_sash(pane_w, view_h, w, h, self._sidebar_floor)
        # A wide deadband, not a few pixels. The sash position is derived from
        # the canvas HEIGHT, so any vertical jitter moves it sideways: in macOS
        # fullscreen, pushing the pointer to the top edge unrolls the menu bar,
        # the window loses ~25 px of height, and at ratio 1.6 that walks the
        # sash ~40 px — the image visibly narrows whenever the mouse leaves
        # upwards, and widens again on the way back. 48 px swallows that while
        # leaving real transitions (a fullscreen toggle moves it by hundreds)
        # untouched. sash_place also re-fires <Configure>, and reacting to a
        # one-pixel difference is how the toolbar oscillated once before.
        if abs(cur - want) <= SASH_DEADBAND:
            return
        try:
            self.paned.sash_place(0, want, 1)
            self._sash_set_by_autofit = want
        except tk.TclError:
            pass

    def _schedule_autofit(self) -> None:
        if getattr(self, "_autofit_after_id", None) is not None:
            self.root.after_cancel(self._autofit_after_id)
        self._autofit_after_id = self.root.after(120, self._autofit_sash)

    def _note_manual_sash(self) -> None:
        """A sash the user let go of somewhere we did not put it is their choice."""
        try:
            cur = self.paned.sash_coord(0)[0]
        except (tk.TclError, IndexError, TypeError):
            return
        if self._sash_set_by_autofit is None or abs(cur - self._sash_set_by_autofit) > 6:
            self._sidebar_floor = int(cur)

    def _show_image_at(self, idx: int) -> None:
        if not self.ctl.has_images:
            return
        # Close the remark editor: it belongs to the image being left, so it
        # must not survive into the next one still showing that text. Nothing is
        # written until Speichern, so an unsaved remark asks before it is lost —
        # and a "no" cancels the navigation rather than discarding it anyway.
        if hasattr(self, "notebox") and not self.notebox.request_close():
            return
        target = max(0, min(idx, len(self.images) - 1))
        path = self.images[target]
        try:
            loaded = self.ctl.load_image_at(target)
        except Exception as e:
            info_dialog(self.root, self.S["title"],
                        self.S["err_load_image"].format(path=path, err=e))
            return
        self.sidebar.set_idx(self.idx)
        if loaded is None:
            return
        if loaded.unsegmented:
            self.toolbar.set_param_mismatch(self.S["status_not_segmented"])
        else:
            self.toolbar.set_param_mismatch(
                self.S["status_param_mismatch"] if loaded.mismatch else None)
        self.canvas.set_image(loaded.image, loaded.masks, loaded.review)
        self.canvas.reset_zoom()
        self._update_status()
        self.sidebar.refresh_statuses()
        # Only the group we moved into needs recomputing; navigating changes no
        # other group's numbers. Every path that DOES change them (re-segment,
        # batch, eval toggle/select, export) calls invalidate_group_cache()
        # itself before landing here.
        self.sidebar.refresh_current_group_counter()
        self._refresh_tool_indicator()
        if self.sidebar.details.mode == "image":
            self._show_current_image_info()
        else:
            self._refresh_details()

    def _step_image(self, delta: int) -> None:
        if self.images:
            self._show_image_at(self.idx + delta)

    # ─────────────────────────────────────────
    # DetailPanel routes
    # ─────────────────────────────────────────
    def _show_current_image_info(self) -> None:
        if self.folder is None or not self.images:
            return
        self.sidebar.details.show_image(
            self.folder, self.images[self.idx], self.profile, review=self.review,
        )

    def _show_subject_info(self, tp_key: str, sub_key: str) -> None:
        if self.folder is None or self.grouped is None:
            return
        self.sidebar.details.show_subject(
            self.folder, tp_key, sub_key, self.profile, self.grouped,
        )

    def _refresh_details(self) -> None:
        try:
            self.sidebar.details.refresh(self.folder, self.profile, self.grouped)
        except Exception:
            pass

    # ─────────────────────────────────────────
    # Handlers
    # ─────────────────────────────────────────
    def _on_cell_note(self, cid: int | None) -> None:
        """Open the remark editor. `cid` None (empty space) targets the image.

        Reached from two gestures on purpose: the `4 Anmerken` tool, which is
        discoverable, and right-click, which is faster once you know it. Both
        land in the same floating box rather than a modal, so the specimen
        stays visible while the remark is written.
        """
        if self.folder is None or not self.images:
            return
        if cid is None:
            self.notebox.show_for_image(self.review.note)
        else:
            self.notebox.show_for_cell(cid, self.review.notes.get(str(cid), ""))
        # Opening a remark from the neutral cursor arms the annotate tool, so
        # the round trip closes itself: edit or pick a neighbouring cell right
        # away, and because closing always hands this tool back to the cursor,
        # the ✕ returns you to where you started.
        if self.settings.active_tool == TOOL_CURSOR and self.notebox.visible:
            self._set_active_tool(TOOL_NOTE)


    def _on_image_note(self) -> None:
        """Remark for the whole current image (detail panel, sidebar menu)."""
        self._on_cell_note(None)

    def _on_note_closed(self) -> None:
        """The remark editor closed — hand the annotate tool back to the cursor.

        On close, not on open: while the box is up the tool stays armed, so the
        box can be dragged, a neighbouring cell can be picked, and the remark
        finished without the mode changing underfoot. Every way out counts —
        saved, deleted or discarded — because one rule is easier to predict than
        a mode that sometimes persists.
        """
        if self.settings.active_tool == TOOL_NOTE:
            self._set_active_tool(TOOL_CURSOR)

    def _on_note_saved(self, target: str | None, text: str) -> None:
        """The box flushed its content. Empty text deletes the remark."""
        if not self.ctl.has_images:
            return
        if not self.ctl.set_note(target, text):
            return              # debounce fired without a change — do not churn
        if target is not None:
            self.canvas.set_cell_note_marker(int(target), bool(text))
        self._save_review()

    def _on_subject_status(self, tp_key: str, sub_key: str, done: bool) -> None:
        """Set the finished flag for every segmented image of one subject."""
        if self.folder is None or self.grouped is None:
            return
        n = self.ctl.set_subject_status(tp_key, sub_key, done)
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_statuses()
        self._update_status()
        self._refresh_details()
        self._set_status_text(self.S["status_bulk_done"].format(n=n))

    def _on_undo(self) -> None:
        self.canvas.undo()
        self._update_status()

    def _on_toggle_overlay(self) -> None:
        self.settings.overlay_visible = not self.settings.overlay_visible
        self.canvas.set_overlay_visible(self.settings.overlay_visible)
        self.settings.save()

    def _on_overlay_autoshown(self) -> None:
        """The canvas revealed a hidden overlay so an edit would be visible.

        Persist it exactly like an `o` press: the state that caused this was a
        stored `overlay_visible: false`, so leaving the setting alone would
        rebuild the same trap on the next start. The status line explains the
        sudden change — an overlay that appears without a reason is its own
        small confusion.
        """
        self.settings.save()
        self._set_status_text(self.S["overlay_auto_shown"])

    def _on_toggle_finished(self) -> None:
        if not self.ctl.has_images:
            return
        self.ctl.toggle_finished()
        self._save_review()
        self.sidebar.refresh_statuses()
        self._update_status()

    def _on_resegment(self) -> None:
        """Cellpose button / r. Segments an unsegmented image on demand (no
        confirm), or re-runs an already-segmented one (with confirm, since that
        discards the review)."""
        if self.folder is None or not self.images:
            return
        # BEFORE the confirm + discard below: a re-segment deletes the existing
        # mask and review first, so an unresolvable model discovered afterwards
        # would destroy work and put nothing back.
        if not self._model_available():
            return
        path = self.images[self.idx]
        if has_cache(self.folder, path):
            if not confirm_dialog(
                self.root, self.S["confirm_resegment_title"],
                self.S["confirm_resegment_msg"].format(name=path.name),
                yes_text=self.S["confirm_yes"], no_text=self.S["confirm_no"],
            ):
                return
            self.ctl.discard_for_resegment(path)
        try:
            # Inference runs on a worker thread while the spinner pumps the
            # event loop, so the window stays responsive instead of freezing.
            with CellposeSpinner(self.root, path.name, self.S) as spin:
                run_task_pumping(spin.top, self.ctl.segment_task(path))
        except ModelUnavailable as e:
            self._report_missing_model(e.name)
            return
        except Exception as e:
            info_dialog(self.root, self.S["title"],
                        self.S["err_cellpose"].format(err=e))
            return
        # Reload uniformly from the fresh cache (masks + duplicates + status).
        self.sidebar.invalidate_group_cache()
        self._show_image_at(self.idx)

    def _model_available(self) -> bool:
        """True if this machine can supply the profile's model; otherwise show
        the explanation and return False."""
        try:
            self.ctl.check_model()
            return True
        except ModelUnavailable as e:
            self._report_missing_model(e.name)
            return False

    def _report_missing_model(self, name: str) -> None:
        """The profile asks for a model this machine has no file for.

        Deliberately a dead stop rather than a fallback: segmenting with cpsam
        instead would put two models' counts into one study, and nothing in the
        exported numbers would show it.
        """
        if confirm_dialog(
            self.root, self.S["model_missing_title"],
            self.S["model_missing_msg"].format(name=name),
            yes_text=self.S["model_missing_open"], no_text=self.S["confirm_no"],
        ):
            self._on_model_registry()

    def _on_batch(self) -> None:
        if self.folder is None or not self.images:
            return
        if not self._model_available():
            return
        batch_dialog(
            parent=self.root, folder=self.folder, images=self.images,
            profile=self.profile, strings=self.S,
            image_status_fn=self.sidebar._image_status,
            on_done=self._after_batch,
            grouped=self.grouped,
            model_registry=self.settings.cellpose_models,
        )

    def _after_batch(self) -> None:
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_statuses()
        if self.images:
            self._show_image_at(self.idx)

    # ─────────────────────────────────────────
    # Evaluation cap toggle
    # ─────────────────────────────────────────
    def _on_toggle_eval(self) -> None:
        """Cycle the current image's evaluation membership: auto → in → out → auto."""
        if self.folder is None or not self.images:
            return
        msg = {"in": self.S["eval_toggled_in"], "out": self.S["eval_toggled_out"],
               "auto": self.S["eval_toggled_auto"]}[self.ctl.cycle_eval()]
        self._save_review()
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_statuses()
        self.sidebar.refresh_current_group_counter()
        self._set_status_text(msg)

    # ─────────────────────────────────────────
    # Share packages
    # ─────────────────────────────────────────
    def _on_package_create(self) -> None:
        if self.folder is None or not self.images or self.grouped is None:
            info_dialog(self.root, self.S["pkg_create_title"], self.S["status_ready"])
            return
        self._flush_time()
        reviewer = package_create_dialog(
            self.root, self.folder, self.grouped, self.profile,
            self.settings.reviewer_name, self.S,
        )
        if reviewer is not None and reviewer != self.settings.reviewer_name:
            self.settings.reviewer_name = reviewer
            self.settings.save()

    def _on_package_import(self) -> None:
        result = package_import_dialog(self.root, self.profile, self.S)
        if result is None:
            return
        # Activate the package's profile so types/parameters match the masks.
        prof = result["profile"]
        save_profile(prof)
        self.ctl.activate_profile(prof)
        self.root.title(self.S["title_with_profile"].format(profile=prof.name))
        self.canvas.set_profile(prof)
        self.sidebar.set_profile(prof)
        self.toolbar.set_profile(prof)
        self._apply_stats_menu_state()
        target = Path(result["target"])
        if target.is_dir():
            self.ctl.remember_folder(str(target))
            self._refresh_recent_menu()
            self._open_folder(target, restore_index=False)

    # ─────────────────────────────────────────
    # Analysis
    # ─────────────────────────────────────────
    def _on_stats(self) -> None:
        if self.folder is None:
            info_dialog(self.root, self.S["stats_title"], self.S["stats_no_data"])
            return
        self._flush_time()
        stats_window(self.root, self.folder, self.profile, self.grouped, self.S)

    def _on_areas(self) -> None:
        """Cell-area outcome. On request only: it walks every evaluated image
        again, which is a cost worth paying when asked, not on every export."""
        if self.folder is None:
            info_dialog(self.root, self.S["areas_title"], self.S["stats_no_data"])
            return
        self._flush_time()
        with BusyDialog(self.root, self.S["areas_title"],
                        self.S["areas_running"], strings=self.S) as busy:
            groups = run_task_pumping(busy.top, self.ctl.areas_task())
        areas_window(self.root, groups, self.S)

    def _on_dashboard(self) -> None:
        if self.folder is None:
            info_dialog(self.root, self.S["dash_title"], self.S["stats_no_data"])
            return
        self._flush_time()
        dashboard_window(self.root, self.folder, self.profile, self.grouped, self.S)

    def _on_overview_xlsx(self) -> None:
        """Build the overview workbook from the CURRENT review state and save it
        wherever the user wants — without having to run a full export first.

        The build walks every group (the same multi-pass path as the export, see
        audit item PF-9), so it runs on a worker thread behind a busy dialog
        rather than freezing the window on a large folder.
        """
        if self.folder is None or self.grouped is None:
            info_dialog(self.root, self.S["menu_overview_xlsx"], self.S["stats_no_data"])
            return
        self._flush_time()
        dest = ask_overview_destination(self.root, self.S)
        if dest is None:
            return
        try:
            with BusyDialog(self.root, self.S["menu_overview_xlsx"],
                            self.S["overview_building"], strings=self.S) as busy:
                written = run_task_pumping(busy.top, self.ctl.overview_task(dest))
        except Exception as e:
            info_dialog(self.root, self.S["menu_overview_xlsx"],
                        self.S["overview_save_err"].format(err=e))
            return
        if written is None:
            # No group has a cached mask yet — nothing to tabulate.
            info_dialog(self.root, self.S["menu_overview_xlsx"], self.S["stats_no_data"])
            return
        info_dialog(self.root, self.S["menu_overview_xlsx"],
                    self.S["overview_save_ok"].format(path=written))

    def _apply_stats_menu_state(self) -> None:
        """Grey out the statistics entry when the profile disables it."""
        try:
            state = "normal" if self.profile.stats_enabled else "disabled"
            self.m_analysis.entryconfig(self._stats_menu_index, state=state)
        except (AttributeError, tk.TclError):
            pass

    def _on_eval_select(self, tp_key: str, sub_key: str) -> None:
        """The ⚙ next to a subject: two tabs over that group's images.

        *Auswertung* picks which images count toward the cap; *Status* marks
        which ones are finished. Both edit the same per-image review, so the
        dialog loads and saves each one once even when both tabs touched it.
        """
        if self.folder is None or not self.images or self.grouped is None:
            return
        images = self.grouped.tree.get(tp_key, {}).get(sub_key, [])
        if not images:
            return
        changed = eval_select_dialog(self.root, self.folder, images, self.profile, self.S)
        if changed:
            self.ctl.reload_review()
            self.sidebar.invalidate_group_cache()
            self.sidebar.refresh_statuses()
            self.sidebar.refresh_current_group_counter()
            self._refresh_details()

    # ─────────────────────────────────────────
    # Refresh (re-scan disk + re-group)
    # ─────────────────────────────────────────
    def _on_refresh(self) -> None:
        if self.folder is None:
            return
        if not self.ctl.rescan():
            info_dialog(self.root, self.S["title"],
                        self.S["err_no_images"].format(path=self.folder))
            return
        self.sidebar.populate(self.folder, self.images, self.grouped)
        self._show_image_at(self.idx)

    def _on_profile_manage(self) -> None:
        chosen = profile_manager_dialog(self.root, self.S)
        if chosen is None or chosen.name == self.profile.name:
            return
        self.ctl.activate_profile(chosen)
        self.root.title(self.S["title_with_profile"].format(profile=self.profile.name))
        self.canvas.set_profile(self.profile)
        self.sidebar.set_profile(self.profile)
        self.toolbar.set_profile(self.profile)
        self._apply_stats_menu_state()
        if self.profile.import_folder:
            p = Path(self.profile.import_folder)
            if p.is_dir():
                self._open_folder(p, restore_index=True)

    def _on_profile_edit(self) -> None:
        result = profile_editor_dialog(self.root, self.profile, self.S,
                                       registry=self.settings.cellpose_models)
        if result is None:
            return
        change = self.ctl.apply_edited_profile(result)
        self.canvas.set_profile(self.profile)
        if self.canvas.image is not None and change.margin_changed:
            self.canvas.set_margin_n(self.profile.margin_n)
        self.toolbar.set_profile(self.profile)
        self._apply_stats_menu_state()
        # Re-group with the (possibly changed) regex/parser and rebuild the sidebar.
        if self.folder is not None and self.images:
            self.sidebar.set_profile(self.profile)
            self.sidebar.populate(self.folder, self.images, self.grouped)
            self._show_image_at(self.idx)
        else:
            self.sidebar.set_profile(self.profile)
        self._update_status()
        # Cellpose parameters changed → existing masks are stale; offer a re-run.
        if change.cellpose_changed and self.ctl.any_masks():
            # What happens next is a per-computer default (Settings).
            choice = self.settings.resegment_default
            if choice == "batch":
                self._on_batch()
            elif choice == "ask":
                if confirm_dialog(self.root, self.S["profile_cp_changed_title"],
                                  self.S["profile_cp_changed_msg"],
                                  yes_text=self.S["confirm_yes"],
                                  no_text=self.S["confirm_no"]):
                    self._on_batch()
            else:
                # "later": the masks stay; the toolbar banner already flags
                # every image whose masks no longer match the parameters.
                self._set_status_text(self.S["status_cp_changed_later"])

    def _on_settings(self) -> None:
        new_settings, lang_changed, theme_changed, action = settings_dialog(
            self.root, self.settings, self.S, study_name=self.profile.name,
        )
        if new_settings is None:
            return
        layout_changed = new_settings.toolbar_layout != self.settings.toolbar_layout
        # In place, not a swap: canvas, toolbar and note box all hold this
        # object, and a swapped-in copy left them writing to a stale one.
        for f in dataclasses.fields(new_settings):
            setattr(self.settings, f.name, getattr(new_settings, f.name))
        self.settings.save()
        if lang_changed:
            info_dialog(self.root, self.S["language_changed_title"],
                        self.S["language_changed_msg"])
        if theme_changed:
            info_dialog(self.root, self.S["settings_theme"],
                        self.S["settings_theme_restart"])
        if layout_changed:
            self.toolbar.rebuild()
            if self.images and 0 <= self.idx < len(self.images):
                self._show_image_at(self.idx)      # restores the param banner
        self._update_status()
        self._refresh_tool_indicator()
        if action == "edit":
            self._on_profile_edit()
        elif action == "switch":
            self._on_profile_manage()

    def _on_study_menu(self, button: tk.Misc) -> None:
        """The toolbar's study button: progress per proband ID plus the ways
        to edit or switch the study. A native menu, posted under the button."""
        S = self.S
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=self.profile.name, state="disabled")
        if self.folder is not None and self.grouped is not None:
            pr = study_progress(self.folder, self.grouped)
            menu.add_command(label=S["study_menu_progress"].format(
                done=pr.done, total=pr.total), state="disabled")
            menu.add_command(label=S["study_menu_rest"].format(
                wip=pr.wip, open=pr.open), state="disabled")
        else:
            menu.add_command(label=S["study_menu_no_folder"], state="disabled")
        menu.add_separator()
        menu.add_command(label=S["study_menu_edit"], command=self._on_profile_edit)
        menu.add_command(label=S["study_menu_switch"], command=self._on_profile_manage)
        menu.add_command(label=S["study_menu_settings"], command=self._on_settings)
        # Same trap as the menu bar: a dialog opened from inside the native
        # menu's tracking loop hangs on its first dropdown (see tkfixes).
        tkfixes.defer_menu_commands(menu)
        try:
            menu.tk_popup(button.winfo_rootx(),
                          button.winfo_rooty() + button.winfo_height() + 2)
        finally:
            menu.grab_release()

    def _on_training_export(self) -> None:
        """Write (image, mask) pairs for fine-tuning Cellpose on this material."""
        if self.folder is None or self.grouped is None:
            info_dialog(self.root, self.S["menu_training_export"], self.S["stats_no_data"])
            return
        self._flush_time()
        chosen = filedialog.askdirectory(
            parent=self.root, title=self.S["training_pick_folder"])
        if not chosen:
            return
        dest = Path(chosen)
        try:
            with BusyDialog(self.root, self.S["menu_training_export"],
                            self.S["training_running"], strings=self.S) as busy:
                summary = run_task_pumping(busy.top, self.ctl.training_task(dest))
        except Exception as e:
            info_dialog(self.root, self.S["menu_training_export"],
                        self.S["overview_save_err"].format(err=e))
            return
        sk = summary["skipped"]
        info_dialog(
            self.root, self.S["menu_training_export"],
            self.S["training_done"].format(
                n=summary["n_written"], path=dest,
                n_unfinished=len(sk["not_finished"]),
                n_manual=len(sk["has_manual_cells"]),
                n_nomask=len(sk["no_mask"]),
            ))

    def _on_model_registry(self) -> None:
        """Map model names to files on this machine (Einstellungen -> Modelle)."""
        reg = model_registry_dialog(self.root, self.settings.cellpose_models, self.S)
        if reg is None:
            return
        self.settings.cellpose_models = reg
        self.settings.save()

    def _on_export(self) -> None:
        """Ask what to export, then run it off the Tk thread."""
        if self.folder is None or self.grouped is None:
            return
        self._flush_time()
        scope = export_scope_dialog(self.root, self.folder, self.grouped, self.S)
        if scope is None:
            return
        groups = scope["groups"]
        if not groups:
            info_dialog(self.root, self.S["export_scope_title"],
                        self.S["export_scope_empty"])
            return
        is_subset = len(groups) != len(self.ctl.all_groups())
        try:
            with BusyDialog(self.root, self.S["menu_export"],
                            self.S["export_running"], strings=self.S) as busy:
                out = run_task_pumping(busy.top, self.ctl.export_task(groups))
        except Exception as e:
            info_dialog(self.root, self.S["title"], f"Export error: {e}")
            return

        n_marked = self.ctl.mark_exported_finished(groups) if scope["mark_finished"] else 0

        info_dialog(self.root, self.S["export_done_title"],
                    self.S["export_done_msg"].format(path=out)
                    + self.S["export_done_extra"].format(
                        n=len(groups), marked=n_marked,
                        overview=self.S["export_no_overview"] if is_subset else ""))
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_statuses()
        self._update_status()
        self._refresh_details()

    def _on_help_hotkeys(self) -> None:
        info_dialog(self.root, self.S["help_hotkeys_title"], self.S["help_hotkeys"])

    def _on_check_updates(self, silent: bool = False) -> None:
        """Ask the public release repo whether something newer exists.

        Runs on a worker thread with the main thread polling a holder — the
        worker must not touch Tk, not even `widget.after()`, which registers a
        command on the interpreter and raises "main thread is not in main loop".
        `silent=True` is the once-a-day startup check: it reports a find but
        stays quiet about being up to date or offline, because neither is news.
        """
        from .updater import check as _check

        self.settings.last_update_check = time.time()
        self.settings.save()
        holder: dict[str, object] = {}
        threading.Thread(
            target=lambda: holder.setdefault("rel", _check(__version__) or False),
            daemon=True,
        ).start()

        def poll(n: int = 0) -> None:
            if not self.root.winfo_exists():
                return
            if "rel" not in holder:
                if n < 40:                       # ~6 s, then give up quietly
                    self.root.after(150, lambda: poll(n + 1))
                return
            rel = holder["rel"]
            if not rel:
                if not silent:
                    info_dialog(self.root, self.S["menu_check_updates"],
                                self.S["update_none"].format(version=__version__))
                return
            notes = f"\n\n{rel.notes}" if rel.notes else ""
            info_dialog(self.root, self.S["menu_check_updates"],
                        self.S["update_found"].format(
                            new=rel.version, cur=__version__, url=rel.url) + notes)

        self.root.after(150, poll)

    def _maybe_check_updates(self) -> None:
        """Startup check, at most once a day, silent on every negative outcome."""
        if not self.settings.update_check_enabled:
            return
        if time.time() - self.settings.last_update_check < 86400:
            return
        self._on_check_updates(silent=True)

    def _on_help_about(self) -> None:
        info_dialog(self.root, self.S["menu_help_about"], f"Erys v{__version__}")

    # ─────────────────────────────────────────
    # Save / status / lifecycle
    # ─────────────────────────────────────────
    def _save_review(self) -> None:
        if not self.ctl.has_images:
            return
        try:
            self.ctl.save_review()
            self.saved_label.config(text=self.S["status_saved"])
            self.root.after(1500, lambda: self.saved_label.config(text=""))
        except Exception as e:
            self.saved_label.config(text=f"! {e}", fg=self.P["accent_error"])
        # Group-local, not folder-wide: this runs on every single click, and an
        # edit can only change rows inside its own group. See
        # Sidebar.refresh_current_group() for the measurements behind that.
        self.sidebar.refresh_current_group()
        self._update_status()
        self._refresh_details()

    def _update_status(self) -> None:
        if self.folder is None or not self.images:
            self._set_status_text(self.S["status_ready"])
            return
        path = self.images[self.idx]
        c = status_counts(self.canvas.cell_infos, self.review, self.profile,
                          self.canvas.border_ids, self.canvas.duplicate_ids)
        n_active, n_changed, pct_changed = c.active, c.changed, c.pct_changed
        n_excluded, n_inactive_auto, n_total = c.excluded, c.inactive_auto, c.total

        S = self.S
        idx_str = f"[{self.idx+1}/{len(self.images)}]"
        finished = f"  ·  {S['status_finished']}" if self.review.is_finished else ""
        text = (f"{idx_str}  {path.name}  ·  "
                f"{n_active} {S['status_active']}  ·  "
                f"{n_changed} {S['status_changed']} ({pct_changed:.1f}%)  ·  "
                f"{n_excluded} {S['status_excluded']}  ·  "
                f"{n_inactive_auto} {S['status_border']}  ·  "
                f"{n_total} {S['status_total']}{finished}")
        self.status_label.config(text=text)

    def _set_status_text(self, text: str) -> None:
        self.status_label.config(text=text)

    def _show_empty_state(self, show: bool) -> None:
        if show:
            self.empty_btn.place(relx=0.5, rely=0.5, anchor="center")
        else:
            self.empty_btn.place_forget()

    def _on_close(self) -> None:
        try:
            self.settings.window_w = self.root.winfo_width()
            self.settings.window_h = self.root.winfo_height()
            self.settings.screen_w = self.root.winfo_screenwidth()
            self.settings.screen_h = self.root.winfo_screenheight()
            # The chosen width, not the current sash: the sash may be carrying
            # surplus the canvas could not use, and persisting that would make
            # the sidebar grow a little wider every session.
            if self._sidebar_floor > 50:
                self.settings.sidebar_width = int(self._sidebar_floor)
        except tk.TclError:
            pass
        if self._profile_save_after_id is not None:
            try:
                self.root.after_cancel(self._profile_save_after_id)
            except tk.TclError:
                pass
            self._profile_save_after_id = None
        self.ctl.close()        # time, settings, profile
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


SASH_DEADBAND = 48          # see _autofit_sash()


def desired_sash(pane_w: int, view_h: int, img_w: int, img_h: int,
                 floor: int, min_side: int = 200, min_canvas: int = 400) -> int:
    """Where the sash belongs so no pane holds width the image cannot use.

    With aspect='equal' an image of ratio `img_w/img_h` draws at most
    `view_h * ratio` pixels wide; anything beyond that is grey. That surplus is
    handed to the sidebar. `floor` — the width the user dragged to — is never
    undercut: giving space back is helpful, taking it away overrides a choice.

    Pure so it can be checked without a window; the ratchet it prevents (the
    sidebar keeping its surplus and growing again next session) would otherwise
    be invisible until someone noticed the sidebar had eaten the window.
    """
    usable = int(view_h * (img_w / img_h))
    want = max(floor, pane_w - usable)
    return max(min_side, min(want, pane_w - min_canvas))


def _enable_windows_dpi_awareness() -> None:
    """Windows: render the Tk UI crisply and at the correct size on scaled /
    high-DPI displays. No-op on macOS and Linux, where the OS already hands Tk
    a DPI-correct surface — so this keeps the Mac behavior exactly as-is.

    Without this, Windows bitmap-stretches the whole window on any display set
    above 100 % scaling (the default on most modern laptops), so text and the
    matplotlib canvas look blurry and oversized. We request *System* DPI
    awareness (level 1), not Per-Monitor: Tk sizes its fonts from the system
    DPI, so system awareness gives a crisp, correctly-scaled UI, whereas
    per-monitor would leave everything tiny unless we also drove Tk's scaling
    factor by hand. Must run before the Tk root is created.
    """
    if sys.platform != "win32":
        return
    import ctypes
    try:
        # PROCESS_SYSTEM_DPI_AWARE = 1  (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except (AttributeError, OSError):
        pass
    try:
        # Vista+ fallback.
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _harden_stdio_encoding() -> None:
    """Windows: make stdout/stderr tolerant of non-ASCII so logging can never
    crash the app. The launcher console uses a legacy code page (e.g. cp850 /
    cp1252); printing a character outside it raises UnicodeEncodeError, which
    on Windows can take the whole app down. Reconfiguring to UTF-8 with
    ``errors='replace'`` makes prints safe. No-op on macOS/Linux, whose stdio
    is already UTF-8 — Mac behavior is unchanged.
    """
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main() -> None:
    _enable_windows_dpi_awareness()
    _harden_stdio_encoding()
    EryApp().run()


if __name__ == "__main__":
    main()
