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
from .dialogs import _center_on_parent, confirm_dialog, info_dialog
from .grouping import GroupedFolder
from .profile import ProfileData


def _fmt_time(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


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

    n_cached = sum(1 for i in grouped.flat if pkg.has_cache(folder, i.path))
    tk.Label(frm, text=S["pkg_n_images"].format(n=n_cached), fg="#888",
             font=("TkDefaultFont", 9)).grid(row=2, column=0, columnspan=2,
                                             sticky="w", pady=(4, 10))

    state = {"reviewer": None}

    def on_create():
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
        tk.Label(frm, text=S["pkg_creating"], font=("TkDefaultFont", 12, "bold")
                 ).pack(pady=(0, 10))
        lbl = tk.Label(frm, text=""); lbl.pack()
        bar = ttk.Progressbar(frm, length=360, mode="determinate", maximum=max(1, n_cached))
        bar.pack(pady=10)

        def prog(i, n, name):
            bar["value"] = i
            lbl.config(text=f"{i}/{n}  {name}")
            frm.update_idletasks(); frm.update()

        try:
            res = pkg.create_package(
                folder, grouped, profile, Path(dest),
                include_images=include_var.get(), reviewer=reviewer,
                progress_cb=prog,
            )
        except Exception as e:
            info_dialog(top, S["pkg_create_title"], f"{e}")
            top.destroy(); return
        state["reviewer"] = reviewer
        top.destroy()
        info_dialog(parent, S["pkg_create_title"],
                    S["pkg_created_msg"].format(n=res["n_images"], path=res["path"]))

    btns = tk.Frame(frm); btns.grid(row=3, column=0, columnspan=2, sticky="e")
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
    lbl = tk.Label(frm, text=S["pkg_importing"], font=("TkDefaultFont", 12, "bold"))
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
    if result["warnings"]:
        msg += "\n\n⚠ " + "\n".join(result["warnings"])
    info_dialog(parent, S["pkg_import_title"], msg)
    return result


# ─────────────────────────────────────────────
# Statistics tab
# ─────────────────────────────────────────────
def stats_window(parent, folder: Path, profile: ProfileData,
                 grouped: GroupedFolder | None, strings: dict) -> None:
    S = strings
    records = statsmod.collect_group_records(folder, profile, grouped)

    top = tk.Toplevel(parent); top.title(S["stats_title"])
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

    def redraw(*_):
        metric_key = label_to_key.get(metric_var.get(), metrics[0][0])
        by = by_var.get()
        groups = statsmod.grouped_values(records, by, metric_key)
        ax.clear()
        if groups:
            labels = list(groups.keys())
            data = [groups[k] for k in labels]
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
    records = statsmod.collect_group_records(folder, profile, grouped)

    top = tk.Toplevel(parent); top.title(S["dash_title"])
    top.transient(parent); top.minsize(640, 460)

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

    total_time = 0.0
    subject_time: dict[str, float] = {}
    target = max(1, profile.target_cells)
    for r in records:
        total_time += r["time_s"]
        subject_time[r["subject"]] = subject_time.get(r["subject"], 0.0) + r["time_s"]
        tree.insert("", "end", values=(
            r["timepoint"], r["subject"], r["n_images"],
            f"{r['rbc']}/{target}",
            S["dash_yes"] if r["reached"] else S["dash_no"],
            _fmt_time(r["time_s"]),
        ))

    info = tk.Frame(top); info.pack(fill="x", padx=12, pady=(0, 6))
    subj_txt = "   ".join(f"{s}: {_fmt_time(t)}" for s, t in sorted(subject_time.items()))
    tk.Label(info, text=S["dash_subject_totals"] + "  " + (subj_txt or "—"),
             anchor="w", justify="left", wraplength=600, fg="#888").pack(anchor="w")
    tk.Label(info, text=S["dash_total_time"].format(time=_fmt_time(total_time)),
             anchor="w", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=(4, 0))

    tk.Button(top, text=S["btn_close"], command=top.destroy).pack(pady=(0, 12))
    _center_on_parent(top, parent)
