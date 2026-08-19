"""Dialogs: cellpose spinner, confirm/info, profile manager, profile editor, settings, batch."""

from __future__ import annotations

import queue
import re
import threading
import time
import tkinter as tk
from copy import deepcopy
from pathlib import Path
from tkinter import colorchooser, filedialog, simpledialog, ttk
from typing import Callable, Optional

from .cellpose_runner import (
    BUILTIN_MODEL, detect_gpu_backend, has_cache, known_builtins, load_image,
    run_for_image,
)
from .persistence import AppSettings, CellposeParams, TypeDef
from .theme import FONT_SCALE_CHOICES, OUTLINE_ORDER, font
from .profile import (
    ProfileData, SizeFilter,
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


def _clampf(var, lo: float, hi: float, default: float) -> float:
    """Read a Tk numeric var as float and clamp to [lo, hi]. The Spinbox arrows
    bound the range but typed input does not, so an out-of-range value (how the
    'diameter 200/2000' values got in) is snapped here before it reaches
    Cellpose. Falls back to `default` if the field holds non-numeric garbage."""
    try:
        v = float(var.get())
    except (tk.TclError, ValueError):
        return default
    return max(lo, min(hi, v))


def _sanitize_key(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", name.strip().lower()).strip("_") or "type"
    key = base
    i = 2
    while key in taken:
        key = f"{base}_{i}"
        i += 1
    return key


# ─────────────────────────────────────────────
# Busy modal (Cellpose inference, overview build, …)
# ─────────────────────────────────────────────
class BusyDialog:
    """Modal "please wait" window for a blocking job about to run on a worker
    thread via run_in_thread_pumping().

    Indeterminate on purpose: it reports "still working", not how far along — a
    real progress bar needs the worker to report steps, which is tracked as UI-4
    in docs/consistency-audit.md.

    **Escape / the close button set `cancelled`** and say so on screen. They do
    NOT interrupt the worker: neither Cellpose inference nor a workbook build
    can be stopped mid-call, and pretending otherwise would be a lie. What they
    remove is the dead end — previously WM_DELETE_WINDOW was bound to a no-op
    and no key did anything, so a hung worker meant force-quitting the app
    (WCAG 2.1.2). Callers decide what a requested cancel means for them.
    """

    def __init__(self, parent: tk.Misc, title: str, message: str,
                 strings: dict | None = None, submessage: str = ""):
        self.parent = parent
        self.title = title
        self.message = message
        self.submessage = submessage
        self.S = strings or {}
        self.cancelled = False
        self.top: tk.Toplevel | None = None
        self._dots = 0
        self._after_id: str | None = None

    def __enter__(self) -> "BusyDialog":
        self.top = tk.Toplevel(self.parent)
        self.top.title(self.title)
        self.top.transient(self.parent)
        self.top.resizable(False, False)
        self.top.protocol("WM_DELETE_WINDOW", self.request_cancel)
        self.top.bind("<Escape>", self.request_cancel)
        frm = tk.Frame(self.top, padx=24, pady=20); frm.pack()
        self._lbl = tk.Label(frm, text=self.message, font=font(13, "bold"))
        self._lbl.pack(pady=(0, 8))
        if self.submessage:
            tk.Label(frm, text=self.submessage, font=font(11)).pack()
        self._hint = tk.Label(frm, text=self.S.get("busy_cancel_hint", ""),
                              fg="#777", font=font(10))
        self._hint.pack(pady=(6, 0))
        _center_on_parent(self.top, self.parent)
        self.top.grab_set()
        self.top.focus_set()          # so Escape reaches this window
        self._tick(); self.top.update()
        return self

    def request_cancel(self, _evt=None) -> None:
        if self.cancelled or self.top is None:
            return
        self.cancelled = True
        try:
            self._hint.config(text=self.S.get("busy_cancelling", ""))
        except tk.TclError:
            pass

    def _tick(self) -> None:
        if self.top is None:
            return
        self._dots = (self._dots + 1) % 4
        self._lbl.config(text=f"{self.message}{'.' * self._dots}{' ' * (3 - self._dots)}")
        try:
            self.top.update_idletasks()      # not update() — the pump owns that
        except tk.TclError:
            return
        self._after_id = self.top.after(350, self._tick)

    def __exit__(self, *exc):
        if self.top is not None:
            if self._after_id:
                try: self.top.after_cancel(self._after_id)
                except tk.TclError: pass
            try: self.top.grab_release()
            except tk.TclError: pass
            self.top.destroy(); self.top = None


class CellposeSpinner(BusyDialog):
    """The busy modal, worded for a single segmentation run."""

    def __init__(self, parent: tk.Misc, image_name: str, strings: dict):
        super().__init__(
            parent,
            title=strings["cp_running"],
            message=strings["cp_running"],
            strings=strings,
            submessage=strings["cp_running_image"].format(name=image_name),
        )


def run_in_thread_pumping(widget: tk.Misc, work_fn: Callable):
    """Run ``work_fn()`` in a daemon thread while keeping ``widget``'s Tk event
    loop responsive, then return its result (re-raising any exception it threw).

    Cellpose inference is CPU/GPU-bound and can take many seconds — on Windows
    (CPU) far longer. Running it directly on the Tk thread freezes the window
    ("Not Responding"). Here the heavy call happens on a worker thread; the main
    thread keeps pumping events (~50 Hz) so spinners animate and Cancel buttons
    stay clickable. ``work_fn`` MUST NOT touch any Tk object — Tk is single-
    threaded; all UI work stays on the main thread, here.
    """
    holder: dict = {"result": None, "error": None}
    done = threading.Event()

    def _worker():
        try:
            holder["result"] = work_fn()
        except BaseException as e:  # noqa: BLE001 — surfaced on the main thread
            holder["error"] = e
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    while not done.is_set():
        try:
            widget.update()          # full pump so a modal Cancel can register
        except tk.TclError:
            break                    # widget destroyed under us — stop pumping
        time.sleep(0.02)
    if holder["error"] is not None:
        raise holder["error"]
    return holder["result"]


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
    # Force the dialog to the front and request focus — on Windows the
    # bootstrap-parent approach used to leave this dialog behind the
    # taskbar / other windows.
    top.lift()
    top.attributes("-topmost", True)
    top.after(120, lambda: top.attributes("-topmost", False))
    top.focus_force()

    tk.Label(top, text=S["pm_existing"], font=font(11, "bold"),
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
def profile_editor_dialog(parent: tk.Misc, profile: ProfileData, strings: dict,
                          registry: dict | None = None
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

    tk.Label(tab_g, text=S["pe_images_per_subject"]).grid(row=row, column=0, sticky="w", **pad)
    perimg_var = tk.IntVar(value=working.images_per_subject)
    tk.Spinbox(tab_g, from_=1, to=10000, increment=1, textvariable=perimg_var, width=10
               ).grid(row=row, column=1, sticky="w", **pad); row += 1

    tk.Label(tab_g, text=S["settings_margin_n"]).grid(row=row, column=0, sticky="w", **pad)
    margin_var = tk.IntVar(value=working.margin_n)
    tk.Spinbox(tab_g, from_=0, to=500, textvariable=margin_var, width=8
               ).grid(row=row, column=1, sticky="w", **pad); row += 1

    # ===== Tab: Grouping =====
    from . import nameparser
    tab_r = tk.Frame(nb); nb.add(tab_r, text=S["pe_section_grouping"])

    # Sample filename — shared by both the simple preview and the regex test.
    def _sample_files() -> list[str]:
        out: list[str] = []
        if working.import_folder:
            p = Path(working.import_folder)
            if p.is_dir():
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix.lower() in nameparser._EXTS:
                        out.append(f.name)
        return out
    sample_files = _sample_files()
    sample_default = sample_files[0] if sample_files else "TRIBAL_195_Prä_19.11.25_021.tif"

    # ── Simple parser (delimiters + roles) ──
    simple_var = tk.BooleanVar(value=working.use_simple_parser)
    tk.Checkbutton(tab_r, text=S["pe_use_simple"], variable=simple_var
                   ).pack(anchor="w", padx=14, pady=(12, 4))

    delim_defs = [("_", "pe_delim_underscore"), (" ", "pe_delim_space"),
                  ("-", "pe_delim_dash"), (".", "pe_delim_dot")]
    delim_vars = {ch: tk.BooleanVar(value=ch in working.delimiters) for ch, _ in delim_defs}
    delim_row = tk.Frame(tab_r); delim_row.pack(anchor="w", padx=28, pady=(0, 6))
    tk.Label(delim_row, text=S["pe_delimiters"]).pack(side="left", padx=(0, 8))
    for ch, key in delim_defs:
        tk.Checkbutton(delim_row, text=S[key], variable=delim_vars[ch]
                       ).pack(side="left", padx=4)

    tk.Label(tab_r, text=S["pe_pattern_test"]).pack(anchor="w", padx=28)
    sample_var = tk.StringVar(value=sample_default)
    tk.Entry(tab_r, textvariable=sample_var, width=72).pack(anchor="w", padx=28)

    tk.Label(tab_r, text=S["pe_roles"]).pack(anchor="w", padx=28, pady=(8, 2))
    roles_frame = tk.Frame(tab_r); roles_frame.pack(anchor="w", padx=28)

    role_keys = [nameparser.ROLE_SUBJECT, nameparser.ROLE_TIMEPOINT,
                 nameparser.ROLE_IMAGE_NUM, nameparser.ROLE_IGNORE]
    role_key_to_label = {
        nameparser.ROLE_SUBJECT: S["pe_role_subject"],
        nameparser.ROLE_TIMEPOINT: S["pe_role_timepoint"],
        nameparser.ROLE_IMAGE_NUM: S["pe_role_image_num"],
        nameparser.ROLE_IGNORE: S["pe_role_ignore"],
    }
    role_label_to_key = {v: k for k, v in role_key_to_label.items()}
    role_labels = [role_key_to_label[k] for k in role_keys]
    role_vars: list[tk.StringVar] = []

    simple_preview = tk.Label(tab_r, text="", anchor="w", justify="left",
                              font=font(9))
    simple_preview.pack(anchor="w", padx=28, pady=(6, 8))

    def _selected_delims() -> list[str]:
        return [ch for ch in delim_vars if delim_vars[ch].get()]

    def _current_roles() -> list[str]:
        return [role_label_to_key.get(v.get(), nameparser.ROLE_IGNORE) for v in role_vars]

    def update_simple_preview(*_):
        roles = _current_roles()
        ok_roles, why = nameparser.validate_roles(roles)
        if not ok_roles:
            simple_preview.config(text="⚠ " + why, fg="#c80"); return
        delims = _selected_delims()
        files = sample_files or [sample_var.get()]
        ok = sum(1 for f in files
                 if nameparser.parse(f, delims, roles) is not None)
        total = len(files)
        color = "#0a0" if ok == total and total else "#c80"
        simple_preview.config(
            text=S["pe_preview"] + "  " + S["pe_preview_ok"].format(ok=ok, total=total),
            fg=color)

    def rebuild_roles(*_):
        for w in roles_frame.winfo_children():
            w.destroy()
        role_vars.clear()
        tokens = nameparser.tokenize(sample_var.get(), _selected_delims())
        for i, tok in enumerate(tokens):
            tk.Label(roles_frame, text=tok, width=16, anchor="w", relief="groove"
                     ).grid(row=i, column=0, sticky="w", padx=(0, 6), pady=1)
            if i < len(working.field_roles):
                default_key = working.field_roles[i]
            else:
                default_key = nameparser.ROLE_IGNORE
            var = tk.StringVar(value=role_key_to_label.get(default_key, S["pe_role_ignore"]))
            cb = ttk.Combobox(roles_frame, textvariable=var, values=role_labels,
                              state="readonly", width=14)
            cb.grid(row=i, column=1, sticky="w", pady=1)
            var.trace_add("write", update_simple_preview)
            role_vars.append(var)
        update_simple_preview()

    for ch in delim_vars:
        delim_vars[ch].trace_add("write", rebuild_roles)
    sample_var.trace_add("write", rebuild_roles)
    rebuild_roles()

    # ── Advanced: classic regex ──
    ttk.Separator(tab_r, orient="horizontal").pack(fill="x", padx=14, pady=(10, 6))
    tk.Label(tab_r, text=S["pe_image_pattern"] + "  (Regex)").pack(anchor="w", padx=14)
    pattern_var = tk.StringVar(value=working.image_pattern)
    tk.Entry(tab_r, textvariable=pattern_var, width=72).pack(anchor="w", padx=14)
    tk.Label(tab_r, text=S["pe_pattern_help"], fg="#666", wraplength=620, justify="left",
             font=font(9)).pack(anchor="w", padx=14, pady=(4, 4))
    test_result = tk.Label(tab_r, text="", fg="#0a0", anchor="w", justify="left",
                            font=("Menlo", 10))
    test_result.pack(anchor="w", padx=14, pady=(2, 0))

    def update_regex_test(*_):
        try:
            r = re.compile(pattern_var.get())
        except re.error as e:
            test_result.config(text=f"Regex error: {e}", fg="#c33"); return
        m = r.match(sample_var.get())
        if not m:
            test_result.config(text=S["pe_pattern_nomatch"], fg="#c33"); return
        gd = m.groupdict()
        text = S["pe_pattern_match"] + ":  " + "  ".join(f"{k}={v}" for k, v in gd.items())
        required = {"subject", "timepoint", "image_num"}
        missing = required - set(gd.keys())
        if missing:
            text += f"   ⚠ missing: {sorted(missing)}"
            test_result.config(text=text, fg="#c80")
        else:
            test_result.config(text=text, fg="#0a0")
    pattern_var.trace_add("write", update_regex_test)
    sample_var.trace_add("write", update_regex_test)
    update_regex_test()

    # ===== Tab: Cellpose =====
    tab_cp = tk.Frame(nb); nb.add(tab_cp, text=S["pe_section_cellpose"])
    row = 0
    diam_var = tk.DoubleVar(value=working.cellpose.diameter)
    flow_var = tk.DoubleVar(value=working.cellpose.flow_threshold)
    cellprob_var = tk.DoubleVar(value=working.cellpose.cellprob_threshold)
    use_gpu_var = tk.BooleanVar(value=working.use_gpu)
    # Model choice. The profile stores a NAME; which file that name points to is
    # a per-machine setting (Einstellungen -> Modelle), because a profile travels
    # inside share packages and an absolute path would be meaningless there.
    builtins = list(known_builtins())
    known = builtins + sorted(k for k in (registry or {}) if k not in builtins)
    current = working.cellpose.model or BUILTIN_MODEL
    if current not in known:
        known.append(current)          # keep an unavailable name visible, not silently reset
    tk.Label(tab_cp, text=S["settings_cp_model"]).grid(row=row, column=0, sticky="w", **pad)
    model_var = tk.StringVar(value=current)
    ttk.Combobox(tab_cp, textvariable=model_var, values=known, state="readonly",
                 width=24).grid(row=row, column=1, sticky="w", **pad); row += 1
    missing = current not in builtins and current not in (registry or {})
    tk.Label(tab_cp, fg="#B83A3A" if missing else "#888", justify="left", wraplength=430,
             text=(S["settings_cp_model_missing"].format(name=current) if missing
                   else S["settings_cp_model_hint"])
             ).grid(row=row, column=0, columnspan=2, sticky="w", **pad); row += 1

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
             fg="#888", font=font(9)
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
             font=font(9, "bold")).pack(anchor="w")
    tk.Label(scheme_frame, text=S["pe_color_scheme_help"], anchor="w",
             fg="#666", wraplength=440, font=font(9),
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

    # Contour per type — the non-colour channel for this type (WCAG 1.4.1).
    tk.Label(right, text=S["pe_type_outline"], anchor="w").pack(fill="x")
    outline_var_t = tk.StringVar()
    outline_labels = {k: S[f"pe_outline_{k or 'none'}"] for k in OUTLINE_ORDER}
    label_to_key = {v: k for k, v in outline_labels.items()}
    ttk.Combobox(right, textvariable=outline_var_t, state="readonly",
                 values=[outline_labels[k] for k in OUTLINE_ORDER]
                 ).pack(fill="x", pady=(2, 4))
    tk.Label(right, text=S["pe_type_outline_hint"], fg="#888", justify="left",
             wraplength=250, font=font(9)).pack(fill="x", pady=(0, 8))

    def save_current():
        i = type_state["selected_idx"]
        if not (0 <= i < len(type_state["types"])): return
        td = type_state["types"][i]
        td.name = name_var_t.get().strip() or td.name or td.key
        # User-renamed types lose their i18n_key (their typed-in name is now authoritative)
        if td.i18n_key and td.name != S.get(td.i18n_key, ""):
            td.i18n_key = None
        td.color = color_var_t.get()
        td.outline = label_to_key.get(outline_var_t.get(), "")
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
        outline_var_t.set(outline_labels.get(getattr(td, "outline", "") or "",
                                             outline_labels[""]))

    def _on_type_tree_select(_e):
        sel = type_tree.selection()
        if not sel: return
        idx = int(sel[0]); type_state["selected_idx"] = idx; _refresh_form(idx)
    type_tree.bind("<<TreeviewSelect>>", _on_type_tree_select)
    _populate(0)

    # ===== Tab: Size filter =====
    tab_d = tk.Frame(nb); nb.add(tab_d, text=S["pe_section_size"])
    sf_enabled_var = tk.BooleanVar(value=working.size_filter.enabled)
    sf_min_var = tk.DoubleVar(value=working.size_filter.min_factor)
    sf_max_var = tk.DoubleVar(value=working.size_filter.max_factor)
    tk.Checkbutton(tab_d, text=S["pe_size_enabled"], variable=sf_enabled_var
                   ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=8)
    tk.Label(tab_d, text=S["pe_size_min"]).grid(row=1, column=0, sticky="w", padx=14, pady=4)
    tk.Spinbox(tab_d, from_=0.05, to=0.95, increment=0.05, textvariable=sf_min_var, width=10
               ).grid(row=1, column=1, sticky="w", padx=4, pady=4)
    tk.Label(tab_d, text=S["pe_size_max"]).grid(row=2, column=0, sticky="w", padx=14, pady=4)
    tk.Spinbox(tab_d, from_=1.05, to=5.0, increment=0.05, textvariable=sf_max_var, width=10
               ).grid(row=2, column=1, sticky="w", padx=4, pady=4)
    tk.Label(tab_d, text=S["pe_size_hint"], fg="#888", justify="left", wraplength=460
             ).grid(row=3, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))

    ttk.Separator(tab_d, orient="horizontal").grid(row=4, column=0, columnspan=2,
                                                   sticky="ew", padx=14, pady=12)
    outlines_var = tk.BooleanVar(value=working.type_outlines_enabled)
    tk.Checkbutton(tab_d, text=S["pe_outlines_enabled"], variable=outlines_var
                   ).grid(row=5, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 2))
    tk.Label(tab_d, text=S["pe_outlines_hint"], fg="#888", justify="left", wraplength=460
             ).grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(0, 10))
    tk.Label(tab_d, text=S["pe_overlay_alpha"]).grid(row=7, column=0, sticky="w",
                                                     padx=14, pady=4)
    alpha_var = tk.DoubleVar(value=working.overlay_alpha)
    tk.Spinbox(tab_d, from_=0.15, to=1.0, increment=0.05, textvariable=alpha_var,
               width=10).grid(row=7, column=1, sticky="w", padx=4, pady=4)
    tk.Label(tab_d, text=S["pe_overlay_alpha_hint"], fg="#888", justify="left",
             wraplength=460).grid(row=8, column=0, columnspan=2, sticky="w",
                                  padx=14, pady=(0, 4))

    # ===== Tab: Appearance =====
    tab_a = tk.Frame(nb); nb.add(tab_a, text=S["pe_section_appear"])
    show_margin_var = tk.BooleanVar(value=working.show_margin_overlay)
    tk.Checkbutton(tab_a, text=S["settings_show_margin_overlay"], variable=show_margin_var
                   ).grid(row=0, column=0, sticky="w", padx=16, pady=8)
    stats_enabled_var = tk.BooleanVar(value=working.stats_enabled)
    tk.Checkbutton(tab_a, text=S["pe_stats_enabled"], variable=stats_enabled_var
                   ).grid(row=1, column=0, sticky="w", padx=16, pady=8)
    overview_xlsx_var = tk.BooleanVar(value=working.overview_xlsx)
    tk.Checkbutton(tab_a, text=S["pe_overview_xlsx"], variable=overview_xlsx_var
                   ).grid(row=2, column=0, sticky="w", padx=16, pady=8)
    overlay_png_var = tk.BooleanVar(value=working.overlay_png)
    tk.Checkbutton(tab_a, text=S["pe_overlay_png"], variable=overlay_png_var
                   ).grid(row=3, column=0, sticky="w", padx=16, pady=8)
    copy_orig_var = tk.BooleanVar(value=working.copy_originals_to_outputs)
    tk.Checkbutton(tab_a, text=S["pe_copy_originals"], variable=copy_orig_var
                   ).grid(row=4, column=0, sticky="w", padx=16, pady=8)

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
        use_simple_parser=bool(simple_var.get()),
        delimiters=_selected_delims() or ["_"],
        field_roles=_current_roles(),
        target_cells=int(target_var.get()),
        images_per_subject=int(perimg_var.get()),
        stats_enabled=bool(stats_enabled_var.get()),
        overview_xlsx=bool(overview_xlsx_var.get()),
        overlay_png=bool(overlay_png_var.get()),
        copy_originals_to_outputs=bool(copy_orig_var.get()),
        cell_types=type_state["types"],
        cellpose=CellposeParams(
            diameter=_clampf(diam_var, 5.0, 500.0, working.cellpose.diameter),
            flow_threshold=_clampf(flow_var, 0.0, 3.0, working.cellpose.flow_threshold),
            cellprob_threshold=_clampf(
                cellprob_var, -6.0, 6.0, working.cellpose.cellprob_threshold),
            model=model_var.get() or BUILTIN_MODEL,
        ),
        use_gpu=bool(use_gpu_var.get()),
        margin_n=int(margin_var.get()),
        show_margin_overlay=bool(show_margin_var.get()),
        size_filter=SizeFilter(
            enabled=bool(sf_enabled_var.get()),
            # Clamped like the Cellpose params (EH-3): a typed-in nonsense value
            # must not silently exclude every cell in the study.
            min_factor=_clampf(sf_min_var, 0.05, 0.95, working.size_filter.min_factor),
            max_factor=_clampf(sf_max_var, 1.05, 5.0, working.size_filter.max_factor),
        ),
        overlay_alpha=_clampf(alpha_var, 0.15, 1.0, working.overlay_alpha),
        type_outlines_enabled=bool(outlines_var.get()),
        sidebar_open_groups=working.sidebar_open_groups,
        details_collapsed=working.details_collapsed,
        recent_folders=working.recent_folders,
        active_type_key=working.active_type_key,
        created_at=working.created_at,
    )
    return new_profile


# ─────────────────────────────────────────────
# App-Tab Settings (globals only)
# ─────────────────────────────────────────────
def model_registry_dialog(parent: tk.Misc, registry: dict, strings: dict) -> dict | None:
    """Map model names to files ON THIS MACHINE. Returns the new mapping, or None.

    Cellpose 4 ships exactly one model (cpsam); everything else is a file the
    user trained or downloaded. Naming those files means a profile can ask for
    "the TRIBAL model" without carrying a path that only exists on one computer.
    """
    S = strings
    reg = dict(registry or {})
    result: dict = {"saved": False}
    top = tk.Toplevel(parent); top.title(S["models_title"])
    top.transient(parent); top.grab_set(); top.minsize(560, 320)
    frm = tk.Frame(top, padx=14, pady=14); frm.pack(fill="both", expand=True)
    tk.Label(frm, text=S["models_hint"], justify="left", wraplength=520, fg="#888"
             ).pack(anchor="w", pady=(0, 10))

    cols = ("name", "path")
    tree = ttk.Treeview(frm, columns=cols, show="headings", height=8)
    tree.heading("name", text=S["models_col_name"])
    tree.heading("path", text=S["models_col_file"])
    tree.column("name", width=150, stretch=False)
    tree.column("path", width=380)
    tree.pack(fill="both", expand=True)

    def refresh():
        for iid in tree.get_children():
            tree.delete(iid)
        for bi in known_builtins():
            tree.insert("", "end", iid=bi, values=(bi, S["models_builtin"]))
        for name in sorted(reg):
            missing = "" if Path(reg[name]).is_file() else f"  ({S['models_file_missing']})"
            tree.insert("", "end", iid=name, values=(name, reg[name] + missing))

    def on_add():
        path = filedialog.askopenfilename(
            parent=top, title=S["models_pick_file"],
            filetypes=[(S["models_filetype"], "*"), ])
        if not path:
            return
        suggested = Path(path).stem
        name = simpledialog.askstring(S["models_title"], S["models_name_prompt"],
                                      initialvalue=suggested, parent=top)
        if not name:
            return
        name = name.strip()
        if not name or name in known_builtins():
            info_dialog(top, S["models_title"], S["models_name_reserved"])
            return
        reg[name] = path
        refresh()

    def on_remove():
        sel = tree.selection()
        if not sel or sel[0] in known_builtins():
            return
        reg.pop(sel[0], None)
        refresh()

    btns = tk.Frame(frm); btns.pack(fill="x", pady=(10, 0))
    tk.Button(btns, text=S["models_add"], command=on_add).pack(side="left")
    tk.Button(btns, text=S["models_remove"], command=on_remove).pack(side="left", padx=6)
    tk.Button(btns, text=S["settings_save"], width=12,
              command=lambda: (result.__setitem__("saved", True), top.destroy())
              ).pack(side="right", padx=4)
    tk.Button(btns, text=S["settings_cancel"], width=12,
              command=top.destroy).pack(side="right", padx=4)
    top.bind("<Escape>", lambda _e: top.destroy())
    refresh()
    _center_on_parent(top, parent); parent.wait_window(top)
    return reg if result["saved"] else None


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

    # WCAG 1.4.4 — the user must be able to enlarge the text. Applied at
    # startup by theme.set_font_scale(), so it needs a restart like the theme.
    tk.Label(top, text=S["settings_font_scale"]).grid(row=row, column=0, sticky="w", **pad)
    font_disp = {S[f"settings_font_{int(v * 100)}"]: v for v in FONT_SCALE_CHOICES}
    font_disp_inv = {v: k for k, v in font_disp.items()}
    font_var = tk.StringVar(value=font_disp_inv.get(
        settings.font_scale, S["settings_font_100"]))
    ttk.Combobox(top, textvariable=font_var, values=list(font_disp.keys()),
                 state="readonly", width=10
                 ).grid(row=row, column=1, sticky="w", **pad)
    row += 1

    tk.Label(top, text=S["settings_reviewer"]).grid(row=row, column=0, sticky="w", **pad)
    reviewer_var = tk.StringVar(value=settings.reviewer_name)
    tk.Entry(top, textvariable=reviewer_var, width=18).grid(row=row, column=1, sticky="w", **pad)
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
        reviewer_name=reviewer_var.get().strip(),
        font_scale=font_disp.get(font_var.get(), settings.font_scale),
    )
    # A font-size change needs the same restart notice as a theme change, so it
    # rides on the third flag rather than adding a fourth to every caller.
    needs_restart = (new_theme != settings.theme
                     or new.font_scale != settings.font_scale)
    return (new, new_lang != settings.language, needs_restart)


# ─────────────────────────────────────────────
# Batch dialog
# ─────────────────────────────────────────────
def overview_preview_dialog(parent: tk.Misc, header: list, rows: list,
                            path, strings: dict, max_rows: int = 12) -> None:
    """Small read-only preview of the overview 'Daten' sheet after export."""
    S = strings
    top = tk.Toplevel(parent); top.title(S["overview_preview_title"])
    top.transient(parent); top.grab_set(); top.minsize(680, 360)
    frm = tk.Frame(top, padx=12, pady=12); frm.pack(fill="both", expand=True)
    tk.Label(frm, text=S["overview_preview_path"].format(path=path), anchor="w",
             fg="#888", wraplength=640, justify="left").pack(fill="x", pady=(0, 6))

    cols = [f"c{i}" for i in range(len(header))]
    tree = ttk.Treeview(frm, columns=cols, show="headings",
                        height=min(max_rows, max(1, len(rows))))
    for i, h in enumerate(header):
        tree.heading(cols[i], text=str(h))
        tree.column(cols[i], width=84, anchor="center", stretch=False)
    for row in rows[:max_rows]:
        tree.insert("", "end", values=["" if v is None else v for v in row])
    xsb = ttk.Scrollbar(frm, orient="horizontal", command=tree.xview)
    tree.configure(xscrollcommand=xsb.set)
    tree.pack(fill="both", expand=True); xsb.pack(fill="x")
    if len(rows) > max_rows:
        tk.Label(frm, text=S["overview_preview_more"].format(n=len(rows) - max_rows),
                 fg="#888").pack(anchor="w", pady=(4, 0))
    btn_row = tk.Frame(frm); btn_row.pack(pady=(8, 0))
    # The workbook was written to `path` moments ago, so "save a copy" is a copy —
    # no need to rebuild it here (the Auswertung menu entry does that for the
    # any-time case).
    tk.Button(btn_row, text=S["overview_save_as"], width=18,
              command=lambda: save_overview_copy(top, Path(path), S)
              ).pack(side="left", padx=6)
    tk.Button(btn_row, text="OK", width=12, command=top.destroy).pack(side="left", padx=6)
    top.bind("<Escape>", lambda e: top.destroy())
    _center_on_parent(top, parent); parent.wait_window(top)


def ask_overview_destination(parent: tk.Misc, strings: dict) -> Path | None:
    """Save-as dialog for the overview workbook. None if the user cancels."""
    chosen = filedialog.asksaveasfilename(
        parent=parent,
        title=strings["overview_save_as"],
        initialfile="Uebersicht.xlsx",
        defaultextension=".xlsx",
        filetypes=[(strings["overview_filetype"], "*.xlsx")],
    )
    return Path(chosen) if chosen else None


def save_overview_copy(parent: tk.Misc, src: Path, strings: dict) -> None:
    """Copy an already-written workbook to a user-chosen location."""
    dest = ask_overview_destination(parent, strings)
    if dest is None:
        return
    import shutil
    try:
        shutil.copy2(src, dest)
    except OSError as e:
        info_dialog(parent, strings["overview_save_as"],
                    strings["overview_save_err"].format(err=e))
        return
    info_dialog(parent, strings["overview_save_as"],
                strings["overview_save_ok"].format(path=dest))


def batch_dialog(
    parent: tk.Misc, folder: Path, images: list[Path],
    profile: ProfileData, strings: dict,
    image_status_fn: Callable[[Path], str],
    on_done: Callable[[], None],
    grouped=None,
    model_registry: dict | None = None,
) -> None:
    S = strings
    from .grouping import (ImageGroupInfo, UNGROUPED_TIMEPOINT, UNGROUPED_SUBJECT)
    from .counts import image_counts

    top = tk.Toplevel(parent); top.title(S["batch_title"])
    top.transient(parent); top.grab_set(); top.minsize(640, 600)

    target = max(1, getattr(profile, "target_cells", 2000))
    avg_seconds_per_image = 30.0

    # ── Build (timepoint -> [(subject, [ImageGroupInfo])]) preserving order ──
    info_by_name: dict[str, ImageGroupInfo] = {}
    subs_by_tp: dict[str, list[tuple[str, list]]] = {}
    tp_order: list[str] = []
    if grouped is not None and getattr(grouped, "tree", None):
        for tp in grouped.timepoints():
            for sub in grouped.subjects_in(tp):
                infos = list(grouped.tree[tp][sub])
                subs_by_tp.setdefault(tp, [])
                if tp not in tp_order:
                    tp_order.append(tp)
                subs_by_tp[tp].append((sub, infos))
                for info in infos:
                    info_by_name[info.path.name] = info
    # Any image not represented in `grouped` -> ungrouped bucket.
    leftover = [p for p in images if p.name not in info_by_name]
    if leftover:
        infos = [ImageGroupInfo(path=p, timepoint=UNGROUPED_TIMEPOINT,
                                subject=UNGROUPED_SUBJECT, image_num=None,
                                is_ungrouped=True) for p in leftover]
        subs_by_tp.setdefault(UNGROUPED_TIMEPOINT, [])
        if UNGROUPED_TIMEPOINT not in tp_order:
            tp_order.append(UNGROUPED_TIMEPOINT)
        subs_by_tp[UNGROUPED_TIMEPOINT].append((UNGROUPED_SUBJECT, infos))
        for info in infos:
            info_by_name[info.path.name] = info

    checked = {nm: not has_cache(folder, info.path) for nm, info in info_by_name.items()}
    n_total = len(info_by_name)

    tp_values = list(tp_order)
    sub_values = list(dict.fromkeys(
        sub for tp in tp_order for sub, _ in subs_by_tp[tp]))

    def _disp_tp(tp): return S["sb_ungrouped"] if tp == UNGROUPED_TIMEPOINT else tp
    def _disp_sub(sub): return S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT else sub

    container = tk.Frame(top); container.pack(fill="both", expand=True, padx=12, pady=12)

    # ── Row: dropdown quick-filters (Zeitpunkt / Proband) ──
    filt = tk.Frame(container); filt.pack(fill="x", pady=(0, 6))
    tk.Label(filt, text=S["batch_filter_tp"]).pack(side="left")
    tp_combo = ttk.Combobox(filt, state="readonly", width=12,
                            values=[S["batch_filter_all"]] + [_disp_tp(t) for t in tp_values])
    tp_combo.current(0); tp_combo.pack(side="left", padx=(2, 12))
    tk.Label(filt, text=S["batch_filter_sub"]).pack(side="left")
    sub_combo = ttk.Combobox(filt, state="readonly", width=10,
                             values=[S["batch_filter_all"]] + [_disp_sub(s) for s in sub_values])
    sub_combo.current(0); sub_combo.pack(side="left", padx=2)

    def _group_visible(tp, sub):
        if tp_combo.current() > 0 and tp != tp_values[tp_combo.current() - 1]:
            return False
        if sub_combo.current() > 0 and sub != sub_values[sub_combo.current() - 1]:
            return False
        return True

    def _visible_infos():
        out = []
        for tp in tp_order:
            for sub, infos in subs_by_tp[tp]:
                if _group_visible(tp, sub):
                    out += infos
        return out

    # ── Row: status quick-selects (operate on the visible/filtered set) ──
    bar = tk.Frame(container); bar.pack(fill="x", pady=(0, 4))
    def select_all():
        for i in _visible_infos(): checked[i.path.name] = True
        _update_marks(); _refresh_footer()
    def select_unsegmented():
        for i in _visible_infos(): checked[i.path.name] = (image_status_fn(i.path) == "unsegmented")
        _update_marks(); _refresh_footer()
    def select_unfinished():
        for i in _visible_infos(): checked[i.path.name] = (image_status_fn(i.path) != "finished")
        _update_marks(); _refresh_footer()
    def invert_selection():
        for i in _visible_infos(): checked[i.path.name] = not checked[i.path.name]
        _update_marks(); _refresh_footer()
    def select_first_n_per_group():
        try: n = max(0, int(per_n_var.get()))
        except (tk.TclError, ValueError): return
        first_n = set()
        for tp in tp_order:
            for sub, infos in subs_by_tp[tp]:
                if _group_visible(tp, sub):
                    first_n.update(i.path.name for i in infos[:n])
        for i in _visible_infos():
            nm = i.path.name
            checked[nm] = checked[nm] and (nm in first_n)
        _update_marks(); _refresh_footer()
    tk.Button(bar, text=S["batch_select_all"], command=select_all).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_unsegmented"], command=select_unsegmented).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_unfinished"], command=select_unfinished).pack(side="left", padx=2)
    tk.Button(bar, text=S["batch_select_invert"], command=invert_selection).pack(side="left", padx=2)

    bar2 = tk.Frame(container); bar2.pack(fill="x", pady=(0, 6))
    tk.Label(bar2, text=S["batch_per_subject_label"]).pack(side="left", padx=(0, 4))
    per_n_var = tk.IntVar(value=max(1, getattr(profile, "images_per_subject", 13)))
    tk.Spinbox(bar2, from_=1, to=10000, increment=1, textvariable=per_n_var, width=6
               ).pack(side="left", padx=2)
    tk.Button(bar2, text=S["batch_select_first_n"], command=select_first_n_per_group).pack(side="left", padx=2)

    # ── Hierarchical tree: Zeitpunkt > Proband > Bild, with check column ──
    tree_frame = tk.Frame(container); tree_frame.pack(fill="both", expand=True)
    tree = ttk.Treeview(tree_frame, columns=("check", "status"),
                        show="tree headings", height=15)
    tree.heading("#0", text=S["batch_col_tree"]); tree.column("#0", width=340, anchor="w", stretch=True)
    tree.heading("check", text="✓"); tree.column("check", width=44, anchor="center", stretch=False)
    tree.heading("status", text=""); tree.column("status", width=44, anchor="center", stretch=False)
    sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")
    tree.pack(side="left", fill="both", expand=True)
    icons = {"unsegmented": "○", "segmented": "◐", "in_progress": "●", "finished": "◉"}

    def _grp_mark(infos):
        vals = [checked[i.path.name] for i in infos]
        if vals and all(vals): return "✓"
        if any(vals): return "–"
        return " "

    def _rebuild_tree():
        tree.delete(*tree.get_children())
        for tp in tp_order:
            vis = [(sub, infos) for sub, infos in subs_by_tp[tp] if _group_visible(tp, sub)]
            if not vis: continue
            tp_infos = [i for _, infos in vis for i in infos]
            tp_iid = f"tp::{tp}"
            tree.insert("", "end", iid=tp_iid, text=_disp_tp(tp), open=True,
                        values=(_grp_mark(tp_infos), ""))
            for sub, infos in vis:
                sub_iid = f"sub::{tp}::{sub}"
                tree.insert(tp_iid, "end", iid=sub_iid,
                            text=f"{_disp_sub(sub)}  ({len(infos)})", open=False,
                            values=(_grp_mark(infos), ""))
                for i in infos:
                    tree.insert(sub_iid, "end", iid="img::" + i.path.name,
                                text=i.path.name,
                                values=("✓" if checked[i.path.name] else " ",
                                        icons[image_status_fn(i.path)]))

    def _infos_of(iid):
        if iid.startswith("img::"):
            return [info_by_name[iid[5:]]]
        if iid.startswith("sub::"):
            _, tp, sub = iid.split("::", 2)
            return [i for s, infos in subs_by_tp[tp] if s == sub for i in infos]
        if iid.startswith("tp::"):
            tp = iid[4:]
            return [i for sub, infos in subs_by_tp[tp]
                    if _group_visible(tp, sub) for i in infos]
        return []

    def _update_marks():
        for tp_iid in tree.get_children(""):
            tree.set(tp_iid, "check", _grp_mark(_infos_of(tp_iid)))
            for sub_iid in tree.get_children(tp_iid):
                tree.set(sub_iid, "check", _grp_mark(_infos_of(sub_iid)))
                for img_iid in tree.get_children(sub_iid):
                    nm = img_iid[5:]
                    tree.set(img_iid, "check", "✓" if checked[nm] else " ")

    def _toggle(evt):
        iid = tree.identify_row(evt.y)
        if not iid or tree.identify_column(evt.x) != "#1":
            return
        infos = _infos_of(iid)
        if not infos: return
        if iid.startswith("img::"):
            nm = iid[5:]; checked[nm] = not checked[nm]
        else:
            newval = not all(checked[i.path.name] for i in infos)
            for i in infos: checked[i.path.name] = newval
        _update_marks(); _refresh_footer()
    tree.bind("<Button-1>", _toggle)

    def _on_filter(*_):
        _rebuild_tree(); _refresh_footer()
    tp_combo.bind("<<ComboboxSelected>>", _on_filter)
    sub_combo.bind("<<ComboboxSelected>>", _on_filter)

    # ── Options: Bis-Ziel mode + keep-awake ──
    opts = tk.Frame(container); opts.pack(fill="x", pady=(6, 0))
    target_mode_var = tk.BooleanVar(value=False)
    tk.Checkbutton(opts, text=S["batch_target_mode"].format(target=target),
                   variable=target_mode_var).pack(side="left")
    awake_var = tk.BooleanVar(value=True)
    tk.Checkbutton(opts, text=S["batch_keep_awake"], variable=awake_var).pack(side="left", padx=(16, 0))

    footer = tk.Frame(container); footer.pack(fill="x", pady=(8, 0))
    footer_left = tk.Label(footer, text="", fg="#888", anchor="w", font=font(10))
    footer_left.pack(side="left", fill="x", expand=True)
    tk.Button(footer, text=S["batch_cancel"], width=12, command=top.destroy).pack(side="right", padx=4)
    start_btn = tk.Button(footer, text=S["batch_start"], width=12)
    start_btn.pack(side="right", padx=4)

    def _refresh_footer():
        n = sum(1 for v in checked.values() if v)
        est_min = max(1, int(round(n * avg_seconds_per_image / 60.0)))
        footer_left.config(text=S["batch_n_selected"].format(n=n, total=n_total, minutes=est_min))

    _rebuild_tree(); _refresh_footer()

    # ── Run machinery ──
    state = {"cancel": False, "running": False}

    def _on_escape(_evt=None):
        """a11y 2.1.2 — Escape must always lead out of the modal. While a batch
        is running it requests the same cancel the button does (the loop stops
        after the current image); before it starts it just closes the dialog."""
        if state["running"]:
            state["cancel"] = True
        else:
            top.destroy()
    top.bind("<Escape>", _on_escape)

    def _start_awake():
        # Returns an opaque token for _stop_awake, or None. Cross-platform:
        # macOS -> caffeinate (auto-quits with our PID via -w, so a crash can't
        # leave it running); Windows -> SetThreadExecutionState.
        if not awake_var.get():
            return None
        import sys, os
        try:
            if sys.platform == "darwin":
                import subprocess
                proc = subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())])
                return ("mac", proc)
            if sys.platform == "win32":
                import ctypes
                ES_CONTINUOUS, ES_SYSTEM, ES_DISPLAY = 0x80000000, 0x1, 0x2
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM | ES_DISPLAY)
                return ("win", None)
        except Exception:
            return None
        return None

    def _stop_awake(token):
        if not token:
            return
        kind, obj = token
        try:
            if kind == "mac" and obj is not None:
                obj.terminate()
            elif kind == "win":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)  # ES_CONTINUOUS
        except Exception:
            pass

    def _progress_ui(maximum):
        state["running"] = True     # from here Escape means "cancel the run"
        for w in container.winfo_children(): w.destroy()
        tk.Label(container, text=S["batch_progress_title"],
                 font=font(13, "bold")).pack(pady=(0, 10))
        lbl = tk.Label(container, text="", font=font(11)); lbl.pack(pady=(0, 6))
        pb = ttk.Progressbar(container, orient="horizontal", mode="determinate",
                             length=460, maximum=max(1, maximum))
        pb.pack(pady=(0, 14))
        def on_cancel_after():
            state["cancel"] = True; cancel_btn.config(state="disabled", text="…")
        cancel_btn = tk.Button(container, text=S["batch_progress_cancel"], command=on_cancel_after)
        cancel_btn.pack()
        return lbl, pb

    def _summary(done, skipped, errors, extra=""):
        for w in container.winfo_children(): w.destroy()
        tk.Label(container, text=S["batch_summary_title"],
                 font=font(13, "bold")).pack(pady=(0, 10))
        txt = (S["batch_summary_done"].format(n=done) + "\n"
               + S["batch_summary_skipped"].format(n=skipped))
        if extra: txt += "\n" + extra
        if errors:
            txt += "\n\n" + S["batch_summary_errors"].format(n=len(errors))
            for nm, e in errors[:20]: txt += f"\n  – {nm}: {e}"
        tk.Label(container, text=txt, justify="left", wraplength=540).pack(pady=(0, 14))
        tk.Button(container, text="OK", width=12,
                  command=lambda: (on_done(), top.destroy())).pack()

    def _compute_one(info):
        """Segment one image. Called from inside work(), i.e. ALREADY on the
        worker thread that _run_worker starts — so it must not touch Tk at all.

        Merge note: this used to wrap the call in run_in_thread_pumping(), which
        pumps the Tk event loop. Combined with the queue-based runner below that
        would have meant calling widget.update() from a background thread, which
        tkinter does not support. The pumping helper is still right for the
        single-image path in app.py, where the caller IS the main thread.
        """
        run_for_image(folder, info.path, load_image(info.path),
                      profile.cellpose, force=False,
                      use_gpu=profile.use_gpu,
                      model_registry=model_registry)

    def _active_count(info):
        c = image_counts(folder, info, profile)
        return (c or {}).get("rbc", 0) or 0

    # ── Threaded batch runner ────────────────────────────────────────────
    # Cellpose runs for many seconds per image. Doing that on the Tk main
    # thread freezes the window — on Windows it goes grey with "(keine
    # Rückmeldung)" / "Not Responding" and looks like a crash. Instead the
    # heavy loop runs in a worker thread that only ever touches a queue; the
    # main thread drains that queue from a periodic after()-poll and is the
    # only thread that talks to Tk (tkinter is not thread-safe).
    def _run_worker(total, work):
        """`work(progress, is_cancelled)` runs in a background thread and must
        NOT touch Tk (tkinter is not thread-safe). It pushes updates through a
        queue; the main thread drains the queue from a periodic after()-poll
        and is the only one that talks to widgets. Returns (done, skipped,
        errors[, extra])."""
        lbl, pb = _progress_ui(total)
        proc = _start_awake()
        q: "queue.Queue" = queue.Queue()

        def progress(value, text=None):
            q.put(("progress", value, text))

        def runner():
            try:
                result = work(progress, lambda: state["cancel"])
            except Exception as e:  # never let a worker exception vanish silently
                result = (0, 0, [("batch", f"{type(e).__name__}: {e}")])
            q.put(("done", result))

        def poll():
            try:
                while True:
                    item = q.get_nowait()
                    if item[0] == "progress":
                        _, value, text = item
                        try:
                            if value is not None:
                                pb["value"] = value
                            if text is not None:
                                lbl.config(text=text)
                        except tk.TclError:
                            return  # dialog closed mid-run
                    else:  # ("done", result)
                        _stop_awake(proc)
                        result = item[1]
                        done, skipped, errors = result[0], result[1], result[2]
                        extra = result[3] if len(result) > 3 else ""
                        try:
                            _summary(done, skipped, errors, extra=extra)
                        except tk.TclError:
                            pass
                        return
            except queue.Empty:
                pass
            try:
                top.after(80, poll)
            except tk.TclError:
                _stop_awake(proc)

        threading.Thread(target=runner, daemon=True).start()
        top.after(80, poll)  # scheduled from the main thread — safe

    def _enter_progress_normal(sel_infos):
        n = len(sel_infos)

        def work(progress, is_cancelled):
            errors, skipped, done = [], 0, 0
            for idx, info in enumerate(sel_infos, start=1):
                if is_cancelled(): break
                progress(idx - 1, S["batch_progress_running"].format(
                    i=idx, n=n, name=info.path.name))
                if has_cache(folder, info.path):
                    skipped += 1; progress(idx); continue
                try:
                    _compute_one(info); done += 1
                except Exception as e:
                    errors.append((info.path.name, str(e)))
                progress(idx)
            return done, skipped, errors

        _run_worker(n, work)

    def _enter_progress_target(sel_groups):
        total = sum(len(infos) for _, _, infos in sel_groups)

        def work(progress, is_cancelled):
            errors, skipped, done, touched, n_groups = [], 0, 0, 0, 0
            for tp, sub, infos in sel_groups:
                if is_cancelled(): break
                n_groups += 1
                cumulative = 0
                for info in infos:                      # image_num order
                    if is_cancelled(): break
                    if cumulative >= target:
                        break                            # group target reached
                    touched += 1
                    progress(touched, S["batch_progress_target"].format(
                        tp=_disp_tp(tp), sub=_disp_sub(sub), cum=cumulative, target=target))
                    if has_cache(folder, info.path):
                        skipped += 1
                    else:
                        try:
                            _compute_one(info); done += 1
                        except Exception as e:
                            errors.append((info.path.name, str(e))); continue
                    cumulative += _active_count(info)
            return done, skipped, errors, S["batch_summary_groups"].format(g=n_groups)

        _run_worker(total, work)

    def _on_start():
        if target_mode_var.get():
            sel = [(tp, sub, infos) for tp in tp_order
                   for sub, infos in subs_by_tp[tp]
                   if any(checked[i.path.name] for i in infos)]
            if sel: _enter_progress_target(sel)
        else:
            sel = [info_by_name[nm] for nm, v in checked.items() if v]
            if sel: _enter_progress_normal(sel)
    start_btn.config(command=_on_start)

    _center_on_parent(top, parent); parent.wait_window(top)
