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


def _group_rows(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> list[tuple[str, dict | None, ReviewState]]:
    """ONE disk pass over a group: (stem, counts-or-None, review) per segmented image.

    Both public functions below derive from this list. Previously
    group_eval_summary() called effective_eval_stems() and then walked the same
    images again, so every `<stem>_counts.json` and `<stem>_review.json` in the
    group was read twice per call — on a folder-sized sidebar refresh that is the
    single largest source of I/O.

    `counts` stays None for an image whose mask is cached but unreadable (corrupt
    .npy). Such an image is excluded from the evaluation but its review — and
    therefore its accrued time — still counts, matching the previous behaviour.
    """
    rows: list[tuple[str, dict | None, ReviewState]] = []
    for info in images_in_group:
        if folder is None or not has_cache(folder, info.path):
            continue
        stem = info.path.stem
        rows.append((stem, image_counts(folder, info, profile),
                     ReviewState.load(folder, stem)))
    return rows


def _stems_from_rows(rows: list[tuple[str, dict | None, ReviewState]],
                     target: int) -> set[str]:
    """Cap logic — pure, no I/O. See the module docstring for the rules."""
    included: set[str] = set()
    cumulative = 0
    auto_reached = False
    for stem, c, review in rows:
        if c is None:
            continue
        n = c["rbc"]
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


def effective_eval_stems(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> set[str]:
    """Image stems in this group that count toward the target, in image order."""
    target = max(1, profile.target_cells)
    return _stems_from_rows(_group_rows(folder, images_in_group, profile), target)


def group_eval_summary(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> dict:
    """Aggregate counts restricted to the effective evaluation set of one group."""
    target = max(1, profile.target_cells)
    rows = _group_rows(folder, images_in_group, profile)
    stems = _stems_from_rows(rows, target)
    n_active = 0
    by_type: dict[str, int] = {t.key: 0 for t in profile.cell_types}
    n_images = 0
    time_s = 0.0
    for stem, c, review in rows:
        # Time accrues for every reviewed image so timing reflects all work.
        time_s += review.time_spent_s
        if stem not in stems or c is None:
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
