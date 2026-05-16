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

from .persistence import AppSettings, TOOL_ADD, TOOL_REMOVE, TOOL_TYPE
from .profile import ProfileData


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
        super().__init__(parent, bg=palette["toolbar_bg"], height=52)
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

        self.pack_propagate(False)
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
            padding=(12, 6), relief="flat",
            font=("TkDefaultFont", 11),
        )
        style.map(
            "ErysTool.TButton",
            background=[("active", P["sidebar_alt_bg"]),
                        ("pressed", P["sidebar_alt_bg"])],
            foreground=[("active", P["fg"])],
        )
        style.configure(
            "ErysToolActive.TButton",
            background=P["accent_primary"], foreground=P["fg_inverse"],
            bordercolor=P["accent_primary"], lightcolor=P["accent_primary"],
            darkcolor=P["accent_primary"], padding=(12, 6), relief="flat",
            font=("TkDefaultFont", 11, "bold"),
        )
        style.map(
            "ErysToolActive.TButton",
            background=[("active", P["accent_primary_hover"]),
                        ("pressed", P["accent_primary_press"])],
            foreground=[("active", P["fg_inverse"])],
        )
        style.configure(
            "ErysAction.TButton",
            background=P["panel_bg"], foreground=P["fg"],
            bordercolor=P["border"], lightcolor=P["panel_bg"],
            darkcolor=P["panel_bg"], padding=(10, 6), relief="flat",
            font=("TkDefaultFont", 11),
        )
        style.map(
            "ErysAction.TButton",
            background=[("active", P["sidebar_alt_bg"]),
                        ("pressed", P["sidebar_alt_bg"])],
            foreground=[("active", P["fg"])],
        )
        style.configure(
            "ErysPrimary.TButton",
            background=P["accent_primary"], foreground=P["fg_inverse"],
            bordercolor=P["accent_primary"], lightcolor=P["accent_primary"],
            darkcolor=P["accent_primary"], padding=(12, 6), relief="flat",
            font=("TkDefaultFont", 11, "bold"),
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
            selectforeground=P["fg_inverse"],
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
    def _build(self) -> None:
        S = self.S
        P = self.P

        # Tool palette
        self.tool_buttons: dict[str, ttk.Button] = {}
        for i, (key, label_key) in enumerate([
            (TOOL_ADD, "tool_add"),
            (TOOL_REMOVE, "tool_remove"),
            (TOOL_TYPE, "tool_type"),
        ], start=1):
            b = ttk.Button(
                self, text=f"{i}   {S[label_key]}",
                style="ErysTool.TButton",
                command=lambda k=key: self._cb_tool(k),
            )
            b.pack(side="left", padx=(6 if i == 1 else 3, 3), pady=8)
            self.tool_buttons[key] = b

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

        # Action buttons
        self.btn_resegment = ttk.Button(
            self, text="⟳   " + S["tool_resegment"],
            style="ErysAction.TButton", command=self._cb_resegment,
        )
        self.btn_resegment.pack(side="left", padx=3, pady=8)
        self.btn_overlay = ttk.Button(
            self, text="⊙   " + S["tool_overlay"],
            style="ErysAction.TButton", command=self._cb_overlay,
        )
        self.btn_overlay.pack(side="left", padx=3, pady=8)
        self.btn_reset = ttk.Button(
            self, text="⌂   " + S["tool_reset_zoom"],
            style="ErysAction.TButton", command=self._cb_reset,
        )
        self.btn_reset.pack(side="left", padx=3, pady=8)
        self.btn_undo = ttk.Button(
            self, text="↶   " + S["tool_undo"],
            style="ErysAction.TButton", command=self._cb_undo,
        )
        self.btn_undo.pack(side="left", padx=3, pady=8)
        self.btn_info = ttk.Button(
            self, text="ⓘ   " + S["tool_info"],
            style="ErysAction.TButton", command=self._cb_info,
        )
        self.btn_info.pack(side="left", padx=3, pady=8)

        # Spacer
        spacer = tk.Frame(self, bg=P["toolbar_bg"])
        spacer.pack(side="left", fill="x", expand=True)

        # Primary CTA
        self.btn_export = ttk.Button(
            self, text="↓   " + S["tool_export"],
            style="ErysPrimary.TButton", command=self._cb_export,
        )
        self.btn_export.pack(side="right", padx=(3, 10), pady=8)

        # Param mismatch banner
        self.param_banner = tk.Label(
            self, text="", bg=P["toolbar_bg"], fg=P["accent_warn"],
            font=("TkDefaultFont", 10, "italic"), cursor="hand2",
        )
        self.param_banner.pack(side="right", padx=8)
        self.param_banner.bind("<Button-1>", lambda e: self._cb_resegment())

        # Initial highlight from current setting
        self.set_active_tool(self.settings.active_tool)

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────
    def set_active_tool(self, key: str) -> None:
        for k, btn in self.tool_buttons.items():
            btn.configure(style="ErysToolActive.TButton" if k == key else "ErysTool.TButton")

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
