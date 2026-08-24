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

import sys

import numpy as np
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse
from matplotlib.patheffects import Stroke, Normal
from matplotlib.transforms import Bbox
from skimage.draw import disk as sk_disk
from skimage.measure import find_contours, regionprops_table

from .persistence import (
    AppSettings, ManualCell, ReviewState,
    TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE,
)
from .profile import ProfileData
from .theme import OUTLINE_STYLES, palette as _palette


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
def _hex_to_rgb_u8(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# Unified visual scheme: one inactive color for both border + excluded (per design).
# Light gray fill + higher alpha so it stays visible on dark images.
# uint8 (0-255), not float — matplotlib reads RGBA uint8 natively and the array is
# H*W*4 bytes instead of H*W*16: 9.2 MB instead of 36.9 MB for a 1920x1200 image.
COLOR_INACTIVE_U8    = (204, 204, 204)
_DEFAULT_COLOR_U8    = (255, 217, 0)

# Fill opacity. The original 0.42 washed the type colours out against a bright
# field — measured on real images (median grey 245/255) the smallest colour
# distance between two types rose from ΔE 21.6 to 27.6 by going to 0.55.
# Per profile via ProfileData.overlay_alpha; these are only the fallbacks.
DEFAULT_OVERLAY_ALPHA = 0.55
INACTIVE_ALPHA_BONUS  = 0.13     # excluded cells read as a more solid grey
ALPHA_ACTIVE_U8       = round(DEFAULT_OVERLAY_ALPHA * 255)
ALPHA_INACTIVE_U8     = round(min(1.0, DEFAULT_OVERLAY_ALPHA + INACTIVE_ALPHA_BONUS) * 255)

RGBA = tuple[int, int, int, int]


def cell_contours(masks: np.ndarray, info: CellInfo) -> list[np.ndarray]:
    """Boundary polylines of one cell as (x, y) arrays in image coordinates.

    Only the cell's own bounding box is scanned, so this stays cheap even though
    it runs per marked cell. The box is padded by one pixel before tracing, so a
    cell that reaches its own bbox edge still yields a closed loop.
    """
    h, w = masks.shape
    y0, y1 = max(0, info.bbox_y0), min(h, info.bbox_y1)
    x0, x1 = max(0, info.bbox_x0), min(w, info.bbox_x1)
    if y0 >= y1 or x0 >= x1:
        return []
    sub = np.pad((masks[y0:y1, x0:x1] == info.cell_id).astype(np.float32), 1)
    out = []
    for c in find_contours(sub, 0.5):
        out.append(np.column_stack([c[:, 1] - 1.0 + x0, c[:, 0] - 1.0 + y0]))
    return out


class OverlayColors:
    """The ONLY place a cell's overlay color is decided.

    Both the full rebuild (`build_overlay_rgba`) and the single-cell repaint
    (`patch_overlay_cell`) resolve colors through this object, so an incremental
    update cannot drift away from what a full rebuild would have produced.
    `tests/test_overlay.py` asserts exactly that, pixel for pixel.

    Cheap to construct (a few sets + one small dict), so callers build one per
    operation rather than caching a stale view of the review state.
    """

    def __init__(self, review: ReviewState, profile: ProfileData,
                 border_ids: set[int], duplicate_ids: set[int] | None = None):
        self._excluded = set(review.force_excluded)
        self._included = set(review.force_included)
        self._types = review.types
        self._border_ids = border_ids
        self._duplicate_ids = duplicate_ids or set()
        self._default_key = profile.default_type_key()
        self._type_colors = {t.key: _hex_to_rgb_u8(t.color) for t in profile.cell_types}
        self._default_color = self._type_colors.get(self._default_key, _DEFAULT_COLOR_U8)
        a = float(getattr(profile, "overlay_alpha", DEFAULT_OVERLAY_ALPHA))
        a = max(0.05, min(1.0, a))
        self.alpha_active = round(a * 255)
        self.alpha_inactive = round(min(1.0, a + INACTIVE_ALPHA_BONUS) * 255)

    def is_active(self, cid: int) -> bool:
        """Effective active state — the same rule the status bar counts by."""
        if cid in self._excluded:
            return False
        auto_inactive = (cid in self._border_ids) or (cid in self._duplicate_ids)
        return (not auto_inactive) or (cid in self._included)

    def type_key(self, cid: int) -> str:
        return self._types.get(str(cid), self._default_key)

    def of(self, cid: int) -> RGBA:
        if not self.is_active(cid):
            return (*COLOR_INACTIVE_U8, self.alpha_inactive)
        return (*self._type_colors.get(self.type_key(cid), self._default_color),
                self.alpha_active)


def build_overlay_rgba(
    masks: np.ndarray,
    cell_infos: dict[int, CellInfo],
    review: ReviewState,
    profile: ProfileData,
    border_ids: set[int],
    duplicate_ids: set[int] | None = None,
) -> np.ndarray:
    """Full RGBA overlay: active cells = type color, inactive (border/excluded/
    duplicate) = unified gray. Force_included cells render as active.

    Vectorized via color_table[masks]. Needed whenever *many* cells can change at
    once (new image, margin change, profile swap); a single-cell edit should go
    through patch_overlay_cell() instead.
    """
    colors = OverlayColors(review, profile, border_ids, duplicate_ids)
    max_id = int(masks.max())
    color_table = np.zeros((max_id + 1, 4), dtype=np.uint8)
    for cid in cell_infos:
        if cid <= 0 or cid > max_id:
            continue
        color_table[cid] = colors.of(cid)
    return color_table[masks]


def patch_overlay_cell(rgba: np.ndarray, masks: np.ndarray,
                       info: CellInfo, value: RGBA) -> None:
    """Repaint exactly one cell into an existing overlay, in place.

    Touches only the cell's bounding box instead of re-deriving the whole
    H*W*4 array — the difference between a few hundred bytes and ~9 MB per click.
    The bbox is clamped: manual cells are stored as centre +/- radius
    (see _act_add) and may stick out of the image, and a negative slice bound
    would silently index from the far edge.
    """
    h, w = masks.shape
    y0, y1 = max(0, info.bbox_y0), min(h, info.bbox_y1)
    x0, x1 = max(0, info.bbox_x0), min(w, info.bbox_x1)
    if y0 >= y1 or x0 >= x1:
        return
    window = rgba[y0:y1, x0:x1]                      # view, writes through
    window[masks[y0:y1, x0:x1] == info.cell_id] = value


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
        note_callback: Callable[[int], None] | None = None,
        on_overlay_autoshown: Callable[[], None] | None = None,
        new_circle_radius: int = 17,
    ):
        P = _palette(settings.theme)
        super().__init__(parent, bg=P["canvas_bg"])
        self.settings = settings
        self.profile = profile
        # Auto-excluded by the size filter. The name stays `duplicate_ids`
        # because it maps 1:1 to the `is_duplicate` column of the exported
        # stats CSV and erys.db, which downstream analysis relies on.
        self.duplicate_ids: set[int] = set()
        self.save_callback = save_callback
        # Right-click on a cell -> the app opens the remark editor for it.
        self.note_callback = note_callback or (lambda _cid: None)
        # Fired after an edit had to reveal a hidden overlay, so the app can
        # persist the setting and say why the view changed.
        self.on_overlay_autoshown = on_overlay_autoshown or (lambda: None)
        self._theme_palette = P
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

        # Figure — theme-aware background
        self.fig = Figure(figsize=(10, 7), facecolor=P["canvas_bg"])
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(P["canvas_bg"])
        self.ax.axis("off")
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # Persistent image handles (set on first set_image)
        self._im_handle = None
        self._overlay_handle = None
        self._margin_handle = None
        # The live overlay array is kept so a single-cell edit can repaint just
        # that cell's bbox instead of rebuilding all H*W*4 bytes.
        self._overlay_rgba: np.ndarray | None = None
        # cell_id -> Text artist, alive for as long as the image is. Cells come
        # and go only via _act_add / undo; activity changes just flip visibility.
        self._number_artists: dict[int, object] = {}
        self._number_active: set[int] = set()
        # cell_id -> the Line2D pair(s) drawing that cell's type contour. Only
        # cells whose type defines an outline get an entry, which in practice is
        # a handful per image (~0.2% of cells are not "normal").
        self._outline_artists: dict[int, list] = {}
        # cell_id -> marker Text for cells carrying a remark. Same bookkeeping as
        # the numbers: created per image, individually removable.
        self._note_artists: dict[int, object] = {}
        self._image_shape: tuple[int, int] | None = None  # to detect re-init

        # ── Blit cache for single-cell edits ────────────────────────────────
        # A full figure redraw re-resamples all three full-size image layers.
        # Measured on 1200x1920 review data at a 1920x944 canvas that is ~510 ms
        # per click — the whole reason toggling a cell felt sluggish, and ~97 %
        # of the click. Toggling one cell changes a rectangle a few dozen pixels
        # across, so the fast path restores the last full rendering, rebuilds
        # only that rectangle, and blits it. See _blit_cell().
        self._blit_bg = None                        # Agg region of the last render
        self._blit_key: tuple | None = None         # viewport it was captured at
        self._blit_sub: dict[str, object] = {}      # small reusable sub-artists

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
        self.duplicate_ids = set()
        self.history.clear()
        self.selected_id = None
        self.ax.cla()
        self.ax.set_facecolor(self._theme_palette["canvas_bg"])
        self.ax.axis("off")
        self._im_handle = self._overlay_handle = self._margin_handle = None
        self._overlay_rgba = None
        self._number_artists = {}
        self._number_active = set()
        self._outline_artists = {}
        self._note_artists = {}
        self._image_shape = None
        self._full_redraw()

    def _colors(self) -> OverlayColors:
        """Current color/activity resolver. Cheap — rebuilt per operation so it
        can never hold a stale view of the review state."""
        return OverlayColors(self.review, self.profile, self.border_ids,
                             self.duplicate_ids)

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
        # Duplicates belong to the derived state of THIS image, exactly like
        # border_ids — so they are resolved here, before anything renders.
        # (They used to be computed by the app *after* set_image returned, which
        # meant the first overlay was painted with the previous image's
        # duplicates and a failure left those stale IDs feeding the status bar.)
        # Local import: sizefilter.py imports CellInfo from this module.
        from .sizefilter import detect_size_outliers
        self.duplicate_ids = detect_size_outliers(
            self.cell_infos, self.profile.size_filter,
        )

        h, w = self.image.shape[:2]
        same_shape = (self._image_shape == (h, w)) and self._im_handle is not None

        if same_shape:
            self._im_handle.set_data(self.image_norm)
        else:
            self.ax.cla()          # drops every artist, including the numbers
            self.ax.set_facecolor(self._theme_palette["canvas_bg"])
            self.ax.axis("off")
            self._im_handle = self.ax.imshow(self.image_norm, interpolation="nearest", zorder=0)
            blank_overlay = np.zeros((h, w, 4), dtype=np.uint8)
            self._overlay_handle = self.ax.imshow(blank_overlay, interpolation="nearest", zorder=1)
            self._margin_handle = self.ax.imshow(
                np.zeros((h, w, 4), dtype=np.float32), interpolation="nearest", zorder=2)
            self._number_artists = {}
            self._number_active = set()
            self._note_artists = {}
            self._image_shape = (h, w)
            self.ax.set_xlim(-0.5, w - 0.5)
            self.ax.set_ylim(h - 0.5, -0.5)

        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self._refresh_outlines()
        self._refresh_notes()
        self._full_redraw()

    def reset_zoom(self) -> None:
        if self.image is None:
            return
        h, w = self.image.shape[:2]
        self.ax.set_xlim(-0.5, w - 0.5)
        self.ax.set_ylim(h - 0.5, -0.5)
        self._full_redraw()

    def set_overlay_visible(self, visible: bool) -> None:
        if self._overlay_handle is not None:
            self._overlay_handle.set_visible(visible)
        # Margin overlay is coupled to the main overlay (per user spec):
        # one switch controls both. Margin still respects the profile-level
        # `show_margin_overlay` preference (so user can disable it in profile).
        if self._margin_handle is not None:
            self._margin_handle.set_visible(
                visible and self.profile.show_margin_overlay and self.profile.margin_n > 0
            )
        self._apply_number_visibility()
        self._apply_outline_visibility()
        self._apply_note_visibility()
        self._full_redraw()

    def set_margin_n(self, n: int) -> None:
        self.profile.margin_n = int(n)
        self._recompute_borders()
        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()   # borders moved -> which cells count moved with them
        self._refresh_outlines()
        self._full_redraw()

    def set_profile(self, profile: ProfileData) -> None:
        """Swap to a different study profile. Re-renders with new colors/margin/types."""
        self.profile = profile
        self._recompute_borders()
        self._recompute_duplicates()   # the profile carries the duplicate config
        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self._refresh_outlines()
        self._full_redraw()

    def refresh_appearance(self) -> None:
        """Called after Settings change: re-render margin overlay + cell numbers."""
        self._refresh_margin()
        self._refresh_numbers()
        self._full_redraw()

    def refresh_overlay_from_data(self) -> None:
        """Called externally if review data changed without going through canvas methods."""
        self._refresh_overlay()
        self._refresh_numbers()
        self._refresh_outlines()
        self._full_redraw()

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
        # One new cell -> one new artist and one patched bbox, not a full rebuild.
        self._number_artists[new_id] = self._make_number(new_id, self.cell_infos[new_id])
        self._patch_cell(new_id)
        # Same shape as a toggle: one cell's rectangle changed, and its number
        # artist already exists, so the blit rebuild picks it up.
        self._draw_after_cell_edit(new_id)
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
        # Patching also flips this cell's number — previously the number stayed
        # on screen after the cell was switched inactive.
        self._patch_cell(cid)
        self._draw_after_cell_edit(cid)
        return True

    def _act_type(self, x: float, y: float) -> bool:
        cid = self._find_cell_at(x, y)
        if cid is None or self.review is None:
            return False
        prev = self.review.types.get(str(cid))
        self.review.types[str(cid)] = self.profile.active_type_key
        self.history.append(("type", cid, prev))
        self.save_callback()
        self._patch_cell(cid)
        self._draw_after_cell_edit(cid)
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
            self.save_callback()
            self._patch_cell(cid)
        elif kind == "add":
            _, cid = action
            self.masks[self.masks == cid] = 0
            self.review.manual_cells = [m for m in self.review.manual_cells if m.cell_id != cid]
            self.cell_infos.pop(cid, None)
            self.border_ids.discard(cid)
            self.next_id = max(1, self.next_id - 1)
            self.review.next_manual_id = self.next_id
            self.save_callback()
            # The label vanished from `masks`, so there is nothing left to patch
            # against — and undoing an add is rare. Rebuild both layers.
            self._refresh_overlay()
            self._refresh_numbers()
            self._refresh_outlines()
        elif kind == "type":
            _, cid, prev = action
            if prev is None:
                self.review.types.pop(str(cid), None)
            else:
                self.review.types[str(cid)] = prev
            self.save_callback()
            self._patch_cell(cid)
        self._full_redraw()
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

    def _recompute_duplicates(self) -> None:
        # Local import: sizefilter.py imports CellInfo from this module.
        from .sizefilter import detect_size_outliers
        self.duplicate_ids = detect_size_outliers(
            self.cell_infos, self.profile.size_filter,
        )

    def _refresh_overlay(self) -> None:
        """Full rebuild. Use when many cells can change at once (new image,
        margin, profile); a single edited cell should go through _patch_cell."""
        if self.masks is None or self.review is None or self._overlay_handle is None:
            return
        self._overlay_rgba = build_overlay_rgba(
            self.masks, self.cell_infos, self.review, self.profile,
            self.border_ids, self.duplicate_ids,
        )
        self._overlay_handle.set_data(self._overlay_rgba)

    def _patch_cell(self, cid: int, colors: OverlayColors | None = None) -> None:
        """Repaint exactly the one cell an edit touched, and sync its number."""
        info = self.cell_infos.get(cid)
        if info is None or self.masks is None or self._overlay_handle is None:
            return
        if self._overlay_rgba is None:      # nothing built yet — nothing to patch
            self._refresh_overlay()
            return
        colors = colors or self._colors()
        patch_overlay_cell(self._overlay_rgba, self.masks, info, colors.of(cid))
        self._overlay_handle.set_data(self._overlay_rgba)
        self._set_number_active(cid, colors.is_active(cid))
        self._set_cell_outline(cid, colors)

    # ─────────────────────────────────────────
    # Drawing: full redraw vs. single-cell blit
    # ─────────────────────────────────────────
    def _full_redraw(self) -> None:
        """Redraw the whole figure and drop the blit cache.

        Everything that is not one cell changing appearance goes through here:
        a new image, a new margin, zoom, pan, theme, overlay on/off. The cache
        is a snapshot of a rendering, so any of those makes it a lie.
        """
        self._blit_bg = None
        self.canvas.draw_idle()

    # The fast path is Windows-only, and that is a deliberate exception to the
    # rule that these performance changes carry no platform branch.
    #
    # Measured on macOS (Agg buffer, real study images, 1400x900): the rebuilt
    # rectangle differs from a full redraw by up to 103 of 255 per channel over
    # ~5700 pixels — the edited cell renders visibly darker and stays that way
    # until the next full redraw. Reproducible at integer scales 1.0 and 2.0 as
    # well, so it is not a resampling phase problem; data, norm object, colormap,
    # alpha, zorder and interpolation were each checked to be identical between
    # the full-size artists and their stand-ins. Cause not found.
    #
    # Windows is where the fast path earns its keep (575 ms -> 85 ms per click,
    # against 30-45x slower filesystem operations on NTFS), and it was verified
    # there. A macOS click was already fast, so switching it off costs nothing
    # that anyone would notice. tests/test_blit_equivalence.py keeps guarding it
    # wherever it is on.
    BLIT_ENABLED = sys.platform == "win32"

    def _draw_after_cell_edit(self, cid: int) -> None:
        """Cheap repaint for a one-cell edit, with the full redraw as fallback."""
        if not self.BLIT_ENABLED or not self._blit_cell(cid):
            self._full_redraw()

    # Padding around the cell, in display pixels, so a number or an outline
    # that reaches past its own bbox still lands inside the repainted rectangle.
    BLIT_PAD_PX = 48

    def _blit_viewport_key(self) -> tuple:
        widget = self.canvas.get_tk_widget()
        return (widget.winfo_width(), widget.winfo_height(),
                tuple(self.ax.get_xlim()), tuple(self.ax.get_ylim()))

    def _blit_subartists(self) -> dict:
        """The three small stand-ins for the full-size layers, created once.

        They are `animated`, so a normal draw skips them — otherwise a full
        redraw would paint a stale postage stamp over the figure. They share the
        real image layer's colormap and norm object, so the rectangle they
        rebuild is colour-identical to what a full redraw would produce.
        """
        if self._blit_sub:
            return self._blit_sub
        blank_f = np.zeros((2, 2), dtype=np.float32)
        blank_rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        image = self.ax.imshow(blank_f, interpolation="nearest", animated=True, zorder=0)
        image.set_cmap(self._im_handle.get_cmap())
        image.set_norm(self._im_handle.norm)
        overlay = self.ax.imshow(blank_rgba, interpolation="nearest", animated=True, zorder=1)
        margin = self.ax.imshow(np.zeros((2, 2, 4), dtype=np.float32),
                                interpolation="nearest", animated=True, zorder=2)
        self._blit_sub = {"image": image, "overlay": overlay, "margin": margin}
        return self._blit_sub

    def _blit_cell(self, cid: int) -> bool:
        """Repaint only the rectangle around one cell. False -> caller must full-draw.

        The rectangle is rebuilt from scratch — image, overlay and margin
        sub-arrays plus every cell artist reaching into it — so it does not
        matter what the restored background happened to show there. Only that
        rectangle is copied to the screen, so the rest of the canvas keeps the
        pixels of the last full rendering, which is why the cached region is
        re-taken afterwards: it must keep describing what is actually on screen.
        """
        info = self.cell_infos.get(cid)
        if (info is None or self.masks is None or self.image_norm is None
                or self._overlay_rgba is None or self._im_handle is None
                or self._overlay_handle is None or self._margin_handle is None):
            return False
        try:
            key = self._blit_viewport_key()
            if self._blit_bg is None or self._blit_key != key:
                # First edit after any other change pays for one full render;
                # every following one is the cheap path.
                self.canvas.draw()
                self._blit_bg = self.canvas.copy_from_bbox(self.ax.bbox)
                self._blit_key = key

            h, w = self.masks.shape
            inv = self.ax.transData.inverted()
            origin = inv.transform((0.0, 0.0))
            padded = inv.transform((self.BLIT_PAD_PX, self.BLIT_PAD_PX))
            pad_x = abs(padded[0] - origin[0])
            pad_y = abs(padded[1] - origin[1])
            x0 = max(0, int(np.floor(info.bbox_x0 - pad_x)))
            x1 = min(w - 1, int(np.ceil(info.bbox_x1 + pad_x)))
            y0 = max(0, int(np.floor(info.bbox_y0 - pad_y)))
            y1 = min(h - 1, int(np.ceil(info.bbox_y1 + pad_y)))
            if x1 < x0 or y1 < y0:
                return False

            subs = self._blit_subartists()
            extent = (x0 - 0.5, x1 + 0.5, y1 + 0.5, y0 - 0.5)
            subs["image"].set_data(self.image_norm[y0:y1 + 1, x0:x1 + 1])
            subs["image"].set_extent(extent)
            subs["overlay"].set_data(self._overlay_rgba[y0:y1 + 1, x0:x1 + 1])
            subs["overlay"].set_extent(extent)
            margin_data = self._margin_handle.get_array()
            subs["margin"].set_data(np.asarray(margin_data)[y0:y1 + 1, x0:x1 + 1])
            subs["margin"].set_extent(extent)

            self.canvas.restore_region(self._blit_bg)
            self.ax.draw_artist(subs["image"])
            if self._overlay_handle.get_visible():
                self.ax.draw_artist(subs["overlay"])
            if self._margin_handle.get_visible():
                self.ax.draw_artist(subs["margin"])
            # Neighbours whose number, outline or remark marker reaches into the
            # rectangle were wiped by the rebuild above and have to come back.
            for other, other_info in self.cell_infos.items():
                if (other_info.bbox_x1 < x0 or other_info.bbox_x0 > x1
                        or other_info.bbox_y1 < y0 or other_info.bbox_y0 > y1):
                    continue
                number = self._number_artists.get(other)
                if number is not None and number.get_visible():
                    self.ax.draw_artist(number)
                for artist in self._outline_artists.get(other, ()) or ():
                    if artist.get_visible():
                        self.ax.draw_artist(artist)
                for artist in self._note_artists.get(other, ()) or ():
                    if artist.get_visible():
                        self.ax.draw_artist(artist)

            corners = self.ax.transData.transform(
                [[x0 - 0.5, y1 + 0.5], [x1 + 0.5, y0 - 0.5]])
            region = Bbox([[corners[:, 0].min(), corners[:, 1].min()],
                           [corners[:, 0].max(), corners[:, 1].max()]])
            region = Bbox.intersection(region, self.ax.bbox)
            if region is None or region.width < 1 or region.height < 1:
                return False
            self.canvas.blit(region)
            self.canvas.flush_events()
            # The Agg buffer now holds the previous rendering plus this rebuilt
            # rectangle — exactly what the screen shows. Keep it as the new
            # background so overlapping edits cannot undo each other.
            self._blit_bg = self.canvas.copy_from_bbox(self.ax.bbox)
            return True
        except Exception:
            # Any surprise (a resize mid-blit, a backend without blitting) must
            # cost correctness nothing: drop the cache and let the caller redraw.
            self._blit_bg = None
            return False

    def _refresh_margin(self) -> None:
        if self.image is None or self._margin_handle is None:
            return
        h, w = self.image.shape[:2]
        # Coupled to overlay visibility (single toggle controls both layers).
        overlay_on = self._overlay_handle is not None and self._overlay_handle.get_visible()
        if overlay_on and self.profile.show_margin_overlay and self.profile.margin_n > 0:
            rgba = build_margin_rgba(h, w, self.profile.margin_n)
            self._margin_handle.set_data(rgba)
            self._margin_handle.set_visible(True)
        else:
            self._margin_handle.set_visible(False)

    # LOD: numbers only show once the visible viewport is below this fraction of image width.
    LOD_VIEWPORT_FRACTION = 0.40

    def _make_number(self, cid: int, info: CellInfo):
        """One number artist. White text, thin black outline so it reads on any
        background."""
        t = self.ax.text(
            info.cx, info.cy, str(cid),
            color="white", fontsize=9, ha="center", va="center",
            fontweight="bold", zorder=3,
        )
        t.set_path_effects([Stroke(linewidth=2.0, foreground="black"), Normal()])
        return t

    def _refresh_numbers(self) -> None:
        """Rebuild every number artist — one per cell, kept for the life of the
        image. *Which* of them show is decided by _apply_number_visibility.

        Numbers appear on active cells only (Frage 3a). Activity now comes from
        the same OverlayColors rule the fill color uses, so a duplicate-flagged
        cell no longer keeps a number it isn't counted for.
        """
        for a in self._number_artists.values():
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass          # already gone with an ax.cla()
        self._number_artists = {}
        self._number_active = set()
        if self.review is None:
            return
        colors = self._colors()
        for cid, info in self.cell_infos.items():
            self._number_artists[cid] = self._make_number(cid, info)
            if colors.is_active(cid):
                self._number_active.add(cid)
        self._apply_number_visibility()

    # ─────────────────────────────────────────
    # Remark markers
    # ─────────────────────────────────────────
    NOTE_GLYPH = "❝"          # U+275D — the same mark the tool carries, so
                              # button and marker read as one thing
    NOTE_COLOR = "#5B9CFF"    # accent_primary; see _make_note_marker()

    def _refresh_notes(self) -> None:
        """Rebuild the remark markers from review.notes."""
        for artists in self._note_artists.values():
            for a in artists:
                try:
                    a.remove()
                except (ValueError, NotImplementedError):
                    pass
        self._note_artists = {}
        if self.review is None:
            return
        for key in self.review.notes:
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if self.review.notes[key].strip():
                self._make_note_marker(cid)
        self._apply_note_visibility()

    def _make_note_marker(self, cid: int) -> None:
        """Blue ring plus the ✎ glyph on one annotated cell.

        Blue, not red: the cell type `stechapfel` is #FF6B6B and the error
        accent #F26B6B, so a red ring would be indistinguishable from a cell
        typed as an echinocyte. Blue is unused by every type and is the safest
        signal under red-green deficiency.

        A ring rather than an arrow to the centre: a field holds ~271 cells, and
        a dozen arrows cross each other and cover the neighbours they point past.
        The ring scales with the zoom and hides nothing.
        """
        info = self.cell_infos.get(cid)
        if info is None:
            return
        w = max(info.bbox_x1 - info.bbox_x0, 6)
        h = max(info.bbox_y1 - info.bbox_y0, 6)
        ring = Ellipse(
            (info.cx, info.cy), width=w * 1.45, height=h * 1.45,
            fill=False, edgecolor=self.NOTE_COLOR, linewidth=1.8, zorder=4,
        )
        ring.set_path_effects([Stroke(linewidth=3.2, foreground="black"), Normal()])
        self.ax.add_patch(ring)
        t = self.ax.text(
            info.cx + w * 0.32, info.cy - h * 0.32,
            self.NOTE_GLYPH, color=self.NOTE_COLOR, fontsize=11, ha="center",
            va="center", fontweight="bold", zorder=5,
        )
        t.set_path_effects([Stroke(linewidth=2.4, foreground="black"), Normal()])
        for a in (ring, t):
            a.set_visible(self._notes_shown())
        self._note_artists[cid] = [ring, t]

    def set_cell_note_marker(self, cid: int, has_note: bool) -> None:
        """Add or drop one cell's marker after its remark changed."""
        for a in self._note_artists.pop(cid, []) or []:
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass
        if has_note:
            self._make_note_marker(cid)
        self._full_redraw()

    def _notes_shown(self) -> bool:
        """Markers ride the overlay switch, like the numbers and the contours —
        one toggle for the whole annotation layer."""
        return self._overlay_handle is not None and self._overlay_handle.get_visible()

    def _apply_note_visibility(self) -> None:
        shown = self._notes_shown()
        for artists in self._note_artists.values():
            for a in artists:
                a.set_visible(shown)

    def _numbers_shown(self) -> bool:
        overlay_on = self._overlay_handle is not None and self._overlay_handle.get_visible()
        return overlay_on and self._is_zoomed_in()

    def _set_number_active(self, cid: int, active: bool) -> None:
        """Flip one cell's number without touching the other ~200 artists."""
        if active:
            self._number_active.add(cid)
        else:
            self._number_active.discard(cid)
        a = self._number_artists.get(cid)
        if a is not None:
            a.set_visible(active and self._numbers_shown())

    def _apply_number_visibility(self) -> None:
        """Hide numbers when the overlay is off, when zoomed out beyond the LOD
        threshold, or on cells that don't count."""
        if not self._number_artists:
            return
        shown = self._numbers_shown()
        for cid, a in self._number_artists.items():
            a.set_visible(shown and cid in self._number_active)

    # ─────────────────────────────────────────
    # Type contours (WCAG 1.4.1 — type not carried by fill colour alone)
    # ─────────────────────────────────────────
    OUTLINE_LW = 1.4          # white line; a black one sits under it, wider

    def _outlines_shown(self) -> bool:
        """Contours share the overlay switch — one toggle for the whole
        annotation layer. Unlike the numbers they have NO zoom threshold: at
        full-image view a cell is ~41 px across, which is exactly where the
        reviewer needs to see at a glance which cells are not normal."""
        return (self.profile.type_outlines_enabled
                and self._overlay_handle is not None
                and self._overlay_handle.get_visible())

    def _outline_style_for(self, cid: int, colors: OverlayColors):
        """Line style for this cell's type, or None if it should have no contour."""
        if not self.profile.type_outlines_enabled:
            return None       # switched off -> build no artists at all
        if not colors.is_active(cid):
            return None       # inactive cells render grey — their type is moot
        td = self.profile.type_by_key(colors.type_key(cid))
        return OUTLINE_STYLES.get(getattr(td, "outline", "") or "") if td else None

    def _make_outline(self, cid: int, style) -> list:
        """Black line under a white one, so the contour reads on any background."""
        info = self.cell_infos.get(cid)
        if info is None or self.masks is None:
            return []
        artists = []
        visible = self._outlines_shown()
        for c in cell_contours(self.masks, info):
            for color, lw, z in (("black", self.OUTLINE_LW + 1.6, 3.5),
                                 ("white", self.OUTLINE_LW, 3.6)):
                (line,) = self.ax.plot(c[:, 0], c[:, 1], ls=style, color=color,
                                       lw=lw, zorder=z, solid_capstyle="butt")
                line.set_visible(visible)
                artists.append(line)
        return artists

    def _drop_outline(self, cid: int) -> None:
        for a in self._outline_artists.pop(cid, []):
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass          # already gone with an ax.cla()

    def _set_cell_outline(self, cid: int, colors: OverlayColors) -> None:
        """Rebuild just this cell's contour after an edit."""
        self._drop_outline(cid)
        style = self._outline_style_for(cid, colors)
        if style is not None:
            self._outline_artists[cid] = self._make_outline(cid, style)

    def _refresh_outlines(self) -> None:
        """Rebuild every contour — after a new image, margin or profile change."""
        for cid in list(self._outline_artists):
            self._drop_outline(cid)
        if self.review is None or self.masks is None:
            return
        colors = self._colors()
        for cid in self.cell_infos:
            style = self._outline_style_for(cid, colors)
            if style is not None:
                self._outline_artists[cid] = self._make_outline(cid, style)

    def _apply_outline_visibility(self) -> None:
        shown = self._outlines_shown()
        for artists in self._outline_artists.values():
            for a in artists:
                a.set_visible(shown)

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
    def _autoshow_overlay(self) -> bool:
        """Reveal a hidden overlay before an edit. True if it had been hidden.

        Every editing gesture draws its result *into* the overlay — the cell
        colour, the number, the outline, the ✎ marker. `set_overlay_visible`
        hides all four together, so with the overlay off a click still lands
        and still saves, but nothing on screen moves. That is indistinguishable
        from a broken click, and it is exactly how this was first reported.
        Picking a tool and clicking a cell is unambiguous intent to edit, so
        the view follows the intent; `o` stays available for looking at the
        raw image.
        """
        if self.settings.overlay_visible:
            return False
        self.settings.overlay_visible = True
        self.set_overlay_visible(True)
        return True

    def _on_button_press(self, event):
        if event.inaxes != self.ax or self.image is None:
            return
        x, y = event.xdata, event.ydata
        if x is None or y is None:
            return
        if event.button == 1:
            revealed = self._autoshow_overlay()
            tool = self.settings.active_tool
            if tool == TOOL_ADD:
                self._act_add(x, y)
            elif tool == TOOL_REMOVE:
                self._act_remove_toggle(x, y)
            elif tool == TOOL_TYPE:
                self._act_type(x, y)
            elif tool == TOOL_NOTE:
                # None means "no cell here" — the app reads that as the remark
                # for the whole image, so one tool covers both targets and the
                # user never has to hunt for a second entry point.
                self.note_callback(self._find_cell_at(x, y))
            elif tool == TOOL_CURSOR:
                # Read-only mode: only a cell that already carries a remark
                # responds, and it only opens that remark. Everything else is
                # inert on purpose — this is the one mode where a stray click
                # cannot alter the review.
                cid = self._find_cell_at(x, y)
                if cid is not None and self.review is not None \
                        and self.review.notes.get(str(cid), "").strip():
                    self.note_callback(cid)
            # After the dispatch: the edit runs save_callback -> _update_status,
            # which rewrites the status line and would swallow an earlier note.
            if revealed:
                self.on_overlay_autoshown()
        elif event.button == 3:
            # Remarks are occasional, so they get their own gesture instead of a
            # fourth tool: no mode switch, available whatever tool is active.
            # matplotlib normalises button numbers (3 = right) on every platform,
            # unlike raw Tk where macOS reports right-click as Button-2.
            cid = self._find_cell_at(x, y)
            if cid is not None:
                revealed = self._autoshow_overlay()
                self.note_callback(cid)
                if revealed:
                    self.on_overlay_autoshown()
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
            self._full_redraw()

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
        self._full_redraw()
