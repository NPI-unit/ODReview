"""Lazy per-image Cellpose runner with parameter-aware caching.

Cache layout (Frage 4 + 10c):
    <folder>/.erys_cache/
        <stem>_masks.npy          – uint16 label image
        <stem>_masks.json         – {diameter, flow_threshold, cellprob_threshold,
                                      model, cellpose_version, created_at}

Single-threaded by design. The UI calls run_for_image(...) and blocks until done;
the modal spinner above keeps the UI alive.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np

from .persistence import CellposeParams, cache_dir_for

IMG_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}


# ─────────────────────────────────────────────
# Image loading
# ─────────────────────────────────────────────
def load_image(path: Path) -> np.ndarray:
    """Robust loader: tifffile for .tif, PIL for the rest. Drops alpha channels."""
    suffix = path.suffix.lower()
    if suffix in (".tif", ".tiff"):
        try:
            import tifffile
            arr = tifffile.imread(str(path))
        except Exception:
            from PIL import Image
            arr = np.array(Image.open(str(path)))
    else:
        from PIL import Image
        arr = np.array(Image.open(str(path)))

    # Drop alpha
    if arr.ndim == 3 and arr.shape[-1] == 4:
        arr = arr[..., :3]
    # Z-stacks: collapse to first plane
    if arr.ndim == 3 and arr.shape[0] not in (1, 3) and arr.shape[-1] not in (1, 3):
        arr = arr[0]
    return arr


# Sub-directories never scanned for images (export copies + hidden caches).
_SKIP_DIRS = {"outputs", "__pycache__"}


def list_images(folder: Path, exclude: set[Path] | None = None) -> list[Path]:
    """All images under `folder`, RECURSIVELY (sub-folders included).

    Skips the export folder (`outputs/`), hidden dirs (e.g. `.erys_cache/`) and
    anything in `exclude`. Because grouping keys on the filename, duplicate
    basenames across sub-folders are redundant — only the first is kept and the
    rest are reported on stdout.
    """
    exclude = exclude or set()
    out: list[Path] = []
    seen: dict[str, Path] = {}
    dupes: list[Path] = []
    for p in sorted(folder.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG_EXTS:
            continue
        rel_parts = p.relative_to(folder).parts[:-1]  # parent dirs only
        if any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts):
            continue
        if p in exclude or any(p == e or e in p.parents for e in exclude):
            continue
        if p.name in seen:
            dupes.append(p)
            continue
        seen[p.name] = p
        out.append(p)
    if dupes:
        print(f"[list_images] {len(dupes)} doppelte Dateinamen übersprungen "
              f"(z.B. {dupes[0].name})", flush=True)
    return out


# ─────────────────────────────────────────────
# Cache files
# ─────────────────────────────────────────────
def cache_paths(folder: Path, image_path: Path) -> tuple[Path, Path]:
    cache = cache_dir_for(folder)
    stem = image_path.stem
    return cache / f"{stem}_masks.npy", cache / f"{stem}_masks.json"


def has_cache(folder: Path, image_path: Path) -> bool:
    npy, _ = cache_paths(folder, image_path)
    return npy.exists()


def cached_params(folder: Path, image_path: Path) -> dict | None:
    _, meta = cache_paths(folder, image_path)
    if not meta.exists():
        return None
    try:
        with meta.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def params_match(folder: Path, image_path: Path, params: CellposeParams) -> bool:
    cp = cached_params(folder, image_path)
    if not cp:
        return False
    return (
        float(cp.get("diameter", -1)) == float(params.diameter)
        and float(cp.get("flow_threshold", -1)) == float(params.flow_threshold)
        and float(cp.get("cellprob_threshold", -1)) == float(params.cellprob_threshold)
        and cp.get("model", "") == params.model
    )


def load_cached_masks(folder: Path, image_path: Path) -> np.ndarray | None:
    npy, _ = cache_paths(folder, image_path)
    if not npy.exists():
        return None
    try:
        return np.load(npy).astype(np.int32)
    except (OSError, ValueError):
        return None


# ─────────────────────────────────────────────
# Cellpose execution
# ─────────────────────────────────────────────
_MODEL_CACHE: dict = {}  # keyed by (model_name, use_gpu); switching toggles re-init.


def detect_gpu_backend() -> str:
    """Returns 'mps', 'cuda', or 'cpu'. Used for the Settings info label and decision."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if torch.backends.mps.is_available():
            return "mps"
    except ImportError:
        pass
    return "cpu"


def _get_model(model_name: str, use_gpu: bool):
    key = (model_name, bool(use_gpu))
    if key not in _MODEL_CACHE:
        from cellpose import models
        gpu_flag = bool(use_gpu) and detect_gpu_backend() != "cpu"
        _MODEL_CACHE[key] = models.CellposeModel(
            gpu=gpu_flag, pretrained_model=model_name,
        )
    return _MODEL_CACHE[key]


def run_cellpose(image: np.ndarray, params: CellposeParams, use_gpu: bool = True) -> np.ndarray:
    """Run Cellpose synchronously. Returns int32 mask."""
    model = _get_model(params.model, use_gpu)
    # Cellpose 4 returns (masks, flows, styles)
    out = model.eval(
        image,
        diameter=float(params.diameter),
        flow_threshold=float(params.flow_threshold),
        cellprob_threshold=float(params.cellprob_threshold),
    )
    masks = out[0] if isinstance(out, (tuple, list)) else out
    return np.asarray(masks, dtype=np.int32)


def run_for_image(
    folder: Path,
    image_path: Path,
    image: np.ndarray,
    params: CellposeParams,
    force: bool = False,
    use_gpu: bool = True,
) -> np.ndarray:
    """Run-or-cache. Returns the mask, writes <stem>_masks.npy + .json."""
    if not force:
        if params_match(folder, image_path, params):
            cached = load_cached_masks(folder, image_path)
            if cached is not None:
                return cached

    masks = run_cellpose(image, params, use_gpu=use_gpu)

    npy, meta = cache_paths(folder, image_path)
    np.save(npy, masks.astype(np.int32))

    try:
        import cellpose as _cp
        cp_version = getattr(_cp, "version", "unknown")
    except Exception:
        cp_version = "unknown"

    with meta.open("w", encoding="utf-8") as f:
        json.dump(
            {
                **asdict(params),
                "cellpose_version": cp_version,
                "created_at": _dt.datetime.now().isoformat(timespec="seconds"),
            },
            f,
            indent=2,
        )
    return masks


def discard_cache(folder: Path, image_path: Path) -> None:
    """Used by re-segment to invalidate before re-running."""
    npy, meta = cache_paths(folder, image_path)
    for p in (npy, meta):
        try:
            p.unlink()
        except FileNotFoundError:
            pass
