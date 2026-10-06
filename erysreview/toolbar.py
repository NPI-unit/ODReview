"""Toolbar widget: study button + tool palette + cell-type selector + action
buttons + param banner, in one of two layouts (AppSettings.toolbar_layout):
"grouped" — one row, captioned groups; "two_row" — tools and the cell types as
colour chips in a second row (dropdown again above CHIP_MAX_TYPES types).

Owns the top row of the main window. The app pushes state via
set_active_tool() / refresh_type_combo() / set_param_mismatch() and reacts
to user input via the callbacks provided at construction.

ttk styles (`ErysTool`, `ErysToolActive`, `ErysAction`, `ErysPrimary`,
`Erys.TCombobox`) are configured once on the active palette.
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .persistence import (
    AppSettings, TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE,
    TOOLS_ORDER,
)
from .profile import ProfileData
from .theme import (  # noqa: F401 — GLYPHS etc. re-exported for older imports
    CHIP_MAX_TYPES, GLYPHS, TOOLBAR_GROUPED, TOOLBAR_TWO_ROW, font,
)

def _swatch(master: tk.Misc, color: str, ring: str, size: int = 14) -> tk.PhotoImage:
    """A filled disc in the type colour with a thin ring, so a pale type
    (yellow on white) still has an edge."""
    img = tk.PhotoImage(master=master, width=size, height=size)
    c = (size - 1) / 2
    for y in range(size):
        for x in range(size):
            d = ((x - c) ** 2 + (y - c) ** 2) ** 0.5
            if d <= c - 1:
                img.put(color, (x, y))
            elif d <= c + 0.2:
                img.put(ring, (x, y))
    return img


class Toolbar(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        palette: dict,
        strings: dict,
        settings: AppSettings,
        profile: ProfileData,
        # Callbacks
        on_tool_change: Callable[[str], None],
        on_type_change: Callable[[str], None],
        on_resegment: Callable[[], None],
        on_toggle_overlay: Callable[[], None],
        on_reset_zoom: Callable[[], None],
        on_undo: Callable[[], None],
        on_info: Callable[[], None],
        on_export: Callable[[], None],
        on_study: Callable[[tk.Misc], None] | None = None,
    ):
        # No fixed height + pack_propagate(False) here: the row has to grow with
        # its buttons. It used to be pinned at 52 px, which silently clipped the
        # buttons the moment their padding grew for WCAG 2.5.5 (45 px tall
        # buttons rendered as 36), and would clip again at 125/150 % text size.
        super().__init__(parent, bg=palette["toolbar_bg"])
        self.P = palette
        self.S = strings
        self.settings = settings
        self.profile = profile
        self._cb_tool = on_tool_change
        self._cb_type = on_type_change
        self._cb_resegment = on_resegment
        self._cb_overlay = on_toggle_overlay
        self._cb_reset = on_reset_zoom
        self._cb_undo = on_undo
        self._cb_info = on_info
        self._cb_export = on_export
        # Gets the study button, so the app can post its menu right under it.
        self._cb_study = on_study or (lambda _w: None)

        self._configure_styles()
        self._build()

    # ─────────────────────────────────────────
    # ttk style setup
    # ─────────────────────────────────────────
    def _configure_styles(self) -> None:
        P = self.P
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(
            "ErysTool.TButton",
            background=P["panel_bg"], foreground=P["fg"],
            bordercolor=P["border"], lightcolor=P["panel_bg"],
            darkcolor=P["panel_bg"], focuscolor=P["accent_primary"],
            padding=(14, 10), relief="flat",   # a11y 2.5.5: taller hit target
            font=font(11),
        )
        style.map(
            "ErysTool.TButton",
            background=[("active", P["sidebar_alt_bg"]),
                        ("pressed", P["sidebar_alt_bg"])],
            foreground=[("active", P["fg"])],
        )
        style.configure(
            "ErysToolActive.TButton",
            # a11y 1.4.3: white on the light blue accent is 2.75:1 in the dark
            # theme — and this style marks the ACTIVE tool. fg_on_accent is
            # per-theme (dark text on dark theme) and reaches 5.75:1.
            background=P["accent_primary"], foreground=P["fg_on_accent"],
            bordercolor=P["accent_primary"], lightcolor=P["accent_primary"],
            darkcolor=P["accent_primary"], padding=(14, 10), relief="flat",
            focuscolor=P["fg_on_accent"],   # a11y 2.4.7
            font=font(11, "bold"),
        )
        style.map(
            "ErysToolActive.TButton",
            background=[("active", P["accent_primary_hover"]),
                        ("pressed", P["accent_primary_press"])],
            foreground=[("active", P["fg_on_accent"])],
        )
        style.configure(
            "ErysAction.TButton",
            background=P["panel_bg"], foreground=P["fg"],
            bordercolor=P["border"], lightcolor=P["panel_bg"],
            darkcolor=P["panel_bg"], padding=(12, 10), relief="flat",
            focuscolor=P["accent_primary"],   # a11y 2.4.7
            font=font(11),
        )
        style.map(
            "ErysAction.TButton",
            background=[("active", P["sidebar_alt_bg"]),
                        ("pressed", P["sidebar_alt_bg"])],
            foreground=[("active", P["fg"])],
        )
        style.configure(
            "ErysPrimary.TButton",
            background=P["accent_primary"], foreground=P["fg_on_accent"],
            bordercolor=P["accent_primary"], lightcolor=P["accent_primary"],
            darkcolor=P["accent_primary"], padding=(14, 10), relief="flat",
            focuscolor=P["fg_on_accent"],   # a11y 2.4.7
            font=font(11, "bold"),
        )
        style.map(
            "ErysPrimary.TButton",
            background=[("active", P["accent_primary_hover"]),
                        ("pressed", P["accent_primary_press"])],
        )
        style.configure(
            "ErysStudy.TButton",
            background=P["panel_bg"], foreground=P["fg"],
            bordercolor=P["accent_primary"], lightcolor=P["panel_bg"],
            darkcolor=P["panel_bg"], focuscolor=P["accent_primary"],
            padding=(12, 10), relief="flat", font=font(11, "bold"),
        )
        style.map("ErysStudy.TButton",
                  background=[("active", P["sidebar_alt_bg"]),
                              ("pressed", P["sidebar_alt_bg"])])
        for name, border, weight in (("ErysChip.TButton", P["border"], "normal"),
                                     ("ErysChipActive.TButton", P["fg"], "bold")):
            style.configure(
                name, background=P["panel_bg"], foreground=P["fg"],
                bordercolor=border, lightcolor=P["panel_bg"], darkcolor=P["panel_bg"],
                focuscolor=P["accent_primary"], padding=(8, 6), relief="flat",
                font=font(10, weight),
            )
            style.map(name, background=[("active", P["sidebar_alt_bg"]),
                                        ("pressed", P["sidebar_alt_bg"])])
        # Combobox: distinct accent border + focus state so it doesn't blend
        # into the toolbar background (previous version had low contrast).
        style.configure(
            "Erys.TCombobox",
            fieldbackground=P["panel_bg"], background=P["panel_bg"],
            foreground=P["fg"],
            bordercolor=P["accent_primary"],
            lightcolor=P["accent_primary"],
            darkcolor=P["accent_primary"],
            arrowcolor=P["accent_primary"],
            selectbackground=P["accent_primary"],
            selectforeground=P["fg_on_accent"],
            padding=(8, 5),
        )
        style.map(
            "Erys.TCombobox",
            fieldbackground=[("readonly", P["panel_bg"]),
                             ("focus", P["panel_bg"])],
            foreground=[("readonly", P["fg"]),
                        ("disabled", P["fg_muted"])],
            bordercolor=[("focus", P["accent_primary_hover"]),
                         ("hover", P["accent_primary_hover"])],
            arrowcolor=[("active", P["accent_primary_hover"]),
                        ("disabled", P["fg_muted"])],
        )

    # ─────────────────────────────────────────
    # Build
    # ─────────────────────────────────────────
    # Below the width the full labels actually need, they drop and only the
    # icons remain. Without this the *last-packed* widgets get squeezed out —
    # the Export button and the banner (UI-10).
    #
    # This used to be a hard-coded `COMPACT_WIDTH = 1870`, measured once on
    # macOS. A pixel figure cannot survive the trip between platforms: Windows
    # at 150 % display scaling runs Tk at `tk scaling` ≈ 2.0, where the very
    # same labels need 2339 px. The row therefore switched *to* full labels at
    # ~1900 px — a width they no longer fit into — and the cut landed inside
    # "Rückgängig", the button whose cumulative offset (2006 px) straddles the
    # edge. Font, UI language and DPI all move that number, so the threshold is
    # measured from the widgets instead of guessed. On macOS this asks the same
    # widgets the 1870 was eyeballed from, so nothing there changes character;
    # it just stops being a constant that is only ever right on one machine.

    def _add_tip(self, widget: tk.Misc, text: str) -> None:
        """Plain tooltip — the only label an icon-only button still carries."""
        state: dict = {"win": None, "after": None}

        def show():
            state["after"] = None
            if state["win"] is not None or not text:
                return
            # Flat chip: no relief, no border, roomy padding, inverted against
            # the theme so it separates on its own. Rounded corners would need a
            # transparent toplevel, and the attribute for that differs between
            # macOS (-transparent) and Windows (-transparentcolor) — a rectangle
            # is the shape that renders identically on both.
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.configure(bg=self.P["tooltip_bg"])       # no 1 px system frame
            tk.Label(tw, text=text,
                     bg=self.P["tooltip_bg"], fg=self.P["tooltip_fg"],
                     bd=0, highlightthickness=0, padx=10, pady=6,
                     font=font(10)).pack()
            tw.update_idletasks()
            # Centred under the button rather than aligned to its left edge.
            x = (widget.winfo_rootx() + widget.winfo_width() // 2
                 - tw.winfo_reqwidth() // 2)
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            tw.wm_geometry(f"+{x}+{y}")
            state["win"] = tw

        def enter(_e=None):
            state["after"] = widget.after(450, show)

        def leave(_e=None):
            if state["after"] is not None:
                widget.after_cancel(state["after"]); state["after"] = None
            if state["win"] is not None:
                state["win"].destroy(); state["win"] = None

        widget.bind("<Enter>", enter, add="+")
        widget.bind("<Leave>", leave, add="+")
        # Keyboard users get the same hint when the button takes focus.
        widget.bind("<FocusIn>", enter, add="+")
        widget.bind("<FocusOut>", leave, add="+")

    def _build(self) -> None:
        # (widget, compact text, full text) for the width-driven relabelling
        self._labels: list[tuple[ttk.Button, str, str]] = []
        self._compact: bool | None = None
        # Width the row needs with full labels. Measured once, lazily, on the
        # first <Configure> — at that point the labels are still full, and
        # switching to compact would change the very widths we want to know.
        self._full_width_need: int | None = None
        self.tool_buttons: dict[str, ttk.Button] = {}
        self._rows: list[tk.Frame] = []
        self._spacers: list[tk.Frame] = []
        self.type_combo: ttk.Combobox | None = None
        self._chips: dict[str, ttk.Button] = {}
        self._chip_imgs: list[tk.PhotoImage] = []
        if self.settings.toolbar_layout == TOOLBAR_TWO_ROW:
            self._build_two_row()
        else:
            self._build_grouped()
        self.set_active_tool(self.settings.active_tool)
        self.bind("<Configure>", self._on_configure)

    def rebuild(self) -> None:
        """Re-lay the toolbar after the layout setting changed, in place, so
        its slot in the window's pack order is kept."""
        for child in self.winfo_children():
            child.destroy()
        self._build()

    # Layout A — one row, buttons in captioned groups:
    #   Studie | ZELLEN BEARBEITEN 1 2 3 [Typ] | PRÜFEN ❝ ↖ | ANSICHT ◉ ⛶ … ↺ ⟳ ⓘ ⬇
    def _build_grouped(self) -> None:
        row = self._row()
        self._study_button(row)
        edit = self._group(row, self.S["tb_group_edit"])
        self._numbered_tools(edit)
        self._type_holder = tk.Frame(edit, bg=self.P["toolbar_bg"])
        self._type_holder.pack(side="left", padx=(8, 0))
        self._type_selector(chips=False)
        check = self._group(row, self.S["tb_group_check"])
        self._mode_tools(check)
        view = self._group(row, self.S["tb_group_view"])
        self._action(view, "btn_overlay", "overlay", "tool_overlay", self._cb_overlay)
        self._action(view, "btn_reset", "reset_zoom", "tool_reset_zoom", self._cb_reset)
        self._spacer(row)
        self._finish_actions(row)

    # Layout C — two rows. Top: study, banner, undo/Cellpose/info/export.
    # Bottom: every tool, then the cell types as colour chips (one click, colour
    # visible) as long as there are few enough of them; overlay + zoom right.
    def _build_two_row(self) -> None:
        # Both rows exist first; the tools are filled in before the top row's
        # actions so the numbered tools still lead the relabel list.
        top = self._row()
        bottom = self._row(bg=self.P["sidebar_bg"], border=True)
        tools = tk.Frame(bottom, bg=self.P["sidebar_bg"])
        tools.pack(side="left", padx=(6, 0))
        self._numbered_tools(tools)
        self._mode_tools(tools)
        self._type_holder = tk.Frame(bottom, bg=self.P["sidebar_bg"])
        self._type_holder.pack(side="left", padx=(10, 0))
        self._type_selector(chips=True)
        self._spacer(bottom, bg=self.P["sidebar_bg"])
        self._action(bottom, "btn_reset", "reset_zoom", "tool_reset_zoom", self._cb_reset,
                     side="right")
        self._action(bottom, "btn_overlay", "overlay", "tool_overlay", self._cb_overlay,
                     side="right")
        self._study_button(top)
        self._spacer(top)
        self._finish_actions(top)

    # ── building blocks ──
    def _row(self, bg: str | None = None, border: bool = False) -> tk.Frame:
        if border:
            tk.Frame(self, bg=self.P["border"], height=1).pack(side="top", fill="x")
        row = tk.Frame(self, bg=bg or self.P["toolbar_bg"])
        row.pack(side="top", fill="x")
        self._rows.append(row)
        return row

    def _spacer(self, parent: tk.Misc, bg: str | None = None) -> None:
        sp = tk.Frame(parent, bg=bg or self.P["toolbar_bg"])
        sp.pack(side="left", fill="x", expand=True)
        self._spacers.append(sp)

    def _group(self, parent: tk.Misc, caption: str) -> tk.Frame:
        P = self.P
        outer = tk.Frame(parent, bg=P["toolbar_bg"])
        outer.pack(side="left", padx=(10, 4), pady=(4, 6))
        tk.Label(outer, text=caption.upper(), bg=P["toolbar_bg"], fg=P["fg_muted"],
                 font=font(9), anchor="w").pack(side="top", fill="x", padx=3)
        inner = tk.Frame(outer, bg=P["toolbar_bg"])
        inner.pack(side="top", fill="x")
        return inner

    def _study_button(self, parent: tk.Misc) -> None:
        self.btn_study = ttk.Button(parent, text=self._study_text(),
                                    style="ErysStudy.TButton",
                                    command=lambda: self._cb_study(self.btn_study))
        self.btn_study.pack(side="left", padx=(10, 6), pady=8)
        self._add_tip(self.btn_study, self.S["tb_study_tip"])

    def _study_text(self) -> str:
        return f"●  {self.profile.name}  ▾"

    def _numbered_tools(self, parent: tk.Misc) -> None:
        for i, (key, label_key) in enumerate([
            (TOOL_ADD, "tool_add"),
            (TOOL_REMOVE, "tool_remove"),
            (TOOL_TYPE, "tool_type"),
        ], start=1):
            full = f"{i}   {self.S[label_key]}"
            b = ttk.Button(parent, text=full, style="ErysTool.TButton",
                           command=lambda k=key: self._cb_tool(k))
            b.pack(side="left", padx=(0, 3), pady=4)
            self.tool_buttons[key] = b
            # Compact keeps the shortcut digit — it is the shortest possible
            # label and doubles as the hotkey hint.
            self._labels.append((b, str(i), full))
            self._add_tip(b, f"{self.S[label_key]}  ({i})")

    def _mode_tools(self, parent: tk.Misc) -> None:
        # Annotate and cursor: modes, so they keep the tool style and its active
        # highlight, but shown by glyph without a digit. They stay in
        # TOOLS_ORDER, so keys 4 and 5 still reach them — the tooltip says so.
        for key, label_key in ((TOOL_NOTE, "tool_note"), (TOOL_CURSOR, "tool_cursor")):
            glyph = GLYPHS["note"] if key == TOOL_NOTE else GLYPHS["cursor"]
            full = f"{glyph}   {self.S[label_key]}"
            b = ttk.Button(parent, text=full, style="ErysTool.TButton",
                           command=lambda k=key: self._cb_tool(k))
            b.pack(side="left", padx=(0, 3), pady=4)
            self.tool_buttons[key] = b
            self._labels.append((b, glyph, full))
            hot = TOOLS_ORDER.index(key) + 1
            self._add_tip(b, f"{self.S[label_key]}  ({hot})")

    def _action(self, parent: tk.Misc, attr: str, glyph_key: str, label_key: str,
                cmd: Callable[[], None], side: str = "left") -> None:
        glyph = GLYPHS[glyph_key]
        full = f"{glyph}   {self.S[label_key]}"
        b = ttk.Button(parent, text=full, style="ErysAction.TButton", command=cmd)
        b.pack(side=side, padx=(0, 3) if side == "left" else (3, 6), pady=4)
        setattr(self, attr, b)
        self._labels.append((b, glyph, full))
        self._add_tip(b, self.S[label_key])

    def _finish_actions(self, row: tk.Frame) -> None:
        """Right end of the (top) row: banner, undo, Cellpose, info, export."""
        export_full = f"{GLYPHS['export']}   " + self.S["tool_export"]
        self.btn_export = ttk.Button(row, text=export_full, style="ErysPrimary.TButton",
                                     command=self._cb_export)
        self.btn_export.pack(side="right", padx=(3, 10), pady=8)
        for attr, glyph_key, key, cmd in (
            ("btn_info",      "info",      "tool_info",      self._cb_info),
            ("btn_resegment", "resegment", "tool_resegment", self._cb_resegment),
            ("btn_undo",      "undo",      "tool_undo",      self._cb_undo),
        ):
            self._action(row, attr, glyph_key, key, cmd, side="right")
        self._labels.append((self.btn_export, GLYPHS["export"], export_full))
        self._add_tip(self.btn_export, self.S["tool_export"])
        # Param mismatch banner
        self.param_banner = tk.Label(
            row, text="", bg=self.P["toolbar_bg"], fg=self.P["accent_warn"],
            font=font(10, "italic"), cursor="hand2",
        )
        self.param_banner.pack(side="right", padx=8)
        self.param_banner.bind("<Button-1>", lambda e: self._cb_resegment())

    # ── cell-type selector: colour chips or the dropdown ──
    def _type_selector(self, chips: bool) -> None:
        for child in self._type_holder.winfo_children():
            child.destroy()
        self.type_combo = None
        self._chips = {}
        self._chip_imgs = []
        self._chip_mode = chips and 0 < len(self.profile.cell_types) <= CHIP_MAX_TYPES
        if self._chip_mode:
            for t in self.profile.cell_types:
                img = _swatch(self, t.color, self.P["fg"])
                self._chip_imgs.append(img)
                b = ttk.Button(self._type_holder, text=self._type_display_name(t.key),
                               image=img, compound="left", style="ErysChip.TButton",
                               command=lambda k=t.key: self._on_chip(k))
                b.pack(side="left", padx=(0, 4), pady=4)
                self._chips[t.key] = b
                self._add_tip(b, self._type_display_name(t.key))
            self._highlight_chip()
            return
        self.type_var = tk.StringVar(value=self._type_display_name(self.profile.active_type_key))
        self.type_combo = ttk.Combobox(
            self._type_holder, textvariable=self.type_var, state="readonly",
            width=12 if self._compact else 22,
            values=self._type_display_values(), style="Erys.TCombobox",
        )
        self.type_combo.pack(side="left", pady=4)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_combo_change)

    def _on_chip(self, key: str) -> None:
        self.profile.active_type_key = key
        self._highlight_chip()
        self._cb_type(key)

    def _highlight_chip(self) -> None:
        for k, b in self._chips.items():
            b.configure(style="ErysChipActive.TButton"
                        if k == self.profile.active_type_key else "ErysChip.TButton")

    # ─────────────────────────────────────────
    # Width-driven relabelling
    # ─────────────────────────────────────────
    # Hysteresis band. Relabelling changes the toolbar's natural width, so a
    # single threshold can flip back and forth on its own <Configure> events;
    # switching back to full labels only well above the shrink point stops that.
    COMPACT_HYSTERESIS = 30

    def _natural_full_width(self) -> int:
        """Pixels the row needs with full labels, asked of the widgets.

        `winfo_reqwidth` is the size a widget *asks* for, independent of what
        pack has actually granted it, so this is valid even while the toolbar
        is squeezed — which is exactly when we need to know.

        The parameter banner is excluded on purpose: it is the one item allowed
        to wrap rather than shrink (see set_compact), so its text must not drag
        the threshold up. The spacer is excluded because it only absorbs slack.
        """
        need = 0
        for row in self._rows:          # rows stack, so the widest one counts
            row_need = 0
            for child in row.winfo_children():
                if child is self.param_banner or child in self._spacers:
                    continue
                padx = child.pack_info().get("padx", 0)
                row_need += child.winfo_reqwidth() + (
                    sum(int(v) for v in padx)
                    if isinstance(padx, (tuple, list)) else int(padx) * 2
                )
            need = max(need, row_need)
        return need

    def _compact_threshold(self) -> int:
        if self._full_width_need is None:
            self._full_width_need = self._natural_full_width()
        return self._full_width_need

    def _on_configure(self, evt) -> None:
        if evt.width < 50:
            return                    # not mapped yet — width is meaningless
        if self._full_width_need is None and self._compact:
            return                    # compact already: cannot measure full now
        threshold = self._compact_threshold()
        if self._compact:
            if evt.width > threshold + self.COMPACT_HYSTERESIS:
                self.set_compact(False)
        elif evt.width < threshold:
            self.set_compact(True)

    def set_compact(self, compact: bool) -> None:
        """Drop the button labels when the window is too narrow for them.

        Every action stays visible and one click away — only its wording moves
        into the tooltip. Without this the widgets packed last (Export, the
        parameter banner) simply vanish off the right edge.
        """
        if compact == self._compact:
            return
        self._compact = compact
        # The parameter banner carries the longest text in the row — unwrapped
        # it alone adds 236 px, which is what pushed the row past its own 900 px
        # minimum. Truncating a warning is the worst option, so it always wraps;
        # the toolbar sizes to content and simply gets a little taller.
        try:
            self.param_banner.configure(wraplength=170 if compact else 260)
        except (AttributeError, tk.TclError):
            pass
        # The type combobox is the single widest item in the row; letting it
        # shrink with the labels is what makes the 900 px minimum reachable.
        try:
            self.type_combo.configure(width=12 if compact else 22)
        except (AttributeError, tk.TclError):
            pass
        # Chips drop their name, not their colour; the tooltip keeps the name.
        for chip in self._chips.values():
            try:
                chip.configure(compound="image" if compact else "left")
            except tk.TclError:
                pass
        for widget, short, full in self._labels:
            try:
                # `width` matters: a ttk.Button keeps a ~144 px minimum on its
                # own, so shortening only the text saves almost nothing. width
                # is in characters; 0 restores the automatic sizing.
                # `width` matters: a ttk.Button keeps a ~144 px minimum on its
                # own, so shortening only the text saves almost nothing. width
                # is in characters; 0 restores the automatic sizing.
                widget.configure(text=short if compact else full,
                                 width=3 if compact else 0)
            except tk.TclError:
                pass

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────
    def set_active_tool(self, key: str) -> None:
        for k, btn in self.tool_buttons.items():
            btn.configure(
                style="ErysToolActive.TButton" if k == key else "ErysTool.TButton")

    def set_param_mismatch(self, message: str | None) -> None:
        self.param_banner.config(text=message or "")

    def refresh_type_combo(self, profile: ProfileData) -> None:
        """Called when the profile's type list or active_type_key changed."""
        self.profile = profile
        if profile.type_by_key(profile.active_type_key) is None:
            profile.active_type_key = profile.default_type_key()
        # The type list may have grown past (or shrunk under) the chip limit.
        self._type_selector(chips=self.settings.toolbar_layout == TOOLBAR_TWO_ROW)

    def set_profile(self, profile: ProfileData) -> None:
        self.profile = profile
        self.btn_study.configure(text=self._study_text())
        self.refresh_type_combo(profile)

    # ─────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────
    def _type_display_name(self, key: str) -> str:
        td = self.profile.type_by_key(key)
        return (td.name or td.key) if td else key

    def _type_display_values(self) -> list[str]:
        return [self._type_display_name(t.key) for t in self.profile.cell_types]

    def _on_type_combo_change(self, _evt=None) -> None:
        selected = self.type_var.get()
        for t in self.profile.cell_types:
            if self._type_display_name(t.key) == selected:
                self._cb_type(t.key)
                return
