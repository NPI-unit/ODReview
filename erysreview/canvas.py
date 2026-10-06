"""Tk front end of the image view. All drawing lives in canvas_core.CanvasCore."""

from __future__ import annotations

import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from .canvas_core import CanvasCore
from .theme import palette as _palette

from .overlay import (  # noqa: F401 — re-exported; these used to live here
    ALPHA_ACTIVE_U8, ALPHA_INACTIVE_U8, COLOR_INACTIVE_U8, DEFAULT_OVERLAY_ALPHA,
    INACTIVE_ALPHA_BONUS, RGBA, CellInfo, OverlayColors, build_margin_rgba,
    build_overlay_rgba, cell_contours, compute_cell_infos, is_border_cell,
    normalize_image_for_display, patch_overlay_cell,
)


class EryCanvas(tk.Frame, CanvasCore):
    def __init__(self, parent: tk.Misc, settings, profile, save_callback, **kwargs):
        tk.Frame.__init__(self, parent, bg=_palette(settings.theme)["canvas_bg"])
        CanvasCore.__init__(self, settings, profile, save_callback, **kwargs)
        fc = FigureCanvasTkAgg(self.fig, master=self)
        fc.get_tk_widget().pack(fill="both", expand=True)
        self._attach_canvas(fc)

    def _widget_size(self) -> tuple[int, int]:
        w = self.canvas.get_tk_widget()
        return w.winfo_width(), w.winfo_height()
