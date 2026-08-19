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
from .canvas import build_overlay_rgba, normalize_image_for_display
from .cellpose_runner import load_image
from .grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from .image_state import compute_image_state
from .profile import ProfileData
from .summary import write_proband_zeitpunkt_summary


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
                  dest: Path, in_eval: bool = True) -> bool:
    """Export one image's derived files into `dest`. Returns True if mask was cached.

    Pulls everything from one compute_image_state() call so the exported numbers
    are guaranteed identical to what the app's status bar + detail panel show.
    """
    from .cellpose_runner import cache_paths, save_mask
    from .persistence import ReviewState

    stem = image_info.path.stem
    npy, _ = cache_paths(folder, image_info.path)
    if not npy.exists():
        return False

    # Incremental: skip rewriting if neither the mask nor the review changed
    # since the last export (compare against the existing stats CSV).
    stats_out = dest / f"{stem}_stats.csv"
    rev = ReviewState.review_path(folder, stem)
    src_mtime = max(npy.stat().st_mtime,
                    rev.stat().st_mtime if rev.exists() else 0.0)
    if stats_out.exists() and stats_out.stat().st_mtime >= src_mtime:
        return True

    state = compute_image_state(folder, image_info, profile)
    if state is None:
        return False

    image = load_image(image_info.path)
    default_type = profile.default_type_key()
    type_name_by_key = {t.key: (t.name or t.key) for t in profile.cell_types}

    rows = []
    for cid, info in state.cell_infos.items():
        type_key = state.review.types.get(str(cid), default_type)
        is_border = cid in state.border_ids
        is_dup = cid in state.duplicate_ids
        is_active = cid in state.active_ids
        rows.append({
            "cell_id": cid,
            # ML-insurance columns: stable canonical type key + human name and
            # whether the label came from the reviewer or the default fallback.
            "type": type_key,
            "type_name": type_name_by_key.get(type_key, type_key),
            "label_source": "reviewer" if str(cid) in state.review.types else "auto",
            "image_in_evaluation": bool(in_eval),
            # Per-image, repeated on every row like image_in_evaluation: was a
            # human done with this image, or is the mask still raw Cellpose?
            # Without it a consumer cannot tell reviewed from unreviewed data.
            "reviewed": bool(state.review.is_finished),
            "is_active": is_active, "is_border": is_border,
            "is_duplicate": is_dup,
            "is_force_included": cid in state.included,
            "is_force_excluded": cid in state.excluded,
            "is_manual": info.is_manual,
            "area": info.area,
            "centroid_x": round(info.cx, 2), "centroid_y": round(info.cy, 2),
        })
    # rows can be empty when Cellpose found no cells in this image; sorting an
    # empty frame by "cell_id" would raise KeyError, so only sort when present.
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("cell_id")

    # Copy original (off by default — avoids duplicating the source TIFs).
    if getattr(profile, "copy_originals_to_outputs", False):
        try:
            shutil.copy2(image_info.path, dest / image_info.path.name)
        except (FileNotFoundError, shutil.SameFileError):
            pass

    df.to_csv(dest / f"{stem}_stats.csv", index=False)
    save_mask(dest / f"{stem}_masks.npy", state.masks)

    # Overlay PNG (off by default — large + slow).
    if getattr(profile, "overlay_png", False):
        fig = plt.figure(figsize=(14, 9))
        ax = fig.add_subplot(111)
        ax.imshow(normalize_image_for_display(image), interpolation="nearest")
        rgba = build_overlay_rgba(state.masks, state.cell_infos, state.review, profile,
                                  state.border_ids, state.duplicate_ids)
        ax.imshow(rgba, interpolation="nearest")
        ax.axis("off")
        fig.suptitle(f"{image_info.path.name}  -  {state.n_active} active erys",
                     fontsize=11)
        fig.tight_layout()
        fig.savefig(dest / f"{stem}_overlay.png", dpi=150, bbox_inches="tight")
        plt.close(fig)
    return True


def export_training_data(folder: Path, profile: ProfileData,
                         grouped: GroupedFolder | None, dest: Path) -> dict:
    """Write (image, mask) pairs for fine-tuning Cellpose on this material.

    Layout is what `cellpose.io.load_train_test_data` expects out of the box:
    `<stem>.tif` next to `<stem>_masks.tif`, mask as a uint16 label image.

    **Which images.** Only those a human marked *fertig* AND that contain no
    manually drawn cells. A manual cell is a fixed-radius disc (canvas.py draws
    it with sk_disk), not a traced outline — training on those teaches the model
    to predict discs. Excluding whole images is the honest option; there is no
    way to recover the true boundary after the fact.

    **Which cells.** All of them, unchanged: border cells, size-filter rejects
    and manually excluded ones stay in the mask. Those flags mean "does not
    count towards the target", NOT "wrongly segmented". In Cellpose training an
    unlabelled pixel is background, so dropping a correctly segmented cell would
    actively teach the model to miss it — the single most expensive mistake
    available here.

    Returns a summary dict; writes `manifest.json` alongside the pairs.
    """
    import tifffile
    from .cellpose_runner import load_cached_masks
    from .persistence import ReviewState

    dest.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    skipped_unfinished: list[str] = []
    skipped_manual: list[str] = []
    skipped_nomask: list[str] = []

    for info in (grouped.flat if grouped else []):
        stem = info.path.stem
        masks = load_cached_masks(folder, info.path)
        if masks is None:
            skipped_nomask.append(stem)
            continue
        review = ReviewState.load(folder, stem)
        if not review.is_finished:
            skipped_unfinished.append(stem)
            continue
        if review.manual_cells:
            skipped_manual.append(stem)
            continue
        shutil.copy2(info.path, dest / f"{stem}{info.path.suffix}")
        tifffile.imwrite(dest / f"{stem}_masks.tif",
                         masks.astype(np.uint16), compression="zlib")
        written.append(stem)

    summary = {
        "app_version": __version__,
        "exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "source_folder": str(folder),
        "profile_name": profile.name,
        "cellpose": asdict(profile.cellpose),
        "n_written": len(written),
        "written": written,
        "skipped": {
            "not_finished": skipped_unfinished,
            "has_manual_cells": skipped_manual,
            "no_mask": skipped_nomask,
        },
        "notes": [
            "Masks are unmodified Cellpose output that a reviewer inspected and "
            "accepted; border, size-filtered and manually excluded cells are all "
            "still labelled on purpose.",
            "Images containing manually drawn cells are omitted: those are "
            "fixed-radius discs, not traced outlines.",
            "Layout matches cellpose.io.load_train_test_data(mask_filter='_masks').",
        ],
    }
    with (dest / "manifest.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    return summary


def export_folder_structured(folder: Path, profile: ProfileData,
                             grouped: GroupedFolder | None) -> Path:
    """Run the full structured export against the current folder. Returns outputs root."""
    root = outputs_root(folder)

    # If grouping is missing (shouldn't happen in normal flow), build a single bucket
    if grouped is None or not grouped.tree:
        return root

    from .evaluation import effective_eval_stems

    for tp in grouped.timepoints():
        for sub in grouped.subjects_in(tp):
            images = grouped.tree[tp][sub]
            dest = _group_folder(root, tp, sub)
            eval_stems = effective_eval_stems(folder, images, profile)
            any_cached = False
            for info in images:
                in_eval = info.path.stem in eval_stems
                if _export_image(folder, info, profile, dest, in_eval=in_eval):
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

    # Cross-group overview workbook (opt-in per profile).
    if getattr(profile, "overview_xlsx", True):
        try:
            from .overview import write_overview_xlsx
            write_overview_xlsx(folder, profile, grouped, root / "Uebersicht.xlsx")
        except Exception:
            pass  # missing openpyxl etc. must not abort the CSV export

    # Consolidated SQLite reporting store (additive — alongside the CSVs, never
    # replacing them). The DB is a bonus, so a failure must not abort the export.
    try:
        from .db import write_db
        write_db(folder, profile, grouped, root / "erys.db")
    except Exception:
        pass

    return root
