"""Fiji-conform measurement of one cell — the method section, step by step:

  * grey value per pixel: ImageJ's RGB → 8-bit conversion, (R+G+B)/3 rounded
    (or the weighted 0.299/0.587/0.114 if the study says so);
  * I₀: the mean of `i0_fields` background fields (size × size px) at random
    places next to the cell — inside a ring `i0_ring_inner`…`i0_ring_outer` px
    from the contour, touching no cell and no stained debris. The draw is
    seeded per image, cell and re-draw count, so it is reproducible; fields
    set by hand replace it;
  * OD = −log₁₀(Ī / I₀) with Ī the mean grey value inside the contour (the OD
    of the mean, as Fiji's macro computes it, not the mean of pixel ODs);
  * a cell whose I₀ is outside [i0_min, i0_max] is excluded, not deleted.

On top, the distribution inside the cell: per-pixel OD statistics and a
radial profile (5 rings from the centre to the membrane).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
from scipy import ndimage as ndi

from .state import GRAY_WEIGHTED, Settings

PROFILE_RINGS = 5


def gray_image(img: np.ndarray, mode: str) -> np.ndarray:
    rgb = img[..., :3].astype(np.float32)
    if mode == GRAY_WEIGHTED:
        g = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    else:
        g = rgb.mean(axis=-1)
    return np.round(g)


@dataclass
class CellMeasure:
    fields: list                 # [[x, y], …] centres of the I₀ fields
    field_means: list            # grey mean per field
    i0: float | None
    i0_ok: bool
    od: float | None             # −log₁₀(Ī / I₀)
    mean_gray: float
    pix_mean: float | None
    pix_sd: float | None
    pix_median: float | None
    pix_p5: float | None
    pix_p95: float | None
    share_high: float | None
    profile: list                # PROFILE_RINGS values, centre → membrane
    hist: list                   # per-pixel OD values (for the panel only)


def _seed(image_key: str, cid: int, redraw: int) -> int:
    return zlib.crc32(f"{image_key}|{cid}|{redraw}".encode())


def place_fields(labels: np.ndarray, cid: int, sl, background_ok: np.ndarray,
                 s: Settings, seed: int) -> list:
    """Random field centres in the ring around the cell (image coordinates)."""
    H, W = labels.shape
    pad = s.i0_ring_outer + s.i0_field_size
    y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, H)
    x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, W)
    sub = labels[y0:y1, x0:x1]
    cell = sub == cid
    ring = ndi.binary_dilation(cell, iterations=s.i0_ring_outer)
    ring &= ~ndi.binary_dilation(sub > 0, iterations=s.i0_ring_inner)
    ring &= background_ok[y0:y1, x0:x1]
    # a centre is valid when its whole field lies in the ring
    ok = ndi.binary_erosion(ring, structure=np.ones((s.i0_field_size, s.i0_field_size)),
                            border_value=0)
    ys, xs = np.nonzero(ok)
    if not len(ys):
        return []
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(ys))
    min_d2 = (2 * s.i0_field_size) ** 2
    chosen: list = []
    for i in order:
        y, x = int(ys[i]), int(xs[i])
        if all((y - cy) ** 2 + (x - cx) ** 2 >= min_d2 for cx, cy in chosen):
            chosen.append((x, y))
            if len(chosen) == s.i0_fields:
                break
    return [[x + x0, y + y0] for x, y in chosen]


def field_means(gray: np.ndarray, fields: list, size: int) -> list:
    half = size // 2
    H, W = gray.shape
    out = []
    for x, y in fields:
        x, y = int(round(x)), int(round(y))
        patch = gray[max(y - half, 0):min(y + half + 1, H), max(x - half, 0):min(x + half + 1, W)]
        if patch.size:
            out.append(float(patch.mean()))
    return out


def radial_profile(mask: np.ndarray, values: np.ndarray, rings: int = PROFILE_RINGS) -> list:
    """Mean of `values` in `rings` bands from the centre (0) to the membrane."""
    dist = ndi.distance_transform_edt(np.pad(mask, 1))[1:-1, 1:-1]
    if dist.max() <= 0:
        return [None] * rings
    rel = 1.0 - dist[mask] / dist.max()          # 0 centre … 1 membrane
    v = values[mask]
    out = []
    for k in range(rings):
        lo, hi = k / rings, (k + 1) / rings
        sel = (rel >= lo) & ((rel < hi) if k < rings - 1 else (rel <= hi))
        out.append(float(v[sel].mean()) if sel.any() else None)
    return out


def measure_cell(gray: np.ndarray, labels: np.ndarray, cid: int, sl,
                 background_ok: np.ndarray, s: Settings, image_key: str,
                 manual_fields: list | None = None, redraw: int = 0) -> CellMeasure:
    sub_mask = labels[sl] == cid
    g = gray[sl][sub_mask]
    mean_gray = float(g.mean())
    fields = manual_fields if manual_fields else place_fields(
        labels, cid, sl, background_ok, s, _seed(image_key, cid, redraw))
    means = field_means(gray, fields, s.i0_field_size)
    i0 = float(np.mean(means)) if means else None
    if not i0:
        return CellMeasure(fields, means, None, False, None, mean_gray,
                           None, None, None, None, None, None, [None] * PROFILE_RINGS, [])
    pix = -np.log10(np.clip(g, 1.0, None) / i0)
    full = np.zeros(sub_mask.shape, np.float32)
    full[sub_mask] = pix
    return CellMeasure(
        fields=[[round(float(x), 1), round(float(y), 1)] for x, y in fields],
        field_means=[round(m, 2) for m in means],
        i0=i0,
        i0_ok=bool(s.i0_min <= i0 <= s.i0_max),
        od=float(-np.log10(mean_gray / i0)),
        mean_gray=mean_gray,
        pix_mean=float(pix.mean()),
        pix_sd=float(pix.std(ddof=1)) if pix.size > 1 else 0.0,
        pix_median=float(np.median(pix)),
        pix_p5=float(np.percentile(pix, 5)),
        pix_p95=float(np.percentile(pix, 95)),
        share_high=float((pix > s.od_high).mean()),
        profile=radial_profile(sub_mask, full),
        hist=pix.astype(np.float32),
    )
