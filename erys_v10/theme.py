"""Theme palettes — dark (default) and light. Scientific blue/green accents.

Theme is a global UI preference (lives in AppSettings.theme). A change requires
restart for clean re-styling of ttk widgets; transient widgets (dialogs) inherit
the system look and don't strictly need re-styling.
"""

from __future__ import annotations


PALETTES = {
    "dark": {
        # Surfaces
        "bg":             "#1e1e1e",
        "sidebar_bg":     "#252a32",
        "toolbar_bg":     "#262c36",
        "status_bg":      "#15181d",
        "tree_bg":        "#1b1f26",
        "tree_alt_bg":    "#22262e",
        "canvas_bg":      "#15181d",
        # Text
        "fg":             "#e6e8ec",
        "fg_muted":       "#8a93a3",
        "fg_label":       "#a5adbb",
        # Accents — scientific palette
        "accent_primary": "#4D8DDF",   # blue (active tool, selection)
        "accent_primary_hover": "#5BA0F0",
        "accent_secondary": "#54B98A", # green (positive, saved, finished)
        "accent_warning": "#E59B45",   # orange (in-progress, banner)
        "accent_error":   "#E66464",   # red (unsegmented, errors)
        # Selection
        "sel_bg":         "#4D8DDF",
        "sel_fg":         "#ffffff",
        # Lines / dividers
        "sash":           "#3a4150",
        "border":         "#2c3340",
    },
    "light": {
        "bg":             "#f4f6fa",
        "sidebar_bg":     "#e8edf3",
        "toolbar_bg":     "#dde4ec",
        "status_bg":      "#e3e7ee",
        "tree_bg":        "#ffffff",
        "tree_alt_bg":    "#f3f5fa",
        "canvas_bg":      "#fafbfd",
        "fg":             "#1e2230",
        "fg_muted":       "#6a7280",
        "fg_label":       "#43485a",
        "accent_primary": "#2659B6",
        "accent_primary_hover": "#3171D0",
        "accent_secondary": "#268B58",
        "accent_warning": "#B86C0F",
        "accent_error":   "#B83A3A",
        "sel_bg":         "#2659B6",
        "sel_fg":         "#ffffff",
        "sash":           "#c8cdd6",
        "border":         "#cfd5df",
    },
}


def palette(name: str) -> dict:
    return PALETTES.get(name, PALETTES["dark"])


# Mapping from status key to palette accent key
STATUS_TO_COLOR = {
    "unsegmented": "accent_error",
    "in_progress": "accent_warning",
    "finished":    "accent_secondary",
}

# Status icons (Frage answer C: 3 stages — ○ / ◐ / ●)
STATUS_ICONS_3 = {
    "unsegmented": "○",
    "in_progress": "◐",
    "finished":    "●",
}
