"""One image from pixels to per-cell rows: contour → I₀ → OD → statistics.

Cellpose is the only slow step and is cached; everything here runs in well
under a second per image, so it is simply recomputed whenever the user changes
something (threshold, method, a discarded or drawn cell).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from . import od as odm
from .segment import (
    METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, cached_cellpose, polygon_mask,
    refine_hybrid, threshold_segment,
)
from .state import MANUAL_ID_BASE, Review, Settings


@dataclass
class ImageResult:
    labels: np.ndarray                  # final cell labels (manual cells included)
    method: str                         # what actually produced the contours
    threshold: float
    background: odm.Background
    stains: np.ndarray                  # HxWx3 OD per stain channel
    tod: np.ndarray                     # HxW total OD (unsmoothed)
    rows: list[dict] = field(default_factory=list)   # every cell, discarded ones flagged
    note: str = ""


def _contours(img, s: Settings, rv: Review, cp: np.ndarray | None,
              method: str, thr: float):
    """(labels, method actually used, background, note)."""
    note = ""
    if method in (METHOD_HYBRID, METHOD_CELLPOSE) and cp is None:
        method, note = METHOD_THRESHOLD, "Cellpose noch nicht gelaufen – OD-Schwelle verwendet"
    # I₀ first from a provisional mask (Cellpose, or a rough threshold on an
    # image-median I₀), then the contour from the OD that I₀ defines.
    if cp is not None:
        provisional = cp > 0
    else:
        rough = odm.estimate_background(img, None)
        provisional = threshold_segment(odm.smoothed_total_od(img, rough.i0), thr, s.min_area) > 0
    bg = odm.estimate_background(img, provisional, rv.roi)
    tod_s = odm.smoothed_total_od(img, bg.i0)
    if method == METHOD_CELLPOSE:
        labels = cp.copy()
    elif method == METHOD_HYBRID:
        labels = refine_hybrid(cp, tod_s, thr, s.grow)
    else:
        labels = threshold_segment(tod_s, thr, s.min_area)
    return labels, method, bg, note


def _ring_i0(img, labels, cid, sl, inner, outer):
    H, W = labels.shape
    y0, y1 = max(sl[0].start - outer - 1, 0), min(sl[0].stop + outer + 1, H)
    x0, x1 = max(sl[1].start - outer - 1, 0), min(sl[1].stop + outer + 1, W)
    sub = labels[y0:y1, x0:x1]
    cell = sub == cid
    ring = ndi.binary_dilation(cell, iterations=outer) & ~ndi.binary_dilation(cell, iterations=inner)
    ring &= ~ndi.binary_dilation(sub > 0, iterations=inner)     # keep clear of all cells
    pix = img[y0:y1, x0:x1][ring]
    if len(pix) < 50:
        return None
    return np.median(pix.reshape(-1, 3), axis=0)


def analyze(folder: Path, image: Path, img: np.ndarray, s: Settings, rv: Review,
            cp: np.ndarray | None = None) -> ImageResult:
    if cp is None:
        cp = cached_cellpose(folder, image, s.cellpose)
    method = rv.method or s.method
    thr = float(rv.threshold if rv.threshold is not None else s.threshold)
    labels, used, bg, note = _contours(img, s, rv, cp, method, thr)

    for k, poly in enumerate(rv.manual):
        m = polygon_mask(labels.shape, poly)
        labels[m] = MANUAL_ID_BASE + k

    od = odm.od_rgb(img, bg.i0)
    stains = odm.deconvolve(od, s.stain_vectors)
    tod = odm.total_od(od)
    res = ImageResult(labels, used, thr, bg, stains, tod, note=note)

    H, W = labels.shape
    discarded = set(int(v) for v in rv.discarded)
    i0 = np.asarray(bg.i0)
    for idx, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        cid = idx
        m = labels[sl] == cid
        n = int(m.sum())
        if n == 0:
            continue
        dab = stains[sl][..., odm.DAB][m]
        h = stains[sl][..., 0][m]
        rest = stains[sl][..., 2][m]
        t = tod[sl][m]
        ys, xs = np.nonzero(m)
        ring = _ring_i0(img, labels, cid, sl, s.ring_inner, s.ring_outer)
        dev = None if ring is None else float(np.max(np.abs(ring - i0) / i0) * 100)
        manual = cid >= MANUAL_ID_BASE
        res.rows.append({
            "zelle": cid,
            "verworfen": cid in discarded,
            "manuell": manual,
            "kontur": "manuell" if manual else used,
            "flaeche_px": n,
            "x": round(float(xs.mean() + sl[1].start), 1),
            "y": round(float(ys.mean() + sl[0].start), 1),
            "randzelle": bool(sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == H or sl[1].stop == W),
            "dab_od_mean": float(dab.mean()),
            "dab_od_sd": float(dab.std(ddof=1)) if n > 1 else 0.0,
            "dab_od_median": float(np.median(dab)),
            "dab_od_p95": float(np.percentile(dab, 95)),
            "dab_od_max": float(dab.max()),
            "dab_iod": float(dab.sum()),
            "gesamt_od_mean": float(t.mean()),
            "gesamt_iod": float(t.sum()),
            "kanal_h_od_mean": float(h.mean()),
            "kanal_rest_od_mean": float(rest.mean()),
            "i0_r": bg.i0[0], "i0_g": bg.i0[1], "i0_b": bg.i0[2],
            "i0_quelle": bg.source,
            "ring_i0_abweichung_pct": None if dev is None else round(dev, 2),
            "ring_warnung": bool(dev is not None and dev > s.ring_warn_pct),
            "schwelle_od": thr,
        })
    return res
