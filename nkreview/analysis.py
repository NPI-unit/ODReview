"""One image from pixels to per-cell rows: contour → I₀ fields → OD → statistics.

Cellpose is the only slow step and is cached; everything here runs in well
under a second per image, so it is simply recomputed whenever the user changes
something (threshold, method, a discarded or drawn cell, an I₀ field).

Main value per cell is the Fiji-conform grey OD (see measure.py). The DAB OD
from colour deconvolution, the total RGB OD and a ring-I₀ control are extra
columns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy import ndimage as ndi

from . import od as odm
from .measure import CellMeasure, PROFILE_RINGS, gray_image, measure_cell
from .segment import (
    METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, cached_cellpose, polygon_mask,
    refine_hybrid, threshold_segment,
)
from .state import I0_RELATIVE, MANUAL_ID_BASE, Review, Settings

STATUS_KEPT = "gewertet"
STATUS_DISCARDED = "verworfen"
STATUS_EXCLUDED = "ausgeschlossen"


@dataclass
class ImageResult:
    labels: np.ndarray                  # final cell labels (manual cells included)
    method: str                         # what actually produced the contours
    threshold: float
    background: odm.Background
    stains: np.ndarray                  # HxWx3 OD per stain channel
    tod: np.ndarray                     # HxW total OD (unsmoothed)
    gray: np.ndarray                    # HxW grey value as the method defines it
    rows: list[dict] = field(default_factory=list)   # every cell, with its status
    cells: dict[int, CellMeasure] = field(default_factory=dict)
    note: str = ""


def _contours(img, s: Settings, rv: Review, cp: np.ndarray | None,
              method: str, thr: float):
    """(labels, method actually used, background, smoothed total OD, note)."""
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
    return labels, method, bg, tod_s, note


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


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def analyze(folder: Path, image: Path, img: np.ndarray, s: Settings, rv: Review,
            cp: np.ndarray | None = None) -> ImageResult:
    if cp is None:
        cp = cached_cellpose(folder, image, s.cellpose)
    method = rv.method or s.method
    thr = float(rv.threshold if rv.threshold is not None else s.threshold)
    labels, used, bg, tod_s, note = _contours(img, s, rv, cp, method, thr)
    if s.min_cell_area > 0 and labels.max():
        # Cellpose finds specks (debris, noise) far smaller than any nucleus
        sizes = np.bincount(labels.ravel())
        small = np.flatnonzero(sizes < s.min_cell_area)
        labels[np.isin(labels, small[small > 0])] = 0

    for k, poly in enumerate(rv.manual):
        m = polygon_mask(labels.shape, poly)
        labels[m] = MANUAL_ID_BASE + k

    od = odm.od_rgb(img, bg.i0)
    stains = odm.deconvolve(od, s.stain_vectors)
    tod = odm.total_od(od)
    gray = gray_image(img, s.gray_mode)
    res = ImageResult(labels, used, thr, bg, stains, tod, gray, note=note)

    # Background fields may not sit on stained debris either.
    background_ok = tod_s < thr
    # The image's free background: far from every cell, unstained. Reference
    # for the relative I₀ check (exposure differs between series).
    free = ~ndi.binary_dilation(labels > 0, iterations=40) & background_ok
    i0_ref = float(np.median(gray[free][::7])) if free.sum() > 5000 else None
    H, W = labels.shape
    discarded = set(int(v) for v in rv.discarded)
    i0_img = np.asarray(bg.i0)
    for idx, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        cid = idx
        m = labels[sl] == cid
        n = int(m.sum())
        if n == 0:
            continue
        cm = measure_cell(gray, labels, cid, sl, background_ok, s, image.name,
                          manual_fields=rv.i0_fields.get(str(cid)),
                          redraw=int(rv.i0_seed.get(str(cid), 0)))
        res.cells[cid] = cm
        dab = stains[sl][..., odm.DAB][m]
        h = stains[sl][..., 0][m]
        t = tod[sl][m]
        ys, xs = np.nonzero(m)
        ring = _ring_i0(img, labels, cid, sl, s.ring_inner, s.ring_outer)
        ring_dev = None if ring is None else float(np.max(np.abs(ring - i0_img) / i0_img) * 100)
        manual = cid >= MANUAL_ID_BASE
        h_mean = float(h.mean())
        dev = None if (cm.i0 is None or not i0_ref) else (cm.i0 - i0_ref) / i0_ref * 100
        if s.i0_check == I0_RELATIVE:
            cm.i0_ok = dev is not None and abs(dev) <= s.i0_rel_pct
        if cid in discarded:
            status, reason = STATUS_DISCARDED, "von Hand"
        elif cm.i0 is None:
            status, reason = STATUS_EXCLUDED, "kein Hintergrund neben der Zelle"
        elif not cm.i0_ok and s.i0_check == I0_RELATIVE:
            status, reason = STATUS_EXCLUDED, (
                "kein freier Hintergrund im Bild" if dev is None
                else f"I₀ {cm.i0:.1f} weicht {dev:+.1f} % vom Hintergrund ab")
        elif not cm.i0_ok:
            status, reason = STATUS_EXCLUDED, f"I₀ {cm.i0:.1f} außerhalb {s.i0_min:g}–{s.i0_max:g}"
        else:
            status, reason = STATUS_KEPT, ""
        row = {
            "zelle": cid,
            "status": status,
            "grund": reason,
            "verworfen": cid in discarded,
            "manuell": manual,
            "kontur": "manuell" if manual else used,
            "flaeche_px": n,
            "x": round(float(xs.mean() + sl[1].start), 1),
            "y": round(float(ys.mean() + sl[0].start), 1),
            "randzelle": bool(sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == H or sl[1].stop == W),
            "od_fiji": _r(cm.od),
            "i0": _r(cm.i0, 2),
            "i0_ok": cm.i0_ok,
            "i0_hintergrund": _r(i0_ref, 2),
            "i0_abw_pct": _r(dev, 2),
            "positiv": (None if s.pos_threshold <= 0 or cm.od is None
                        else bool(cm.od > s.pos_threshold)),
            "mittel_grau": _r(cm.mean_gray, 2),
            "pixel_od_mean": _r(cm.pix_mean),
            "pixel_od_sd": _r(cm.pix_sd),
            "pixel_od_median": _r(cm.pix_median),
            "pixel_od_p5": _r(cm.pix_p5),
            "pixel_od_p95": _r(cm.pix_p95),
            "anteil_od_ueber": _r(cm.share_high),
        }
        for k in range(s.i0_fields):
            row[f"i0_feld{k + 1}"] = cm.field_means[k] if k < len(cm.field_means) else None
        for k in range(PROFILE_RINGS):
            row[f"profil_r{k + 1}"] = _r(cm.profile[k])
        row.update({
            "dab_od_mean": _r(dab.mean()),
            "dab_iod": _r(dab.sum(), 1),
            "gesamt_od_mean": _r(t.mean()),
            "kanal_h_od_mean": _r(h_mean),
            "ring_i0_abweichung_pct": None if ring_dev is None else round(ring_dev, 2),
            "debris_verdacht": bool(s.debris_h > 0 and h_mean >= s.debris_h),
            "schwelle_od": thr,
        })
        res.rows.append(row)
    return res
