"""Cell contours: Cellpose, the hybrid refinement, and the threshold backup.

Hybrid (default): Cellpose finds and separates the cells; each cell's edge is
then redrawn from the total OD — every pixel above the threshold that is
connected to the cell, within `grow` px of the Cellpose contour. On bright-
field DAB this recovers the strongly stained rim Cellpose-SAM tends to cut
off, while debris and the halo stay outside.

Cellpose note (tested on the NK test image): with Cellpose-SAM a *smaller*
diameter upscales the image and the cell is lost; cellprob < 0 only inflates
the mask evenly, halo included. So the default diameter stays.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

METHOD_HYBRID = "hybrid"
METHOD_CELLPOSE = "cellpose"
METHOD_THRESHOLD = "threshold"
METHODS = (METHOD_HYBRID, METHOD_CELLPOSE, METHOD_THRESHOLD)


@dataclass
class CellposeParams:
    diameter: float = 30.0
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    model: str = "cpsam"
    # Preprocessing: Cellpose only runs on crops around DAB-positive regions
    # (smoothed DAB OD > roi_dab_threshold, ≥ roi_min_area px, + roi_pad px).
    # Unstained debris never reaches Cellpose, and a sparse image takes
    # seconds instead of a minute. roi_mode "full" = whole image as before.
    roi_mode: str = "dab"
    roi_dab_threshold: float = 0.04
    roi_min_area: int = 150
    roi_pad: int = 48


# ─────────────────────────────────────────────
# Cellpose (+ cache)
# ─────────────────────────────────────────────
_MODEL_CACHE: dict = {}


def detect_gpu_backend() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _get_model(name: str):
    if name not in _MODEL_CACHE:
        from cellpose import models
        _MODEL_CACHE[name] = models.CellposeModel(
            gpu=detect_gpu_backend() != "cpu", pretrained_model=name)
    return _MODEL_CACHE[name]


def _eval(img: np.ndarray, p: CellposeParams) -> np.ndarray:
    out = _get_model(p.model).eval(
        img[..., :3], diameter=float(p.diameter),
        flow_threshold=float(p.flow_threshold),
        cellprob_threshold=float(p.cellprob_threshold))
    masks = out[0] if isinstance(out, (tuple, list)) else out
    return np.asarray(masks, dtype=np.int32)


MIN_CROP = 256          # Cellpose-SAM works on 256 px tiles; smaller crops lose context
CROP_MULTIPLE = 32      # tested: crops whose sides are not multiples of 16 return
                        # no masks at all (cpsam, cellpose 4.1.1) — pad to 32


def _eval_padded(crop: np.ndarray, p: CellposeParams, fill) -> np.ndarray:
    """Cellpose on a crop padded with the background colour to sides that are
    multiples of CROP_MULTIPLE; the result is cut back to the crop."""
    h, w = crop.shape[:2]
    H = -(-h // CROP_MULTIPLE) * CROP_MULTIPLE
    W = -(-w // CROP_MULTIPLE) * CROP_MULTIPLE
    top, left = (H - h) // 2, (W - w) // 2
    canvas = np.empty((H, W, 3), dtype=crop.dtype)
    canvas[:] = np.asarray(fill, dtype=np.float64).astype(crop.dtype)
    canvas[top:top + h, left:left + w] = crop[..., :3]
    return _eval(canvas, p)[top:top + h, left:left + w]


def dab_regions(img: np.ndarray, p: CellposeParams,
                stain_vectors: list | None = None):
    """(boxes, background): y/x slice pairs around DAB-positive regions,
    padded to at least MIN_CROP and merged where they overlap."""
    from . import od as odm
    vectors = stain_vectors or odm.STAIN_PRESETS[odm.DEFAULT_PRESET]
    bg = odm.estimate_background(img, None)
    dab = odm.deconvolve(odm.od_rgb(img, bg.i0), vectors)[..., odm.DAB]
    pos = ndi.gaussian_filter(dab, 3) > p.roi_dab_threshold
    lab, n = ndi.label(pos)
    if not n:
        return [], bg
    sizes = ndi.sum(pos, lab, range(1, n + 1))
    H, W = pos.shape
    boxes = []
    for i, sl in enumerate(ndi.find_objects(lab)):
        if sl is None or sizes[i] < p.roi_min_area:
            continue
        y0, y1 = sl[0].start - p.roi_pad, sl[0].stop + p.roi_pad
        x0, x1 = sl[1].start - p.roi_pad, sl[1].stop + p.roi_pad
        cy, cx = (y0 + y1) / 2, (x0 + x1) / 2
        hh, hw = max(y1 - y0, MIN_CROP) / 2, max(x1 - x0, MIN_CROP) / 2
        y0, y1 = int(max(cy - hh, 0)), int(min(cy + hh, H))
        x0, x1 = int(max(cx - hw, 0)), int(min(cx + hw, W))
        boxes.append([y0, y1, x0, x1])
    merged = True
    while merged:                     # merge overlapping boxes until stable
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[1] and b[0] < a[1] and a[2] < b[3] and b[2] < a[3]:
                    boxes[i] = [min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])]
                    boxes.pop(j)
                    merged = True
                    break
            if merged:
                break
    return [(slice(y0, y1), slice(x0, x1)) for y0, y1, x0, x1 in boxes], bg


def run_cellpose(img: np.ndarray, p: CellposeParams,
                 stain_vectors: list | None = None) -> np.ndarray:
    if p.roi_mode != "dab":
        return _eval(img, p)
    out = np.zeros(img.shape[:2], dtype=np.int32)
    next_id = 1
    boxes, bg = dab_regions(img, p, stain_vectors)
    for sl in boxes:
        crop = _eval_padded(img[sl], p, bg.i0)
        tgt = out[sl]
        for cid in np.unique(crop[crop > 0]):
            m = (crop == cid) & (tgt == 0)     # merged boxes never overlap; belt and braces
            if m.any():
                tgt[m] = next_id
                next_id += 1
    return out


def cache_dir(folder: Path) -> Path:
    d = Path(folder) / ".nk_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cp_paths(folder: Path, image: Path) -> tuple[Path, Path]:
    rel = Path(image).relative_to(folder).with_suffix("")
    stem = "__".join(rel.parts)
    d = cache_dir(folder)
    return d / f"{stem}_cellpose.npz", d / f"{stem}_cellpose.json"


def cached_cellpose(folder: Path, image: Path, p: CellposeParams) -> np.ndarray | None:
    npz, meta = _cp_paths(folder, image)
    try:
        info = json.loads(meta.read_text("utf-8"))
        if any(info.get(k) != v for k, v in asdict(p).items()):
            return None
        with np.load(npz) as z:
            return z["masks"].astype(np.int32)
    except (OSError, ValueError, KeyError):
        return None


def cellpose_for_image(folder: Path, image: Path, img: np.ndarray,
                       p: CellposeParams, force: bool = False,
                       stain_vectors: list | None = None) -> np.ndarray:
    if not force:
        hit = cached_cellpose(folder, image, p)
        if hit is not None:
            return hit
    masks = run_cellpose(img, p, stain_vectors)
    npz, meta = _cp_paths(folder, image)
    np.savez_compressed(npz, masks=masks.astype(np.uint16))
    try:
        import cellpose
        ver = getattr(cellpose, "version", "unknown")
    except Exception:
        ver = "unknown"
    meta.write_text(json.dumps({**asdict(p), "cellpose_version": ver,
                                "created_at": _dt.datetime.now().isoformat(timespec="seconds")},
                               indent=2), "utf-8")
    return masks


def has_cellpose_cache(folder: Path, image: Path, p: CellposeParams) -> bool:
    npz, meta = _cp_paths(folder, image)
    if not (npz.exists() and meta.exists()):
        return False
    try:
        info = json.loads(meta.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return all(info.get(k) == v for k, v in asdict(p).items())


# ─────────────────────────────────────────────
# OD-based contours
# ─────────────────────────────────────────────
def _clean(b: np.ndarray, open_iter: int = 2) -> np.ndarray:
    if open_iter:
        b = ndi.binary_opening(b, iterations=open_iter)
    return ndi.binary_fill_holes(b)


def refine_hybrid(cp: np.ndarray, tod_s: np.ndarray, threshold: float,
                  grow: int = 10, min_keep: float = 0.5) -> np.ndarray:
    """Redraw each Cellpose cell's edge from the smoothed total OD.

    Per cell, inside its bounding box: candidates = OD > threshold within
    `grow` px of the cell and not inside another Cellpose cell; keep the
    components that overlap the cell, fill holes. If that leaves less than
    `min_keep` of the Cellpose area (a pale cell below the threshold), the
    Cellpose contour is kept. Ids are preserved."""
    out = np.zeros_like(cp)
    others_all = cp > 0
    H, W = cp.shape
    for sl_idx, sl in enumerate(ndi.find_objects(cp), start=1):
        if sl is None:
            continue
        y0, y1 = max(sl[0].start - grow - 2, 0), min(sl[0].stop + grow + 2, H)
        x0, x1 = max(sl[1].start - grow - 2, 0), min(sl[1].stop + grow + 2, W)
        sub = cp[y0:y1, x0:x1]
        cell = sub == sl_idx
        reach = ndi.binary_dilation(cell, iterations=grow)
        others = others_all[y0:y1, x0:x1] & ~cell
        cand = _clean((tod_s[y0:y1, x0:x1] > threshold) & reach & ~others)
        lab, n = ndi.label(cand)
        if n:
            hit = np.unique(lab[cell & (lab > 0)])
            new = ndi.binary_fill_holes(np.isin(lab, hit[hit > 0]))
        else:
            new = np.zeros_like(cell)
        if new.sum() < min_keep * cell.sum():
            new = cell
        tgt = out[y0:y1, x0:x1]
        tgt[new & (tgt == 0)] = sl_idx
    return out


def threshold_segment(tod_s: np.ndarray, threshold: float, min_area: int = 500) -> np.ndarray:
    """Backup without Cellpose: connected regions of OD > threshold."""
    b = _clean(tod_s > threshold)
    lab, n = ndi.label(b)
    if not n:
        return lab.astype(np.int32)
    sizes = ndi.sum(b, lab, range(1, n + 1))
    keep = np.flatnonzero(sizes >= min_area) + 1
    out = np.zeros_like(lab, dtype=np.int32)
    for new_id, old in enumerate(keep, start=1):
        out[lab == old] = new_id
    return out


def polygon_mask(shape: tuple[int, int], pts: list[tuple[float, float]]) -> np.ndarray:
    from skimage.draw import polygon
    m = np.zeros(shape, dtype=bool)
    if len(pts) >= 3:
        xs, ys = zip(*pts)
        rr, cc = polygon(np.asarray(ys), np.asarray(xs), shape)
        m[rr, cc] = True
    return m
