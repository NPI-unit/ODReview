"""Lazy per-image Cellpose runner with parameter-aware caching.

Cache layout (Frage 4 + 10c):
    <folder>/.erys_cache/
        <stem>_masks.npy          – uint16 label image
        <stem>_masks.json         – {diameter, flow_threshold, cellprob_threshold,
                                      model, cellpose_version, created_at}

run_for_image(...) is synchronous and CPU/GPU-bound. The UI never calls it on
the Tk thread directly — it runs it on a worker thread via
dialogs.run_in_thread_pumping(), which keeps the event loop (and the modal
spinner) responsive while inference runs. Nothing here touches Tk, so it is
safe to call off the main thread. A single worker runs at a time (the modal
grab prevents concurrent segmentation), so the module-level model cache needs
no locking.
"""

from __future__ import annotations

import datetime as _dt
import json
import zipfile
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
    # Normalize the folder path first. On Windows a path coming from
    # filedialog.askdirectory (forward slashes) joined with a subfolder, or one
    # with odd casing / trailing separators, can make rglob enumerate nothing
    # even though the files are right there — the blank-sidebar-on-import bug.
    # resolve() canonicalizes slashes, case and `..`.
    try:
        folder = Path(folder).resolve()
    except OSError:
        folder = Path(folder)
    excl = set()
    for e in (exclude or set()):
        try:
            excl.add(Path(e).resolve())
        except OSError:
            excl.add(Path(e))

    out: list[Path] = []
    seen: dict[str, Path] = {}
    dupes: list[Path] = []
    # Per-reason drop tally so a single Windows run reveals where images are lost.
    drops = {"not_file": 0, "ext": 0, "overlay": 0, "hidden": 0, "excluded": 0}

    try:
        candidates = sorted(folder.rglob("*"))
    except OSError as e:
        print(f"[list_images] rglob failed on {folder!r}: {e}", flush=True)
        candidates = []
    raw = len(candidates)

    for p in candidates:
        try:
            if not p.is_file():
                drops["not_file"] += 1; continue
            if p.suffix.lower() not in IMG_EXTS:
                drops["ext"] += 1; continue
            if p.name.endswith("_overlay.png"):
                drops["overlay"] += 1; continue  # export artifact, not a source
            rel_parts = p.relative_to(folder).parts[:-1]  # parent dirs only
            if any(part in _SKIP_DIRS or part.startswith(".") for part in rel_parts):
                drops["hidden"] += 1; continue
            rp = p.resolve()
            if excl and (rp in excl or any(e == rp or e in rp.parents for e in excl)):
                drops["excluded"] += 1; continue
            if p.name in seen:
                dupes.append(p); continue
            seen[p.name] = p
            out.append(p)
        except OSError:
            # Unstattable path (long path, permission, race) — skip, never crash.
            drops["not_file"] += 1
            continue

    # Fallback: recursion yielded nothing usable, but the top level itself holds
    # images (e.g. a freshly imported folder). Scan one level non-recursively so
    # an import never lands on a blank sidebar when the files are clearly there.
    if not out:
        try:
            flat = sorted(
                q for q in folder.iterdir()
                if q.is_file() and q.suffix.lower() in IMG_EXTS
                and not q.name.endswith("_overlay.png")
            )
        except OSError:
            flat = []
        if flat:
            print(f"[list_images] recursive scan empty; flat fallback found "
                  f"{len(flat)} image(s) in {folder}", flush=True)
            out = flat

    if not out or dupes:
        print(f"[list_images] folder={folder} raw={raw} kept={len(out)} "
              f"drops={drops} dupes={len(dupes)}", flush=True)
    if dupes:
        print(f"[list_images] {len(dupes)} doppelte Dateinamen übersprungen "
              f"(z.B. {dupes[0].name})", flush=True)
    return out


# ─────────────────────────────────────────────
# Cache files
# ─────────────────────────────────────────────
def cache_paths(folder: Path, image_path: Path) -> tuple[Path, Path]:
    """WRITE target for a mask + its params: always the hidden .erys_cache."""
    cache = cache_dir_for(folder)
    stem = image_path.stem
    return cache / f"{stem}_masks.npy", cache / f"{stem}_masks.json"


def _sibling_paths(image_path: Path) -> tuple[Path, Path]:
    """Export layout: mask + params sit right next to the image."""
    stem = image_path.stem
    d = image_path.parent
    return d / f"{stem}_masks.npy", d / f"{stem}_masks.json"


def mask_read_paths(folder: Path, image_path: Path) -> tuple[Path | None, Path | None]:
    """Resolve where to READ a mask/params from. The cache always wins (it is
    the live, editable copy); otherwise fall back to the export layout next to
    the image, so an `outputs/` folder is directly readable/packageable.
    Returns (npy_or_None, meta_or_None)."""
    c_npy, c_meta = cache_paths(folder, image_path)
    s_npy, s_meta = _sibling_paths(image_path)
    npy = c_npy if c_npy.exists() else (s_npy if s_npy.exists() else None)
    meta = c_meta if c_meta.exists() else (s_meta if s_meta.exists() else None)
    return npy, meta


def has_cache(folder: Path, image_path: Path) -> bool:
    npy, _ = mask_read_paths(folder, image_path)
    return npy is not None


def cached_params(folder: Path, image_path: Path) -> dict | None:
    _, meta = mask_read_paths(folder, image_path)
    if meta is None:
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


def save_mask(path: Path, masks: np.ndarray) -> None:
    """Write a label mask compactly: int16 (falls back to int32 only if there
    are >32k labels) + zlib, in an .npz container kept under the historical
    `_masks.npy` filename. np.load auto-detects the zip on read, so packages,
    the rename script and every reader keep working unchanged. ~180x smaller
    than the old uncompressed int32 dump."""
    m = np.asarray(masks)
    dtype = np.int16 if int(m.max(initial=0)) < 32767 else np.int32
    with open(path, "wb") as f:
        np.savez_compressed(f, m=m.astype(dtype))


def _read_mask(path: Path) -> np.ndarray:
    """Read either the new compressed .npz-in-.npy or a legacy plain int32 .npy."""
    obj = np.load(path)
    if isinstance(obj, np.lib.npyio.NpzFile):
        try:
            arr = obj[obj.files[0]]
        finally:
            obj.close()
        return np.asarray(arr)
    return np.asarray(obj)


def load_cached_masks(folder: Path, image_path: Path) -> np.ndarray | None:
    npy, _ = mask_read_paths(folder, image_path)
    if npy is None:
        return None
    try:
        return _read_mask(npy).astype(np.int32)
    except (OSError, ValueError, zipfile.BadZipFile):
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
    save_mask(npy, masks)

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
