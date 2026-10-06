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
from tkinter import font as tkfont, ttk
import sys
from typing import Callable

from .cellpose_runner import has_cache
from .detail_panel import DetailPanel
from .grouping import (
    GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT,
)
from .persistence import ReviewState
from .profile import ProfileData
from .sidebar_model import image_status, passes_filter
from .theme import font, STATUS_ICONS_3, STATUS_TO_COLOR


# 3-state mapping (Frage answer C earlier)
STATUS_ICONS = STATUS_ICONS_3

# Trailing per-subject affordance: clicking exactly this glyph opens the
# "choose counted images" dialog for that subject.
SETTINGS_ICON = "⚙"


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
        on_subject_settings: Callable[[str, str], None] | None = None,
        on_details_collapsed_changed: Callable[[bool], None] | None = None,
        on_edit_image_note: Callable[[], None] | None = None,
        on_subject_status: Callable[[str, str, bool], None] | None = None,
    ):
        super().__init__(parent, bg=palette["sidebar_bg"])
        self.P = palette
        self.S = strings
        self.profile = profile
        self._cb_open_folder = on_open_folder
        self._cb_image_select = on_image_select
        self._cb_subject_select = on_subject_select
        self._cb_subject_settings = on_subject_settings
        self._cb_open_changed = on_open_changed
        self._cb_details_collapsed = on_details_collapsed_changed
        self._cb_edit_image_note = on_edit_image_note
        self._cb_subject_status = on_subject_status

        # Mirror state (filled via populate())
        # stem -> has a remark; filled as a by-product of _image_status().
        self._note_flags: dict[str, bool] = {}
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
                 font=font(9)).pack(anchor="w")
        self.filter_var = tk.StringVar(value="all")
        for value, label_key in [
            ("all", "filter_all"),
            ("unsegmented", "filter_unsegmented"),
            ("unfinished", "filter_unfinished"),
            ("finished", "filter_finished"),
            ("with_notes", "filter_with_notes"),
        ]:
            tk.Radiobutton(
                filt, text=S[label_key], value=value, variable=self.filter_var,
                bg=P["sidebar_bg"], fg=P["fg"], selectcolor=P["toolbar_bg"],
                activebackground=P["sidebar_bg"], activeforeground=P["fg"],
                font=font(9),
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
            fieldbackground=P["tree_bg"], borderwidth=0,
            rowheight=28,   # a11y 2.5.5: 22 px rows were a small target
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
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")
        # Right-click. Raw Tk reports it as Button-2 on macOS and Button-3
        # elsewhere — unlike matplotlib, which normalises to 3 everywhere.
        for seq in (("<Button-2>",) if sys.platform == "darwin" else ()) + ("<Button-3>",):
            self.tree.bind(seq, self._on_tree_right_click, add="+")
        self._claim_navigation_keys()

        # Folder label removed per user spec — sidebar is cleaner without it.
        # DetailPanel docked at bottom of sidebar, with collapse toggle
        self.details = DetailPanel(
            self, P, S,
            collapsed=self.profile.details_collapsed,
            on_collapse_changed=self._on_details_collapse,
            on_edit_image_note=self._cb_edit_image_note,
        )
        self.details.pack(fill="x", side="bottom", padx=8, pady=(4, 4), ipady=2)

    # Keys the Treeview navigates with, which the main window ALSO binds
    # (<Left>/<Right> step through images, <Home> resets the zoom).
    _NAV_KEYS = ("<Left>", "<Right>", "<Up>", "<Down>",
                 "<Home>", "<End>", "<Prior>", "<Next>")
    _NAV_TAG = "ErysTreeNav"

    def _claim_navigation_keys(self) -> None:
        """Stop tree navigation keys from ALSO firing the window's shortcuts.

        Measured before this: pressing Left on a focused tree collapsed the node
        *and* stepped to the previous image in one keystroke. Tk delivers a key
        to widget -> class -> toplevel -> all, so a plain widget binding that
        returns "break" would also kill the class binding that does the actual
        navigating. Splicing an extra bindtag BETWEEN the class and the toplevel
        lets the Treeview do its job and stops the event right after.
        """
        self.tree.bindtags((str(self.tree), "Treeview", self._NAV_TAG, "all"))
        for seq in self._NAV_KEYS:
            self.tree.bind_class(self._NAV_TAG, seq, lambda _e: "break")

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

    def _apply_status_row(self, info: ImageGroupInfo) -> None:
        """Repaint one image row from disk. One review read — the costly bit."""
        status = self._image_status(info.path)
        icon = STATUS_ICONS[status]
        self.tree.item(info.path.name,
                       text=f"{icon}  {info.path.name}{self._eval_marker(info, status)}"
                            f"{self._note_marker(info)}",
                       tags=(f"status_{status}",))

    def refresh_statuses(self) -> None:
        """Update per-row status icons + per-group counters after edits.

        Folder-wide, so keep it for the operations that really can change any
        row (batch segmentation, opening a folder, bulk group status). A single
        cell edit cannot — use refresh_current_group() for that.
        """
        if self.grouped is None:
            return
        for info in self.grouped.flat:
            if not self.tree.exists(info.path.name):
                continue
            self._apply_status_row(info)
        for iid in self.tree.get_children():
            self._refresh_group_node(iid, recurse=True)

    def refresh_current_group(self) -> None:
        """Repaint just the group being edited: its rows and its counter.

        An edit changes the status and remark marker of the image it was made
        on, and — once the cap refills — the ⊘ eval marker of its siblings in
        the same group. It cannot change a row in any other group, so the
        folder-wide sweep re-read every review JSON in the folder for nothing.

        That sweep hides in the noise on macOS: tests/bench_stick.py calibrates
        APFS at 4.2-4.5 µs per filesystem operation. Measured on Windows/NTFS
        the same operation costs 130-200 µs, so a 780-image folder spent
        ~340 ms of *every single click* re-reading files that had not changed.

        The group node is refreshed BEFORE the rows on purpose: it fills the
        eval-stem cache from the same disk pass that produces its counter, so
        the ⊘ markers below cost nothing extra.
        """
        keys = self.current_group_keys()
        if keys is None or self.grouped is None:
            self.refresh_statuses()      # no group context — fall back to the sweep
            return
        tp_key, sub_key = keys
        stems_before = self._group_eval_cache.get((tp_key, sub_key))
        self._invalidate_group_cache(tp_key, sub_key)
        sub_iid = f"sub::{tp_key}::{sub_key}"
        if self.tree.exists(sub_iid):
            self._refresh_group_node(sub_iid)     # refills the eval-stem cache
        stems_after = self._group_eval_cache.get((tp_key, sub_key))
        # A sibling row can only change its ⊘ marker, and only when the cap
        # boundary actually moved. If the stem set came back identical, reading
        # 25 more review files would repaint 25 rows with what they already say.
        # stems_before is None after any wider invalidation — then repaint all.
        cap_moved = stems_after != stems_before
        current = self.images[self.idx] if 0 <= self.idx < len(self.images) else None
        for info in self.grouped.tree.get(tp_key, {}).get(sub_key, []):
            if not self.tree.exists(info.path.name):
                continue
            if info.path != current and not cap_moved:
                continue
            self._apply_status_row(info)

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
                    if not self._passes_filter(status,
                                               self._note_flags.get(info.path.stem, False)):
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
                                     text=f"{icon}  {info.path.name}{self._eval_marker(info, status)}"
                                          f"{self._note_marker(info)}",
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
        """Status of one image; the remark flag is recorded as a side effect
        (rule in sidebar_model.image_status, shared with the Qt sidebar)."""
        status, has_note = image_status(self.folder, image_path)
        self._note_flags[image_path.stem] = has_note
        return status

    def _note_marker(self, info: ImageGroupInfo) -> str:
        """Trailing ❝ on images that carry a remark.

        A glyph, not a blue row: the row foreground already carries the status
        (unsegmented / in progress / finished), and recolouring exactly the rows
        you are looking for would cost that. The blue marks the cell on the
        canvas, where nothing competes for it; here the filter is the exact tool.
        """
        return "  ❝" if self._note_flags.get(info.path.stem) else ""

    def _passes_filter(self, status: str, has_note: bool = False) -> bool:
        return passes_filter(self.filter_var.get(), status, has_note)

    def _sub_label(self, label: str, tp_key: str, sub_key: str) -> str:
        counts = self._group_counters(tp_key, sub_key)
        compact = self.S["sb_group_closed_counter"].format(
            n_active=counts["n_active"], target=counts["target"],
        )
        return f"{label}  ·  {compact}   {SETTINGS_ICON}"

    def _group_counters(self, tp_key: str, sub_key: str) -> dict:
        """Counters for one subject row. Only `n_active` costs disk, and only on
        a cache miss — the row label is `{n_active}/{target}` and nothing else.

        (There used to be a `n_done` loop here that re-read has_cache +
        ReviewState for every image in the group on every refresh. No label
        format ever interpolated it; the DetailPanel computes its own. Removed.)
        """
        target = max(1, self.profile.target_cells)
        images = self.grouped.tree.get(tp_key, {}).get(sub_key, []) if self.grouped else []
        cache_key = (tp_key, sub_key)
        n_active = self._group_active_cache.get(cache_key)
        if n_active is None:
            from .evaluation import group_eval
            try:
                summary, stems = group_eval(self.folder, images, self.profile)
                n_active = summary["n_active"]
                # That pass already decided which stems count toward the cap.
                # Keeping them means _eval_marker() does not send
                # effective_eval_stems() over the very same group a second time
                # on the very same refresh — it was the largest single cost on
                # the click path after the folder-wide status sweep.
                self._group_eval_cache[cache_key] = stems
            except Exception:
                n_active = 0
            self._group_active_cache[cache_key] = n_active
        return {"n_total": len(images), "n_active": n_active, "target": target}

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
                self.tree.item(iid, text=f"{label_base}  ·  {full}   {SETTINGS_ICON}")
            else:
                compact = self.S["sb_group_closed_counter"].format(
                    n_active=counts["n_active"], target=counts["target"],
                )
                self.tree.item(iid, text=f"{label_base}  ·  {compact}   {SETTINGS_ICON}")
        if recurse:
            for child in self.tree.get_children(iid):
                self._refresh_group_node(child, recurse=True)

    # ─────────────────────────────────────────
    # Tree event handlers
    # ─────────────────────────────────────────
    def _hit_settings_icon(self, iid: str, x: int) -> bool:
        """True if event x-coordinate falls on the trailing ⚙ of a subject row.

        Tkinter's Treeview has no per-glyph hit testing, so we measure the row
        text with the tree font and treat the rightmost glyph (plus a small
        tolerance, extended to the cell's right edge) as the icon hot zone.
        """
        text = self.tree.item(iid, "text")
        if SETTINGS_ICON not in text:
            return False
        bbox = self.tree.bbox(iid, "#0")
        if not bbox:
            return False
        bx, _by, bw, _bh = bbox
        try:
            tree_font = tkfont.nametofont("TkDefaultFont")
        except tk.TclError:
            return False
        full_w = tree_font.measure(text)
        icon_w = tree_font.measure(SETTINGS_ICON)
        pad = 6
        icon_left = bx + full_w - icon_w - pad
        # Anything from just left of the icon to the cell's right edge counts —
        # the icon is the last glyph, with only empty cell padding after it.
        return x >= icon_left

    def _on_tree_click(self, evt) -> None:
        """Open the per-subject 'counted images' dialog when the ⚙ is clicked.

        A click anywhere else on the row falls through to normal selection.
        """
        if self._cb_subject_settings is None:
            return
        iid = self.tree.identify_row(evt.y)
        if not iid or not iid.startswith("sub::"):
            return
        if not self._hit_settings_icon(iid, evt.x):
            return
        try:
            _, tp_key, sub_key = iid.split("::", 2)
        except ValueError:
            return
        # Defer so this click finishes (selection/focus) before a modal opens.
        self.after_idle(lambda: self._cb_subject_settings(tp_key, sub_key))

    def _on_tree_right_click(self, evt) -> None:
        """Bulk-set the finished flag for a whole subject.

        The gear is already taken (it opens "which images count"), so the status
        gets its own gesture rather than a second glyph competing for the same
        few pixels.
        """
        if self._cb_subject_status is None:
            return
        iid = self.tree.identify_row(evt.y)
        if not iid or not iid.startswith("sub::"):
            return
        try:
            _, tp_key, sub_key = iid.split("::", 2)
        except ValueError:
            return
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=self.S["status_mark_all_done"],
                         command=lambda: self._cb_subject_status(tp_key, sub_key, True))
        menu.add_command(label=self.S["status_mark_all_open"],
                         command=lambda: self._cb_subject_status(tp_key, sub_key, False))
        try:
            menu.tk_popup(evt.x_root, evt.y_root)
        finally:
            menu.grab_release()

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
