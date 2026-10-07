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

import os

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


LAST_ACCESS_DENIED: Path | None = None     # set by list_images when macOS refused access


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

    # rglob swallows PermissionError and just yields nothing — e.g. when macOS
    # has not (yet) allowed this Python to read a USB stick. Ask directly so
    # the app can say so instead of "no images".
    global LAST_ACCESS_DENIED
    LAST_ACCESS_DENIED = None
    try:
        os.listdir(folder)
    except PermissionError as e:
        LAST_ACCESS_DENIED = folder
        print(f"[list_images] no access to {folder!r}: {e}", flush=True)
        return []
    except OSError:
        pass
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
            # Every part INCLUDING the filename. The old `[:-1]` checked only
            # parent directories, so a dot-file itself always passed. On a
            # FAT/exFAT volume — any USB stick — macOS writes an AppleDouble
            # `._<name>` twin next to every file, so `._image.tif` was counted
            # as an image: 238 photos became 476 "images", half of them 4 KB
            # binary blobs. `._` sorts before letters, so the folder opened on
            # a blob, load_image failed, the canvas stayed empty and every
            # click died at the `image is None` guard. It also doubled the
            # image count feeding the 2000-cell evaluation.
            rel_parts = p.relative_to(folder).parts
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
                and not q.name.startswith(".")      # AppleDouble / dot-files
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
    """Cheap existence probe for a cached mask.

    Deliberately does NOT go through mask_read_paths(): that also resolves the
    *meta* path (one or two extra stat calls) which this caller throws away, and
    has_cache runs once per image on every sidebar refresh.
    """
    c_npy, _ = cache_paths(folder, image_path)
    if c_npy.exists():
        return True
    return _sibling_paths(image_path)[0].exists()


def cached_params(folder: Path, image_path: Path) -> dict | None:
    _, meta = mask_read_paths(folder, image_path)
    if meta is None:
        return None
    try:
        with meta.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        # ValueError covers UnicodeDecodeError too — see read_json(). An
        # unreadable manifest means "parameters unknown", which is exactly the
        # `None` this already returns.
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


# Masks already reported as unreadable, so one broken file does not flood the
# console — every counter, the statistics window and the export each read it.
_UNREADABLE: set[str] = set()


def load_cached_masks(folder: Path, image_path: Path) -> np.ndarray | None:
    """The cached mask, or None if there is none — or it cannot be read.

    `EOFError` was missing from the caught set and is neither an OSError nor a
    ValueError, so it escaped all the way up and crashed the statistics window.
    It is what np.load raises for a file whose bytes are not there: on macOS
    iCloud and on Windows OneDrive evict file *contents* while keeping the
    directory entry, leaving a full-size file with zero allocated blocks.

    Returning None makes such an image read as "not segmented", which is the
    honest state — but silently doing so would hide real data loss, so the file
    is named on stdout once (the launchers keep a console open).
    """
    npy, _ = mask_read_paths(folder, image_path)
    if npy is None:
        return None
    try:
        return _read_mask(npy).astype(np.int32)
    except (OSError, ValueError, EOFError, zipfile.BadZipFile) as e:
        key = str(npy)
        if key not in _UNREADABLE:
            _UNREADABLE.add(key)
            print(f"[Maske unlesbar] {image_path.name}: {type(e).__name__} — "
                  f"{npy}. Datei ist vorhanden, aber ihr Inhalt nicht "
                  f"(Cloud-Auslagerung?). Das Bild gilt als nicht segmentiert.",
                  flush=True)
        return None


# ─────────────────────────────────────────────
# Cellpose execution
# ─────────────────────────────────────────────
_MODEL_CACHE: dict = {}  # keyed by (model_name, use_gpu); switching toggles re-init.

# Models the pinned Cellpose ships with. 4.1.1 has exactly one: the v3 zoo
# (cyto/cyto2/cyto3/nuclei) is gone with Cellpose-SAM. 4.2 added cpdino,
# cpdino-vitb and cpsam_v2, so this list is NOT authoritative — known_builtins()
# asks the installed package before anything is refused. Hard-coding it would
# make this app block models its own Cellpose supports.
# Only what THIS pin (4.1.1) actually ships. Listing names a newer Cellpose has
# (4.2 added cpsam_v2, cpdino, cpdino-vitb) would be worse than useless: the name
# would pass our check, reach a Cellpose that does not know it, and be silently
# replaced by cpsam — the very substitution this module exists to prevent.
BUILTIN_MODELS: tuple[str, ...] = ("cpsam",)
BUILTIN_MODEL = BUILTIN_MODELS[0]        # what an empty profile setting means

_BUILTIN_CACHE: tuple[str, ...] | None = None


def known_builtins(force: bool = False) -> tuple[str, ...]:
    """Model names the INSTALLED Cellpose provides itself.

    Authoritative — never merged with BUILTIN_MODELS, so upgrading Cellpose adds
    its new models here without a code change, and *not* upgrading cannot make a
    name pass that the installed package would then quietly swap for cpsam.

    `force=False` (UI) only reads cellpose if it is already imported, so opening
    a dialog never pays for the torch import; the pinned list is the fallback.
    `force=True` (resolution, which is about to run inference anyway) imports it.
    """
    global _BUILTIN_CACHE
    if _BUILTIN_CACHE is not None:
        return _BUILTIN_CACHE
    import sys as _sys
    if not force and "cellpose.models" not in _sys.modules:
        return BUILTIN_MODELS          # best guess, never used to allow a name
    try:
        from cellpose.models import MODEL_NAMES
        names = tuple(MODEL_NAMES)
    except Exception:
        names = BUILTIN_MODELS
    _BUILTIN_CACHE = names
    return names


class ModelUnavailable(RuntimeError):
    """The profile asks for a model this machine has no file for.

    Raised instead of letting Cellpose do what it does on its own: log
    "pretrained model ... not found, using default model" and segment with cpsam
    anyway. That silent substitution would mix two models' counts inside one
    study, and the mask metadata would still claim the requested one.
    """

    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Cellpose model {name!r} is not registered on this machine")


def resolve_model(name: str, registry: dict | None) -> str:
    """Model NAME (as stored in a profile) -> what to hand Cellpose.

    Returns "cpsam" for the built-in, otherwise the registered file path.
    Raises ModelUnavailable if the name is unknown here or the file is gone.
    """
    if not name:
        return BUILTIN_MODEL
    path = (registry or {}).get(name)
    if path and Path(path).is_file():
        return path
    # The installed package decides, not a list baked in here — that way an
    # upgrade brings cpdino/cpsam_v2 along, and *not* upgrading refuses them
    # instead of letting Cellpose substitute cpsam behind our back.
    if name in known_builtins(force=True):
        return name
    raise ModelUnavailable(name)


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


def run_cellpose(image: np.ndarray, params: CellposeParams, use_gpu: bool = True,
                 model_registry: dict | None = None) -> np.ndarray:
    """Run Cellpose synchronously. Returns int32 mask.

    Raises ModelUnavailable before doing any work if the profile's model is not
    resolvable here.
    """
    model = _get_model(resolve_model(params.model, model_registry), use_gpu)
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
    model_registry: dict | None = None,
) -> np.ndarray:
    """Run-or-cache. Returns the mask, writes <stem>_masks.npy + .json."""
    if not force:
        if params_match(folder, image_path, params):
            cached = load_cached_masks(folder, image_path)
            if cached is not None:
                return cached

    resolved = resolve_model(params.model, model_registry)
    masks = run_cellpose(image, params, use_gpu=use_gpu,
                         model_registry=model_registry)

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
                # What actually ran, not what was asked for — the two can only
                # differ via a bug now that resolve_model() raises, but the
                # provenance belongs in the file either way.
                "model_file": resolved,
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
