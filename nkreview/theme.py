"""Dual color palettes — Lab Neutral (light) and Soft Dark (dark).

Both palettes expose the same semantic keys so the rest of the app can pull
values by purpose (`accent_primary`, `surface_bg`, …) rather than by literal
color. A theme change is applied on app restart.

Semantic keys
-------------
bg                  outer window background
sidebar_bg          sidebar background
sidebar_alt_bg      slight contrast for grouping inside sidebar
toolbar_bg          top toolbar background
status_bg           bottom status bar background
panel_bg            cards / inputs / dialogs
tree_bg             treeview body
tree_alt_bg         zebra-striping
canvas_bg           matplotlib figure background

fg                  primary text
fg_secondary        less important text (labels, captions)
fg_muted            very subtle text (hints)
fg_inverse          text on a dark accent surface (usually white)
fg_on_accent        text on accent_primary — differs per theme, see below

border              hairline divider color
sash                paned-window separator color

accent_primary      blue: active tool, selection, primary buttons
accent_primary_hover
accent_primary_press
accent_positive     green: saved, finished
accent_warn         amber: in-progress, banners
accent_error        red: unsegmented, errors

state_unsegmented   status icon color (red family)
state_in_progress   status icon color (amber family)
state_finished      status icon color (green family)
"""

from __future__ import annotations

import sys


PALETTES = {
    # ─────────────────────────────────────────
    # Lab Neutral — clean white, indigo + forest accent
    # ─────────────────────────────────────────
    "light": {
        "bg":              "#FAFBFC",
        "sidebar_bg":      "#F2F4F7",
        "sidebar_alt_bg":  "#E8ECF2",
        "toolbar_bg":      "#FFFFFF",
        "status_bg":       "#F2F4F7",
        "panel_bg":        "#FFFFFF",
        "tree_bg":         "#FFFFFF",
        "tree_alt_bg":     "#F6F8FB",
        "canvas_bg":       "#FFFFFF",

        "fg":              "#1A2233",
        "fg_secondary":    "#5A6577",
        # a11y: was #8B94A4 = 2.58:1 on sidebar_alt_bg — below the 4.5:1 that
        # WCAG 1.4.3 wants for body text. #616B7C clears it on all three
        # surfaces it is used over (status_bg, sidebar_alt_bg, panel_bg).
        "fg_muted":        "#616B7C",
        "fg_inverse":      "#FFFFFF",
        "fg_on_accent":    "#FFFFFF",     # on #2161D9 = 5.56:1, fine as is

        "border":          "#E3E7EE",     # decorative hairline, not a control
        # a11y: the sash IS a draggable control, so 1.4.11 applies (>=3:1).
        "sash":            "#8291AB",

        # Tooltip chip. Rule: it inverts against the theme, so a borderless chip
        # still separates from whatever it floats over — 10.99:1 against the
        # lightest surface here, 13.03:1 text.
        "tooltip_bg":      "#2A3140",
        "tooltip_fg":      "#FFFFFF",

        "accent_primary":        "#2161D9",
        "accent_primary_hover":  "#2F73EB",
        "accent_primary_press":  "#194FB3",
        "accent_positive":       "#1B7A4D",
        # a11y: was #B8730F = 3.22:1 on sidebar_alt_bg (warn banner, % verändert).
        "accent_warn":           "#975E0C",
        "accent_error":          "#B83A3A",

        "state_unsegmented": "#B83A3A",
        "state_in_progress": "#975E0C",
        "state_finished":    "#1B7A4D",
    },

    # ─────────────────────────────────────────
    # Soft Dark — marine background, cool blue + mint accent
    # ─────────────────────────────────────────
    "dark": {
        "bg":              "#1B2330",
        "sidebar_bg":      "#232C3B",
        "sidebar_alt_bg":  "#2A3447",
        "toolbar_bg":      "#2A3445",
        "status_bg":       "#181F2B",
        "panel_bg":        "#1F2837",
        "tree_bg":         "#1B2330",
        "tree_alt_bg":     "#202938",
        "canvas_bg":       "#11161F",

        "fg":              "#E8ECF2",
        "fg_secondary":    "#98A2B3",
        # a11y: was #6B7384 = 2.62:1 on sidebar_alt_bg (WCAG 1.4.3 wants 4.5:1).
        "fg_muted":        "#969DAA",
        "fg_inverse":      "#FFFFFF",
        # a11y: white on the light blue accent_primary is only 2.75:1 — and that
        # combination labels the ACTIVE tool, the most important state in the
        # toolbar. Dark text on the same blue reaches 5.75:1.
        "fg_on_accent":    "#1B2330",

        "border":          "#364152",     # decorative hairline, not a control
        # a11y: the sash IS a draggable control, so 1.4.11 applies (>=3:1).
        "sash":            "#5C6C90",

        # Tooltip chip — inverted here too, i.e. light on the dark theme:
        # 10.57:1 against the darkest surface, 13.32:1 text. A dark chip on a
        # dark UI measured 1.47:1 against the toolbar and would have blurred
        # into it, which a borderless chip cannot afford.
        "tooltip_bg":      "#E8ECF2",
        "tooltip_fg":      "#1B2330",

        "accent_primary":        "#5B9CFF",
        "accent_primary_hover":  "#73AEFF",
        "accent_primary_press":  "#3F84E8",
        "accent_positive":       "#4CC596",
        "accent_warn":           "#F5A742",
        "accent_error":          "#F26B6B",

        "state_unsegmented": "#F26B6B",
        "state_in_progress": "#F5A742",
        "state_finished":    "#4CC596",
    },
}


# "system" follows the operating system's appearance. The front end that can
# ask the OS (Qt: styleHints().colorScheme()) sets which one that is right now;
# without one, "system" means light.
THEME_SYSTEM = "system"
_system_scheme = "light"


def set_system_scheme(name: str) -> None:
    global _system_scheme
    _system_scheme = name if name in PALETTES else "light"


def resolve_theme(name: str) -> str:
    """'light' or 'dark' for a stored theme setting (incl. 'system')."""
    if name == THEME_SYSTEM:
        return _system_scheme
    return name if name in PALETTES else "dark"


def palette(name: str) -> dict:
    return PALETTES[resolve_theme(name)]
