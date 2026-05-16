"""Structured export — one folder per (timepoint, subject), originals copied in.

Layout:
    <import_folder>/outputs/
        <Zeitpunkt>/
            <Zeitpunkt>_<Proband>/
                <original_image>.tif          (copied from import folder)
                <stem>_stats.csv              (per-image cell-level flags)
                <stem>_masks.npy              (final masks)
                <stem>_overlay.png            (overlay PNG)
                <Zeitpunkt>_<Proband>_summary.csv
        _ungrouped/
            ...
        manifest.json                          (profile snapshot)
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from . import __version__
from .canvas import (
    CellInfo, build_overlay_rgba, compute_cell_infos,
    is_border_cell, normalize_image_for_display,
)
from .cellpose_runner import load_cached_masks, load_image
from .duplicates import detect_duplicates
from .grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from .persistence import ReviewState
from .profile import ProfileData
from .summary import write_proband_zeitpunkt_summary

from skimage.draw import disk as sk_disk


def outputs_root(folder: Path) -> Path:
    d = folder / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _group_folder(root: Path, tp: str, sub: str) -> Path:
    if tp == UNGROUPED_TIMEPOINT:
        d = root / "_ungrouped"
    else:
        d = root / tp / f"{tp}_{sub}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _export_image(folder: Path, image_info: ImageGroupInfo, profile: ProfileData,
                  dest: Path) -> bool:
    """Export one image's derived files into `dest`. Returns True if mask was cached."""
    masks = load_cached_masks(folder, image_info.path)
    if masks is None:
        return False

    review = ReviewState.load(folder, image_info.path.stem)
    image = load_image(image_info.path)

    # Materialize manual cells
    masks_export = masks.astype(np.int32).copy()
    manual_ids: set[int] = set()
    max_id = int(masks_export.max())
    for mc in review.manual_cells:
        rr, cc = sk_disk((int(mc.y), int(mc.x)), int(mc.radius), shape=masks_export.shape)
        masks_export[rr, cc] = mc.cell_id
        manual_ids.add(mc.cell_id)
        max_id = max(max_id, mc.cell_id)

    cell_infos = compute_cell_infos(masks_export, manual_ids)
    h, w = masks_export.shape
    border_ids = {
        cid for cid, info in cell_infos.items()
        if is_border_cell(info, h, w, profile.margin_n)
    }
    duplicate_ids = detect_duplicates(cell_infos, profile.duplicate_detection)

    excluded = set(review.force_excluded)
    included = set(review.force_included)
    default_type = profile.default_type_key()

    rows = []
    for cid, info in cell_infos.items():
        type_key = review.types.get(str(cid), default_type)
        is_border = cid in border_ids
        is_dup = cid in duplicate_ids
        auto_inactive = is_border or is_dup
        is_active = (cid not in excluded) and (not auto_inactive or cid in included)
        rows.append({
            "cell_id": cid, "type": type_key,
            "is_active": is_active, "is_border": is_border,
            "is_duplicate": is_dup, "is_force_included": cid in included,
            "is_force_excluded": cid in excluded, "is_manual": info.is_manual,
            "area": info.area,
            "centroid_x": round(info.cx, 2), "centroid_y": round(info.cy, 2),
        })
    df = pd.DataFrame(rows).sort_values("cell_id")

    stem = image_info.path.stem

    # Copy original
    try:
        shutil.copy2(image_info.path, dest / image_info.path.name)
    except (FileNotFoundError, shutil.SameFileError):
        pass

    df.to_csv(dest / f"{stem}_stats.csv", index=False)
    np.save(dest / f"{stem}_masks.npy", masks_export)

    fig = plt.figure(figsize=(14, 9))
    ax = fig.add_subplot(111)
    ax.imshow(normalize_image_for_display(image), interpolation="nearest")
    rgba = build_overlay_rgba(masks_export, cell_infos, review, profile,
                              border_ids, duplicate_ids)
    ax.imshow(rgba, interpolation="nearest")
    ax.axis("off")
    fig.suptitle(f"{image_info.path.name}  -  {int(df['is_active'].sum())} active erys",
                 fontsize=11)
    fig.tight_layout()
    fig.savefig(dest / f"{stem}_overlay.png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    return True


def export_folder_structured(folder: Path, profile: ProfileData,
                             grouped: GroupedFolder | None) -> Path:
    """Run the full structured export against the current folder. Returns outputs root."""
    root = outputs_root(folder)

    # If grouping is missing (shouldn't happen in normal flow), build a single bucket
    if grouped is None or not grouped.tree:
        return root

    for tp in grouped.timepoints():
        for sub in grouped.subjects_in(tp):
            images = grouped.tree[tp][sub]
            dest = _group_folder(root, tp, sub)
            any_cached = False
            for info in images:
                if _export_image(folder, info, profile, dest):
                    any_cached = True
            if any_cached:
                if tp == UNGROUPED_TIMEPOINT:
                    summary_name = "_ungrouped_summary.csv"
                else:
                    summary_name = f"{tp}_{sub}_summary.csv"
                write_proband_zeitpunkt_summary(
                    folder, profile, images, dest / summary_name,
                )

    # Profile snapshot in manifest
    manifest = {
        "app_version": __version__,
        "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "folder": str(folder),
        "profile": profile.to_dict(),
    }
    with (root / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    return root
