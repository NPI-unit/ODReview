"""Toolbar widget: tool palette + type dropdown + action buttons + param banner.

Owns the top row of the main window. The app pushes state via
set_active_tool() / refresh_type_combo() / set_param_mismatch() and reacts
to user input via the callbacks provided at construction.

ttk styles (`ErysTool`, `ErysToolActive`, `ErysAction`, `ErysPrimary`,
`Erys.TCombobox`) are configured once on the active palette.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .persistence import (
    AppSettings, TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE,
    TOOLS_ORDER,
)
from .profile import ProfileData
from .theme import font


# Toolbar glyphs, in one place so they are a one-line change.
#
# Chosen for a bold, filled look. Two carry a known risk, accepted deliberately:
# ⛶ (U+26F6, Misc Symbols) and ⬇ (U+2B07, Misc Symbols and Arrows) sit in blocks
# whose Windows coverage is patchy, so Windows may substitute them from another
# font — or show a box. That cannot be checked from macOS; verify on the first
# Windows run and swap here if they look wrong. Safe equivalents of the same
# weight: ▣ (U+25A3) and ⇩ (U+21E9), both in well-covered blocks.
#
# ◉ (U+25C9) rather than ◐: ◐ is already the sidebar's "in progress" status
# icon (theme.STATUS_ICONS_3), and one glyph must not mean two things in one
# window.
GLYPHS = {
    "resegment":  "⟳",     # U+27F3
    "overlay":    "◉",     # U+25C9
    "reset_zoom": "⛶",     # U+26F6  — see the note above
    "undo":       "↺",     # U+21BA
    "info":       "ⓘ",     # U+24D8
    "note":       "❝",     # U+275D — reads as "remark"; present in Segoe UI
                           # Symbol, which UI-15 showed is worth checking.
    "cursor":     "↖",     # U+2196
    "export":     "⬇",     # U+2B07  — see the note above
}


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
    # Below this window width the labels drop and only the icons remain.
    # Measured with the auto-switching disabled and the parameter banner filled
    # (an empty banner collapses to 1 px and hides the real need): labels want
    # 1866 px, glyphs alone 968 px. Without this the *last-packed* widgets get
    # squeezed out — the Export button and the banner (UI-10). A maximised
    # 1920 px screen keeps the labels, anything narrower goes glyph-only.
    COMPACT_WIDTH = 1870

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
        S = self.S
        P = self.P
        # (widget, compact text, full text) for the width-driven relabelling
        self._labels: list[tuple[ttk.Button, str, str]] = []
        self._compact: bool | None = None

        # Tool palette
        self.tool_buttons: dict[str, ttk.Button] = {}
        for i, (key, label_key) in enumerate([
            (TOOL_ADD, "tool_add"),
            (TOOL_REMOVE, "tool_remove"),
            (TOOL_TYPE, "tool_type"),
        ], start=1):
            full = f"{i}   {S[label_key]}"
            b = ttk.Button(
                self, text=full,
                style="ErysTool.TButton",
                command=lambda k=key: self._cb_tool(k),
            )
            b.pack(side="left", padx=(6 if i == 1 else 3, 3), pady=8)
            self.tool_buttons[key] = b
            # Compact keeps the shortcut digit — it is the shortest possible
            # label and doubles as the hotkey hint.
            self._labels.append((b, str(i), full))
            self._add_tip(b, f"{S[label_key]}  ({i})")

        # Type dropdown
        self.type_var = tk.StringVar(value=self._type_display_name(self.profile.active_type_key))
        self.type_combo = ttk.Combobox(
            self, textvariable=self.type_var, state="readonly", width=22,
            values=self._type_display_values(), style="Erys.TCombobox",
        )
        self.type_combo.pack(side="left", padx=(8, 14), pady=10)
        self.type_combo.bind("<<ComboboxSelected>>", self._on_type_combo_change)

        tk.Frame(self, bg=P["border"], width=1, height=28
                 ).pack(side="left", padx=6, pady=12)

        # Annotate and cursor: modes, so they keep the tool style and its active
        # highlight, but shown by glyph without a digit and placed ahead of the
        # actions. They stay in TOOLS_ORDER, so keys 4 and 5 still reach them —
        # the tooltip carries that hint now that the label no longer can.
        for key, label_key in ((TOOL_NOTE, "tool_note"), (TOOL_CURSOR, "tool_cursor")):
            glyph = GLYPHS["note"] if key == TOOL_NOTE else GLYPHS["cursor"]
            full = f"{glyph}   {S[label_key]}"
            b = ttk.Button(self, text=full, style="ErysTool.TButton",
                           command=lambda k=key: self._cb_tool(k))
            b.pack(side="left", padx=3, pady=8)
            self.tool_buttons[key] = b
            self._labels.append((b, glyph, full))
            hot = TOOLS_ORDER.index(key) + 1
            self._add_tip(b, f"{S[label_key]}  ({hot})")

        # Action buttons
        actions = [
            ("btn_resegment", GLYPHS["resegment"],  "tool_resegment",  self._cb_resegment),
            ("btn_overlay",   GLYPHS["overlay"],    "tool_overlay",    self._cb_overlay),
            ("btn_reset",     GLYPHS["reset_zoom"], "tool_reset_zoom", self._cb_reset),
            ("btn_undo",      GLYPHS["undo"],       "tool_undo",       self._cb_undo),
            ("btn_info",      GLYPHS["info"],       "tool_info",       self._cb_info),
        ]
        for attr, glyph, key, cmd in actions:
            full = f"{glyph}   {S[key]}"
            b = ttk.Button(self, text=full, style="ErysAction.TButton", command=cmd)
            b.pack(side="left", padx=3, pady=8)
            setattr(self, attr, b)
            self._labels.append((b, glyph, full))
            self._add_tip(b, S[key])

        # Spacer
        spacer = tk.Frame(self, bg=P["toolbar_bg"])
        spacer.pack(side="left", fill="x", expand=True)

        # Primary CTA
        export_full = f"{GLYPHS['export']}   " + S["tool_export"]
        self.btn_export = ttk.Button(
            self, text=export_full,
            style="ErysPrimary.TButton", command=self._cb_export,
        )
        self.btn_export.pack(side="right", padx=(3, 10), pady=8)
        self._labels.append((self.btn_export, GLYPHS["export"], export_full))
        self._add_tip(self.btn_export, S["tool_export"])

        # Param mismatch banner
        self.param_banner = tk.Label(
            self, text="", bg=P["toolbar_bg"], fg=P["accent_warn"],
            font=font(10, "italic"), cursor="hand2",
        )
        self.param_banner.pack(side="right", padx=8)
        self.param_banner.bind("<Button-1>", lambda e: self._cb_resegment())

        # Initial highlight from current setting
        self.set_active_tool(self.settings.active_tool)
        self.bind("<Configure>", self._on_configure)

    # ─────────────────────────────────────────
    # Width-driven relabelling
    # ─────────────────────────────────────────
    # Hysteresis band. Relabelling changes the toolbar's natural width, so a
    # single threshold can flip back and forth on its own <Configure> events;
    # switching back to full labels only well above the shrink point stops that.
    # Small enough that a 1920 px window comes back to labels (1870+30).
    COMPACT_HYSTERESIS = 30

    def _on_configure(self, evt) -> None:
        if evt.width < 50:
            return                    # not mapped yet — width is meaningless
        if self._compact:
            if evt.width > self.COMPACT_WIDTH + self.COMPACT_HYSTERESIS:
                self.set_compact(False)
        elif evt.width < self.COMPACT_WIDTH:
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
        self.type_combo["values"] = self._type_display_values()
        if profile.type_by_key(profile.active_type_key) is None:
            profile.active_type_key = profile.default_type_key()
        self.type_var.set(self._type_display_name(profile.active_type_key))

    def set_profile(self, profile: ProfileData) -> None:
        self.profile = profile
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
