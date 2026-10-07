"""Name the point under the mouse in a matplotlib figure: "Proband A3 ·
HDT60 · 12,4 %" — for outliers in the box plots, the trajectories and the
agreement plots. The plots register their points with add(); one small label
follows the mouse and disappears off the points."""

from __future__ import annotations

import numpy as np


class Hover:
    RADIUS_PX = 9

    def __init__(self, canvas, P: dict):
        self.canvas, self.P = canvas, P
        self.items: list[tuple] = []          # (ax, xs, ys, labels)
        self.annot = None
        canvas.mpl_connect("motion_notify_event", self._move)
        canvas.mpl_connect("figure_leave_event", lambda _e: self._hide())

    def clear(self) -> None:
        """Call when the figure is redrawn (its axes are new)."""
        self.items = []
        self.annot = None

    def add(self, ax, xs, ys, labels) -> None:
        if len(xs):
            self.items.append((ax, np.asarray(xs, float), np.asarray(ys, float), list(labels)))

    def nearest(self, ax, x_px: float, y_px: float) -> tuple[float, float, str] | None:
        """The registered point of `ax` closest to the mouse (display pixels),
        if within RADIUS_PX."""
        best, best_d = None, self.RADIUS_PX ** 2
        for a, xs, ys, labels in self.items:
            if a is not ax:
                continue
            pts = a.transData.transform(np.column_stack([xs, ys]))
            d = (pts[:, 0] - x_px) ** 2 + (pts[:, 1] - y_px) ** 2
            k = int(np.argmin(d))
            if d[k] <= best_d:
                best, best_d = (xs[k], ys[k], labels[k]), d[k]
        return best

    def _hide(self) -> None:
        if self.annot is not None and self.annot.get_visible():
            self.annot.set_visible(False)
            self.canvas.draw_idle()

    def _move(self, e) -> None:
        if e.inaxes is None:
            self._hide()
            return
        hit = self.nearest(e.inaxes, e.x, e.y)
        if hit is None:
            self._hide()
            return
        x, y, text = hit
        if self.annot is None or self.annot.axes is not e.inaxes:
            if self.annot is not None:
                self.annot.remove()
            P = self.P
            self.annot = e.inaxes.annotate(
                "", (0, 0), xytext=(10, 10), textcoords="offset points", fontsize=8.5, zorder=10,
                color=P["fg"], bbox={"boxstyle": "round,pad=0.35", "fc": P["panel_bg"],
                                     "ec": P["border"], "alpha": 0.95})
        # keep the label inside the plot: flip it left/down near the right/top edge
        bbox = e.inaxes.bbox
        dx = -10 if e.x > bbox.x0 + bbox.width * 0.7 else 10
        dy = -18 if e.y > bbox.y0 + bbox.height * 0.85 else 10
        self.annot.xy = (x, y)
        self.annot.set_text(text)
        self.annot.set_position((dx, dy))
        self.annot.set_ha("right" if dx < 0 else "left")
        self.annot.set_visible(True)
        self.canvas.draw_idle()
