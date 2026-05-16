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
fg_inverse          text on accent surfaces (usually white)

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
        "fg_muted":        "#8B94A4",
        "fg_inverse":      "#FFFFFF",

        "border":          "#E3E7EE",
        "sash":            "#CFD5DF",

        "accent_primary":        "#2161D9",
        "accent_primary_hover":  "#2F73EB",
        "accent_primary_press":  "#194FB3",
        "accent_positive":       "#1B7A4D",
        "accent_warn":           "#B8730F",
        "accent_error":          "#B83A3A",

        "state_unsegmented": "#B83A3A",
        "state_in_progress": "#B8730F",
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
        "status_bg":       "#15192B" if False else "#181F2B",
        "panel_bg":        "#1F2837",
        "tree_bg":         "#1B2330",
        "tree_alt_bg":     "#202938",
        "canvas_bg":       "#11161F",

        "fg":              "#E8ECF2",
        "fg_secondary":    "#98A2B3",
        "fg_muted":        "#6B7384",
        "fg_inverse":      "#FFFFFF",

        "border":          "#364152",
        "sash":            "#384258",

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


def palette(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


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
