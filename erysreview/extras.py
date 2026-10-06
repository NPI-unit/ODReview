"""Dialogs/windows for the v17 features: share package create/import, the
in-app statistics tab, and the progress dashboard.

Kept separate from dialogs.py so the (already large) core dialog module stays
focused on the per-image editing flow.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

from . import package as pkg
from . import stats as statsmod
from .cellpose_runner import has_cache
from .evaluation import inclusion_with_overrides
from .counts import image_counts
from .dialogs import _center_on_parent, confirm_dialog, info_dialog
from .grouping import (
    GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT,
)
from .persistence import ReviewState
from .profile import ProfileData
from .theme import font


def eval_select_dialog(parent, folder, images_in_group, profile: ProfileData,
                       strings: dict) -> bool:
    """Per-group picker for the images that count toward the target.

    Returns True if any image's eval flag was changed and saved.
    """
    S = strings
    target = max(1, profile.target_cells)

    # Precompute active counts (cached) + original overrides.
    counts: dict[str, int | None] = {}
    originals: dict[str, bool | None] = {}
    done_orig: dict[str, bool] = {}
    for info in images_in_group:
        stem = info.path.stem
        if has_cache(folder, info.path):
            c = image_counts(folder, info, profile)
            counts[stem] = c["rbc"] if c else None
        else:
            counts[stem] = None
        rv = ReviewState.load(folder, stem)
        originals[stem] = rv.eval_include
        # Same load, second field: the status tab needs it and a separate pass
        # would double the disk work of opening this dialog.
        done_orig[stem] = bool(rv.is_finished)
    overrides = dict(originals)
    done = dict(done_orig)

    top = tk.Toplevel(parent); top.title(S["eval_dlg_title"])
    top.transient(parent); top.grab_set(); top.minsize(560, 460)

    nb = ttk.Notebook(top)
    nb.pack(fill="both", expand=True, padx=12, pady=(12, 4))
    tab_eval = tk.Frame(nb); nb.add(tab_eval, text=S["eval_dlg_tab_eval"])
    tab_status = tk.Frame(nb); nb.add(tab_status, text=S["eval_dlg_tab_status"])

    bar = tk.Frame(tab_eval); bar.pack(fill="x", padx=0, pady=(8, 4))
    def set_all(val):
        for info in images_in_group:
            if counts.get(info.path.stem) is not None:
                overrides[info.path.stem] = val
        _refresh()
    def set_auto():
        for k in overrides:
            overrides[k] = None
        _refresh()
    tk.Button(bar, text=S["eval_dlg_auto"], command=set_auto).pack(side="left", padx=2)
    tk.Button(bar, text=S["eval_dlg_all"], command=lambda: set_all(True)).pack(side="left", padx=2)
    tk.Button(bar, text=S["eval_dlg_none"], command=lambda: set_all(False)).pack(side="left", padx=2)

    tf = tk.Frame(tab_eval); tf.pack(fill="both", expand=True)
    cols = ("count", "status", "image", "active", "cum")
    tree = ttk.Treeview(tf, columns=cols, show="headings", height=14)
    tree.heading("count", text="✓"); tree.column("count", width=36, anchor="center", stretch=False)
    tree.heading("status", text=""); tree.column("status", width=30, anchor="center", stretch=False)
    tree.heading("image", text=S["eval_dlg_col_image"]); tree.column("image", anchor="w", stretch=True)
    tree.heading("active", text=S["eval_dlg_col_active"]); tree.column("active", width=70, anchor="e", stretch=False)
    tree.heading("cum", text=S["eval_dlg_col_cum"]); tree.column("cum", width=80, anchor="e", stretch=False)
    sb = ttk.Scrollbar(tf, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y"); tree.pack(side="left", fill="both", expand=True)

    footer = tk.Frame(tab_eval); footer.pack(fill="x", pady=8)
    summary = tk.Label(footer, text="", anchor="w", font=font(11, "bold"))
    summary.pack(side="left")

    def _refresh():
        included, _ = inclusion_with_overrides(images_in_group, counts, overrides, target)
        for iid in tree.get_children():
            tree.delete(iid)
        cum = 0
        total_active = 0
        for info in images_in_group:
            stem = info.path.stem
            n = counts.get(stem)
            is_in = stem in included
            if is_in and n is not None:
                cum += n
            if n is not None:
                total_active += n if is_in else 0
            status = "○" if n is None else "●"
            label = (str(info.image_num) if info.image_num is not None else info.path.name)
            tree.insert("", "end", iid=stem, values=(
                "✓" if is_in else " ", status, f"{label}  {info.path.name}",
                "—" if n is None else n,
                "—" if n is None else (cum if is_in else ""),
            ))
        _, total = inclusion_with_overrides(images_in_group, counts, overrides, target)
        summary.config(text=S["eval_dlg_summary"].format(n=total, target=target))

    def _toggle(evt):
        stem = tree.identify_row(evt.y)
        if not stem or counts.get(stem) is None:
            return
        included, _ = inclusion_with_overrides(images_in_group, counts, overrides, target)
        # Toggle effective membership: in -> exclude, out -> include.
        overrides[stem] = False if stem in included else True
        _refresh()
    tree.bind("<Button-1>", _toggle)
    _refresh()

    # ── Status tab: which images are finished ──────────────────────────
    tk.Label(tab_status, text=S["status_dlg_hint"], anchor="w", justify="left",
             wraplength=520).pack(fill="x", pady=(10, 6))
    sbar = tk.Frame(tab_status); sbar.pack(fill="x", pady=(0, 4))

    def set_done(val):
        # Only segmented images: declaring an image finished that has no mask
        # would claim a review nobody has done.
        for i in images_in_group:
            if counts.get(i.path.stem) is not None:
                done[i.path.stem] = val
        _refresh_status()

    def invert_done():
        for i in images_in_group:
            st = i.path.stem
            if counts.get(st) is not None:
                done[st] = not done.get(st, False)
        _refresh_status()

    tk.Button(sbar, text=S["status_dlg_all"], command=lambda: set_done(True)
              ).pack(side="left", padx=2)
    tk.Button(sbar, text=S["status_dlg_none"], command=lambda: set_done(False)
              ).pack(side="left", padx=2)
    tk.Button(sbar, text=S["status_dlg_invert"], command=invert_done
              ).pack(side="left", padx=2)

    stf = tk.Frame(tab_status); stf.pack(fill="both", expand=True)
    scols = ("done", "status", "image")
    stree = ttk.Treeview(stf, columns=scols, show="headings", height=14)
    stree.heading("done", text=S["status_dlg_col_done"])
    stree.column("done", width=54, anchor="center", stretch=False)
    stree.heading("status", text="")
    stree.column("status", width=30, anchor="center", stretch=False)
    stree.heading("image", text=S["eval_dlg_col_image"])
    stree.column("image", anchor="w", stretch=True)
    ssb = ttk.Scrollbar(stf, orient="vertical", command=stree.yview)
    stree.configure(yscrollcommand=ssb.set)
    ssb.pack(side="right", fill="y"); stree.pack(side="left", fill="both", expand=True)
    ssummary = tk.Label(tab_status, text="", anchor="w", font=font(11, "bold"))
    ssummary.pack(fill="x", pady=8)

    def _refresh_status():
        for iid in stree.get_children():
            stree.delete(iid)
        n_done = 0
        for i in images_in_group:
            st = i.path.stem
            segmented = counts.get(st) is not None
            is_done = bool(done.get(st)) and segmented
            n_done += is_done
            stree.insert("", "end", iid=st, values=(
                "✓" if is_done else " ",
                "○" if not segmented else "●",
                i.path.name,
            ))
        ssummary.config(text=S["status_dlg_summary"].format(
            n=n_done, total=len(images_in_group)))

    def _toggle_done(evt):
        st = stree.identify_row(evt.y)
        if not st or counts.get(st) is None:
            return
        done[st] = not done.get(st, False)
        _refresh_status()

    stree.bind("<Button-1>", _toggle_done)
    _refresh_status()

    result = {"changed": False}
    def on_save():
        changed = False
        for info in images_in_group:
            stem = info.path.stem
            want_eval = overrides.get(stem) != originals.get(stem)
            want_done = bool(done.get(stem)) != done_orig.get(stem, False)
            if not (want_eval or want_done):
                continue
            # One load and one save per image even when both tabs changed it.
            rv = ReviewState.load(folder, stem)
            if want_eval:
                rv.eval_include = overrides.get(stem)
            if want_done:
                rv.is_finished = bool(done.get(stem))
            rv.save(folder, stem)
            changed = True
        result["changed"] = changed
        top.destroy()
    btns = tk.Frame(top); btns.pack(fill="x", padx=12, pady=(0, 12))
    tk.Button(btns, text=S["settings_cancel"], width=12, command=top.destroy).pack(side="right", padx=4)
    tk.Button(btns, text=S["settings_save"], width=12, command=on_save).pack(side="right", padx=4)
    top.bind("<Escape>", lambda e: top.destroy())
    _center_on_parent(top, parent); parent.wait_window(top)
    return result["changed"]


def _fmt_time(seconds: float) -> str:
    return statsmod.fmt_duration(seconds)


# ─────────────────────────────────────────────
# Package: create
# ─────────────────────────────────────────────
def package_create_dialog(parent, folder: Path, grouped: GroupedFolder,
                          profile: ProfileData, reviewer_default: str,
                          strings: dict) -> str | None:
    """Create a share ZIP. Returns the reviewer name used (so the caller can
    persist it), or None if cancelled."""
    S = strings
    top = tk.Toplevel(parent); top.title(S["pkg_create_title"])
    top.transient(parent); top.grab_set(); top.resizable(False, False)
    frm = tk.Frame(top, padx=20, pady=16); frm.pack(fill="both", expand=True)

    include_var = tk.BooleanVar(value=True)
    tk.Checkbutton(frm, text=S["pkg_include_images"], variable=include_var
                   ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    tk.Label(frm, text=S["pkg_reviewer"]).grid(row=1, column=0, sticky="w", pady=4)
    reviewer_var = tk.StringVar(value=reviewer_default)
    tk.Entry(frm, textvariable=reviewer_var, width=28
             ).grid(row=1, column=1, sticky="w", pady=4)

    # ── Per-image picker (only images that already have a cached mask) ──
    CHK, OFF, PART = "☑", "☐", "▣"
    checked: dict[str, bool] = {}            # stem -> selected?
    group_stems: dict[str, list[str]] = {}   # group iid -> its descendant stems
    img_name: dict[str, str] = {}            # stem -> filename

    hdr = tk.Frame(frm); hdr.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    tk.Label(hdr, text=S["pkg_pick_images"], fg="#888",
             font=font(9)).pack(side="left")
    tk.Button(hdr, text=S["pkg_select_none"], padx=4,
              command=lambda: _set_all(False)).pack(side="right")
    tk.Button(hdr, text=S["pkg_select_all"], padx=4,
              command=lambda: _set_all(True)).pack(side="right", padx=(0, 4))

    pick = tk.Frame(frm); pick.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(2, 6))
    tv = ttk.Treeview(pick, show="tree", selectmode="none", height=11)
    sb = ttk.Scrollbar(pick, orient="vertical", command=tv.yview)
    tv.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); tv.pack(side="left", fill="both", expand=True)

    def _disp_tp(tp):
        return S["sb_ungrouped"] if tp == UNGROUPED_TIMEPOINT else tp

    def _disp_sub(sub):
        return S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT else sub

    for tp in grouped.timepoints():
        tp_iid = f"tp::{tp}"; tp_stems: list[str] = []; tp_in = False
        for sub in grouped.subjects_in(tp):
            sub_iid = f"sub::{tp}::{sub}"; sub_stems: list[str] = []; sub_in = False
            for info in grouped.tree[tp][sub]:
                if not has_cache(folder, info.path):
                    continue
                stem = info.path.stem
                checked[stem] = True; img_name[stem] = info.path.name
                if not tp_in:
                    tv.insert("", "end", iid=tp_iid, text="", open=True); tp_in = True
                if not sub_in:
                    tv.insert(tp_iid, "end", iid=sub_iid, text="", open=True); sub_in = True
                tv.insert(sub_iid, "end", iid=f"img::{stem}", text="")
                sub_stems.append(stem); tp_stems.append(stem)
            if sub_in:
                group_stems[sub_iid] = sub_stems
        if tp_in:
            group_stems[tp_iid] = tp_stems

    def _group_glyph(iid):
        stems = group_stems.get(iid, [])
        on = sum(1 for s in stems if checked[s])
        return CHK if (stems and on == len(stems)) else (OFF if on == 0 else PART)

    def _update_count():
        sel = sum(1 for v in checked.values() if v)
        count_lbl.config(text=S["pkg_n_images"].format(n=sel))

    def _redraw():
        for tp in grouped.timepoints():
            tp_iid = f"tp::{tp}"
            if tp_iid not in group_stems:
                continue
            tv.item(tp_iid, text=f"{_group_glyph(tp_iid)}  {_disp_tp(tp)}")
            for sub in grouped.subjects_in(tp):
                sub_iid = f"sub::{tp}::{sub}"
                if sub_iid not in group_stems:
                    continue
                stems = group_stems[sub_iid]
                on = sum(1 for s in stems if checked[s])
                tv.item(sub_iid,
                        text=f"{_group_glyph(sub_iid)}  {_disp_sub(sub)}  ({on}/{len(stems)})")
                for stem in stems:
                    glyph = CHK if checked[stem] else OFF
                    tv.item(f"img::{stem}", text=f"{glyph}  {img_name[stem]}")
        _update_count()

    def _set_all(val):
        for s in checked:
            checked[s] = val
        _redraw()

    def _on_click(evt):
        iid = tv.identify_row(evt.y)
        if not iid:
            return
        if iid.startswith("img::"):
            stem = iid[5:]
            checked[stem] = not checked[stem]
        elif iid in group_stems:
            stems = group_stems[iid]
            new = not all(checked[s] for s in stems)   # all on -> off, else on
            for s in stems:
                checked[s] = new
        else:
            return
        _redraw()
    tv.bind("<Button-1>", _on_click, add="+")

    count_lbl = tk.Label(frm, text="", fg="#888", font=font(9))
    count_lbl.grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 8))
    frm.rowconfigure(3, weight=1); frm.columnconfigure(1, weight=1)
    _redraw()

    state = {"reviewer": None}

    def on_create():
        selected = {s for s, v in checked.items() if v}
        if not selected:
            info_dialog(top, S["pkg_create_title"], S["pkg_n_images"].format(n=0))
            return
        default_name = f"{profile.name}_{folder.name}.eryspkg.zip".replace(" ", "_")
        dest = filedialog.asksaveasfilename(
            parent=top, title=S["pkg_save_as"], defaultextension=".zip",
            initialfile=default_name, filetypes=[("ZIP", "*.zip")],
        )
        if not dest:
            return
        reviewer = reviewer_var.get().strip()
        for w in frm.winfo_children():
            w.destroy()
        tk.Label(frm, text=S["pkg_creating"], font=font(12, "bold")
                 ).pack(pady=(0, 10))
        lbl = tk.Label(frm, text=""); lbl.pack()
        bar = ttk.Progressbar(frm, length=360, mode="determinate",
                              maximum=max(1, len(selected)))
        bar.pack(pady=10)

        def prog(i, n, name):
            bar["value"] = i
            lbl.config(text=f"{i}/{n}  {name}")
            frm.update_idletasks(); frm.update()

        try:
            res = pkg.create_package(
                folder, grouped, profile, Path(dest),
                include_images=include_var.get(), reviewer=reviewer,
                selected_stems=selected, progress_cb=prog,
            )
        except Exception as e:
            info_dialog(top, S["pkg_create_title"], f"{e}")
            top.destroy(); return
        state["reviewer"] = reviewer
        top.destroy()
        info_dialog(parent, S["pkg_create_title"],
                    S["pkg_created_msg"].format(n=res["n_images"], path=res["path"]))

    btns = tk.Frame(frm); btns.grid(row=5, column=0, columnspan=2, sticky="e")
    tk.Button(btns, text=S["settings_cancel"], width=12, command=top.destroy
              ).pack(side="right", padx=4)
    tk.Button(btns, text=S["pkg_create_btn"], width=14, command=on_create
              ).pack(side="right", padx=4)
    top.bind("<Escape>", lambda e: top.destroy())
    _center_on_parent(top, parent); parent.wait_window(top)
    return state["reviewer"]


# ─────────────────────────────────────────────
# Package: conflict resolver
# ─────────────────────────────────────────────
def _conflict_resolver_factory(parent, strings: dict) -> Callable[[str], str]:
    S = strings

    def resolver(stem: str) -> str:
        result = {"v": pkg.KEEP}
        top = tk.Toplevel(parent); top.title(S["pkg_conflict_title"])
        top.transient(parent); top.grab_set(); top.resizable(False, False)
        tk.Label(top, text=S["pkg_conflict_msg"].format(name=stem),
                 padx=20, pady=16, justify="left", wraplength=420).pack()
        btns = tk.Frame(top); btns.pack(padx=16, pady=(0, 14))

        def pick(v):
            result["v"] = v; top.destroy()
        tk.Button(btns, text=S["pkg_conflict_import"], width=14,
                  command=lambda: pick(pkg.IMPORT)).grid(row=0, column=0, padx=4, pady=2)
        tk.Button(btns, text=S["pkg_conflict_keep"], width=14,
                  command=lambda: pick(pkg.KEEP)).grid(row=0, column=1, padx=4, pady=2)
        tk.Button(btns, text=S["pkg_conflict_import_all"], width=14,
                  command=lambda: pick(pkg.IMPORT_ALL)).grid(row=1, column=0, padx=4, pady=2)
        tk.Button(btns, text=S["pkg_conflict_keep_all"], width=14,
                  command=lambda: pick(pkg.KEEP_ALL)).grid(row=1, column=1, padx=4, pady=2)
        # a11y 2.1.2: Escape picks the SAFE default (keep the local review),
        # never the destructive one — result already defaults to pkg.KEEP.
        top.bind("<Escape>", lambda _e: pick(pkg.KEEP))
        _center_on_parent(top, parent); parent.wait_window(top)
        return result["v"]

    return resolver


# ─────────────────────────────────────────────
# Package: import
# ─────────────────────────────────────────────
def package_import_dialog(parent, active_profile: ProfileData | None,
                          strings: dict) -> dict | None:
    """Pick a package, choose a target folder, merge. Returns the import result
    dict (incl. the package's ProfileData) or None if cancelled."""
    S = strings
    zip_str = filedialog.askopenfilename(
        parent=parent, title=S["pkg_import_pick"],
        filetypes=[("Erys package / ZIP", "*.zip"), ("All files", "*.*")],
    )
    if not zip_str:
        return None
    zip_path = Path(zip_str)
    manifest = pkg.inspect_package(zip_path)
    if manifest is None:
        info_dialog(parent, S["pkg_import_title"], S["pkg_not_a_package"])
        return None

    info_msg = S["pkg_import_info"].format(
        reviewer=manifest.get("reviewer") or "?",
        n=manifest.get("n_images", 0),
        images=(S["confirm_yes"] if manifest.get("includes_images") else S["confirm_no"]),
        profile=manifest.get("profile", {}).get("name", "?"),
    )
    if not confirm_dialog(parent, S["pkg_import_title"], info_msg,
                          yes_text=S["pkg_import_choose_target"], no_text=S["settings_cancel"]):
        return None

    target_str = filedialog.askdirectory(parent=parent, title=S["pkg_pick_target"])
    if not target_str:
        return None
    target = Path(target_str)
    # Suggest a subfolder named after the package source if user picked an empty parent.
    if manifest.get("includes_images") and target.name != manifest.get("source_folder_name"):
        sub = target / (manifest.get("source_folder_name") or "imported")
        if confirm_dialog(parent, S["pkg_import_title"],
                          S["pkg_import_subfolder"].format(name=sub.name),
                          yes_text=S["confirm_yes"], no_text=S["confirm_no"]):
            target = sub

    resolver = _conflict_resolver_factory(parent, S)

    # progress window
    top = tk.Toplevel(parent); top.title(S["pkg_importing"])
    top.transient(parent); top.grab_set(); top.resizable(False, False)
    frm = tk.Frame(top, padx=20, pady=16); frm.pack()
    lbl = tk.Label(frm, text=S["pkg_importing"], font=font(12, "bold"))
    lbl.pack(pady=(0, 8))
    detail = tk.Label(frm, text=""); detail.pack()
    bar = ttk.Progressbar(frm, length=360, mode="determinate",
                          maximum=max(1, manifest.get("n_images", 1)))
    bar.pack(pady=10)

    def prog(i, n, stem):
        bar["value"] = i; detail.config(text=f"{i}/{n}  {stem}")
        frm.update_idletasks(); frm.update()

    try:
        result = pkg.import_package(zip_path, target, active_profile, resolver, prog)
    except Exception as e:
        top.destroy()
        info_dialog(parent, S["pkg_import_title"], f"{e}")
        return None
    top.destroy()

    msg = S["pkg_import_done"].format(
        imported=len(result["imported"]), skipped=len(result["skipped"]),
        target=result["target"],
    )
    warnings = list(result["warnings"])
    # Package without originals + target has no images => reviewing won't display.
    if not manifest.get("includes_images"):
        from .cellpose_runner import IMG_EXTS
        tgt = Path(result["target"])
        has_imgs = tgt.is_dir() and any(
            p.is_file() and p.suffix.lower() in IMG_EXTS for p in tgt.iterdir())
        if not has_imgs:
            warnings.append(S["pkg_import_no_images"])
    if warnings:
        msg += "\n\n⚠ " + "\n".join(warnings)
    info_dialog(parent, S["pkg_import_title"], msg)
    return result


# ─────────────────────────────────────────────
# Statistics tab
# ─────────────────────────────────────────────
def stats_window(parent, folder: Path, profile: ProfileData,
                 grouped: GroupedFolder | None, strings: dict) -> None:
    S = strings
    holder = {"records": statsmod.collect_group_records(folder, profile, grouped)}
    records = holder["records"]

    top = tk.Toplevel(parent); top.title(S["stats_title"])
    top.bind("<Escape>", lambda _e: top.destroy())   # a11y 2.1.2
    top.transient(parent); top.minsize(720, 560)

    if not records:
        tk.Label(top, text=S["stats_no_data"], padx=40, pady=40).pack()
        tk.Button(top, text=S["btn_close"], command=top.destroy).pack(pady=(0, 16))
        _center_on_parent(top, parent)
        return

    # ── Controls ──
    ctrl = tk.Frame(top); ctrl.pack(fill="x", padx=12, pady=(12, 6))
    metrics = statsmod.available_metrics(profile)
    metric_labels = [lbl for _k, lbl in metrics]
    label_to_key = {lbl: k for k, lbl in metrics}

    tk.Label(ctrl, text=S["stats_metric"]).pack(side="left")
    metric_var = tk.StringVar(value=metric_labels[0])
    ttk.Combobox(ctrl, textvariable=metric_var, values=metric_labels,
                 state="readonly", width=28).pack(side="left", padx=(4, 16))

    tk.Label(ctrl, text=S["stats_view"]).pack(side="left")
    by_var = tk.StringVar(value="timepoint")
    ttk.Radiobutton(ctrl, text=S["stats_by_timepoint"], value="timepoint",
                    variable=by_var).pack(side="left", padx=2)
    ttk.Radiobutton(ctrl, text=S["stats_by_subject"], value="subject",
                    variable=by_var).pack(side="left", padx=2)
    tk.Button(ctrl, text=S["btn_refresh"],
              command=lambda: _do_refresh()).pack(side="right")

    # ── Plot ──
    try:
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    except Exception as e:  # matplotlib should always be present (used in export)
        tk.Label(top, text=f"matplotlib unavailable: {e}").pack()
        return
    fig = Figure(figsize=(7, 3.6), dpi=100)
    ax = fig.add_subplot(111)
    canvas = FigureCanvasTkAgg(fig, master=top)
    canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=6)

    # ── Table ──
    table_frame = tk.Frame(top); table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    cols = ("group", "n", "mean", "median", "sd", "min", "max", "q1", "q3")
    headers = {"group": S["stats_col_group"], "n": "n", "mean": S["stats_mean"],
               "median": S["stats_median"], "sd": S["stats_sd"], "min": S["stats_min"],
               "max": S["stats_max"], "q1": "Q1", "q3": "Q3"}
    table = ttk.Treeview(table_frame, columns=cols, show="headings", height=8)
    for c in cols:
        table.heading(c, text=headers[c])
        table.column(c, width=70, anchor="center", stretch=True)
    table.column("group", width=120, anchor="w")
    sb = ttk.Scrollbar(table_frame, orient="vertical", command=table.yview)
    table.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); table.pack(side="left", fill="both", expand=True)

    def _do_refresh():
        holder["records"] = statsmod.collect_group_records(folder, profile, grouped)
        redraw()

    def redraw(*_):
        metric_key = label_to_key.get(metric_var.get(), metrics[0][0])
        by = by_var.get()
        groups = statsmod.grouped_values(holder["records"], by, metric_key)
        ax.clear()
        if groups:
            labels = list(groups.keys())
            data = [groups[k] for k in labels]
            # matplotlib renamed boxplot's `labels` -> `tick_labels` in 3.9 and
            # removed `labels` in 3.11, which would crash the stats plot. Use the
            # new name, falling back to the old one on pre-3.9 matplotlib.
            try:
                ax.boxplot(data, tick_labels=labels, showmeans=True)
            except TypeError:
                ax.boxplot(data, labels=labels, showmeans=True)
            ax.set_ylabel(metric_var.get())
            ax.set_xlabel(S["stats_by_subject"] if by == "subject" else S["stats_by_timepoint"])
            ax.grid(axis="y", alpha=0.3)
            fig.tight_layout()
        canvas.draw_idle()
        for iid in table.get_children():
            table.delete(iid)
        for label, vals in groups.items():
            d = statsmod.describe(vals)
            table.insert("", "end", values=(
                label, d["n"], f"{d['mean']:.2f}", f"{d['median']:.2f}",
                f"{d['sd']:.2f}", f"{d['min']:.2f}", f"{d['max']:.2f}",
                f"{d['q1']:.2f}", f"{d['q3']:.2f}"))

    metric_var.trace_add("write", redraw)
    by_var.trace_add("write", redraw)
    redraw()

    tk.Button(top, text=S["btn_close"], command=top.destroy).pack(pady=(0, 12))
    _center_on_parent(top, parent)


# ─────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────
def dashboard_window(parent, folder: Path, profile: ProfileData,
                     grouped: GroupedFolder | None, strings: dict) -> None:
    S = strings
    target = max(1, profile.target_cells)

    top = tk.Toplevel(parent); top.title(S["dash_title"])
    top.bind("<Escape>", lambda _e: top.destroy())   # a11y 2.1.2
    top.transient(parent); top.minsize(640, 460)

    barbar = tk.Frame(top); barbar.pack(fill="x", padx=12, pady=(10, 0))
    tk.Button(barbar, text=S["btn_refresh"], command=lambda: _populate()).pack(side="right")

    frame = tk.Frame(top); frame.pack(fill="both", expand=True, padx=12, pady=12)
    cols = ("tp", "sub", "images", "progress", "reached", "time")
    headers = {"tp": S["dash_col_tp"], "sub": S["dash_col_sub"],
               "images": S["dash_col_images"], "progress": S["dash_col_progress"],
               "reached": S["dash_col_reached"], "time": S["dash_col_time"]}
    tree = ttk.Treeview(frame, columns=cols, show="headings", height=14)
    for c in cols:
        tree.heading(c, text=headers[c]); tree.column(c, anchor="center", stretch=True)
    tree.column("tp", anchor="w"); tree.column("sub", anchor="w")
    sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y"); tree.pack(side="left", fill="both", expand=True)

    info = tk.Frame(top); info.pack(fill="x", padx=12, pady=(0, 6))
    subj_label = tk.Label(info, text="", anchor="w", justify="left", wraplength=600, fg="#888")
    subj_label.pack(anchor="w")
    total_label = tk.Label(info, text="", anchor="w", font=font(11, "bold"))
    total_label.pack(anchor="w", pady=(4, 0))

    def _populate():
        records = statsmod.collect_group_records(folder, profile, grouped)
        for iid in tree.get_children():
            tree.delete(iid)
        total_time = 0.0
        subject_time: dict[str, float] = {}
        for r in records:
            total_time += r["time_s"]
            subject_time[r["subject"]] = subject_time.get(r["subject"], 0.0) + r["time_s"]
            tree.insert("", "end", values=(
                r["timepoint"], r["subject"], r["n_images"],
                f"{r['rbc']}/{target}",
                S["dash_yes"] if r["reached"] else S["dash_no"],
                _fmt_time(r["time_s"]),
            ))
        subj_txt = "   ".join(f"{s}: {_fmt_time(t)}" for s, t in sorted(subject_time.items()))
        subj_label.config(text=S["dash_subject_totals"] + "  " + (subj_txt or "—"))
        total_label.config(text=S["dash_total_time"].format(time=_fmt_time(total_time)))
    _populate()

    tk.Button(top, text=S["btn_close"], command=top.destroy).pack(pady=(0, 12))
    _center_on_parent(top, parent)


def areas_window(parent, groups, strings: dict) -> None:
    """Cell-area outcome: one row per group, expandable to its images.

    `groups` comes pre-computed from the caller, which runs compute_areas() on a
    worker thread — it walks every evaluated image again, and doing that inside
    the constructor would freeze the window while it ran.
    """
    import csv
    from tkinter import filedialog
    from .areas import to_rows

    S = strings
    top = tk.Toplevel(parent)
    top.title(S["areas_title"])
    top.geometry("880x560")
    top.bind("<Escape>", lambda _e: top.destroy())

    tk.Label(top, text=S["areas_hint"], justify="left", anchor="w",
             wraplength=840).pack(fill="x", padx=12, pady=(10, 6))

    cols = ("n", "mean", "median", "sd", "min", "max")
    tree = ttk.Treeview(top, columns=cols, show="tree headings")
    tree.heading("#0", text=S["areas_col_group"])
    tree.column("#0", width=300, anchor="w")
    for c, key, w in zip(cols,
                         ("areas_col_n", "areas_col_mean", "areas_col_median",
                          "areas_col_sd", "areas_col_min", "areas_col_max"),
                         (80, 110, 110, 90, 90, 90)):
        tree.heading(c, text=S[key])
        tree.column(c, width=w, anchor="e")
    tree.pack(fill="both", expand=True, padx=12, pady=6)

    def vals(st):
        return (st.n, f"{st.mean:.1f}", f"{st.median:.1f}",
                f"{st.sd:.1f}", f"{st.minimum:.0f}", f"{st.maximum:.0f}")

    if not groups:
        tree.insert("", "end", text=S["stats_no_data"])
    for g in groups:
        gid = tree.insert("", "end", text=f"{g.timepoint} / {g.subject}",
                          values=vals(g.stats), open=False)
        for stem, st in g.per_image:
            tree.insert(gid, "end", text=f"    {stem}", values=vals(st))

    def save_csv():
        dest = filedialog.asksaveasfilename(
            parent=top, defaultextension=".csv",
            initialfile="Zellflaechen.csv",
            filetypes=[("CSV", "*.csv")])
        if not dest:
            return
        rows = to_rows(groups)
        with open(dest, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["ebene"])
            w.writeheader()
            w.writerows(rows)
        info_dialog(top, S["areas_title"], S["areas_saved"].format(path=dest))

    bar = tk.Frame(top)
    bar.pack(fill="x", padx=12, pady=(0, 12))
    ttk.Button(bar, text=S["areas_save_csv"], command=save_csv).pack(side="right")
    ttk.Button(bar, text=S["btn_close"], command=top.destroy).pack(side="right", padx=(0, 8))
