"""Cell geometry and the RGBA overlay, without any UI toolkit.

Everything here is pure numpy/skimage on a label image plus the review state:
the per-cell geometry (`CellInfo`), the border rule, the one place a cell's
colour is decided (`OverlayColors`) and the full / single-cell overlay builds.
It used to live in canvas.py, which drags in Tk and the TkAgg backend — and
export, image_state and sizefilter imported it from there. Kept toolkit-free
so the Qt view and the headless code paths share it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from skimage.measure import find_contours, regionprops_table

from .persistence import ReviewState
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
