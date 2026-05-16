"""Matplotlib canvas with tool-palette-aware click handling.

Performance design:
  - compute_cell_infos uses skimage.measure.regionprops_table (~22 ms for 300 cells
    vs. ~1400 ms in the previous per-cell np.where loop)
  - Three persistent AxesImage handles are kept alive (base image, overlay RGBA,
    margin overlay). Updates use set_data() / set_visible() rather than ax.cla()+rebuild,
    so overlay-toggle / data-edit redraws stay in the tens-of-ms range.

Visual scheme (Frage 3A — three-stage gray fill, no dashed circles):
  - Active cells  -> their type color
  - Border cells  -> mid-gray fill
  - Excluded cells -> dark-gray fill
  - Force-included border cells -> type color (count as active)

Tool semantics (Frage 1A):
  - "select"  : click on cell selects it (used for sidebar info)
  - "add"     : click anywhere creates a new manual cell
  - "remove"  : click toggles effective active-state (handles border re-include)
  - "type"    : click on cell assigns settings.active_type_key
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patheffects import Stroke, Normal
from skimage.draw import disk as sk_disk
from skimage.measure import regionprops_table

from .persistence import (
    AppSettings, ManualCell, ReviewState,
    TOOL_ADD, TOOL_REMOVE, TOOL_TYPE,
)
from .profile import ProfileData


@dataclass
class CellInfo:
    cell_id: int
    cy: float
    cx: float
    area: int
    bbox_y0: int
    bbox_x0: int
    bbox_y1: int  # exclusive
    bbox_x1: int  # exclusive
    is_manual: bool = False


def compute_cell_infos(masks: np.ndarray, manual_ids: set[int]) -> dict[int, CellInfo]:
    """Single pass over the label image via skimage.measure.regionprops_table.
    ~22 ms for 300 cells on a 1920x1200 image (vs. ~1400 ms for the naive loop)."""
    if masks.max() == 0:
        return {}
    props = regionprops_table(
        masks,
        properties=("label", "bbox", "area", "centroid"),
    )
    labels = props["label"]
    cy = props["centroid-0"]
    cx = props["centroid-1"]
    y0 = props["bbox-0"]
    x0 = props["bbox-1"]
    y1 = props["bbox-2"]
    x1 = props["bbox-3"]
    area = props["area"]

    out: dict[int, CellInfo] = {}
    for i, lab in enumerate(labels):
        cid = int(lab)
        out[cid] = CellInfo(
            cell_id=cid,
            cy=float(cy[i]),
            cx=float(cx[i]),
            area=int(area[i]),
            bbox_y0=int(y0[i]),
            bbox_x0=int(x0[i]),
            bbox_y1=int(y1[i]),
            bbox_x1=int(x1[i]),
            is_manual=cid in manual_ids,
        )
    return out


def is_border_cell(info: CellInfo, h: int, w: int, margin_n: int) -> bool:
    return (
        info.bbox_y0 <= margin_n
        or info.bbox_x0 <= margin_n
        or info.bbox_y1 >= h - margin_n
        or info.bbox_x1 >= w - margin_n
    )


# ─────────────────────────────────────────────
# Colors
# ─────────────────────────────────────────────
def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16) / 255, int(h[2:4], 16) / 255, int(h[4:6], 16) / 255)


# Unified visual scheme: one inactive color for both border + excluded (per design).
# Light gray fill + higher alpha so it stays visible on dark images.
COLOR_INACTIVE       = (0.80, 0.80, 0.80)
ALPHA_ACTIVE         = 0.42
ALPHA_INACTIVE       = 0.55


def build_overlay_rgba(
    masks: np.ndarray,
    cell_infos: dict[int, CellInfo],
    review: ReviewState,
    profile: ProfileData,
    border_ids: set[int],
    duplicate_ids: set[int] | None = None,
) -> np.ndarray:
    """RGBA overlay: active cells = type color, inactive (border/excluded/duplicate)
    = unified gray. Force_included cells render as active (their type color).

    Vectorized via color_table[masks] — ~5 ms for 300 cells on a 1920x1200 image.
    """
    duplicate_ids = duplicate_ids or set()
    max_id = int(masks.max())
    color_table = np.zeros((max_id + 1, 4), dtype=np.float32)

    excluded = set(review.force_excluded)
    included = set(review.force_included)
    type_colors = {t.key: _hex_to_rgb(t.color) for t in profile.cell_types}
    default_key = profile.default_type_key()
    default_color = type_colors.get(default_key, (1.0, 0.85, 0.0))

    for cid in cell_infos:
        if cid <= 0 or cid > max_id:
            continue
        is_auto_inactive = (cid in border_ids) or (cid in duplicate_ids)
        is_inactive = (cid in excluded) or (is_auto_inactive and cid not in included)
        if is_inactive:
            color = COLOR_INACTIVE
            alpha = ALPHA_INACTIVE
        else:
            type_key = review.types.get(str(cid), default_key)
            color = type_colors.get(type_key, default_color)
            alpha = ALPHA_ACTIVE
        color_table[cid, 0] = color[0]
        color_table[cid, 1] = color[1]
        color_table[cid, 2] = color[2]
        color_table[cid, 3] = alpha

    return color_table[masks]


def build_margin_rgba(h: int, w: int, margin_n: int) -> np.ndarray:
    """A semi-transparent black RGBA strip covering the outer margin_n pixels.
    Used to visualize the border zone (Frage 2c, II)."""
    rgba = np.zeros((h, w, 4), dtype=np.float32)
    if margin_n <= 0:
        return rgba
    rgba[..., 3] = 0.35
    # punch a hole through the inner region
    rgba[margin_n:h - margin_n, margin_n:w - margin_n, 3] = 0.0
    return rgba


def normalize_image_for_display(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        a = img.astype(np.float32)
        lo, hi = float(a.min()), float(a.max())
        return (a - lo) / (hi - lo + 1e-8)
    a = img.astype(np.float32)
    if a.max() > 1.5:
        a = a / 255.0
    return np.clip(a, 0.0, 1.0)


# ─────────────────────────────────────────────
# Canvas widget
# ─────────────────────────────────────────────
class EryCanvas(tk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        settings: AppSettings,
        profile: ProfileData,
        save_callback: Callable[[], None],
        status_callback: Callable[[str], None] | None = None,
        new_circle_radius: int = 17,
    ):
        super().__init__(parent, bg="#1e1e1e")
        self.settings = settings
        self.profile = profile
        self.duplicate_ids: set[int] = set()
        self.save_callback = save_callback
        self.status_callback = status_callback or (lambda s: None)
        self.new_circle_radius = new_circle_radius

        # Data state
        self.image: np.ndarray | None = None
        self.image_norm: np.ndarray | None = None
        self.masks: np.ndarray | None = None
        self.review: ReviewState | None = None
        self.cell_infos: dict[int, CellInfo] = {}
        self.border_ids: set[int] = set()
        self.next_id: int = 1
        self.history: list[tuple] = []
        self.cursor_xy: tuple[float, float] | None = None
        self.selected_id: int | None = None

        # Pan state
        self._pan_start: tuple[float, float, float, float, float, float] | None = None

        # Figure
        self.fig = Figure(figsize=(10, 7), facecolor="#1e1e1e")
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("#1e1e1e")
        self.ax.axis("off")
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Persistent image handles (set on first set_image)
        self._im_handle = None
        self._overlay_handle = None
        self._margin_handle = None
        self._number_artists: list = []
        self._image_shape: tuple[int, int] | None = None  # to detect re-init

        # mpl events
        self.canvas.mpl_connect("button_press_event", self._on_button_press)
        self.canvas.mpl_connect("button_release_event", self._on_button_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        # LOD: drive number visibility from zoom level
        self.ax.callbacks.connect("xlim_changed", lambda _ax: self._apply_number_visibility())

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────
    def clear(self) -> None:
        self.image = self.image_norm = self.masks = self.review = None
        self.cell_infos = {}
        self.border_ids = set()
        self.history.clear()
        self.selected_id = None
        self.ax.cla()
        self.ax.set_facecolor("#1e1e1e")
        self.ax.axis("off")
        self._im_handle = self._overlay_handle = self._margin_handle = None
        self._number_artists = []
        self._image_shape = None
        self.canvas.draw_idle()

    def set_image(self, image: np.ndarray, masks: np.ndarray, review: ReviewState) -> None:
        self.image = image
        self.image_norm = normalize_image_for_display(image)
        self.masks = masks.astype(np.int32)
        self.review = review
        self.history.clear()
        self.selected_id = None

        # Materialize manual cells into the live mask
        manual_ids: set[int] = set()
        max_id = int(self.masks.max())
        for mc in review.manual_cells:
            mid = mc.cell_id
            max_id = max(max_id, mid)
            rr, cc = sk_disk((int(mc.y), int(mc.x)), int(mc.radius), shape=self.masks.shape)
            self.masks[rr, cc] = mid
            manual_ids.add(mid)
        self.next_id = max_id + 1
        if review.next_manual_id < self.next_id:
            review.next_manual_id = self.next_id

        self.cell_infos = compute_cell_infos(self.masks, manual_ids)
        self._recompute_borders()

        h, w = self.image.shape[:2]
        same_shape = (self._image_shape == (h, w)) and self._im_handle is not None

        if same_shape:
            self._im_handle.set_data(self.image_norm)
        else:
            self.ax.cla()
            self.ax.set_facecolor("#1e1e1e")
            self.ax.axis("off")
            self._im_handle = self.ax.imshow(self.image_norm, interpolation="nearest", zorder=0)
            blank_rgba = np.zeros((h, w, 4), dtype=np.float32)
            self._overlay_handle = self.ax.imshow(blank_rgba, interpolation="nearest", zorder=1)
            self._margin_handle = self.ax.imshow(blank_rgba, interpolation="nearest", zorder=2)
            self._number_artists = []
            self._image_shape = (h, w)
            self.ax.set_xlim(-0.5, w - 0.5)
            self.ax.set_ylim(h - 0.5, -0.5)

        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self.canvas.draw_idle()

    def reset_zoom(self) -> None:
        if self.image is None:
            return
        h, w = self.image.shape[:2]
        self.ax.set_xlim(-0.5, w - 0.5)
        self.ax.set_ylim(h - 0.5, -0.5)
        self.canvas.draw_idle()

    def set_overlay_visible(self, visible: bool) -> None:
        if self._overlay_handle is not None:
            self._overlay_handle.set_visible(visible)
        self._apply_number_visibility()
        self.canvas.draw_idle()

    def set_margin_n(self, n: int) -> None:
        self.profile.margin_n = int(n)
        self._recompute_borders()
        self._refresh_overlay()
        self._refresh_margin()
        self.canvas.draw_idle()

    def set_profile(self, profile: ProfileData) -> None:
        """Swap to a different study profile. Re-renders with new colors/margin/types."""
        self.profile = profile
        self._recompute_borders()
        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self.canvas.draw_idle()

    def refresh_appearance(self) -> None:
        """Called after Settings change: re-render margin overlay + cell numbers."""
        self._refresh_margin()
        self._refresh_numbers()
        self.canvas.draw_idle()

    def refresh_overlay_from_data(self) -> None:
        """Called externally if review data changed without going through canvas methods."""
        self._refresh_overlay()
        self._refresh_numbers()
        self.canvas.draw_idle()

    # ─────────────────────────────────────────
    # Tool-aware actions
    # ─────────────────────────────────────────
    def _act_add(self, x: float, y: float) -> bool:
        if self.masks is None or self.review is None:
            return False
        new_id = self.next_id
        self.next_id += 1
        self.review.next_manual_id = self.next_id
        rr, cc = sk_disk((int(y), int(x)), self.new_circle_radius, shape=self.masks.shape)
        self.masks[rr, cc] = new_id
        mc = ManualCell(cell_id=new_id, x=float(x), y=float(y),
                        radius=float(self.new_circle_radius))
        self.review.manual_cells.append(mc)
        self.cell_infos[new_id] = CellInfo(
            cell_id=new_id, cy=float(y), cx=float(x),
            area=int(np.pi * self.new_circle_radius ** 2),
            bbox_y0=int(y) - self.new_circle_radius,
            bbox_x0=int(x) - self.new_circle_radius,
            bbox_y1=int(y) + self.new_circle_radius + 1,
            bbox_x1=int(x) + self.new_circle_radius + 1,
            is_manual=True,
        )
        # check border
        h, w = self.image.shape[:2]
        if is_border_cell(self.cell_infos[new_id], h, w, self.profile.margin_n):
            self.border_ids.add(new_id)
        self.history.append(("add", new_id))
        self.save_callback()
        self._refresh_overlay()
        self._refresh_numbers()
        self.canvas.draw_idle()
        return True

    def _act_remove_toggle(self, x: float, y: float) -> bool:
        """Toggle the cell's effective active state.

        Cases:
          - currently active normal       -> add to force_excluded (now excluded)
          - currently force_excluded      -> remove from force_excluded (back to default)
          - currently border (inactive)   -> add to force_included (now active)
          - currently force_included      -> remove from force_included (back to border)
        """
        cid = self._find_cell_at(x, y)
        if cid is None or self.review is None:
            return False
        prev = (cid in self.review.force_excluded, cid in self.review.force_included)
        if cid in self.review.force_excluded:
            self.review.force_excluded.remove(cid)
        elif cid in self.review.force_included:
            self.review.force_included.remove(cid)
        elif cid in self.border_ids:
            self.review.force_included.append(cid)
        else:
            self.review.force_excluded.append(cid)
        self.history.append(("remove_toggle", cid, prev))
        self.save_callback()
        self._refresh_overlay()
        self.canvas.draw_idle()
        return True

    def _act_type(self, x: float, y: float) -> bool:
        cid = self._find_cell_at(x, y)
        if cid is None or self.review is None:
            return False
        prev = self.review.types.get(str(cid))
        self.review.types[str(cid)] = self.profile.active_type_key
        self.history.append(("type", cid, prev))
        self.save_callback()
        self._refresh_overlay()
        self.canvas.draw_idle()
        return True

    def undo(self) -> bool:
        if not self.history or self.review is None or self.masks is None:
            return False
        action = self.history.pop()
        kind = action[0]
        if kind == "remove_toggle":
            _, cid, prev = action
            was_excluded, was_included = prev
            if cid in self.review.force_excluded:
                self.review.force_excluded.remove(cid)
            if cid in self.review.force_included:
                self.review.force_included.remove(cid)
            if was_excluded:
                self.review.force_excluded.append(cid)
            if was_included:
                self.review.force_included.append(cid)
        elif kind == "add":
            _, cid = action
            self.masks[self.masks == cid] = 0
            self.review.manual_cells = [m for m in self.review.manual_cells if m.cell_id != cid]
            self.cell_infos.pop(cid, None)
            self.border_ids.discard(cid)
            self.next_id = max(1, self.next_id - 1)
            self.review.next_manual_id = self.next_id
        elif kind == "type":
            _, cid, prev = action
            if prev is None:
                self.review.types.pop(str(cid), None)
            else:
                self.review.types[str(cid)] = prev
        self.save_callback()
        self._refresh_overlay()
        self._refresh_numbers()
        self.canvas.draw_idle()
        return True

    # ─────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────
    def _find_cell_at(self, x: float, y: float) -> int | None:
        if self.masks is None:
            return None
        yi, xi = int(round(y)), int(round(x))
        if 0 <= yi < self.masks.shape[0] and 0 <= xi < self.masks.shape[1]:
            cid = int(self.masks[yi, xi])
            return cid if cid > 0 else None
        return None

    def _recompute_borders(self) -> None:
        if self.image is None:
            self.border_ids = set()
            return
        h, w = self.image.shape[:2]
        n = self.profile.margin_n
        self.border_ids = {
            cid for cid, info in self.cell_infos.items()
            if is_border_cell(info, h, w, n)
        }

    def _refresh_overlay(self) -> None:
        if self.masks is None or self.review is None or self._overlay_handle is None:
            return
        rgba = build_overlay_rgba(
            self.masks, self.cell_infos, self.review, self.profile,
            self.border_ids, self.duplicate_ids,
        )
        self._overlay_handle.set_data(rgba)

    def _refresh_margin(self) -> None:
        if self.image is None or self._margin_handle is None:
            return
        h, w = self.image.shape[:2]
        if self.profile.show_margin_overlay and self.profile.margin_n > 0:
            rgba = build_margin_rgba(h, w, self.profile.margin_n)
            self._margin_handle.set_data(rgba)
            self._margin_handle.set_visible(True)
        else:
            self._margin_handle.set_visible(False)

    # LOD: numbers only show once the visible viewport is below this fraction of image width.
    LOD_VIEWPORT_FRACTION = 0.40

    def _refresh_numbers(self) -> None:
        """Render cell numbers only on *active* cells (Frage 3a constraint).
        Style: white text with thin black outline so it reads on any background.
        Visibility is gated by zoom via _apply_number_visibility() (LOD).
        """
        for a in self._number_artists:
            a.remove()
        self._number_artists = []
        if self.review is None:
            return
        excluded = set(self.review.force_excluded)
        included = set(self.review.force_included)
        for cid, info in self.cell_infos.items():
            is_active = (cid not in excluded) and (cid not in self.border_ids or cid in included)
            if not is_active:
                continue
            t = self.ax.text(
                info.cx, info.cy, str(cid),
                color="white", fontsize=9, ha="center", va="center",
                fontweight="bold", zorder=3,
            )
            t.set_path_effects([Stroke(linewidth=2.0, foreground="black"), Normal()])
            self._number_artists.append(t)
        self._apply_number_visibility()

    def _apply_number_visibility(self) -> None:
        """Hide numbers when overlay is off or when zoomed out beyond LOD threshold."""
        if not self._number_artists:
            return
        overlay_on = self._overlay_handle is not None and self._overlay_handle.get_visible()
        zoomed_in = self._is_zoomed_in()
        visible = overlay_on and zoomed_in
        for a in self._number_artists:
            a.set_visible(visible)

    def _is_zoomed_in(self) -> bool:
        if self.image is None:
            return False
        h, w = self.image.shape[:2]
        xl = self.ax.get_xlim()
        viewport_w = abs(xl[1] - xl[0])
        return viewport_w < (w * self.LOD_VIEWPORT_FRACTION)

    # ─────────────────────────────────────────
    # Mouse handlers
    # ─────────────────────────────────────────
    def _on_button_press(self, event):
        if event.inaxes != self.ax or self.image is None:
            return
        x, y = event.xdata, event.ydata
        if x is None or y is None:
            return
        if event.button == 1:
            tool = self.settings.active_tool
            if tool == TOOL_ADD:
                self._act_add(x, y)
            elif tool == TOOL_REMOVE:
                self._act_remove_toggle(x, y)
            elif tool == TOOL_TYPE:
                self._act_type(x, y)
        elif event.button == 2:
            self._pan_start = (
                event.x, event.y, *self.ax.get_xlim(), *self.ax.get_ylim(),
            )

    def _on_button_release(self, event):
        if event.button == 2:
            self._pan_start = None

    def _on_motion(self, event):
        if event.inaxes == self.ax and event.xdata is not None:
            self.cursor_xy = (event.xdata, event.ydata)
        if self._pan_start is not None and event.x is not None and event.y is not None:
            x0, y0, xl0, xl1, yl0, yl1 = self._pan_start
            inv = self.ax.transData.inverted()
            d0 = inv.transform((x0, y0))
            d1 = inv.transform((event.x, event.y))
            dx = d0[0] - d1[0]
            dy = d0[1] - d1[1]
            self.ax.set_xlim(xl0 + dx, xl1 + dx)
            self.ax.set_ylim(yl0 + dy, yl1 + dy)
            self.canvas.draw_idle()

    def _on_scroll(self, event):
        if event.inaxes != self.ax or self.image is None or event.xdata is None:
            return
        scale = 0.85 if event.button == "up" else 1.176
        xl = self.ax.get_xlim()
        yl = self.ax.get_ylim()
        self.ax.set_xlim(
            event.xdata + (xl[0] - event.xdata) * scale,
            event.xdata + (xl[1] - event.xdata) * scale,
        )
        self.ax.set_ylim(
            event.ydata + (yl[0] - event.ydata) * scale,
            event.ydata + (yl[1] - event.ydata) * scale,
        )
        self.canvas.draw_idle()
