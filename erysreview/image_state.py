"""Single source of truth for everything derived from a (mask, review, profile) triple.

Before v14 three consumers — canvas.py, summary.py, export.py — independently
recomputed border/size-outlier/active/per-type-count logic. That worked, but kept
the risk of subtle drift between counters and exported numbers. Now they all
go through `compute_image_state(folder, image_info, profile)` and read the
returned ImageState.

The canvas keeps its *live* (in-memory) mutation state alongside this — but
when the app needs counts for the sidebar, detail panel, summary CSV or export,
it asks here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from skimage.draw import disk as sk_disk

from .overlay import CellInfo, compute_cell_infos, is_border_cell
from .cellpose_runner import load_cached_masks
from .sizefilter import detect_size_outliers
from .grouping import ImageGroupInfo
from .persistence import ReviewState
from .profile import ProfileData


@dataclass
class ImageState:
    """Everything derived from one image's cached mask + on-disk review state."""
    image_info: ImageGroupInfo
    masks: np.ndarray
    cell_infos: dict[int, CellInfo]
    review: ReviewState

    border_ids: set[int]
    duplicate_ids: set[int]        # size-filter rejects; name matches is_duplicate
    manual_ids: set[int]

    excluded: set[int]                  # = set(review.force_excluded)
    included: set[int]                  # = set(review.force_included)
    active_ids: set[int]                # cells effectively counted

    # Aggregated counts (always in sync with active_ids)
    n_total: int = 0
    n_active: int = 0
    n_excluded: int = 0
    n_inactive_auto: int = 0            # border or duplicate, not force-included
    n_changed: int = 0                  # active cells with type != "normal"
    by_type: dict[str, int] = field(default_factory=dict)
    pct_changed: float = 0.0            # per-image % verändert (= 100 × n_changed/n_active)


def compute_image_state(
    folder: Path,
    image_info: ImageGroupInfo,
    profile: ProfileData,
) -> ImageState | None:
    """Load mask + review from disk; compute every derived value at once.

    Returns None if the image has no cached Cellpose mask yet.
    Safe to call repeatedly: it always reads fresh from disk, no caching.
    """
    masks = load_cached_masks(folder, image_info.path)
    if masks is None:
        return None

    review = ReviewState.load(folder, image_info.path.stem)

    # Materialize manual cells into a working masks copy
    masks_work = masks.astype(np.int32).copy()
    manual_ids: set[int] = set()
    max_id = int(masks_work.max())
    for mc in review.manual_cells:
        rr, cc = sk_disk((int(mc.y), int(mc.x)), int(mc.radius), shape=masks_work.shape)
        masks_work[rr, cc] = mc.cell_id
        manual_ids.add(mc.cell_id)
        max_id = max(max_id, mc.cell_id)

    cell_infos = compute_cell_infos(masks_work, manual_ids)
    h, w = masks_work.shape
    border_ids = {
        cid for cid, info in cell_infos.items()
        if is_border_cell(info, h, w, profile.margin_n)
    }
    duplicate_ids = detect_size_outliers(cell_infos, profile.size_filter)

    excluded = set(review.force_excluded)
    included = set(review.force_included)
    default_key = profile.default_type_key()

    by_type: dict[str, int] = {t.key: 0 for t in profile.cell_types}
    active_ids: set[int] = set()
    n_excluded = 0
    n_inactive_auto = 0
    n_changed = 0

    for cid in cell_infos:
        if cid in excluded:
            n_excluded += 1
            continue
        auto_inactive = (cid in border_ids) or (cid in duplicate_ids)
        if auto_inactive and cid not in included:
            n_inactive_auto += 1
            continue
        active_ids.add(cid)
        type_key = review.types.get(str(cid), default_key)
        if type_key in by_type:
            by_type[type_key] += 1
        else:
            # Type was deleted from the profile after labelling — fall back to default.
            by_type[default_key] = by_type.get(default_key, 0) + 1
        if type_key != "normal":
            n_changed += 1

    n_active = len(active_ids)
    pct_changed = (100.0 * n_changed / n_active) if n_active > 0 else 0.0

    return ImageState(
        image_info=image_info,
        masks=masks_work,
        cell_infos=cell_infos,
        review=review,
        border_ids=border_ids,
        duplicate_ids=duplicate_ids,
        manual_ids=manual_ids,
        excluded=excluded,
        included=included,
        active_ids=active_ids,
        n_total=len(cell_infos),
        n_active=n_active,
        n_excluded=n_excluded,
        n_inactive_auto=n_inactive_auto,
        n_changed=n_changed,
        by_type=by_type,
        pct_changed=pct_changed,
    )
