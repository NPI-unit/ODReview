"""Main Tk application — orchestrator.

Layout is composed of three components that own their own UI + state:
    * Toolbar (`toolbar.py`) — tool palette, action buttons, type combo
    * Sidebar (`sidebar.py`) — hierarchical Treeview, filter, DetailPanel
    * EryCanvas (`canvas.py`) — matplotlib canvas with click handlers

EryApp wires them together via callbacks and holds the canonical app-level
state: folder, images, idx, profile, settings, current ReviewState.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from . import __version__
from .canvas import EryCanvas
from .cellpose_runner import (
    has_cache, list_images, load_cached_masks, load_image,
    params_match, run_for_image, discard_cache,
)
from .dialogs import (
    CellposeSpinner, batch_dialog, confirm_dialog, info_dialog,
    profile_editor_dialog, profile_manager_dialog, settings_dialog,
)
from .export import export_folder_structured
from .grouping import group_images
from .i18n import get_strings
from .persistence import (
    AppSettings, FolderState, ReviewState,
    TOOL_ADD, TOOL_REMOVE, TOOL_TYPE, TOOLS_ORDER,
)
from .profile import ProfileData, ensure_default_profile, load_profile, save_profile
from .sidebar import Sidebar
from .theme import palette
from .toolbar import Toolbar


class EryApp:
    def __init__(self):
        self.settings = AppSettings.load()
        self.S = get_strings(self.settings.language)
        self.P = palette(self.settings.theme)

        self.profile: ProfileData = self._initial_profile_load()

        self.root = tk.Tk()
        self.root.title(self.S["title_with_profile"].format(profile=self.profile.name))
        self.root.geometry(f"{self.settings.window_w}x{self.settings.window_h}")
        self.root.minsize(900, 600)
        self.root.configure(bg=self.P["bg"])

        # App-level state (canonical)
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.grouped = None
        self.idx: int = 0
        self.folder_state: FolderState = FolderState()
        self.review: ReviewState = ReviewState()

        # Debounce bookkeeping for profile.save() calls on hot paths.
        self._profile_save_after_id: str | None = None
        self._profile_save_debounce_ms = 200

        if self.profile.type_by_key(self.profile.active_type_key) is None:
            self.profile.active_type_key = self.profile.default_type_key()

        self._build_menu()
        self._build_layout()
        self._bind_hotkeys()
        self._refresh_tool_indicator()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        if self.profile.import_folder:
            p = Path(self.profile.import_folder)
            if p.is_dir():
                # Seed recents with the auto-loaded folder so it shows up in the menu
                self._push_recent_folder(str(p))
                self._refresh_recent_menu()
                self.root.after(100, lambda: self._open_folder(p, restore_index=True))

    # ─────────────────────────────────────────
    # Startup helpers
    # ─────────────────────────────────────────
    def _initial_profile_load(self) -> ProfileData:
        bootstrap = tk.Tk()
        bootstrap.withdraw()
        try:
            chosen: ProfileData | None = None
            if self.settings.last_profile_name:
                chosen = load_profile(self.settings.last_profile_name)
            if chosen is None:
                chosen = profile_manager_dialog(bootstrap, self.S)
            if chosen is None:
                chosen = ensure_default_profile()
            self.settings.last_profile_name = chosen.name
            self.settings.save()
            return chosen
        finally:
            bootstrap.destroy()

    # ─────────────────────────────────────────
    # UI construction
    # ─────────────────────────────────────────
    def _build_menu(self) -> None:
        S = self.S
        menubar = tk.Menu(self.root)
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label=S["menu_open_folder"], command=self._on_open_folder, accelerator="Cmd+O")
        # Recent folders submenu (rebuilt on demand from profile.recent_folders)
        self.m_recent = tk.Menu(m_file, tearoff=0)
        m_file.add_cascade(label=S["menu_recent_folders"], menu=self.m_recent)
        self._refresh_recent_menu()
        m_file.add_separator()
        m_file.add_command(label=S["menu_batch"], command=self._on_batch)
        m_file.add_separator()
        m_file.add_command(label=S["menu_export"], command=self._on_export, accelerator="Cmd+S")
        m_file.add_separator()
        m_file.add_command(label=S["menu_quit"], command=self._on_close, accelerator="Cmd+Q")
        menubar.add_cascade(label=S["menu_file"], menu=m_file)

        m_prof = tk.Menu(menubar, tearoff=0)
        m_prof.add_command(label=S["menu_profile_manage"], command=self._on_profile_manage)
        m_prof.add_command(label=S["menu_profile_edit"], command=self._on_profile_edit)
        menubar.add_cascade(label=S["menu_profile"], menu=m_prof)

        m_settings = tk.Menu(menubar, tearoff=0)
        m_settings.add_command(label=S["menu_settings_open"], command=self._on_settings)
        menubar.add_cascade(label=S["menu_settings"], menu=m_settings)

        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label=S["menu_help_hotkeys"], command=self._on_help_hotkeys)
        m_help.add_command(label=S["menu_help_about"], command=self._on_help_about)
        menubar.add_cascade(label=S["menu_help"], menu=m_help)

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
        )
        self.toolbar.pack(fill="x", side="top")
        tk.Frame(self.root, bg=P["border"], height=1).pack(fill="x", side="top")

        # ─── Status bar (bottom) ───
        self.status = tk.Frame(self.root, bg=P["status_bg"], height=26)
        self.status.pack(fill="x", side="bottom")
        self.status.pack_propagate(False)
        self.status_label = tk.Label(
            self.status, text=self.S["status_ready"], anchor="w",
            fg=P["fg_muted"], bg=P["status_bg"], padx=10,
            font=("TkDefaultFont", 10),
        )
        self.status_label.pack(side="left", fill="both", expand=True)
        self.tool_label = tk.Label(
            self.status, text="", fg=P["accent_primary"], bg=P["status_bg"],
            padx=10, font=("TkDefaultFont", 10, "bold"),
        )
        self.tool_label.pack(side="right")
        self.saved_label = tk.Label(
            self.status, text="", fg=P["accent_positive"], bg=P["status_bg"], padx=10,
            font=("TkDefaultFont", 10),
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
            on_open_changed=self._schedule_profile_save,
            on_details_collapsed_changed=lambda _b: self._schedule_profile_save(),
        )
        self.paned.add(self.sidebar, minsize=200, width=self.settings.sidebar_width)

        # ─── Center: canvas + empty state ───
        center = tk.Frame(self.paned, bg=P["bg"])
        self.canvas = EryCanvas(
            center, self.settings, self.profile,
            save_callback=self._save_review,
            status_callback=self._set_status_text,
        )
        self.canvas.pack(fill="both", expand=True)
        self.canvas.set_overlay_visible(self.settings.overlay_visible)

        self.empty_btn = tk.Button(
            center, text=self.S["btn_open_folder"],
            font=("TkDefaultFont", 14), padx=24, pady=14,
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

    # ─────────────────────────────────────────
    # Tool / type plumbing
    # ─────────────────────────────────────────
    def _set_active_tool(self, key: str) -> None:
        if key not in TOOLS_ORDER:
            return
        self.settings.active_tool = key
        self.settings.save()
        self.toolbar.set_active_tool(key)
        self._refresh_tool_indicator()
        cursors = {TOOL_ADD: "crosshair", TOOL_REMOVE: "X_cursor", TOOL_TYPE: "hand2"}
        try:
            self.canvas.canvas.get_tk_widget().configure(cursor=cursors.get(key, "arrow"))
        except tk.TclError:
            pass

    def _on_type_changed(self, type_key: str) -> None:
        """Called from Toolbar combobox when user picks a different type."""
        self.profile.active_type_key = type_key
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
            self.profile.import_folder = str(path)
            self._push_recent_folder(path)
            save_profile(self.profile)
            self._refresh_recent_menu()
            self._open_folder(Path(path), restore_index=False)

    # ─────────────────────────────────────────
    # Recent folders
    # ─────────────────────────────────────────
    MAX_RECENT_FOLDERS = 5

    def _push_recent_folder(self, path: str) -> None:
        """Move `path` to the front of profile.recent_folders, dedupe, truncate."""
        recents = [p for p in self.profile.recent_folders if p != path]
        recents.insert(0, path)
        self.profile.recent_folders = recents[: self.MAX_RECENT_FOLDERS]

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

    def _open_recent(self, path_str: str) -> None:
        p = Path(path_str)
        if not p.is_dir():
            # Stale entry — remove and refresh
            self.profile.recent_folders = [
                x for x in self.profile.recent_folders if x != path_str
            ]
            save_profile(self.profile)
            self._refresh_recent_menu()
            info_dialog(self.root, self.S["title"],
                        self.S["err_no_images"].format(path=p))
            return
        self.profile.import_folder = path_str
        self._push_recent_folder(path_str)
        save_profile(self.profile)
        self._refresh_recent_menu()
        self._open_folder(p, restore_index=True)

    def _open_folder(self, path: Path, restore_index: bool) -> None:
        out_dir = path / "outputs"
        images = list_images(path, exclude={out_dir})
        if not images:
            info_dialog(self.root, self.S["title"], self.S["err_no_images"].format(path=path))
            return
        self.folder = path
        self.images = images
        self.grouped = group_images(images, self.profile)
        self.folder_state = FolderState.load(path)
        self._show_empty_state(False)
        self.sidebar.populate(path, images, self.grouped)
        start = self.folder_state.last_index if (
            restore_index and 0 <= self.folder_state.last_index < len(images)
        ) else 0
        self._show_image_at(start)

    def _show_image_at(self, idx: int) -> None:
        if not self.images or self.folder is None:
            return
        idx = max(0, min(idx, len(self.images) - 1))
        self.idx = idx
        self.folder_state.last_index = idx
        self.folder_state.save(self.folder)
        path = self.images[idx]
        self.sidebar.set_idx(idx)

        try:
            img = load_image(path)
        except Exception as e:
            info_dialog(self.root, self.S["title"],
                        self.S["err_load_image"].format(path=path, err=e))
            return

        cp_params = self.profile.cellpose
        if has_cache(self.folder, path):
            masks = load_cached_masks(self.folder, path)
            mismatch = not params_match(self.folder, path, cp_params)
        else:
            try:
                with CellposeSpinner(self.root, path.name, self.S):
                    masks = run_for_image(self.folder, path, img, cp_params,
                                          force=False, use_gpu=self.profile.use_gpu)
            except Exception as e:
                info_dialog(self.root, self.S["title"],
                            self.S["err_cellpose"].format(err=e))
                return
            mismatch = False

        if masks is None:
            return

        review = ReviewState.load(self.folder, path.stem)
        self.review = review

        self.toolbar.set_param_mismatch(
            self.S["status_param_mismatch"] if mismatch else None
        )
        self.canvas.set_image(img, masks, review)
        # Per-image duplicate detection on the freshly loaded mask
        try:
            from .duplicates import detect_duplicates
            self.canvas.duplicate_ids = detect_duplicates(
                self.canvas.cell_infos, self.profile.duplicate_detection,
            )
            self.canvas._refresh_overlay()
            self.canvas._refresh_numbers()
            self.canvas.canvas.draw_idle()
        except Exception:
            pass
        self.canvas.reset_zoom()
        self._update_status()
        self.sidebar.refresh_statuses()
        self.sidebar.invalidate_group_cache()
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
            self.folder, self.images[self.idx], self.profile,
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
    def _on_undo(self) -> None:
        self.canvas.undo()
        self._update_status()

    def _on_toggle_overlay(self) -> None:
        self.settings.overlay_visible = not self.settings.overlay_visible
        self.canvas.set_overlay_visible(self.settings.overlay_visible)
        self.settings.save()

    def _on_toggle_finished(self) -> None:
        if self.review is None:
            return
        self.review.is_finished = not self.review.is_finished
        self._save_review()
        self.sidebar.refresh_statuses()
        self._update_status()

    def _on_resegment(self) -> None:
        if self.folder is None or not self.images:
            return
        path = self.images[self.idx]
        if not confirm_dialog(
            self.root, self.S["confirm_resegment_title"],
            self.S["confirm_resegment_msg"].format(name=path.name),
            yes_text=self.S["confirm_yes"], no_text=self.S["confirm_no"],
        ):
            return
        discard_cache(self.folder, path)
        try:
            ReviewState.review_path(self.folder, path.stem).unlink()
        except FileNotFoundError:
            pass
        self.review = ReviewState()
        try:
            img = load_image(path)
            with CellposeSpinner(self.root, path.name, self.S):
                masks = run_for_image(self.folder, path, img, self.profile.cellpose,
                                      force=True, use_gpu=self.profile.use_gpu)
        except Exception as e:
            info_dialog(self.root, self.S["title"],
                        self.S["err_cellpose"].format(err=e))
            return
        self.canvas.set_image(img, masks, self.review)
        self.canvas.reset_zoom()
        self.toolbar.set_param_mismatch(None)
        self._update_status()
        self.sidebar.refresh_statuses()
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_current_group_counter()
        self._refresh_details()

    def _on_batch(self) -> None:
        if self.folder is None or not self.images:
            return
        batch_dialog(
            parent=self.root, folder=self.folder, images=self.images,
            profile=self.profile, strings=self.S,
            image_status_fn=self.sidebar._image_status,
            on_done=self._after_batch,
        )

    def _after_batch(self) -> None:
        self.sidebar.refresh_statuses()
        if self.images:
            self._show_image_at(self.idx)

    def _on_profile_manage(self) -> None:
        chosen = profile_manager_dialog(self.root, self.S)
        if chosen is None or chosen.name == self.profile.name:
            return
        self.profile = chosen
        self.settings.last_profile_name = chosen.name
        self.settings.save()
        self.root.title(self.S["title_with_profile"].format(profile=self.profile.name))
        self.canvas.set_profile(self.profile)
        self.sidebar.set_profile(self.profile)
        self.toolbar.set_profile(self.profile)
        if self.profile.import_folder:
            p = Path(self.profile.import_folder)
            if p.is_dir():
                self._open_folder(p, restore_index=True)

    def _on_profile_edit(self) -> None:
        result = profile_editor_dialog(self.root, self.profile, self.S)
        if result is None:
            return
        old_margin = self.profile.margin_n
        self.profile = result
        save_profile(self.profile)
        if self.canvas.image is not None:
            if self.profile.margin_n != old_margin:
                self.canvas.set_margin_n(self.profile.margin_n)
            self.canvas.set_profile(self.profile)
        self.sidebar.set_profile(self.profile)
        self.toolbar.set_profile(self.profile)
        self._update_status()

    def _on_settings(self) -> None:
        new_settings, lang_changed, theme_changed = settings_dialog(
            self.root, self.settings, self.S,
        )
        if new_settings is None:
            return
        self.settings = new_settings
        self.canvas.settings = new_settings
        self.toolbar.settings = new_settings  # keep toolbar's settings ref live too
        self.settings.save()
        if lang_changed:
            info_dialog(self.root, self.S["language_changed_title"],
                        self.S["language_changed_msg"])
        if theme_changed:
            info_dialog(self.root, self.S["settings_theme"],
                        self.S["settings_theme_restart"])
        self._update_status()
        self._refresh_tool_indicator()

    def _on_export(self) -> None:
        if self.folder is None:
            return
        try:
            out = export_folder_structured(self.folder, self.profile, self.grouped)
        except Exception as e:
            info_dialog(self.root, self.S["title"], f"Export error:\n{e}")
            return
        # "Export → fertig" (user spec): mark every exported image as finished.
        if self.grouped is not None:
            for info in self.grouped.flat:
                if not has_cache(self.folder, info.path):
                    continue
                review = ReviewState.load(self.folder, info.path.stem)
                if not review.is_finished:
                    review.is_finished = True
                    review.save(self.folder, info.path.stem)
            if self.images and 0 <= self.idx < len(self.images):
                self.review = ReviewState.load(self.folder, self.images[self.idx].stem)
        info_dialog(self.root, self.S["export_done_title"],
                    self.S["export_done_msg"].format(path=out))
        self.sidebar.invalidate_group_cache()
        self.sidebar.refresh_statuses()
        self._update_status()
        self._refresh_details()

    def _on_help_hotkeys(self) -> None:
        info_dialog(self.root, self.S["help_hotkeys_title"], self.S["help_hotkeys"])

    def _on_help_about(self) -> None:
        info_dialog(self.root, self.S["menu_help_about"], f"Erys v{__version__}")

    # ─────────────────────────────────────────
    # Save / status / lifecycle
    # ─────────────────────────────────────────
    def _save_review(self) -> None:
        if self.folder is None or not self.images:
            return
        try:
            self.review.save(self.folder, self.images[self.idx].stem)
            self.saved_label.config(text=self.S["status_saved"])
            self.root.after(1500, lambda: self.saved_label.config(text=""))
        except Exception as e:
            self.saved_label.config(text=f"! {e}", fg=self.P["accent_error"])
        self.sidebar.refresh_current_group_counter()
        self.sidebar.refresh_statuses()
        self._update_status()
        self._refresh_details()

    def _update_status(self) -> None:
        if self.folder is None or not self.images:
            self._set_status_text(self.S["status_ready"])
            return
        path = self.images[self.idx]
        infos = self.canvas.cell_infos
        excluded = set(self.review.force_excluded)
        included = set(self.review.force_included)
        border = self.canvas.border_ids
        dup = self.canvas.duplicate_ids
        n_total = len(infos)
        auto_inactive = lambda cid: cid in border or cid in dup
        n_active = sum(
            1 for cid in infos
            if cid not in excluded and (not auto_inactive(cid) or cid in included)
        )
        n_excluded = sum(1 for cid in infos if cid in excluded)
        n_inactive_auto = sum(
            1 for cid in infos
            if auto_inactive(cid) and cid not in included and cid not in excluded
        )
        default_key = self.profile.default_type_key()
        types_map = self.review.types
        n_changed = 0
        for cid in infos:
            if cid in excluded:
                continue
            if auto_inactive(cid) and cid not in included:
                continue
            t = types_map.get(str(cid), default_key)
            if t != "normal":
                n_changed += 1
        pct_changed = (100.0 * n_changed / n_active) if n_active > 0 else 0.0

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
            sash = self.paned.sash_coord(0)
            if sash and sash[0] > 50:
                self.settings.sidebar_width = int(sash[0])
        except tk.TclError:
            pass
        self.settings.save()
        if self._profile_save_after_id is not None:
            try:
                self.root.after_cancel(self._profile_save_after_id)
            except tk.TclError:
                pass
            self._profile_save_after_id = None
        save_profile(self.profile)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    EryApp().run()


if __name__ == "__main__":
    main()
