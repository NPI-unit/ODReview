"""Disk-backed per-image count cache.

The sidebar counters, the evaluation cap, the detail panel, the stats tab and
the summary writer all only need a per-image *count* (active RBC + per-type),
not the full pixel-level state. Recomputing that meant loading the mask `.npy`
and re-running `compute_image_state` for every image on every sidebar refresh —
the main source of UI lag.

This module caches the counts in `<cache>/<stem>_counts.json` and only
recomputes when the inputs that affect the numbers change:

  * the mask file (mtime)
  * the review file (mtime)
  * the relevant profile settings (margin, size filter, type keys) — a
    short signature string

Cheap consumers read a tiny JSON; the expensive `compute_image_state` runs only
on a real change.
"""

from __future__ import annotations

from pathlib import Path

from .cellpose_runner import mask_read_paths
from .grouping import ImageGroupInfo
from .image_state import compute_image_state
from .persistence import ReviewState, read_json, review_dir, write_json_atomic
from .profile import ProfileData


def _counts_path(folder: Path, stem: str) -> Path:
    return review_dir(folder) / f"{stem}_counts.json"     # counts follow the review


def profile_signature(profile: ProfileData) -> str:
    """Everything in the profile that changes per-image active/by-type counts."""
    sf = profile.size_filter
    type_keys = ",".join(t.key for t in profile.cell_types)
    # "s" (not the old "d") so every counts.json written under the previous
    # duplicate-detection rule is treated as stale and recomputed once.
    return (f"m{profile.margin_n}|s{int(sf.enabled)}:{sf.min_factor}:{sf.max_factor}"
            f"|t{type_keys}")


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def image_counts(folder: Path, info: ImageGroupInfo, profile: ProfileData) -> dict | None:
    """Return {'rbc', 'by_type', 'image_num', 'filename'} or None if unsegmented.

    Reads the cache when valid; otherwise computes once and writes it back.
    """
    if folder is None:
        return None
    npy, _ = mask_read_paths(folder, info.path)
    if npy is None:
        return None

    stem = info.path.stem
    mask_mtime = _mtime(npy)
    review_mtime = _mtime(ReviewState.review_path(folder, stem))
    sig = profile_signature(profile)

    cpath = _counts_path(folder, stem)
    cached = read_json(cpath)
    if (cached
            and cached.get("sig") == sig
            and abs(float(cached.get("mask_mtime", -1)) - mask_mtime) < 1e-6
            and abs(float(cached.get("review_mtime", -1)) - review_mtime) < 1e-6):
        return {
            "rbc": int(cached["rbc"]),
            "by_type": dict(cached["by_type"]),
            "image_num": info.image_num,
            "filename": info.path.name,
        }

    state = compute_image_state(folder, info, profile)
    if state is None:
        return None
    write_json_atomic(cpath, {
        "sig": sig,
        "mask_mtime": mask_mtime,
        "review_mtime": review_mtime,
        "rbc": state.n_active,
        "by_type": state.by_type,
    })
    return {
        "rbc": state.n_active,
        "by_type": state.by_type,
        "image_num": info.image_num,
        "filename": info.path.name,
    }
