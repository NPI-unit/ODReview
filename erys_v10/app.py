"""Main Tk application — profile-aware.

Lifecycle:
  1. AppSettings.load() (globals only: language, window, sidebar, last_profile_name)
  2. If last_profile_name resolves to a profile, auto-load it.
     Else open the Profile Manager dialog (force-check) until a profile is chosen.
  3. Load the profile's import_folder if set; build hierarchical sidebar via grouping.
  4. UI runs.

Sidebar is a hierarchical Treeview:  Zeitpunkt > Proband > Bild.
Open groups show '<n_active>/<target> erys · <n_done>/<n_total> images done'.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

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
from .grouping import (
    GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT,
    group_images,
)
from .i18n import get_strings
from .persistence import (
    AppSettings, FolderState, ReviewState,
    TOOL_ADD, TOOL_REMOVE, TOOL_TYPE, TOOLS_ORDER,
)
from .profile import ProfileData, ensure_default_profile, load_profile, save_profile
from .theme import STATUS_ICONS_3, STATUS_TO_COLOR, palette


# Status icons collapsed to 3 visual states (Frage answer C)
STATUS_ICONS = STATUS_ICONS_3


class EryApp:
    def __init__(self):
        self.settings = AppSettings.load()
        self.S = get_strings(self.settings.language)
        self.P = palette(self.settings.theme)  # active color palette

        # Load profile (force-check)
        self.profile: ProfileData = self._initial_profile_load()

        self.root = tk.Tk()
        self.root.title(self.S["title_with_profile"].format(profile=self.profile.name))
        self.root.geometry(f"{self.settings.window_w}x{self.settings.window_h}")
        self.root.minsize(900, 600)
        self.root.configure(bg=self.P["bg"])

        # Group-counter cache: (timepoint, subject) -> n_active
        self._group_active_cache: dict[tuple[str, str], int] = {}

        # State
        self.folder: Path | None = None
        self.images: list[Path] = []          # flat ordered list (drives idx)
        self.grouped: GroupedFolder | None = None
        self.image_to_idx: dict[str, int] = {}
        self.idx: int = 0
        self.folder_state: FolderState = FolderState()
        self.review: ReviewState = ReviewState()

        if self.profile.type_by_key(self.profile.active_type_key) is None:
            self.profile.active_type_key = self.profile.default_type_key()

        self._build_menu()
        self._build_layout()
        self._bind_hotkeys()
        self._refresh_tool_indicator()  # show tool state in status bar from the start

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Auto-load profile's import folder
        if self.profile.import_folder:
            p = Path(self.profile.import_folder)
            if p.is_dir():
                self.root.after(100, lambda: self._open_folder(p, restore_index=True))

    # ─────────────────────────────────────────
    # Startup helpers
    # ─────────────────────────────────────────
    def _initial_profile_load(self) -> ProfileData:
        # Temporarily-hidden root for the dialog (Tk requires one)
        bootstrap = tk.Tk()
        bootstrap.withdraw()
        try:
            chosen: ProfileData | None = None
            if self.settings.last_profile_name:
                chosen = load_profile(self.settings.last_profile_name)
            if chosen is None:
                chosen = profile_manager_dialog(bootstrap, self.S)
            if chosen is None:
                # User cancelled — fall back to default
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
        m_file.add_command(label=S["menu_batch"], command=self._on_batch)
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
        self._build_toolbar(self.root)

        self.status = tk.Frame(self.root, bg=P["status_bg"], height=26)
        self.status.pack(fill="x", side="bottom")
        self.status.pack_propagate(False)
        self.status_label = tk.Label(
            self.status, text=self.S["status_ready"], anchor="w",
            fg=P["fg_muted"], bg=P["status_bg"], padx=10,
            font=("TkDefaultFont", 10),
        )
        self.status_label.pack(side="left", fill="both", expand=True)
        # Tool-state indicator (right of counters, before saved indicator)
        self.tool_label = tk.Label(
            self.status, text="", fg=P["accent_primary"], bg=P["status_bg"],
            padx=10, font=("TkDefaultFont", 10, "bold"),
        )
        self.tool_label.pack(side="right")
        self.saved_label = tk.Label(
            self.status, text="", fg=P["accent_secondary"], bg=P["status_bg"], padx=10,
            font=("TkDefaultFont", 10),
        )
        self.saved_label.pack(side="right")

        self.paned = tk.PanedWindow(
            self.root, orient="horizontal", sashwidth=5,
            sashrelief="raised", bg=P["sash"], bd=0,
        )
        self.paned.pack(fill="both", expand=True)

        self.sidebar = tk.Frame(self.paned, bg=P["sidebar_bg"])
        self._build_sidebar()
        self.paned.add(self.sidebar, minsize=180, width=self.settings.sidebar_width)

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

    def _build_sidebar(self) -> None:
        S = self.S
        P = self.P

        top = tk.Frame(self.sidebar, bg=P["sidebar_bg"])
        top.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(top, text=S["btn_open_folder"], command=self._on_open_folder).pack(fill="x")

        filt = tk.Frame(self.sidebar, bg=P["sidebar_bg"])
        filt.pack(fill="x", padx=8, pady=(6, 4))
        tk.Label(filt, text=S["filter_label"], bg=P["sidebar_bg"], fg=P["fg_label"],
                 font=("TkDefaultFont", 9)).pack(anchor="w")
        self.filter_var = tk.StringVar(value="all")
        for value, label_key in [
            ("all", "filter_all"),
            ("unsegmented", "filter_unsegmented"),
            ("unfinished", "filter_unfinished"),
            ("finished", "filter_finished"),
        ]:
            tk.Radiobutton(
                filt, text=S[label_key], value=value, variable=self.filter_var,
                bg=P["sidebar_bg"], fg=P["fg"], selectcolor=P["toolbar_bg"],
                activebackground=P["sidebar_bg"], activeforeground=P["fg"],
                font=("TkDefaultFont", 9),
                command=self._refresh_sidebar,
            ).pack(anchor="w")

        list_frame = tk.Frame(self.sidebar, bg=P["sidebar_bg"])
        list_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Erys.Treeview",
                        background=P["tree_bg"], foreground=P["fg"],
                        fieldbackground=P["tree_bg"], borderwidth=0, rowheight=22)
        style.map("Erys.Treeview",
                  background=[("selected", P["sel_bg"])],
                  foreground=[("selected", P["sel_fg"])])
        style.layout("Erys.Treeview", style.layout("Treeview"))

        self.tree = ttk.Treeview(list_frame, show="tree", selectmode="browse",
                                 style="Erys.Treeview")
        # Per-status tags for colored status icons (Frage answer C)
        self.tree.tag_configure("status_unsegmented", foreground=P[STATUS_TO_COLOR["unsegmented"]])
        self.tree.tag_configure("status_in_progress", foreground=P[STATUS_TO_COLOR["in_progress"]])
        self.tree.tag_configure("status_finished",    foreground=P[STATUS_TO_COLOR["finished"]])
        self.tree.tag_configure("group_header", foreground=P["fg"])
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>",  self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_close)

        self.folder_label = tk.Label(
            self.sidebar, text=S["no_folder"], bg=P["sidebar_bg"], fg=P["fg_muted"],
            font=("TkDefaultFont", 9), wraplength=250, justify="left",
            padx=8, pady=4,
        )
        self.folder_label.pack(fill="x", side="bottom")

    def _build_toolbar(self, parent: tk.Widget) -> None:
        S = self.S
        P = self.P
        bar = tk.Frame(parent, bg=P["toolbar_bg"], height=46)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        self.tool_buttons: dict[str, tk.Button] = {}
        for i, (key, label_key) in enumerate([
            (TOOL_ADD, "tool_add"),
            (TOOL_REMOVE, "tool_remove"),
            (TOOL_TYPE, "tool_type"),
        ], start=1):
            b = tk.Button(
                bar, text=f"{i}  {S[label_key]}", font=("TkDefaultFont", 11),
                padx=10, pady=4,
                command=lambda k=key: self._set_active_tool(k),
            )
            b.pack(side="left", padx=3, pady=4)
            self.tool_buttons[key] = b

        self.type_var = tk.StringVar(value=self._type_display_name(self.profile.active_type_key))
        self.type_combo = ttk.Combobox(
            bar, textvariable=self.type_var, state="readonly", width=20,
            values=self._type_display_values(),
        )
        self.type_combo.pack(side="left", padx=(6, 14), pady=4)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_combo)

        tk.Frame(bar, bg=P["border"], width=1, height=28).pack(side="left", padx=4, pady=8)

        def mkaction(text, cmd):
            b = tk.Button(bar, text=text, command=cmd,
                          font=("TkDefaultFont", 11), padx=10, pady=4)
            b.pack(side="left", padx=3, pady=4)
            return b

        # Cellpose re-run: split-button — main = single, dropdown = batch menu
        self.btn_resegment = tk.Button(
            bar, text="⟳  " + S["tool_resegment"], command=self._on_resegment,
            font=("TkDefaultFont", 11), padx=10, pady=4,
        )
        self.btn_resegment.pack(side="left", padx=(3, 0), pady=4)
        resegment_menu = tk.Menu(bar, tearoff=0)
        resegment_menu.add_command(
            label=S["tool_resegment_more"], command=self._on_batch,
        )
        self.btn_resegment_more = tk.Menubutton(
            bar, text="▾", relief="raised",
            font=("TkDefaultFont", 11), padx=4, pady=4,
            menu=resegment_menu,
        )
        self.btn_resegment_more.pack(side="left", padx=(0, 3), pady=4)

        self.btn_overlay = mkaction("⊙  " + S["tool_overlay"], self._on_toggle_overlay)
        self.btn_undo    = mkaction("↶  " + S["tool_undo"], self._on_undo)
        self.btn_reset   = mkaction("⌂  " + S["tool_reset_zoom"], self.canvas_reset_zoom if False else (lambda: self.canvas.reset_zoom()))
        self.btn_export  = mkaction("↓  " + S["tool_export"], self._on_export)

        self.param_banner = tk.Label(
            bar, text="", bg=P["toolbar_bg"], fg=P["accent_warning"],
            font=("TkDefaultFont", 10, "italic"), cursor="hand2",
        )
        self.param_banner.pack(side="right", padx=12)
        self.param_banner.bind("<Button-1>", lambda e: self._on_resegment())

        self._highlight_active_tool()

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
        self.root.bind("<Command-s>", lambda e: self._on_export())
        self.root.bind("<Command-o>", lambda e: self._on_open_folder())
        self.root.bind("<Command-q>", lambda e: self._on_close())
        self.root.bind("<Home>", lambda e: self.canvas.reset_zoom())
        self.root.bind("<Key-0>", lambda e: self.canvas.reset_zoom())

    # ─────────────────────────────────────────
    # Tool palette
    # ─────────────────────────────────────────
    def _set_active_tool(self, key: str) -> None:
        if key not in TOOLS_ORDER:
            return
        self.settings.active_tool = key
        self.settings.save()
        self._highlight_active_tool()
        cursors = {TOOL_ADD: "crosshair", TOOL_REMOVE: "X_cursor", TOOL_TYPE: "hand2"}
        try:
            self.canvas.canvas.get_tk_widget().configure(cursor=cursors.get(key, "arrow"))
        except tk.TclError:
            pass

    def _highlight_active_tool(self) -> None:
        if not hasattr(self, "_default_btn_bg"):
            any_btn = next(iter(self.tool_buttons.values()))
            self._default_btn_bg = any_btn.cget("bg")
            self._default_btn_fg = any_btn.cget("fg")
            self._default_btn_abg = any_btn.cget("activebackground")
            self._default_btn_afg = any_btn.cget("activeforeground")
        accent = self.P["accent_primary"]
        for key, btn in self.tool_buttons.items():
            if key == self.settings.active_tool:
                btn.configure(relief="sunken", bg=accent, fg="#ffffff",
                              activebackground=accent, activeforeground="#ffffff")
            else:
                btn.configure(relief="raised",
                              bg=self._default_btn_bg, fg=self._default_btn_fg,
                              activebackground=self._default_btn_abg,
                              activeforeground=self._default_btn_afg)
        self._refresh_tool_indicator()

    def _type_display_name(self, key: str) -> str:
        td = self.profile.type_by_key(key)
        return (td.name or td.key) if td else key

    def _type_display_values(self) -> list[str]:
        return [self._type_display_name(t.key) for t in self.profile.cell_types]

    def _on_type_combo(self, _evt=None) -> None:
        selected = self.type_var.get()
        for t in self.profile.cell_types:
            if self._type_display_name(t.key) == selected:
                self.profile.active_type_key = t.key
                save_profile(self.profile)
                self._refresh_tool_indicator()
                return

    def _refresh_tool_indicator(self) -> None:
        """Renders 'Tool: <name> · Typ: <type>' permanently in the status bar
        so the user always sees the current click-routing."""
        tool_key = self.settings.active_tool
        tool_label = {
            TOOL_ADD: self.S["tool_add"],
            TOOL_REMOVE: self.S["tool_remove"],
            TOOL_TYPE: self.S["tool_type"],
        }.get(tool_key, tool_key)
        text = f"{self.S['status_tool']}: {tool_label}"
        if tool_key == TOOL_TYPE:
            type_disp = self._type_display_name(self.profile.active_type_key)
            text += f"  ·  {self.S['status_tool_type']}: {type_disp}"
        try:
            self.tool_label.config(text=text)
        except (AttributeError, tk.TclError):
            pass

    def _refresh_type_combo(self) -> None:
        self.type_combo["values"] = self._type_display_values()
        if self.profile.type_by_key(self.profile.active_type_key) is None:
            self.profile.active_type_key = self.profile.default_type_key()
        self.type_var.set(self._type_display_name(self.profile.active_type_key))

    # ─────────────────────────────────────────
    # Folder + image lifecycle
    # ─────────────────────────────────────────
    def _on_open_folder(self) -> None:
        path = filedialog.askdirectory(title=self.S["menu_open_folder"])
        if path:
            self.profile.import_folder = str(path)
            save_profile(self.profile)
            self._open_folder(Path(path), restore_index=False)

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
        self.folder_label.config(text=f"{path.name}\n{len(images)} Bilder")
        self._show_empty_state(False)
        self._refresh_sidebar()
        start = self.folder_state.last_index if (restore_index and 0 <= self.folder_state.last_index < len(images)) else 0
        self._show_image_at(start)

    def _show_image_at(self, idx: int) -> None:
        if not self.images or self.folder is None:
            return
        idx = max(0, min(idx, len(self.images) - 1))
        self.idx = idx
        self.folder_state.last_index = idx
        self.folder_state.save(self.folder)
        path = self.images[idx]

        try:
            self.tree.selection_set(path.name)
            self.tree.see(path.name)
        except tk.TclError:
            pass

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

        self.param_banner.config(text=self.S["status_param_mismatch"] if mismatch else "")
        self.canvas.set_image(img, masks, review)
        # Detect duplicates for this image
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
        self._refresh_sidebar_status()
        # Image switch / fresh cellpose run → refresh the group counter
        self._invalidate_group_cache()
        self._refresh_current_group_counter()
        self._refresh_tool_indicator()

    def _step_image(self, delta: int) -> None:
        if self.images:
            self._show_image_at(self.idx + delta)

    # ─────────────────────────────────────────
    # Sidebar (hierarchical)
    # ─────────────────────────────────────────
    def _image_status(self, image_path: Path) -> str:
        """Collapsed to 3 states (Frage answer C): unsegmented / in_progress / finished."""
        if self.folder is None or not has_cache(self.folder, image_path):
            return "unsegmented"
        review = ReviewState.load(self.folder, image_path.stem)
        if review.is_finished:
            return "finished"
        # Any image with a cached mask is "in progress" — whether edited or not.
        return "in_progress"

    def _passes_filter(self, status: str) -> bool:
        f = self.filter_var.get()
        if f == "all":
            return True
        if f == "unsegmented":
            return status == "unsegmented"
        if f == "unfinished":
            return status != "finished"
        if f == "finished":
            return status == "finished"
        return True

    def _refresh_sidebar(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        if not self.images or self.grouped is None:
            return

        open_set = set(self.profile.sidebar_open_groups)

        for tp in self.grouped.timepoints():
            tp_label = self.S["sb_ungrouped"] if tp == UNGROUPED_TIMEPOINT else tp
            tp_iid = f"tp::{tp}"
            tp_inserted = False

            for sub in self.grouped.subjects_in(tp):
                sub_label = self.S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT else sub
                sub_iid = f"sub::{tp}::{sub}"
                sub_inserted = False

                for info in self.grouped.tree[tp][sub]:
                    status = self._image_status(info.path)
                    if not self._passes_filter(status):
                        continue
                    if not tp_inserted:
                        self.tree.insert("", "end", iid=tp_iid, text=self._tp_label(tp_label, tp),
                                         open=(tp_iid in open_set), tags=("group_header",))
                        tp_inserted = True
                    if not sub_inserted:
                        self.tree.insert(tp_iid, "end", iid=sub_iid,
                                         text=self._sub_label(sub_label, tp, sub),
                                         open=(sub_iid in open_set), tags=("group_header",))
                        sub_inserted = True
                    icon = STATUS_ICONS[status]
                    self.tree.insert(sub_iid, "end", iid=info.path.name,
                                     text=f"{icon}  {info.path.name}",
                                     tags=(f"status_{status}",))

        if 0 <= self.idx < len(self.images):
            try:
                self.tree.selection_set(self.images[self.idx].name)
                self.tree.see(self.images[self.idx].name)
            except tk.TclError:
                pass

    def _tp_label(self, label: str, tp_key: str) -> str:
        return label

    def _sub_label(self, label: str, tp_key: str, sub_key: str) -> str:
        # When the group node is closed, show only the name + status dot.
        # When opened, the open-handler swaps in the full counter.
        counts = self._group_counters(tp_key, sub_key)
        # Show compact even when closed; full counter shown via _on_tree_open()
        return f"{label}  ({counts['n_done']}/{counts['n_total']})"

    def _group_counters(self, tp_key: str, sub_key: str) -> dict:
        target = max(1, self.profile.target_cells)
        images = self.grouped.tree.get(tp_key, {}).get(sub_key, []) if self.grouped else []
        n_total = len(images)
        n_done = 0
        cache_key = (tp_key, sub_key)
        # Use cached n_active when available; recompute when stale (caller invalidates).
        n_active = self._group_active_cache.get(cache_key)
        compute_active = n_active is None
        if compute_active:
            from .summary import per_image_counts
            n_active = 0
        for info in images:
            if self.folder is None or not has_cache(self.folder, info.path):
                continue
            review = ReviewState.load(self.folder, info.path.stem)
            if review.is_finished:
                n_done += 1
            if compute_active:
                try:
                    c = per_image_counts(self.folder, info, self.profile)
                    if c is not None:
                        n_active += c["rbc"]
                except Exception:
                    pass
        if compute_active:
            self._group_active_cache[cache_key] = n_active
        return {"n_total": n_total, "n_done": n_done, "n_active": n_active, "target": target}

    def _invalidate_group_cache(self, tp_key: str | None = None,
                                 sub_key: str | None = None) -> None:
        """Drop the cached n_active for one or all groups so the next read recomputes."""
        if tp_key is None or sub_key is None:
            self._group_active_cache.clear()
        else:
            self._group_active_cache.pop((tp_key, sub_key), None)

    def _current_group_keys(self) -> tuple[str, str] | None:
        """Returns (timepoint, subject) of the currently selected image, or None."""
        if not self.images or self.grouped is None or self.idx >= len(self.images):
            return None
        cur_path = self.images[self.idx]
        for info in self.grouped.flat:
            if info.path == cur_path:
                return (info.timepoint, info.subject)
        return None

    def _refresh_group_node(self, iid: str, recurse: bool = False) -> None:
        """Update one Treeview node's label (open vs closed counter format)."""
        if iid.startswith("sub::"):
            try:
                _, tp_key, sub_key = iid.split("::", 2)
            except ValueError:
                return
            label_base = self.S["sb_ungrouped"] if sub_key == UNGROUPED_SUBJECT else sub_key
            is_open = bool(self.tree.item(iid, "open"))
            counts = self._group_counters(tp_key, sub_key)
            if is_open:
                full = self.S["sb_group_open_counter"].format(
                    n_active=counts["n_active"], target=counts["target"],
                    n_done=counts["n_done"], n_total=counts["n_total"],
                )
                self.tree.item(iid, text=f"{label_base}  ·  {full}")
            else:
                self.tree.item(iid, text=f"{label_base}  ({counts['n_done']}/{counts['n_total']})")
        if recurse:
            for child in self.tree.get_children(iid):
                self._refresh_group_node(child, recurse=True)

    def _refresh_current_group_counter(self) -> None:
        """Invalidate + refresh the group node of the currently-active image."""
        keys = self._current_group_keys()
        if keys is None:
            return
        tp_key, sub_key = keys
        self._invalidate_group_cache(tp_key, sub_key)
        sub_iid = f"sub::{tp_key}::{sub_key}"
        if self.tree.exists(sub_iid):
            self._refresh_group_node(sub_iid)

    def _refresh_sidebar_status(self) -> None:
        if self.grouped is None:
            return
        for info in self.grouped.flat:
            if not self.tree.exists(info.path.name):
                continue
            status = self._image_status(info.path)
            icon = STATUS_ICONS[status]
            self.tree.item(info.path.name, text=f"{icon}  {info.path.name}",
                           tags=(f"status_{status}",))
        # Refresh open-group counters now that statuses may have changed
        for iid in self.tree.get_children():
            self._refresh_group_node(iid, recurse=True)

    def _on_tree_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        # Skip group nodes (their iids start with tp:: or sub::)
        if name.startswith("tp::") or name.startswith("sub::"):
            return
        for i, p in enumerate(self.images):
            if p.name == name:
                if i != self.idx:
                    self._show_image_at(i)
                break

    def _on_tree_open(self, _evt=None) -> None:
        iid = self.tree.focus()
        if iid.startswith("sub::"):
            # iid = "sub::<tp>::<sub>"
            _, tp_key, sub_key = iid.split("::", 2)
            counts = self._group_counters(tp_key, sub_key)
            label_base = self.S["sb_ungrouped"] if sub_key == UNGROUPED_SUBJECT else sub_key
            full = self.S["sb_group_open_counter"].format(
                n_active=counts["n_active"], target=counts["target"],
                n_done=counts["n_done"], n_total=counts["n_total"],
            )
            self.tree.item(iid, text=f"{label_base}  ·  {full}")
        if iid not in self.profile.sidebar_open_groups:
            self.profile.sidebar_open_groups.append(iid)
            save_profile(self.profile)

    def _on_tree_close(self, _evt=None) -> None:
        iid = self.tree.focus()
        if iid.startswith("sub::"):
            _, tp_key, sub_key = iid.split("::", 2)
            label_base = self.S["sb_ungrouped"] if sub_key == UNGROUPED_SUBJECT else sub_key
            counts = self._group_counters(tp_key, sub_key)
            self.tree.item(iid, text=f"{label_base}  ({counts['n_done']}/{counts['n_total']})")
        if iid in self.profile.sidebar_open_groups:
            self.profile.sidebar_open_groups.remove(iid)
            save_profile(self.profile)

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
        self._refresh_sidebar_status()
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
        self.param_banner.config(text="")
        self._update_status()
        self._refresh_sidebar_status()
        self._invalidate_group_cache()
        self._refresh_current_group_counter()

    def _on_batch(self) -> None:
        if self.folder is None or not self.images:
            return
        batch_dialog(
            parent=self.root, folder=self.folder, images=self.images,
            profile=self.profile, strings=self.S,
            image_status_fn=self._image_status, on_done=self._after_batch,
        )

    def _after_batch(self) -> None:
        self._refresh_sidebar_status()
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
        self._refresh_type_combo()
        # If the profile points at a different folder, load it
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
        self._refresh_type_combo()
        self._update_status()

    def _on_settings(self) -> None:
        new_settings, lang_changed, theme_changed = settings_dialog(
            self.root, self.settings, self.S,
        )
        if new_settings is None:
            return
        self.settings = new_settings
        self.canvas.settings = new_settings  # ← keep canvas in sync; fixes tool-switch bug
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
        info_dialog(self.root, self.S["export_done_title"],
                    self.S["export_done_msg"].format(path=out))
        # Counts may have shifted (force_included propagation, etc.) — refresh.
        self._invalidate_group_cache()
        self._refresh_sidebar_status()

    def _on_help_hotkeys(self) -> None:
        info_dialog(self.root, self.S["help_hotkeys_title"], self.S["help_hotkeys"])

    def _on_help_about(self) -> None:
        info_dialog(self.root, self.S["menu_help_about"], f"Erys v{__version__}")

    # ─────────────────────────────────────────
    # Helpers
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
        # Edit happened → invalidate this group's cached active-count and refresh node.
        self._refresh_current_group_counter()
        # Status + sidebar icon for current image may have changed.
        self._refresh_sidebar_status()
        self._update_status()

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
        n_active = sum(1 for cid in infos
                       if cid not in excluded and (not auto_inactive(cid) or cid in included))
        n_excluded = sum(1 for cid in infos if cid in excluded)
        n_inactive_auto = sum(1 for cid in infos
                              if auto_inactive(cid) and cid not in included and cid not in excluded)
        S = self.S
        idx_str = f"[{self.idx+1}/{len(self.images)}]"
        finished = f"  ·  {S['status_finished']}" if self.review.is_finished else ""
        text = (f"{idx_str}  {path.name}  ·  "
                f"{n_active} {S['status_active']}  ·  "
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
        save_profile(self.profile)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    EryApp().run()


if __name__ == "__main__":
    main()
