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


# ─────────────────────────────────────────────
# Text size (WCAG 1.4.4 — the user must be able to enlarge text)
# ─────────────────────────────────────────────
# Every font spec in the UI goes through font() so a single setting scales all
# of them. Set once from AppSettings.font_scale before the widgets are built;
# changing it takes effect on restart, like the theme.
_FONT_SCALE = 1.0
FONT_SCALE_CHOICES = (1.0, 1.25, 1.5)


def set_font_scale(scale: float) -> None:
    global _FONT_SCALE
    try:
        _FONT_SCALE = max(1.0, min(2.0, float(scale)))
    except (TypeError, ValueError):
        _FONT_SCALE = 1.0


def get_font_scale() -> float:
    return _FONT_SCALE


def font(size: int, *styles: str) -> tuple:
    """Tk font spec at the user's chosen text size.

    `font(9)` -> ("TkDefaultFont", 9); `font(10, "bold")` -> (..., 10, "bold").
    """
    s = max(7, int(round(size * _FONT_SCALE)))
    return ("TkDefaultFont", s, *styles) if styles else ("TkDefaultFont", s)


# ─────────────────────────────────────────────
# Cell-type outline styles (WCAG 1.4.1 — not by color alone)
# ─────────────────────────────────────────────
# A per-type contour drawn around cells of that type, so the type does not rest
# on fill color alone. Measured on real 41 px cells: solid vs. broken reads
# reliably, three different broken patterns do not — so a profile should use
# few of these, not all five. "" = no outline (the default for "normal").
OUTLINE_STYLES: dict[str, object] = {
    "":        None,
    "solid":   "-",
    "dashed":  (0, (5, 3)),
    "dotted":  (0, (1, 2)),
    "dashdot": (0, (7, 2, 1, 2)),
}
OUTLINE_ORDER = ["", "solid", "dashed", "dotted", "dashdot"]


# Mapping consumed by app.py for sidebar tree row colors.
STATUS_TO_COLOR = {
    "unsegmented": "state_unsegmented",
    "in_progress": "state_in_progress",
    "finished":    "state_finished",
}

# Three-state status icons (Frage answer C: ○ / ◐ / ●).
STATUS_ICONS_3 = {
    "unsegmented": "○",
    "in_progress": "◐",
    "finished":    "●",
}


# ─────────────────────────────────────────────
# Cell-type color schemes (5 strategies, applied by position)
# ─────────────────────────────────────────────
COLOR_SCHEMES: dict[str, dict] = {
    "tribal_classic": {
        "label_de": "TRIBAL Klassisch",
        "label_en": "TRIBAL classic",
        "colors": ["#FFD000", "#FF6B6B", "#4ECDC4", "#9D4EDD", "#F77F00"],
    },
    "wavelength": {
        "label_de": "Wellenlänge (warm → kühl)",
        "label_en": "Wavelength (warm → cool)",
        "colors": ["#F5B400", "#F26B3A", "#E04141", "#8B3FBF", "#3A6FE0"],
    },
    "colorblind_safe": {
        "label_de": "Colorblind-Safe (Wong)",
        "label_en": "Colorblind-safe (Wong)",
        "colors": ["#E69F00", "#56B4E9", "#009E73", "#F0E442", "#D55E00"],
    },
    "status_coded": {
        "label_de": "Status (grün/rot)",
        "label_en": "Status (green/red)",
        "colors": ["#2E8B57", "#E04141", "#D6534C", "#C26464", "#A53737"],
    },
    "monochrome_blue": {
        "label_de": "Monochrom Blau",
        "label_en": "Monochrome blue",
        "colors": ["#B3D4F2", "#7AAFE0", "#4D8DDF", "#2961B3", "#143F86"],
    },
}

DEFAULT_COLOR_SCHEME = "tribal_classic"


def scheme_colors(scheme_key: str) -> list[str]:
    return list(COLOR_SCHEMES.get(scheme_key, COLOR_SCHEMES[DEFAULT_COLOR_SCHEME])["colors"])


def apply_scheme_to_types(types_list, scheme_key: str) -> None:
    """Mutate the given TypeDef list in place — assign colors by position.
    Extra types beyond the scheme's color count fall back to the last color."""
    colors = scheme_colors(scheme_key)
    if not colors:
        return
    last = colors[-1]
    for i, td in enumerate(types_list):
        td.color = colors[i] if i < len(colors) else last


# ─────────────────────────────────────────────
# Toolbar glyphs and layout constants (shared by the Tk and Qt toolbars)
# ─────────────────────────────────────────────
TOOLBAR_GROUPED = "grouped"     # layout A: one row, captioned groups
TOOLBAR_TWO_ROW = "two_row"     # layout C: two rows, cell types as colour chips
# Above this many cell types the chips would run off the row; layout C then
# falls back to the dropdown.
CHIP_MAX_TYPES = 6


# Toolbar glyphs, in one place so they are a one-line change.
#
# Chosen for a bold, filled look. ◉ (U+25C9) rather than ◐: ◐ is already the
# sidebar's "in progress" status icon (theme.STATUS_ICONS_3), and one glyph must
# not mean two things in one window.
#
# This is the macOS set, and it is also the fallback everywhere else. Windows
# overrides two of them below — see _GLYPHS_WINDOWS.
_GLYPHS_BASE = {
    "resegment":  "⟳",     # U+27F3 — no bold face on Windows, see below
    "overlay":    "◉",     # U+25C9
    "reset_zoom": "⛶",     # U+26F6
    "undo":       "↺",     # U+21BA
    "info":       "ⓘ",     # U+24D8
    "note":       "❝",     # U+275D — reads as "remark"; present in Segoe UI
                           # Symbol, which UI-15 showed is worth checking.
    "cursor":     "↖",     # U+2196 — overridden on Windows
    "export":     "⬇",     # U+2B07 — overridden on Windows
}

# Windows replacements — measured on Windows 11 / Tk 8.6.13, not guessed.
#
# The toolbar does not render in the font this file assumes. theme.font() builds
# the tuple ("TkDefaultFont", size), and Tk reads a tuple as {family size style}
# — so "TkDefaultFont" is taken as a *family* name, no such family exists, and
# Tk substitutes: Arial, here. Arial carries almost none of these glyphs, so Tk
# font-links them out of a symbol font, and that link fails for the **bold**
# face of two of them, which then draw as a ?-in-a-box.
#
# Only bold-styled buttons are affected, which is why it looks arbitrary:
#   export  ErysPrimary is always bold      -> ⬇ U+2B07 is always a box
#   cursor  ErysToolActive is bold          -> ↖ U+2196 is a box while the tool
#                                              is selected, fine otherwise
#   resegment ⟳ U+27F3 breaks in bold too, but ErysAction is not bold, so it is
#             left as it is — do not put it on a bold style without swapping it.
#
# Both replacements were checked in bold ttk.Buttons at the toolbar's own font
# size, with the DPI awareness the app switches on at startup:
#   ⇓ U+21D3 is in WGL4, i.e. in Arial itself — there is no font link to fail.
#   ◤ U+25E4 draws in both weights and reads as an up-left pointer.
# macOS keeps _GLYPHS_BASE untouched.
_GLYPHS_WINDOWS = {
    "cursor": "◤",         # U+25E4, replaces ↖ U+2196
    "export": "⇓",         # U+21D3, replaces ⬇ U+2B07
}

GLYPHS = dict(_GLYPHS_BASE)
if sys.platform == "win32":
    GLYPHS.update(_GLYPHS_WINDOWS)
