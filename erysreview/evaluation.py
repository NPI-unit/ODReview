"""Per-group evaluation membership — the editable 2000-cell cap.

Each (timepoint, subject) group only needs enough images for the running active
count to land just over the profile target (e.g. 2000). Images are auto-filled
in image order until that happens; the user can override any single image via
its ReviewState.eval_include flag ("statt Bild 8 lieber Bild 9"):

    eval_include is None  -> automatic
    eval_include is True  -> always counts
    eval_include is False -> never counts

Explicit choices always win and are added to the running total in order, so the
auto-fill stops as soon as the cumulative (explicit + auto) count exceeds the
target.
"""

from __future__ import annotations

from pathlib import Path

from .cellpose_runner import has_cache
from .counts import image_counts
from .grouping import ImageGroupInfo
from .persistence import ReviewState
from .profile import ProfileData


def _active_count(folder: Path, info: ImageGroupInfo, profile: ProfileData) -> int | None:
    """Active (counted) cells for one image, or None if not segmented yet."""
    c = image_counts(folder, info, profile)
    return None if c is None else c["rbc"]


def effective_eval_stems(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> set[str]:
    """Image stems in this group that count toward the target, in image order."""
    target = max(1, profile.target_cells)
    included: set[str] = set()
    cumulative = 0
    auto_reached = False

    for info in images_in_group:
        if folder is None or not has_cache(folder, info.path):
            continue
        stem = info.path.stem
        n = _active_count(folder, info, profile)
        if n is None:
            continue
        review = ReviewState.load(folder, stem)
        if review.eval_include is True:
            included.add(stem)
            cumulative += n
        elif review.eval_include is False:
            continue
        else:  # automatic
            if not auto_reached:
                included.add(stem)
                cumulative += n
                if cumulative >= target:
                    auto_reached = True
            # once the auto-fill target is reached, further auto images drop out
    return included


def group_eval_summary(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> dict:
    """Aggregate counts restricted to the effective evaluation set of one group."""
    target = max(1, profile.target_cells)
    stems = effective_eval_stems(folder, images_in_group, profile)
    n_active = 0
    by_type: dict[str, int] = {t.key: 0 for t in profile.cell_types}
    n_images = 0
    time_s = 0.0
    for info in images_in_group:
        if folder is None or not has_cache(folder, info.path):
            continue
        # Time accrues for every reviewed image so timing reflects all work.
        time_s += ReviewState.load(folder, info.path.stem).time_spent_s
        if info.path.stem not in stems:
            continue
        c = image_counts(folder, info, profile)
        if c is None:
            continue
        n_images += 1
        n_active += c["rbc"]
        for k, v in c["by_type"].items():
            by_type[k] = by_type.get(k, 0) + v
    n_normal = by_type.get("normal", 0)
    pct_changed = (100.0 * (n_active - n_normal) / n_active) if n_active > 0 else 0.0
    pct_changed_2000 = (pct_changed * target / n_active) if n_active > 0 else 0.0
    return {
        "n_images": n_images,
        "n_active": n_active,
        "by_type": by_type,
        "target": target,
        "reached": n_active >= target,
        "pct_changed": pct_changed,
        "pct_changed_2000": pct_changed_2000,
        "time_s": time_s,
    }
