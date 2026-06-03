"""Sidebar widget: hierarchical image Treeview + filter + DetailPanel + group counters.

Owns the left column of the main window. The app pushes state to it via
populate() / select_image_by_idx() / refresh_statuses() and reacts to user
input via the callbacks dict provided at construction.

Counter cache: per (timepoint, subject) we keep the last computed n_active to
avoid re-loading masks for un-edited groups. Invalidated on any edit / image
switch within the group.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .cellpose_runner import has_cache
from .detail_panel import DetailPanel
from .grouping import (
    GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT,
)
from .persistence import ReviewState
from .profile import ProfileData
from .theme import STATUS_ICONS_3, STATUS_TO_COLOR


# 3-state mapping (Frage answer C earlier)
STATUS_ICONS = STATUS_ICONS_3


class Sidebar(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        palette: dict,
        strings: dict,
        profile: ProfileData,
        on_open_folder: Callable[[], None],
        on_image_select: Callable[[int], None],
        on_subject_select: Callable[[str, str], None],
        on_open_changed: Callable[[], None],
        on_details_collapsed_changed: Callable[[bool], None] | None = None,
    ):
        super().__init__(parent, bg=palette["sidebar_bg"])
        self.P = palette
        self.S = strings
        self.profile = profile
        self._cb_open_folder = on_open_folder
        self._cb_image_select = on_image_select
        self._cb_subject_select = on_subject_select
        self._cb_open_changed = on_open_changed
        self._cb_details_collapsed = on_details_collapsed_changed

        # Mirror state (filled via populate())
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.grouped: GroupedFolder | None = None
        self.idx: int = 0

        self._group_active_cache: dict[tuple[str, str], int] = {}
        # Cache of image stems that count toward the evaluation, per group.
        self._group_eval_cache: dict[tuple[str, str], set[str]] = {}
        self._build()

    # ─────────────────────────────────────────
    # Construction
    # ─────────────────────────────────────────
    def _build(self) -> None:
        S = self.S
        P = self.P

        top = tk.Frame(self, bg=P["sidebar_bg"])
        top.pack(fill="x", padx=8, pady=(8, 4))
        tk.Button(top, text=S["btn_open_folder"],
                  command=self._cb_open_folder).pack(fill="x")

        filt = tk.Frame(self, bg=P["sidebar_bg"])
        filt.pack(fill="x", padx=8, pady=(6, 4))
        tk.Label(filt, text=S["filter_label"], bg=P["sidebar_bg"], fg=P["fg_secondary"],
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
                command=self.refresh,
            ).pack(anchor="w")

        list_frame = tk.Frame(self, bg=P["sidebar_bg"])
        list_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Erys.Treeview",
            background=P["tree_bg"], foreground=P["fg"],
            fieldbackground=P["tree_bg"], borderwidth=0, rowheight=22,
        )
        style.map(
            "Erys.Treeview",
            background=[("selected", P["accent_primary"])],
            foreground=[("selected", P["fg_inverse"])],
        )

        self.tree = ttk.Treeview(list_frame, show="tree", selectmode="browse",
                                 style="Erys.Treeview")
        self.tree.tag_configure("status_unsegmented",
                                 foreground=P[STATUS_TO_COLOR["unsegmented"]])
        self.tree.tag_configure("status_in_progress",
                                 foreground=P[STATUS_TO_COLOR["in_progress"]])
        self.tree.tag_configure("status_finished",
                                 foreground=P[STATUS_TO_COLOR["finished"]])
        self.tree.tag_configure("group_header", foreground=P["fg"])

        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<<TreeviewOpen>>",  self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", self._on_tree_close)

        # Folder label removed per user spec — sidebar is cleaner without it.
        # DetailPanel docked at bottom of sidebar, with collapse toggle
        self.details = DetailPanel(
            self, P, S,
            collapsed=self.profile.details_collapsed,
            on_collapse_changed=self._on_details_collapse,
        )
        self.details.pack(fill="x", side="bottom", padx=8, pady=(4, 4), ipady=2)

    # ─────────────────────────────────────────
    # Public API used by the App
    # ─────────────────────────────────────────
    def populate(self, folder: Path, images: list[Path], grouped: GroupedFolder) -> None:
        self.folder = folder
        self.images = images
        self.grouped = grouped
        self._invalidate_group_cache()
        self.refresh()

    def _on_details_collapse(self, collapsed: bool) -> None:
        self.profile.details_collapsed = collapsed
        if self._cb_details_collapsed is not None:
            self._cb_details_collapsed(collapsed)

    def set_idx(self, idx: int) -> None:
        self.idx = idx
        if 0 <= idx < len(self.images):
            name = self.images[idx].name
            try:
                self.tree.selection_set(name)
                self.tree.see(name)
            except tk.TclError:
                pass

    def set_profile(self, profile: ProfileData) -> None:
        self.profile = profile
        self._invalidate_group_cache()
        self.refresh()

    def refresh_statuses(self) -> None:
        """Update per-row status icons + per-group counters after edits."""
        if self.grouped is None:
            return
        for info in self.grouped.flat:
            if not self.tree.exists(info.path.name):
                continue
            status = self._image_status(info.path)
            icon = STATUS_ICONS[status]
            self.tree.item(info.path.name,
                           text=f"{icon}  {info.path.name}{self._eval_marker(info, status)}",
                           tags=(f"status_{status}",))
        for iid in self.tree.get_children():
            self._refresh_group_node(iid, recurse=True)

    def refresh_current_group_counter(self) -> None:
        keys = self.current_group_keys()
        if keys is None:
            return
        tp_key, sub_key = keys
        self._invalidate_group_cache(tp_key, sub_key)
        sub_iid = f"sub::{tp_key}::{sub_key}"
        if self.tree.exists(sub_iid):
            self._refresh_group_node(sub_iid)

    def current_group_keys(self) -> tuple[str, str] | None:
        if not self.images or self.grouped is None or self.idx >= len(self.images):
            return None
        cur = self.images[self.idx]
        for info in self.grouped.flat:
            if info.path == cur:
                return (info.timepoint, info.subject)
        return None

    def invalidate_group_cache(self) -> None:
        self._invalidate_group_cache()

    # ─────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────
    def refresh(self) -> None:
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
                        self.tree.insert("", "end", iid=tp_iid, text=tp_label,
                                         open=(tp_iid in open_set), tags=("group_header",))
                        tp_inserted = True
                    if not sub_inserted:
                        self.tree.insert(tp_iid, "end", iid=sub_iid,
                                         text=self._sub_label(sub_label, tp, sub),
                                         open=(sub_iid in open_set),
                                         tags=("group_header",))
                        sub_inserted = True
                    icon = STATUS_ICONS[status]
                    self.tree.insert(sub_iid, "end", iid=info.path.name,
                                     text=f"{icon}  {info.path.name}{self._eval_marker(info, status)}",
                                     tags=(f"status_{status}",))

        if 0 <= self.idx < len(self.images):
            try:
                self.tree.selection_set(self.images[self.idx].name)
                self.tree.see(self.images[self.idx].name)
            except tk.TclError:
                pass

    def _eval_marker(self, info: ImageGroupInfo, status: str) -> str:
        """Trailing marker for segmented images that don't count toward the cap."""
        if status == "unsegmented":
            return ""
        try:
            if info.path.stem not in self.eval_stems(info.timepoint, info.subject):
                return "   ⊘"
        except Exception:
            pass
        return ""

    def _image_status(self, image_path: Path) -> str:
        if self.folder is None or not has_cache(self.folder, image_path):
            return "unsegmented"
        review = ReviewState.load(self.folder, image_path.stem)
        if review.is_finished:
            return "finished"
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

    def _sub_label(self, label: str, tp_key: str, sub_key: str) -> str:
        counts = self._group_counters(tp_key, sub_key)
        compact = self.S["sb_group_closed_counter"].format(
            n_active=counts["n_active"], target=counts["target"],
        )
        return f"{label}  ·  {compact}   ⓘ"

    def _group_counters(self, tp_key: str, sub_key: str) -> dict:
        target = max(1, self.profile.target_cells)
        images = self.grouped.tree.get(tp_key, {}).get(sub_key, []) if self.grouped else []
        n_total = len(images)
        n_done = 0
        cache_key = (tp_key, sub_key)
        n_active = self._group_active_cache.get(cache_key)
        compute_active = n_active is None
        if compute_active:
            from .evaluation import group_eval_summary
            try:
                summ = group_eval_summary(self.folder, images, self.profile)
                n_active = summ["n_active"]
            except Exception:
                n_active = 0
            self._group_active_cache[cache_key] = n_active
        for info in images:
            if self.folder is None or not has_cache(self.folder, info.path):
                continue
            if ReviewState.load(self.folder, info.path.stem).is_finished:
                n_done += 1
        return {"n_total": n_total, "n_done": n_done, "n_active": n_active, "target": target}

    def eval_stems(self, tp_key: str, sub_key: str) -> set[str]:
        """Cached set of image stems that count toward the evaluation for a group."""
        cache_key = (tp_key, sub_key)
        stems = self._group_eval_cache.get(cache_key)
        if stems is None:
            images = self.grouped.tree.get(tp_key, {}).get(sub_key, []) if self.grouped else []
            try:
                from .evaluation import effective_eval_stems
                stems = effective_eval_stems(self.folder, images, self.profile)
            except Exception:
                stems = set()
            self._group_eval_cache[cache_key] = stems
        return stems

    def _invalidate_group_cache(self, tp_key: str | None = None,
                                 sub_key: str | None = None) -> None:
        if tp_key is None or sub_key is None:
            self._group_active_cache.clear()
            self._group_eval_cache.clear()
        else:
            self._group_active_cache.pop((tp_key, sub_key), None)
            self._group_eval_cache.pop((tp_key, sub_key), None)

    def _refresh_group_node(self, iid: str, recurse: bool = False) -> None:
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
                )
                self.tree.item(iid, text=f"{label_base}  ·  {full}   ⓘ")
            else:
                compact = self.S["sb_group_closed_counter"].format(
                    n_active=counts["n_active"], target=counts["target"],
                )
                self.tree.item(iid, text=f"{label_base}  ·  {compact}   ⓘ")
        if recurse:
            for child in self.tree.get_children(iid):
                self._refresh_group_node(child, recurse=True)

    # ─────────────────────────────────────────
    # Tree event handlers
    # ─────────────────────────────────────────
    def _on_tree_select(self, _evt=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        if name.startswith("sub::"):
            try:
                _, tp_key, sub_key = name.split("::", 2)
            except ValueError:
                return
            self._cb_subject_select(tp_key, sub_key)
            return
        if name.startswith("tp::"):
            return
        for i, p in enumerate(self.images):
            if p.name == name:
                if i != self.idx:
                    self._cb_image_select(i)
                break

    def _on_tree_open(self, _evt=None) -> None:
        iid = self.tree.focus()
        if iid.startswith("sub::"):
            self._refresh_group_node(iid)
        if iid not in self.profile.sidebar_open_groups:
            self.profile.sidebar_open_groups.append(iid)
            self._cb_open_changed()

    def _on_tree_close(self, _evt=None) -> None:
        iid = self.tree.focus()
        if iid.startswith("sub::"):
            self._refresh_group_node(iid)
        if iid in self.profile.sidebar_open_groups:
            self.profile.sidebar_open_groups.remove(iid)
            self._cb_open_changed()
