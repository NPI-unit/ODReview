"""Portable share package — a single ZIP that lets a teammate review without
ever running Cellpose, and round-trip reviews back.

ZIP layout:
    manifest.json                 format + app/cellpose version, reviewer, profile
    images/<name>                 original images (optional, default on)
    cache/<stem>_masks.npy        cached Cellpose mask
    cache/<stem>_masks.json       parameters that produced the mask
    cache/<stem>_review.json      per-image user edits (optional per image)

Import always downgrades each image's status from "fertig" to "bearbeitet"
(is_finished -> False) and resolves per-image conflicts with existing local
reviews via a caller-supplied callback.
"""

from __future__ import annotations

import datetime as _dt
import json
import zipfile
from pathlib import Path
from typing import Callable

from . import __version__
from .cellpose_runner import cache_paths, has_cache
from .grouping import GroupedFolder
from .persistence import ReviewState, cache_dir_for
from .profile import ProfileData

FORMAT_TAG = "erys-package"
FORMAT_VERSION = 1

# Conflict resolutions returned by the resolver callback.
IMPORT = "import"
KEEP = "keep"
IMPORT_ALL = "import_all"
KEEP_ALL = "keep_all"


# ─────────────────────────────────────────────
# Create
# ─────────────────────────────────────────────
def create_package(
    folder: Path,
    grouped: GroupedFolder,
    profile: ProfileData,
    dest_zip: Path,
    include_images: bool = True,
    reviewer: str = "",
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Write a share package. Only images with a cached mask are included.

    Returns a small summary dict.
    """
    infos = [i for i in grouped.flat if has_cache(folder, i.path)]
    stems: list[str] = []
    n = len(infos)

    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, info in enumerate(infos, start=1):
            if progress_cb:
                progress_cb(i, n, info.path.name)
            stem = info.path.stem
            npy, meta = cache_paths(folder, info.path)
            if npy.exists():
                zf.write(npy, f"cache/{npy.name}")
            if meta.exists():
                zf.write(meta, f"cache/{meta.name}")
            review_path = ReviewState.review_path(folder, stem)
            if review_path.exists():
                zf.write(review_path, f"cache/{review_path.name}")
            if include_images and info.path.exists():
                zf.write(info.path, f"images/{info.path.name}")
            stems.append(stem)

        manifest = {
            "format": FORMAT_TAG,
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "reviewer": reviewer,
            "includes_images": bool(include_images),
            "source_folder_name": folder.name,
            "n_images": len(stems),
            "stems": stems,
            "profile": profile.to_dict(),
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))

    return {"path": dest_zip, "n_images": len(stems), "includes_images": include_images}


# ─────────────────────────────────────────────
# Inspect
# ─────────────────────────────────────────────
def inspect_package(zip_path: Path) -> dict | None:
    """Return the manifest dict, or None if the file isn't a valid package."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if "manifest.json" not in zf.namelist():
                return None
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
    except (zipfile.BadZipFile, OSError, json.JSONDecodeError, KeyError):
        return None
    if manifest.get("format") != FORMAT_TAG:
        return None
    return manifest


# ─────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────
def _params_of(profile_dict: dict) -> dict:
    cp = (profile_dict or {}).get("cellpose", {})
    return {
        "diameter": cp.get("diameter"),
        "flow_threshold": cp.get("flow_threshold"),
        "cellprob_threshold": cp.get("cellprob_threshold"),
        "model": cp.get("model"),
    }


def import_package(
    zip_path: Path,
    target_folder: Path,
    active_profile: ProfileData | None,
    conflict_resolver: Callable[[str], str],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Extract a package into target_folder and merge reviews.

    `conflict_resolver(stem) -> IMPORT | KEEP | IMPORT_ALL | KEEP_ALL` is called
    once per image that already has local user edits. The *_ALL answers stick.

    Returns a result dict with imported/skipped/warnings lists, the manifest's
    ProfileData, and the written log path.
    """
    manifest = inspect_package(zip_path)
    if manifest is None:
        raise ValueError("not_a_package")

    target_folder.mkdir(parents=True, exist_ok=True)
    cache_dir = cache_dir_for(target_folder)

    imported: list[str] = []
    skipped: list[str] = []
    warnings: list[str] = []

    # Cellpose parameter sanity check vs the active profile.
    if active_profile is not None:
        want = _params_of(manifest.get("profile", {}))
        have = _params_of(active_profile.to_dict())
        if want != have:
            warnings.append(
                f"Cellpose-Parameter im Paket weichen vom aktiven Profil ab "
                f"(Paket: {want}, aktiv: {have})."
            )

    sticky: str | None = None  # IMPORT_ALL / KEEP_ALL once chosen
    stems = list(manifest.get("stems", []))
    n = len(stems)

    with zipfile.ZipFile(zip_path, "r") as zf:
        names = set(zf.namelist())

        # 1) Extract originals (if present)
        for name in names:
            if name.startswith("images/") and name != "images/":
                out = target_folder / Path(name).name
                with zf.open(name) as src, out.open("wb") as dst:
                    dst.write(src.read())

        # 2) Per-image cache + review merge
        for i, stem in enumerate(stems, start=1):
            if progress_cb:
                progress_cb(i, n, stem)

            npy_name = f"cache/{stem}_masks.npy"
            meta_name = f"cache/{stem}_masks.json"
            review_name = f"cache/{stem}_review.json"

            local_review = ReviewState.load(target_folder, stem)
            has_local = local_review.has_user_edits()

            decision = IMPORT
            if has_local:
                if sticky == IMPORT_ALL:
                    decision = IMPORT
                elif sticky == KEEP_ALL:
                    decision = KEEP
                else:
                    ans = conflict_resolver(stem)
                    if ans in (IMPORT_ALL, KEEP_ALL):
                        sticky = ans
                        decision = IMPORT if ans == IMPORT_ALL else KEEP
                    else:
                        decision = ans

            if decision == KEEP:
                skipped.append(stem)
                continue

            # Write mask + params (always safe to overwrite — same provenance)
            if npy_name in names:
                (cache_dir / f"{stem}_masks.npy").write_bytes(zf.read(npy_name))
            if meta_name in names:
                (cache_dir / f"{stem}_masks.json").write_bytes(zf.read(meta_name))

            # Write review, downgraded to "in progress"
            if review_name in names:
                try:
                    raw = json.loads(zf.read(review_name).decode("utf-8"))
                except json.JSONDecodeError:
                    raw = {}
                raw["is_finished"] = False  # imported => bearbeitet, never fertig
                (cache_dir / f"{stem}_review.json").write_text(
                    json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8",
                )
            else:
                # No review in package: ensure any local "finished" is downgraded
                if local_review.is_finished:
                    local_review.is_finished = False
                    local_review.save(target_folder, stem)
            imported.append(stem)

    # 3) Write a human-readable log
    log_path = cache_dir / f"import_log_{_dt.datetime.now():%Y%m%d_%H%M%S}.txt"
    reviewer = manifest.get("reviewer") or "?"
    lines = [
        f"Erys import log — {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"Package: {zip_path.name}",
        f"From reviewer: {reviewer}",
        f"Target folder: {target_folder}",
        f"Imported: {len(imported)}   Skipped (kept local): {len(skipped)}",
        "",
    ]
    if warnings:
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in warnings)
        lines.append("")
    if skipped:
        lines.append("Skipped stems:")
        lines.extend(f"  - {s}" for s in skipped)
    try:
        log_path.write_text("\n".join(lines), encoding="utf-8")
    except OSError:
        log_path = None  # type: ignore[assignment]

    profile = ProfileData.from_dict(manifest.get("profile", {}))
    profile.import_folder = str(target_folder)
    return {
        "imported": imported,
        "skipped": skipped,
        "warnings": warnings,
        "profile": profile,
        "target": target_folder,
        "log_path": log_path,
        "manifest": manifest,
    }
