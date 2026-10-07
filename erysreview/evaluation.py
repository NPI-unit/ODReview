"""Per-group evaluation membership — the editable 2000-cell cap.

Each (timepoint, subject) group only needs enough images for the running active
count to land just over the profile target (e.g. 2000). The automatic set is
the images in image order until that happens — computed WITHOUT looking at the
user's choices, so it never shifts. The user then decides per image via its
ReviewState.eval_include flag (key E):

    eval_include is None  -> automatic (counts if it is in the automatic set)
    eval_include is True  -> counts (added on top)
    eval_include is False -> does not count

Taking an image out only subtracts it; no later image moves up to replace it
("ich kontrolliere, welche Bilder dazukommen"). Below the target the user adds
images themselves.
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


def _apply(order: list[tuple[str, int, bool | None]], target: int) -> set[str]:
    """The rule, pure: `order` is (stem, counted cells, override) per segmented
    image in image order. The automatic set ignores the overrides."""
    auto: set[str] = set()
    cumulative = 0
    for stem, n, _ov in order:
        if cumulative >= target:
            break
        auto.add(stem)
        cumulative += n
    return {stem for stem, _n, ov in order if ov is True or (ov is None and stem in auto)}


def _stems_from_rows(rows: list[tuple[str, dict | None, ReviewState]],
                     target: int) -> set[str]:
    """Cap logic — pure, no I/O. See the module docstring for the rules."""
    return _apply([(stem, c["rbc"], review.eval_include) for stem, c, review in rows
                   if c is not None], target)


def effective_eval_stems(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> set[str]:
    """Image stems in this group that count toward the target, in image order."""
    target = max(1, profile.target_cells)
    return _stems_from_rows(_group_rows(folder, images_in_group, profile), target)


def group_eval(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> tuple[dict, set[str]]:
    """The summary AND the stem set it was taken over, from ONE disk pass.

    The sidebar needs both on every save — the group counter from the summary,
    the ⊘ markers from the stem set — and asking for them through
    group_eval_summary() plus effective_eval_stems() walked the same group
    twice per click, reading every `<stem>_counts.json` and `<stem>_review.json`
    in it a second time for numbers the first pass had already worked out.
    """
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
    }, stems


def group_eval_summary(
    folder: Path, images_in_group: list[ImageGroupInfo], profile: ProfileData,
) -> dict:
    """Aggregate counts restricted to the effective evaluation set of one group."""
    return group_eval(folder, images_in_group, profile)[0]


def inclusion_with_overrides(images, counts, overrides, target):
    """Effective inclusion set + its active count, honoring overrides.

    overrides[stem] in {True, False, None(=auto)}. Same rule as
    effective_eval_stems, but on counts the eval dialog already holds, so the
    dialog can re-run it on every click without touching disk.
    """
    order = [(i.path.stem, counts[i.path.stem], overrides.get(i.path.stem))
             for i in images if counts.get(i.path.stem) is not None]
    included = _apply(order, target)
    return included, sum(n for stem, n, _ov in order if stem in included)


def load_group_selection(folder: Path, images_in_group, profile: ProfileData):
    """State for the 'zählende Bilder' picker of one group, from one pass:
    counts[stem] (counted cells, None = not segmented), the stored eval
    overrides, and the stored finished flags."""
    from .cellpose_runner import has_cache
    from .counts import image_counts
    counts: dict[str, int | None] = {}
    overrides: dict[str, bool | None] = {}
    finished: dict[str, bool] = {}
    for info in images_in_group:
        stem = info.path.stem
        if has_cache(folder, info.path):
            c = image_counts(folder, info, profile)
            counts[stem] = c["rbc"] if c else None
        else:
            counts[stem] = None
        rv = ReviewState.load(folder, stem)
        overrides[stem] = rv.eval_include
        finished[stem] = bool(rv.is_finished)
    return counts, overrides, finished


def save_group_selection(folder: Path, images_in_group, overrides: dict, overrides_orig: dict,
                         finished: dict, finished_orig: dict) -> bool:
    """Write back what changed — one load and one save per image even when
    both the eval override and the finished flag changed. True if anything did."""
    changed = False
    for info in images_in_group:
        stem = info.path.stem
        want_eval = overrides.get(stem) != overrides_orig.get(stem)
        want_done = bool(finished.get(stem)) != bool(finished_orig.get(stem))
        if not (want_eval or want_done):
            continue
        rv = ReviewState.load(folder, stem)
        if want_eval:
            rv.eval_include = overrides.get(stem)
        if want_done:
            rv.is_finished = bool(finished.get(stem))
        rv.save(folder, stem)
        changed = True
    return changed
