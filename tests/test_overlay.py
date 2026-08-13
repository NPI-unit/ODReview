"""Consistency gate for the incremental overlay (canvas.py).

Proves that repainting ONE cell with `patch_overlay_cell` lands on exactly the
same pixels a full `build_overlay_rgba` would have produced — for every edit the
canvas can make. Without this, the two code paths could drift apart silently and
the reviewer would see colors that no longer match the counts.

Also pins the float -> uint8 conversion of the color scheme, so a future edit to
the palette can't quietly change what "active" looks like.

Runs as a plain script, no GUI and no real images:
    ./.venv/bin/python tests/test_overlay.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Allow running the file directly (python tests/test_overlay.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.canvas import (
    COLOR_INACTIVE_U8, CellInfo, OverlayColors, build_overlay_rgba,
    cell_contours, compute_cell_infos, patch_overlay_cell,
)
from erysreview.persistence import ManualCell, ReviewState
from erysreview.profile import ProfileData, SizeFilter

H, W = 80, 80
CHECKS = 0


def _mask_with_cells() -> np.ndarray:
    """Four interior cells plus one that touches the left edge (border cell)."""
    m = np.zeros((H, W), dtype=np.int32)
    m[10:20, 10:20] = 1
    m[10:20, 30:40] = 2
    m[40:50, 10:20] = 3
    m[40:50, 30:40] = 4
    m[60:70, 0:8] = 5          # touches x=0 -> border cell when margin_n > 0
    return m


def _profile() -> ProfileData:
    p = ProfileData()
    p.margin_n = 2
    p.size_filter = SizeFilter(enabled=False)
    return p


def _full(masks, infos, review, profile, borders, dups) -> np.ndarray:
    return build_overlay_rgba(masks, infos, review, profile, borders, dups)


def _assert_patch_matches_full(label: str, masks, infos, review, profile,
                               borders, dups, cid: int, before: np.ndarray) -> None:
    """`before` is the overlay as it stood BEFORE the edit; `review` already has
    the edit applied. Patch the one cell and demand pixel equality."""
    global CHECKS
    colors = OverlayColors(review, profile, borders, dups)
    patched = before.copy()
    patch_overlay_cell(patched, masks, infos[cid], colors.of(cid))
    full = _full(masks, infos, review, profile, borders, dups)
    if not np.array_equal(patched, full):
        diff = int(np.count_nonzero(np.any(patched != full, axis=-1)))
        raise AssertionError(
            f"{label}: patch != full rebuild ({diff} pixels differ)")
    CHECKS += 1
    print(f"  ok  {label}")


def main() -> int:
    profile = _profile()
    masks = _mask_with_cells()
    infos = compute_cell_infos(masks, manual_ids=set())
    borders = {5}                       # cell 5 touches the margin
    dups: set[int] = set()

    # ── 1. dtype + the color scheme itself ──────────────────────────────
    review = ReviewState()
    rgba = _full(masks, infos, review, profile, borders, dups)
    assert rgba.dtype == np.uint8, f"overlay must be uint8, got {rgba.dtype}"
    assert rgba.shape == (H, W, 4), rgba.shape
    ref = OverlayColors(review, profile, borders, dups)
    normal_rgb = tuple(int(c) for c in rgba[15, 15, :3])
    assert int(rgba[15, 15, 3]) == ref.alpha_active, "active alpha changed"
    assert tuple(int(c) for c in rgba[65, 4, :3]) == COLOR_INACTIVE_U8, \
        "border cell is not the inactive gray"
    assert int(rgba[65, 4, 3]) == ref.alpha_inactive, "inactive alpha changed"
    assert tuple(rgba[0, 0]) == (0, 0, 0, 0), "background must stay transparent"
    # The opacity is a profile setting now — prove it actually reaches the pixels.
    profile.overlay_alpha = 0.90
    louder = _full(masks, infos, review, profile, borders, dups)
    assert int(louder[15, 15, 3]) == round(0.90 * 255), \
        "overlay_alpha from the profile does not reach the overlay"
    profile.overlay_alpha = 0.55
    print(f"  ok  uint8 scheme (active rgb={normal_rgb} a={ref.alpha_active}, "
          f"inactive={COLOR_INACTIVE_U8} a={ref.alpha_inactive}); "
          f"profile alpha honoured")

    # ── 2. _act_type: assign a type to cell 2 ───────────────────────────
    before = _full(masks, infos, review, profile, borders, dups)
    review.types["2"] = profile.cell_types[1].key
    _assert_patch_matches_full("type assign", masks, infos, review, profile,
                               borders, dups, 2, before)

    # ── 3. undo of a type assign ────────────────────────────────────────
    before = _full(masks, infos, review, profile, borders, dups)
    review.types.pop("2", None)
    _assert_patch_matches_full("type undo", masks, infos, review, profile,
                               borders, dups, 2, before)

    # ── 4. _act_remove_toggle: exclude an active cell ───────────────────
    before = _full(masks, infos, review, profile, borders, dups)
    review.force_excluded.append(3)
    _assert_patch_matches_full("exclude active cell", masks, infos, review,
                               profile, borders, dups, 3, before)

    # ── 5. …and toggling it back ────────────────────────────────────────
    before = _full(masks, infos, review, profile, borders, dups)
    review.force_excluded.remove(3)
    _assert_patch_matches_full("re-include excluded cell", masks, infos, review,
                               profile, borders, dups, 3, before)

    # ── 6. force-include a border cell (the tricky branch) ──────────────
    before = _full(masks, infos, review, profile, borders, dups)
    review.force_included.append(5)
    _assert_patch_matches_full("force-include border cell", masks, infos, review,
                               profile, borders, dups, 5, before)

    # ── 7. duplicate flag makes a cell inactive ─────────────────────────
    dups = {4}
    before_dups = _full(masks, infos, review, profile, borders, set())
    _assert_patch_matches_full("duplicate flagged", masks, infos, review,
                               profile, borders, dups, 4, before_dups)
    colors = OverlayColors(review, profile, borders, dups)
    assert not colors.is_active(4), "a duplicate must not count as active"
    assert colors.is_active(5), "force_included border cell must count as active"
    print("  ok  is_active agrees with the fill color on border/duplicate cells")
    dups = set()

    # ── 8. _act_add: a manual cell whose bbox sticks OUT of the image ───
    # _act_add stores bbox as centre +/- radius without clamping, so a cell drawn
    # near the edge yields negative bounds. An unclamped slice would index from
    # the far side of the array and corrupt a completely unrelated region.
    radius = 17
    cy, cx = 6, 6
    from skimage.draw import disk as sk_disk
    rr, cc = sk_disk((cy, cx), radius, shape=masks.shape)
    masks[rr, cc] = 6
    infos[6] = CellInfo(
        cell_id=6, cy=float(cy), cx=float(cx), area=int(np.pi * radius ** 2),
        bbox_y0=cy - radius, bbox_x0=cx - radius,           # negative on purpose
        bbox_y1=cy + radius + 1, bbox_x1=cx + radius + 1,
        is_manual=True,
    )
    review.manual_cells.append(ManualCell(cell_id=6, x=float(cx), y=float(cy),
                                          radius=float(radius)))
    before = _full(masks, infos, review, profile, borders, dups)
    before[masks == 6] = (0, 0, 0, 0)          # overlay as it was pre-add
    _assert_patch_matches_full("manual add, bbox clipped at the edge", masks,
                               infos, review, profile, borders, dups, 6, before)

    # ── 9. a patch must not touch anything outside its own cell ─────────
    baseline = _full(masks, infos, review, profile, borders, dups)
    probe = baseline.copy()
    colors = OverlayColors(review, profile, borders, dups)
    patch_overlay_cell(probe, masks, infos[1], colors.of(1))
    changed_outside = np.any(probe[masks != 1] != baseline[masks != 1])
    assert not changed_outside, "patch bled outside the cell it was given"
    print("  ok  patch stays inside its own cell")

    # ── 10. contours trace the cell, and only that cell ─────────────────
    cs = cell_contours(masks, infos[1])
    assert cs, "no contour for a solid interior cell"
    pts = np.vstack(cs)
    assert pts[:, 0].min() >= 9 and pts[:, 0].max() <= 20, "contour x out of the cell"
    assert pts[:, 1].min() >= 9 and pts[:, 1].max() <= 20, "contour y out of the cell"
    edge = cell_contours(masks, infos[5])          # cell touching the image edge
    assert edge, "no contour for a cell on the image edge"
    print("  ok  cell_contours stay on the cell, edge cells included")

    print(f"OK — {CHECKS} mutations: patch_overlay_cell == build_overlay_rgba, "
          f"pixel for pixel; uint8 scheme pinned; bbox clamping verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
