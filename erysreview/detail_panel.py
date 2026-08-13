"""Persistent details panel that lives below the sidebar tree.

Two modes, both live-updating when the underlying data changes:
  - Image mode: stats for one selected image (RBC, per-type counts + %, extras)
  - Subject mode: aggregated stats for one (timepoint, subject) group

The panel is created once and stays in the layout; calling show_image() /
show_subject() / show_empty() swaps its content.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

from .grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from .profile import ProfileData
from .theme import font


class DetailPanel(tk.Frame):
    """Cockpit panel that shows either an image's or a subject's stats."""

    def __init__(self, parent: tk.Misc, palette: dict, strings: dict,
                 collapsed: bool = False,
                 on_collapse_changed=None):
        super().__init__(parent, bg=palette["sidebar_alt_bg"])
        self.P = palette
        self.S = strings
        self.mode: str = "empty"           # "empty" | "image" | "subject"
        self.current_image: Path | None = None
        self.current_subject: tuple[str, str] | None = None
        self.collapsed: bool = collapsed
        self._on_collapse_changed = on_collapse_changed
        self._build()

    # ─── Build ───
    def _build(self) -> None:
        P = self.P
        # Header bar with collapse toggle
        self.header = tk.Frame(self, bg=P["sidebar_alt_bg"], cursor="hand2")
        self.header.pack(fill="x", padx=8, pady=(6, 0))
        self._chevron = tk.Label(
            self.header,
            text="▸" if self.collapsed else "▾",
            bg=P["sidebar_alt_bg"], fg=P["fg_secondary"],
            font=font(10, "bold"),
            cursor="hand2",
        )
        self._chevron.pack(side="left", padx=(0, 6))
        self.header_label = tk.Label(
            self.header, text=self.S["details_title"],
            bg=P["sidebar_alt_bg"], fg=P["fg_secondary"],
            font=font(9, "bold"),
            cursor="hand2",
        )
        self.header_label.pack(side="left")
        for w in (self.header, self._chevron, self.header_label):
            w.bind("<Button-1>", lambda _e: self.toggle_collapsed())

        # Body — hidden when collapsed
        self.body = tk.Frame(self, bg=P["sidebar_alt_bg"])
        if not self.collapsed:
            self.body.pack(fill="both", expand=True, padx=8, pady=4)

        self.empty_label = tk.Label(
            self.body, text=self.S["details_empty_hint"],
            bg=P["sidebar_alt_bg"], fg=P["fg_muted"],
            wraplength=240, justify="left",
            font=font(9, "italic"),
        )
        self.empty_label.pack(anchor="w", pady=8)

    # ─── Collapse API ───
    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self.collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self.collapsed:
            return
        self.collapsed = collapsed
        self._chevron.config(text="▸" if collapsed else "▾")
        if collapsed:
            self.body.pack_forget()
        else:
            self.body.pack(fill="both", expand=True, padx=8, pady=4)
        if self._on_collapse_changed is not None:
            try:
                self._on_collapse_changed(collapsed)
            except Exception:
                pass

    # ─── State API ───
    def show_empty(self) -> None:
        self.mode = "empty"
        self.current_image = None
        self.current_subject = None
        self._clear_body()
        self.empty_label = tk.Label(
            self.body, text=self.S["details_empty_hint"],
            bg=self.P["sidebar_alt_bg"], fg=self.P["fg_muted"],
            wraplength=240, justify="left",
            font=font(9, "italic"),
        )
        self.empty_label.pack(anchor="w", pady=8)

    def show_image(self, folder: Path, image_path: Path,
                   profile: ProfileData) -> None:
        """Render stats for a single image. Loads from cache + review state."""
        self.mode = "image"
        self.current_image = image_path
        self.current_subject = None
        self._clear_body()
        from .summary import per_image_counts
        from .grouping import parse_filename

        info = parse_filename(image_path.name, profile) or ImageGroupInfo(
            path=image_path, timepoint=UNGROUPED_TIMEPOINT, subject=UNGROUPED_SUBJECT,
            image_num=None, is_ungrouped=True,
        )
        info.path = image_path
        counts = per_image_counts(folder, info, profile)

        self._title(f"{self.S['details_image']}: {image_path.name}")

        if counts is None:
            self._row(self.S["details_empty_hint"], "", muted=True)
            return

        rbc = counts["rbc"]
        by_type = counts["by_type"]
        self._row(self.S["details_total"], str(rbc), bold=True)
        self._spacer()

        if rbc > 0:
            self._section(self.S["details_by_type"])
            for t in profile.cell_types:
                n = by_type.get(t.key, 0)
                pct = 100.0 * n / rbc
                self._row(self._type_name(t.key, profile), f"{n}  ({pct:.1f}%)")
            # % changed (= everything that's not Normal)
            n_changed = sum(by_type.get(k, 0) for k in by_type if k != "normal")
            pct_changed = 100.0 * n_changed / rbc
            self._spacer()
            self._row(self.S["details_pct_changed"],
                      f"{n_changed}  ({pct_changed:.2f}%)", bold=True,
                      value_color=self.P["accent_warn"])

    def show_subject(self, folder: Path, tp_key: str, sub_key: str,
                     profile: ProfileData, grouped: GroupedFolder) -> None:
        """Aggregated stats across all images in this (timepoint, subject)."""
        self.mode = "subject"
        self.current_image = None
        self.current_subject = (tp_key, sub_key)
        self._clear_body()
        from .evaluation import group_eval_summary
        from .persistence import ReviewState

        sub_disp = self.S["sb_ungrouped"] if sub_key == UNGROUPED_SUBJECT else sub_key
        tp_disp = self.S["sb_ungrouped"] if tp_key == UNGROUPED_TIMEPOINT else tp_key
        images = grouped.tree.get(tp_key, {}).get(sub_key, [])
        n_total = len(images)
        n_done = sum(1 for info in images
                     if ReviewState.load(folder, info.path.stem).is_finished)

        # Counts restricted to the EVALUATION set (auto-capped near the target),
        # so the number matches the sidebar's "n/target" counter. Summing every
        # computed image would overshoot the cap (e.g. 3000/2000).
        summ = group_eval_summary(folder, images, profile)
        sum_rbc = summ["n_active"]
        target = summ["target"]
        sum_by_type = summ["by_type"]
        pct_target = 100.0 * sum_rbc / target

        self._title(f"{self.S['details_proband']}: {sub_disp}  ·  "
                    f"{self.S['details_timepoint']}: {tp_disp}")
        self._row(self.S["details_total"], f"{sum_rbc} / {target}", bold=True)
        self._progress_bar(min(1.0, pct_target / 100.0), self.P["accent_primary"])
        self._row(self.S["details_progress"], f"{pct_target:.1f}%")
        self._row(self.S["details_images_done"], f"{n_done} / {n_total}")
        self._spacer()

        if sum_rbc > 0:
            self._section(self.S["details_by_type"])
            for t in profile.cell_types:
                n = sum_by_type.get(t.key, 0)
                pct = 100.0 * n / sum_rbc
                self._row(self._type_name(t.key, profile), f"{n}  ({pct:.2f}%)")
            self._spacer()
            self._row(self.S["details_pct_changed"],
                      f"{summ['pct_changed']:.2f}%", bold=True,
                      value_color=self.P["accent_warn"])
            self._row(self.S["details_pct_changed_2000"],
                      f"{summ['pct_changed_2000']:.2f}%",
                      value_color=self.P["accent_warn"])

    def refresh(self, folder: Path | None, profile: ProfileData,
                grouped: GroupedFolder | None) -> None:
        """Called after edits / image-switch / cellpose / export — re-renders
        whatever was last shown using fresh data."""
        if self.mode == "image" and folder and self.current_image:
            self.show_image(folder, self.current_image, profile)
        elif self.mode == "subject" and folder and grouped and self.current_subject:
            tp_key, sub_key = self.current_subject
            self.show_subject(folder, tp_key, sub_key, profile, grouped)

    # ─── Rendering helpers ───
    def _clear_body(self) -> None:
        for w in self.body.winfo_children():
            w.destroy()

    def _title(self, text: str) -> None:
        tk.Label(
            self.body, text=text, bg=self.P["sidebar_alt_bg"],
            fg=self.P["fg"], font=font(10, "bold"),
            anchor="w", justify="left", wraplength=240,
        ).pack(anchor="w", pady=(0, 6))

    def _section(self, text: str) -> None:
        tk.Label(
            self.body, text=text.upper(), bg=self.P["sidebar_alt_bg"],
            fg=self.P["fg_muted"], font=font(8, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(2, 2))

    def _row(self, label: str, value: str, bold: bool = False,
             muted: bool = False, value_color: str | None = None) -> None:
        row = tk.Frame(self.body, bg=self.P["sidebar_alt_bg"])
        row.pack(fill="x")
        fg_l = self.P["fg_muted"] if muted else self.P["fg_secondary"]
        fg_v = value_color or (self.P["fg"] if not muted else self.P["fg_muted"])
        weight = "bold" if bold else "normal"
        tk.Label(row, text=label, bg=self.P["sidebar_alt_bg"], fg=fg_l,
                 font=font(9), anchor="w"
                 ).pack(side="left", anchor="w")
        tk.Label(row, text=value, bg=self.P["sidebar_alt_bg"], fg=fg_v,
                 font=("TkDefaultFont", 9, weight), anchor="e"
                 ).pack(side="right", anchor="e")

    def _progress_bar(self, frac: float, color: str) -> None:
        bar_bg = self.P["panel_bg"]
        outer = tk.Frame(self.body, bg=bar_bg, height=6, width=240)
        outer.pack(fill="x", pady=(2, 4))
        outer.pack_propagate(False)
        if frac > 0:
            inner = tk.Frame(outer, bg=color)
            inner.place(relwidth=frac, relheight=1.0)

    def _spacer(self) -> None:
        tk.Frame(self.body, bg=self.P["sidebar_alt_bg"], height=6).pack(fill="x")

    @staticmethod
    def _type_name(key: str, profile: ProfileData) -> str:
        t = profile.type_by_key(key)
        return t.name or t.key if t else key
