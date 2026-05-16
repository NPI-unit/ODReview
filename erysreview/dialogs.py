"""Dialogs: cellpose spinner, confirm/info, profile manager, profile editor, settings, batch."""

from __future__ import annotations

import re
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import colorchooser, filedialog, simpledialog, ttk
from typing import Callable, Optional

from .cellpose_runner import detect_gpu_backend, has_cache, load_image, run_for_image
from .persistence import AppSettings, CellposeParams, TypeDef
from .profile import (
    DuplicateDetection, ProfileData,
    delete_profile, ensure_default_profile, list_profiles, load_profile, save_profile,
)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _center_on_parent(top: tk.Toplevel, parent: tk.Misc) -> None:
    top.update_idletasks()
    w, h = top.winfo_reqwidth(), top.winfo_reqheight()
    px = parent.winfo_rootx() + max(0, parent.winfo_width() // 2 - w // 2)
    py = parent.winfo_rooty() + max(0, parent.winfo_height() // 2 - h // 2)
    top.geometry(f"{w}x{h}+{px}+{py}")


def _sanitize_key(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "type"
    key = base
    i = 2
    while key in taken:
        key = f"{base}_{i}"
        i += 1
    return key


# ─────────────────────────────────────────────
# Cellpose spinner
# ─────────────────────────────────────────────
class CellposeSpinner:
    def __init__(self, parent: tk.Misc, image_name: str, strings: dict):
        self.parent = parent; self.image_name = image_name; self.S = strings
        self.top: tk.Toplevel | None = None
        self._dot_count = 0
        self._after_id: str | None = None

    def __enter__(self) -> "CellposeSpinner":
        self.top = tk.Toplevel(self.parent)
        self.top.title(self.S["cp_running"])
        self.top.transient(self.parent)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", lambda: None)
        frm = tk.Frame(self.top, padx=24, pady=20); frm.pack()
        self._lbl_main = tk.Label(frm, text=self.S["cp_running"],
                                  font=("TkDefaultFont", 13, "bold"))
        self._lbl_main.pack(pady=(0, 8))
        tk.Label(frm, text=self.S["cp_running_image"].format(name=self.image_name),
                 font=("TkDefaultFont", 11)).pack()
        tk.Label(frm, text=self.S["cp_running_hint"], fg="#777",
                 font=("TkDefaultFont", 10)).pack(pady=(4, 0))
        _center_on_parent(self.top, self.parent)
        self.top.grab_set()
        self._tick(); self.top.update()
        return self

    def _tick(self) -> None:
        if self.top is None: return
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count + " " * (3 - self._dot_count)
        self._lbl_main.config(text=f"{self.S['cp_running']}{dots}")
        try: self.top.update()
        except tk.TclError: return
        self._after_id = self.top.after(350, self._tick)

    def __exit__(self, *exc):
        if self.top is not None:
            if self._after_id:
                try: self.top.after_cancel(self._after_id)
                except tk.TclError: pass
            try: self.top.grab_release()
            except tk.TclError: pass
            self.top.destroy(); self.top = None


# ─────────────────────────────────────────────
# Confirm / Info
# ─────────────────────────────────────────────
def confirm_dialog(parent: tk.Misc, title: str, message: str,
                   yes_text: str = "OK", no_text: str = "Cancel") -> bool:
    result = {"v": False}
    top = tk.Toplevel(parent); top.title(title); top.transient(parent)
    top.resizable(False, False); top.grab_set()
    tk.Label(top, text=message, padx=24, pady=18, justify="left").pack()
    btn = tk.Frame(top); btn.pack(pady=(0, 14))
    def on_yes(): result["v"] = True; top.destroy()
    def on_no():  result["v"] = False; top.destroy()
    tk.Button(btn, text=no_text, width=10, command=on_no).pack(side="left", padx=8)
    tk.Button(btn, text=yes_text, width=10, command=on_yes).pack(side="left", padx=8)
    top.bind("<Return>", lambda e: on_yes())
    top.bind("<Escape>", lambda e: on_no())
    _center_on_parent(top, parent); parent.wait_window(top)
    return result["v"]


def info_dialog(parent: tk.Misc, title: str, message: str) -> None:
    top = tk.Toplevel(parent); top.title(title); top.transient(parent)
    top.resizable(False, False); top.grab_set()
    tk.Label(top, text=message, padx=24, pady=18, justify="left").pack()
    tk.Button(top, text="OK", width=10, command=top.destroy).pack(pady=(0, 14))
    top.bind("<Return>", lambda e: top.destroy())
    top.bind("<Escape>", lambda e: top.destroy())
    _center_on_parent(top, parent); parent.wait_window(top)


# ─────────────────────────────────────────────
# Profile Manager
# ─────────────────────────────────────────────
def profile_manager_dialog(parent: tk.Misc, strings: dict) -> ProfileData | None:
    """Returns the chosen ProfileData, or None if user cancels."""
    S = strings
    result: dict[str, ProfileData | None] = {"profile": None}

    top = tk.Toplevel(parent); top.title(S["pm_title"])
    top.transient(parent); top.grab_set()
    top.minsize(440, 340)

    tk.Label(top, text=S["pm_existing"], font=("TkDefaultFont", 11, "bold"),
             anchor="w").pack(fill="x", padx=14, pady=(14, 4))

    listbox_frame = tk.Frame(top); listbox_frame.pack(fill="both", expand=True, padx=14)
    listbox = tk.Listbox(listbox_frame, height=10)
    sb = ttk.Scrollbar(listbox_frame, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); listbox.pack(side="left", fill="both", expand=True)

    def _refresh():
        listbox.delete(0, "end")
        for n in list_profiles():
            listbox.insert("end", n)
        if listbox.size() == 0:
            listbox.insert("end", S["pm_empty"])
            listbox.config(state="disabled")
        else:
            listbox.config(state="normal")
    _refresh()

    btn_row = tk.Frame(top); btn_row.pack(fill="x", padx=14, pady=10)

    def on_load():
        sel = listbox.curselection()
        if not sel: return
        name = listbox.get(sel[0])
        prof = load_profile(name)
        if prof is None:
            info_dialog(top, S["pm_title"], f"Profil '{name}' konnte nicht geladen werden.")
            return
        result["profile"] = prof; top.destroy()

    def on_default():
        prof = ensure_default_profile()
        result["profile"] = prof; top.destroy()

    def on_new():
        name = simpledialog.askstring(S["pm_title"], S["pm_new_name_prompt"], parent=top)
        if not name: return
        prof = ProfileData(name=name)
        save_profile(prof)
        _refresh()

    def on_duplicate():
        sel = listbox.curselection()
        if not sel: return
        src_name = listbox.get(sel[0])
        src = load_profile(src_name)
        if src is None: return
        new_name = simpledialog.askstring(S["pm_title"], S["pm_dup_name_prompt"], parent=top)
        if not new_name: return
        copy = deepcopy(src); copy.name = new_name; copy.created_at = ""
        save_profile(copy)
        _refresh()

    def on_delete():
        sel = listbox.curselection()
        if not sel: return
        name = listbox.get(sel[0])
        if not confirm_dialog(top, S["pm_title"],
                              S["pm_confirm_delete"].format(name=name),
                              yes_text=S["confirm_yes"], no_text=S["confirm_no"]):
            return
        delete_profile(name)
        _refresh()

    tk.Button(btn_row, text=S["pm_load"], command=on_load).pack(side="left", padx=2)
    tk.Button(btn_row, text=S["pm_new"], command=on_new).pack(side="left", padx=2)
    tk.Button(btn_row, text=S["pm_duplicate"], command=on_duplicate).pack(side="left", padx=2)
    tk.Button(btn_row, text=S["pm_delete"], command=on_delete).pack(side="left", padx=2)
    tk.Button(btn_row, text=S["pm_default"], command=on_default).pack(side="right", padx=2)

    top.bind("<Return>", lambda e: on_load())
    top.bind("<Escape>", lambda e: top.destroy())
    _center_on_parent(top, parent)
    parent.wait_window(top)
    return result["profile"]


# ─────────────────────────────────────────────
# Profile Editor — replaces the old "Settings" study-fields dialog
# ─────────────────────────────────────────────
def profile_editor_dialog(parent: tk.Misc, profile: ProfileData, strings: dict
                          ) -> ProfileData | None:
    """Edit the active profile. Returns the new ProfileData or None on cancel."""
    S = strings
    working = deepcopy(profile)

    top = tk.Toplevel(parent); top.title(S["menu_profile_edit"])
    top.transient(parent); top.grab_set()
    top.minsize(720, 540)

    nb = ttk.Notebook(top); nb.pack(fill="both", expand=True, padx=12, pady=(12, 6))

    pad = {"padx": 16, "pady": 6}

    # ===== Tab: General =====
    tab_g = tk.Frame(nb); nb.add(tab_g, text=S["pe_section_general"])
    row = 0
    tk.Label(tab_g, text=S["pe_name"]).grid(row=row, column=0, sticky="w", **pad)
    name_var = tk.StringVar(value=working.name)
    tk.Entry(tab_g, textvariable=name_var, width=40
             ).grid(row=row, column=1, sticky="w", **pad); row += 1

    tk.Label(tab_g, text=S["pe_import_folder"]).grid(row=row, column=0, sticky="w", **pad)
    folder_var = tk.StringVar(value=working.import_folder or "")
    folder_entry = tk.Entry(tab_g, textvariable=folder_var, width=40)
    folder_entry.grid(row=row, column=1, sticky="w", **pad)
    def pick_folder():
        path = filedialog.askdirectory(parent=top, title=S["pe_import_folder"])
        if path: folder_var.set(path)
    tk.Button(tab_g, text=S["pe_choose_folder"], command=pick_folder
              ).grid(row=row, column=2, sticky="w", padx=4); row += 1

    tk.Label(tab_g, text=S["pe_target_cells"]).grid(row=row, column=0, sticky="w", **pad)
    target_var = tk.IntVar(value=working.target_cells)
    tk.Spinbox(tab_g, from_=100, to=100000, increment=100, textvariable=target_var, width=10
               ).grid(row=row, column=1, sticky="w", **pad); row += 1

    tk.Label(tab_g, text=S["settings_margin_n"]).grid(row=row, column=0, sticky="w", **pad)
    margin_var = tk.IntVar(value=working.margin_n)
    tk.Spinbox(tab_g, from_=0, to=500, textvariable=margin_var, width=8
               ).grid(row=row, column=1, sticky="w", **pad); row += 1

    # ===== Tab: Grouping (regex) =====
    tab_r = tk.Frame(nb); nb.add(tab_r, text=S["pe_section_grouping"])
    tk.Label(tab_r, text=S["pe_image_pattern"]).pack(anchor="w", padx=14, pady=(12, 2))
    pattern_var = tk.StringVar(value=working.image_pattern)
    tk.Entry(tab_r, textvariable=pattern_var, width=78).pack(anchor="w", padx=14)
    tk.Label(tab_r, text=S["pe_pattern_help"], fg="#666", wraplength=620, justify="left",
             font=("TkDefaultFont", 9)).pack(anchor="w", padx=14, pady=(4, 8))

    tk.Label(tab_r, text=S["pe_pattern_test"]).pack(anchor="w", padx=14)
    test_var = tk.StringVar(value="TRIBAL_195_Prä_19.11.25_021.tif")
    tk.Entry(tab_r, textvariable=test_var, width=78).pack(anchor="w", padx=14)
    test_result = tk.Label(tab_r, text="", fg="#0a0", anchor="w", justify="left",
                            font=("Menlo", 10))
    test_result.pack(anchor="w", padx=14, pady=(4, 0))

    def update_regex_test(*_):
        try:
            r = re.compile(pattern_var.get())
        except re.error as e:
            test_result.config(text=f"Regex error: {e}", fg="#c33"); return
        m = r.match(test_var.get())
        if not m:
            test_result.config(text=S["pe_pattern_nomatch"], fg="#c33"); return
        gd = m.groupdict()
        text = S["pe_pattern_match"] + ":  " + "  ".join(f"{k}={v}" for k, v in gd.items())
        # Highlight missing required groups
        required = {"subject", "timepoint", "image_num"}
        missing = required - set(gd.keys())
        if missing:
            text += f"   ⚠ missing: {sorted(missing)}"
            test_result.config(text=text, fg="#c80")
        else:
            test_result.config(text=text, fg="#0a0")
    pattern_var.trace_add("write", update_regex_test)
    test_var.trace_add("write", update_regex_test)
    update_regex_test()

    # ===== Tab: Cellpose =====
    tab_cp = tk.Frame(nb); nb.add(tab_cp, text=S["pe_section_cellpose"])
    row = 0
    diam_var = tk.DoubleVar(value=working.cellpose.diameter)
    flow_var = tk.DoubleVar(value=working.cellpose.flow_threshold)
    cellprob_var = tk.DoubleVar(value=working.cellpose.cellprob_threshold)
    use_gpu_var = tk.BooleanVar(value=working.use_gpu)
    tk.Label(tab_cp, text=S["settings_cp_diameter"]).grid(row=row, column=0, sticky="w", **pad)
    tk.Spinbox(tab_cp, from_=5.0, to=500.0, increment=1.0, textvariable=diam_var, width=10
               ).grid(row=row, column=1, sticky="w", **pad); row += 1
    tk.Label(tab_cp, text=S["settings_cp_flow"]).grid(row=row, column=0, sticky="w", **pad)
    tk.Spinbox(tab_cp, from_=0.0, to=3.0, increment=0.05, textvariable=flow_var, width=10
               ).grid(row=row, column=1, sticky="w", **pad); row += 1
    tk.Label(tab_cp, text=S["settings_cp_cellprob"]).grid(row=row, column=0, sticky="w", **pad)
    tk.Spinbox(tab_cp, from_=-6.0, to=6.0, increment=0.5, textvariable=cellprob_var, width=10
               ).grid(row=row, column=1, sticky="w", **pad); row += 1
    ttk.Separator(tab_cp, orient="horizontal").grid(
        row=row, column=0, columnspan=2, sticky="ew", pady=10); row += 1
    tk.Checkbutton(tab_cp, text=S["settings_use_gpu"], variable=use_gpu_var
                   ).grid(row=row, column=0, columnspan=2, sticky="w", padx=16); row += 1
    tk.Label(tab_cp, text=S["settings_gpu_detected"].format(backend=detect_gpu_backend().upper()),
             fg="#888", font=("TkDefaultFont", 9)
             ).grid(row=row, column=0, columnspan=2, sticky="w", padx=16, pady=(2, 6))

    # ===== Tab: Types =====
    tab_t = tk.Frame(nb); nb.add(tab_t, text=S["pe_section_types"])
    type_state = {"types": [TypeDef(**{k: getattr(t, k) for k in ("key", "i18n_key", "name", "color", "hotkey")})
                            for t in working.cell_types],
                  "selected_idx": 0}
    left = tk.Frame(tab_t); left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)
    right = tk.Frame(tab_t, width=240); right.pack(side="left", fill="y", padx=(6, 12), pady=12)
    right.pack_propagate(False)

    type_tree = ttk.Treeview(left, columns=("name", "color"), show="headings", height=10)
    type_tree.heading("name", text=S["settings_type_name"])
    type_tree.heading("color", text=S["settings_type_color"])
    type_tree.column("name", width=200, stretch=True)
    type_tree.column("color", width=80, anchor="center", stretch=False)
    type_tree.pack(fill="both", expand=True)

    def on_up():
        i = type_state["selected_idx"]
        if i > 0:
            type_state["types"][i - 1], type_state["types"][i] = type_state["types"][i], type_state["types"][i - 1]
            type_state["selected_idx"] = i - 1; _populate(i - 1)
    def on_down():
        i = type_state["selected_idx"]
        if 0 <= i < len(type_state["types"]) - 1:
            type_state["types"][i + 1], type_state["types"][i] = type_state["types"][i], type_state["types"][i + 1]
            type_state["selected_idx"] = i + 1; _populate(i + 1)
    def on_new():
        keys = {t.key for t in type_state["types"]}
        type_state["types"].append(TypeDef(key=_sanitize_key("new_type", keys),
                                           i18n_key=None, name="Neuer Typ",
                                           color="#888888", hotkey=""))
        type_state["selected_idx"] = len(type_state["types"]) - 1
        _populate(type_state["selected_idx"])
    def on_delete():
        i = type_state["selected_idx"]
        if not (0 <= i < len(type_state["types"])): return
        if type_state["types"][i].key == "normal":
            info_dialog(top, S["pe_section_types"], S["settings_type_normal_protected"]); return
        del type_state["types"][i]
        type_state["selected_idx"] = min(i, len(type_state["types"]) - 1)
        _populate(type_state["selected_idx"])

    btns_left = tk.Frame(left); btns_left.pack(fill="x", pady=(6, 0))
    tk.Button(btns_left, text=S["settings_type_up"], command=on_up).pack(side="left", padx=2)
    tk.Button(btns_left, text=S["settings_type_down"], command=on_down).pack(side="left", padx=2)
    tk.Button(btns_left, text=S["settings_type_new"], command=on_new).pack(side="left", padx=2)
    tk.Button(btns_left, text=S["settings_type_delete"], command=on_delete).pack(side="left", padx=2)

    # ─── Color scheme picker (overwrites all type colors in current order) ───
    from .theme import COLOR_SCHEMES, apply_scheme_to_types
    scheme_frame = tk.Frame(left); scheme_frame.pack(fill="x", pady=(10, 0))
    tk.Label(scheme_frame, text=S["pe_color_scheme"], anchor="w",
             font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
    tk.Label(scheme_frame, text=S["pe_color_scheme_help"], anchor="w",
             fg="#666", wraplength=440, font=("TkDefaultFont", 9),
             justify="left").pack(anchor="w", pady=(0, 4))
    scheme_label_key = "label_de" if S.get("settings_language") == "Sprache" else "label_de"
    # Build display-name → key mapping
    scheme_disp_to_key: dict[str, str] = {
        v[scheme_label_key]: k for k, v in COLOR_SCHEMES.items()
    }
    scheme_var = tk.StringVar()
    scheme_combo = ttk.Combobox(
        scheme_frame, textvariable=scheme_var, state="readonly",
        values=list(scheme_disp_to_key.keys()), width=30,
    )
    scheme_combo.pack(side="left", padx=(0, 6))

    def apply_color_scheme():
        sel = scheme_var.get()
        scheme_key = scheme_disp_to_key.get(sel)
        if scheme_key is None:
            return
        if not confirm_dialog(top, S["confirm_apply_scheme_title"],
                              S["confirm_apply_scheme_msg"].format(name=sel),
                              yes_text=S["confirm_yes"], no_text=S["confirm_no"]):
            return
        apply_scheme_to_types(type_state["types"], scheme_key)
        _populate(type_state["selected_idx"])

    tk.Button(scheme_frame, text=S["pe_color_scheme"],
              command=apply_color_scheme).pack(side="left")

    tk.Label(right, text=S["settings_type_name"], anchor="w").pack(fill="x")
    name_var_t = tk.StringVar(); tk.Entry(right, textvariable=name_var_t
                                          ).pack(fill="x", pady=(2, 8))
    tk.Label(right, text=S["settings_type_color"], anchor="w").pack(fill="x")
    color_frame = tk.Frame(right); color_frame.pack(fill="x", pady=(2, 8))
    color_swatch = tk.Label(color_frame, text="    ", bg="#888888", relief="solid", bd=1, width=4)
    color_swatch.pack(side="left", padx=(0, 6))
    color_var_t = tk.StringVar(value="#888888")
    def pick_color():
        _, hexv = colorchooser.askcolor(initialcolor=color_var_t.get(), parent=top)
        if hexv:
            color_var_t.set(hexv); color_swatch.configure(bg=hexv)
    tk.Button(color_frame, text=S["settings_color_pick"], command=pick_color
              ).pack(side="left")

    def save_current():
        i = type_state["selected_idx"]
        if not (0 <= i < len(type_state["types"])): return
        td = type_state["types"][i]
        td.name = name_var_t.get().strip() or td.name or td.key
        # User-renamed types lose their i18n_key (their typed-in name is now authoritative)
        if td.i18n_key and td.name != S.get(td.i18n_key, ""):
            td.i18n_key = None
        td.color = color_var_t.get()
        _populate(i)
    tk.Button(right, text=S["settings_type_save"], command=save_current
              ).pack(fill="x", pady=(8, 0))

    def _populate(idx: int | None = None):
        for iid in type_tree.get_children():
            type_tree.delete(iid)
        for k, t in enumerate(type_state["types"]):
            disp = S.get(t.i18n_key, t.name) if t.i18n_key else (t.name or t.key)
            type_tree.insert("", "end", iid=str(k), values=(disp, t.color))
        if idx is not None and 0 <= idx < len(type_state["types"]):
            type_tree.selection_set(str(idx))
            type_tree.see(str(idx))
            _refresh_form(idx)

    def _refresh_form(idx: int):
        td = type_state["types"][idx]
        disp = S.get(td.i18n_key, td.name) if td.i18n_key else (td.name or td.key)
        name_var_t.set(disp); color_var_t.set(td.color); color_swatch.configure(bg=td.color)

    def _on_type_tree_select(_e):
        sel = type_tree.selection()
        if not sel: return
        idx = int(sel[0]); type_state["selected_idx"] = idx; _refresh_form(idx)
    type_tree.bind("<<TreeviewSelect>>", _on_type_tree_select)
    _populate(0)

    # ===== Tab: Duplicates =====
    tab_d = tk.Frame(nb); nb.add(tab_d, text=S["pe_section_dups"])
    dup_enabled_var = tk.BooleanVar(value=working.duplicate_detection.enabled)
    dup_thresh_var = tk.DoubleVar(value=working.duplicate_detection.threshold_factor)
    dup_policy_var = tk.StringVar(value=working.duplicate_detection.policy)
    tk.Checkbutton(tab_d, text=S["pe_dup_enabled"], variable=dup_enabled_var
                   ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=8)
    tk.Label(tab_d, text=S["pe_dup_threshold"]).grid(row=1, column=0, sticky="w", padx=14, pady=4)
    tk.Spinbox(tab_d, from_=0.1, to=2.0, increment=0.05, textvariable=dup_thresh_var, width=10
               ).grid(row=1, column=1, sticky="w", padx=4, pady=4)
    tk.Label(tab_d, text=S["pe_dup_policy"]).grid(row=2, column=0, sticky="w", padx=14, pady=4)
    ttk.Combobox(tab_d, textvariable=dup_policy_var, state="readonly", width=22,
                 values=[("remove_smaller"), ("remove_both")]
                 ).grid(row=2, column=1, sticky="w", padx=4, pady=4)

    # ===== Tab: Appearance =====
    tab_a = tk.Frame(nb); nb.add(tab_a, text=S["pe_section_appear"])
    show_margin_var = tk.BooleanVar(value=working.show_margin_overlay)
    tk.Checkbutton(tab_a, text=S["settings_show_margin_overlay"], variable=show_margin_var
                   ).grid(row=0, column=0, sticky="w", padx=16, pady=8)

    # ===== Footer =====
    footer = tk.Frame(top); footer.pack(fill="x", padx=12, pady=(0, 12))
    state = {"saved": False}
    def on_cancel(): state["saved"] = False; top.destroy()
    def on_save():
        if not confirm_dialog(top, S["save_profile_title"], S["save_profile_msg"],
                              yes_text=S["confirm_yes"], no_text=S["confirm_no"]):
            return
        state["saved"] = True; top.destroy()
    tk.Button(footer, text=S["settings_cancel"], width=12, command=on_cancel
              ).pack(side="right", padx=4)
    tk.Button(footer, text=S["settings_save"], width=12, command=on_save
              ).pack(side="right", padx=4)
    top.bind("<Escape>", lambda e: on_cancel())
    _center_on_parent(top, parent); parent.wait_window(top)

    if not state["saved"]:
        return None

    new_profile = ProfileData(
        name=name_var.get().strip() or working.name,
        import_folder=folder_var.get() or None,
        image_pattern=pattern_var.get(),
        target_cells=int(target_var.get()),
        cell_types=type_state["types"],
        cellpose=CellposeParams(
            diameter=float(diam_var.get()),
            flow_threshold=float(flow_var.get()),
            cellprob_threshold=float(cellprob_var.get()),
            model=working.cellpose.model,
        ),
        use_gpu=bool(use_gpu_var.get()),
        margin_n=int(margin_var.get()),
        show_margin_overlay=bool(show_margin_var.get()),
        duplicate_detection=DuplicateDetection(
            enabled=bool(dup_enabled_var.get()),
            threshold_factor=float(dup_thresh_var.get()),
            policy=str(dup_policy_var.get()),
        ),
        sidebar_open_groups=working.sidebar_open_groups,
        active_type_key=working.active_type_key,
        created_at=working.created_at,
    )
    return new_profile


# ─────────────────────────────────────────────
# App-Tab Settings (globals only)
# ─────────────────────────────────────────────
def settings_dialog(parent: tk.Misc, settings: AppSettings, strings: dict
                    ) -> tuple[Optional[AppSettings], bool, bool]:
    """Returns (new_settings_or_None, language_changed, theme_changed)."""
    S = strings
    top = tk.Toplevel(parent); top.title(S["settings_title"])
    top.transient(parent); top.grab_set(); top.resizable(False, False)

    pad = {"padx": 16, "pady": 8}
    row = 0
    tk.Label(top, text=S["settings_language"]).grid(row=row, column=0, sticky="w", **pad)
    lang_var = tk.StringVar(value=settings.language)
    ttk.Combobox(top, textvariable=lang_var, values=["de", "en"],
                 state="readonly", width=10).grid(row=row, column=1, sticky="w", **pad)
    row += 1

    tk.Label(top, text=S["settings_theme"]).grid(row=row, column=0, sticky="w", **pad)
    # Map display labels back to internal keys
    theme_disp = {S["settings_theme_dark"]: "dark", S["settings_theme_light"]: "light"}
    theme_disp_inv = {v: k for k, v in theme_disp.items()}
    theme_var = tk.StringVar(value=theme_disp_inv.get(settings.theme, S["settings_theme_dark"]))
    ttk.Combobox(top, textvariable=theme_var,
                 values=[S["settings_theme_dark"], S["settings_theme_light"]],
                 state="readonly", width=10
                 ).grid(row=row, column=1, sticky="w", **pad)
    row += 1

    btns = tk.Frame(top); btns.grid(row=row, column=0, columnspan=2, pady=14, sticky="e", padx=16)
    state = {"saved": False}
    def on_cancel(): state["saved"] = False; top.destroy()
    def on_save():   state["saved"] = True;  top.destroy()
    tk.Button(btns, text=S["settings_cancel"], width=12, command=on_cancel).pack(side="left", padx=4)
    tk.Button(btns, text=S["settings_save"], width=12, command=on_save).pack(side="left", padx=4)
    top.bind("<Escape>", lambda e: on_cancel())
    _center_on_parent(top, parent); parent.wait_window(top)
    if not state["saved"]:
        return (None, False, False)
    new_lang = lang_var.get()
    new_theme = theme_disp.get(theme_var.get(), settings.theme)
    new = AppSettings(
        language=new_lang,
        theme=new_theme,
        window_w=settings.window_w, window_h=settings.window_h,
        sidebar_width=settings.sidebar_width,
        overlay_visible=settings.overlay_visible,
        active_tool=settings.active_tool,
        last_profile_name=settings.last_profile_name,
    )
    return (new, new_lang != settings.language, new_theme != settings.theme)


# ─────────────────────────────────────────────
# Batch dialog
# ─────────────────────────────────────────────
def batch_dialog(
    parent: tk.Misc, folder: Path, images: list[Path],
    profile: ProfileData, strings: dict,
    image_status_fn: Callable[[Path], str],
    on_done: Callable[[], None],
) -> None:
    S = strings
    top = tk.Toplevel(parent); top.title(S["batch_title"])
    top.transient(parent); top.grab_set(); top.minsize(560, 480)

    checked = {p.name: not has_cache(folder, p) for p in images}
    avg_seconds_per_image = 30.0
    container = tk.Frame(top); container.pack(fill="both", expand=True, padx=12, pady=12)

    bar = tk.Frame(container); bar.pack(fill="x", pady=(0, 6))
    def select_all():
        for n in checked: checked[n] = True
        _refresh_tree(); _refresh_footer()
    def select_unsegmented():
        for p in images: checked[p.name] = (image_status_fn(p) == "unsegmented")
        _refresh_tree(); _refresh_footer()
    def select_unfinished():
        for p in images: checked[p.name] = (image_status_fn(p) != "finished")
        _refresh_tree(); _refresh_footer()
    def invert_selection():
        for n in checked: checked[n] = not checked[n]
        _refresh_tree(); _refresh_footer()
    tk.Button(bar, text=S["batch_select_all"], command=select_all).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_unsegmented"], command=select_unsegmented).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_unfinished"], command=select_unfinished).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_invert"], command=invert_selection).pack(side="left", padx=2)

    tree_frame = tk.Frame(container); tree_frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(tree_frame, columns=("check", "status", "name"), show="headings", height=14)
    tree.heading("check", text="✓"); tree.column("check", width=40, anchor="center", stretch=False)
    tree.heading("status", text=""); tree.column("status", width=40, anchor="center", stretch=False)
    tree.heading("name", text="Datei"); tree.column("name", anchor="w", stretch=True)
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y"); tree.pack(side="left", fill="both", expand=True)
    icons = {"unsegmented": "○", "segmented": "◐", "in_progress": "●", "finished": "◉"}
    def _refresh_tree():
        for iid in tree.get_children(): tree.delete(iid)
        for p in images:
            mark = "✓" if checked[p.name] else " "
            icon = icons[image_status_fn(p)]
            tree.insert("", "end", iid=p.name, values=(mark, icon, p.name))
    def _toggle_row(evt):
        sel = tree.identify_row(evt.y)
        if not sel: return
        col = tree.identify_column(evt.x)
        if col != "#1": return
        checked[sel] = not checked[sel]
        tree.set(sel, "check", "✓" if checked[sel] else " ")
        _refresh_footer()
    tree.bind("<Button-1>", _toggle_row)

    footer = tk.Frame(container); footer.pack(fill="x", pady=(8, 0))
    footer_left = tk.Label(footer, text="", fg="#888", anchor="w", font=("TkDefaultFont", 10))
    footer_left.pack(side="left", fill="x", expand=True)
    tk.Button(footer, text=S["batch_cancel"], width=12, command=top.destroy
              ).pack(side="right", padx=4)
    start_btn = tk.Button(footer, text=S["batch_start"], width=12)
    start_btn.pack(side="right", padx=4)
    def _refresh_footer():
        n = sum(1 for v in checked.values() if v)
        est_min = max(1, int(round(n * avg_seconds_per_image / 60.0)))
        footer_left.config(text=S["batch_n_selected"].format(n=n, total=len(images), minutes=est_min))
    _refresh_tree(); _refresh_footer()

    state = {"cancel": False}
    def _enter_progress(selected_paths: list[Path]):
        for w in container.winfo_children(): w.destroy()
        tk.Label(container, text=S["batch_progress_title"],
                 font=("TkDefaultFont", 13, "bold")).pack(pady=(0, 10))
        lbl = tk.Label(container, text="", font=("TkDefaultFont", 11)); lbl.pack(pady=(0, 6))
        pb = ttk.Progressbar(container, orient="horizontal", mode="determinate",
                              length=460, maximum=len(selected_paths))
        pb.pack(pady=(0, 14))
        def on_cancel_after():
            state["cancel"] = True; cancel_btn2.config(state="disabled", text="…")
        cancel_btn2 = tk.Button(container, text=S["batch_progress_cancel"],
                                 command=on_cancel_after)
        cancel_btn2.pack()
        errors: list[tuple[str, str]] = []; skipped = 0
        for i, path in enumerate(selected_paths, start=1):
            if state["cancel"]: break
            lbl.config(text=S["batch_progress_running"].format(
                i=i, n=len(selected_paths), name=path.name))
            pb["value"] = i - 1
            container.update_idletasks(); container.update()
            if has_cache(folder, path):
                skipped += 1; pb["value"] = i; continue
            try:
                img = load_image(path)
                run_for_image(folder, path, img, profile.cellpose,
                              force=False, use_gpu=profile.use_gpu)
            except Exception as e:
                errors.append((path.name, str(e)))
            pb["value"] = i
            container.update_idletasks(); container.update()
        for w in container.winfo_children(): w.destroy()
        tk.Label(container, text=S["batch_summary_title"],
                 font=("TkDefaultFont", 13, "bold")).pack(pady=(0, 10))
        summary_text = (
            S["batch_summary_done"].format(n=i - len(errors)) + "\n" +
            S["batch_summary_skipped"].format(n=skipped)
        )
        if errors:
            summary_text += "\n\n" + S["batch_summary_errors"].format(n=len(errors))
            for n, e in errors[:20]: summary_text += f"\n  – {n}: {e}"
        tk.Label(container, text=summary_text, justify="left", wraplength=520
                 ).pack(pady=(0, 14))
        tk.Button(container, text="OK", width=12,
                  command=lambda: (on_done(), top.destroy())).pack()

    def _on_start():
        sel = [p for p in images if checked[p.name]]
        if sel: _enter_progress(sel)
    start_btn.config(command=_on_start)
    _center_on_parent(top, parent); parent.wait_window(top)
