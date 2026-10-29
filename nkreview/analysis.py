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
from .measure import CellMeasure, PROFILE_RINGS, _seed, gray_image, measure_cell, place_image_fields
from .segment import (
    METHOD_CELLPOSE, METHOD_HYBRID, METHOD_THRESHOLD, cached_cellpose, params_for_image, polygon_mask,
    refine_hybrid, threshold_segment,
)
from .state import I0_LOCAL, I0_PER_IMAGE, I0_RELATIVE, MANUAL_ID_BASE, Review, Settings

IMAGE_KEY = "image"     # Review.i0_fields / i0_seed key of the per-image fields

STATUS_KEPT = "kept"
STATUS_DISCARDED = "discarded"
STATUS_EXCLUDED = "excluded"


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
    image_fields: list | None = None    # I₀ per image: the image's fields (else None)
    cells: dict[int, CellMeasure] = field(default_factory=dict)
    note: str = ""


def _contours(img, s: Settings, rv: Review, cp: np.ndarray | None,
              method: str, thr: float):
    """(labels, method actually used, background, smoothed total OD, note)."""
    note = ""
    if method in (METHOD_HYBRID, METHOD_CELLPOSE) and cp is None:
        method, note = METHOD_THRESHOLD, "Segmentation has not run yet – OD threshold used"
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


def area_limits(s: Settings, image: Path) -> tuple[float, float]:
    """Kept object area in px (0 = no limit). The objective's tile gives a nucleus Ø in µm,
    turned into px with the file's own µm/px; else the study's px areas, scaled when the
    image has another pixel size than the study."""
    from .segment import image_objective
    px = params_for_image(image, s.cellpose).pixel_size
    rule = s.objectives.get(image_objective(image), {})
    if px and (rule.get("min_d") or rule.get("max_d")):
        disc = lambda d: np.pi * (d / 2) ** 2 / px ** 2
        return disc(rule.get("min_d") or 0), disc(rule.get("max_d") or 0)
    k = (s.cellpose.pixel_size / px) ** 2 if px and s.cellpose.pixel_size else 1.0
    return s.min_cell_area * k, s.max_cell_area * k


def field_spread(means: list) -> float | None:
    """Largest deviation of one I₀ field from the fields' mean, in % (None below 2 fields)."""
    if len(means) < 2:
        return None
    m = float(np.mean(means))
    return float(np.max(np.abs(np.asarray(means) - m)) / m * 100) if m else None


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def analyze(folder: Path, image: Path, img: np.ndarray, s: Settings, rv: Review,
            cp: np.ndarray | None = None) -> ImageResult:
    if cp is None:
        cp = cached_cellpose(folder, image, s.cellpose)
    method = rv.method or s.method
    thr = float(rv.threshold if rv.threshold is not None else s.threshold)
    labels, used, bg, tod_s, note = _contours(img, s, rv, cp, method, thr)
    lo, hi = area_limits(s, image)
    if (lo > 0 or hi > 0) and labels.max():
        # Cellpose finds specks (debris, noise) far smaller than any nucleus
        sizes = np.bincount(labels.ravel())
        drop = (sizes < lo) | ((hi > 0) & (sizes > hi))
        drop[0] = False
        labels[drop[labels]] = 0

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
    confirmed = set(int(v) for v in rv.confirmed)
    i0_img = np.asarray(bg.i0)
    if s.i0_scope == I0_PER_IMAGE:
        res.image_fields = rv.i0_fields.get(IMAGE_KEY) or place_image_fields(
            free, s, _seed(image.name, 0, int(rv.i0_seed.get(IMAGE_KEY, 0))))
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
                          redraw=int(rv.i0_seed.get(str(cid), 0)), image_fields=res.image_fields)
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
        spread = field_spread(cm.field_means)
        if s.i0_check == I0_RELATIVE:
            cm.i0_ok = dev is not None and abs(dev) <= s.i0_rel_pct
        elif s.i0_check == I0_LOCAL and cm.i0 is not None:
            cm.i0_ok = spread is None or spread <= s.i0_rel_pct      # one field: nothing to compare
        if cid in discarded:
            status, reason = STATUS_DISCARDED, "by hand"
        elif cm.i0 is None:                 # no OD at all: nothing a reviewer could confirm
            status, reason = STATUS_EXCLUDED, "no background next to the cell"
        # A failed I₀ check is a warning, not an exclusion: the cell stays kept, is shown
        # for a look, and counts once the reviewer confirms it (summary.csv: confirmed only).
        elif not cm.i0_ok and s.i0_check == I0_RELATIVE:
            status, reason = STATUS_KEPT, (
                "no free background in the image" if dev is None
                else f"I₀ {cm.i0:.1f} deviates {dev:+.1f} % from the background")
        elif not cm.i0_ok and s.i0_check == I0_LOCAL:
            status, reason = STATUS_KEPT, f"I₀ fields disagree by {spread:.1f} %"
        elif not cm.i0_ok:
            status, reason = STATUS_KEPT, f"I₀ {cm.i0:.1f} outside {s.i0_min:g}–{s.i0_max:g}"
        else:
            status, reason = STATUS_KEPT, ""
        row = {
            "cell": cid,
            "status": status,
            "reason": reason,
            "discarded": cid in discarded,
            # only a kept cell can be confirmed; summary.csv counts confirmed cells only
            "confirmed": status == STATUS_KEPT and cid in confirmed,
            "has_note": bool(str(rv.notes.get(str(cid), "")).strip()),
            "i0_warning": status == STATUS_KEPT and not cm.i0_ok,
            "manual": manual,
            "contour": "manual" if manual else used,
            "area_px": n,
            "x": round(float(xs.mean() + sl[1].start), 1),
            "y": round(float(ys.mean() + sl[0].start), 1),
            "edge_cell": bool(sl[0].start == 0 or sl[1].start == 0 or sl[0].stop == H or sl[1].stop == W),
            "od": _r(cm.od),
            "i0": _r(cm.i0, 2),
            "i0_ok": cm.i0_ok,
            "i0_background": _r(i0_ref, 2),
            "i0_dev_pct": _r(dev, 2),
            "i0_field_spread_pct": _r(spread, 2),
            "positive": (None if s.pos_threshold <= 0 or cm.od is None
                        else bool(cm.od > s.pos_threshold)),
            "mean_gray": _r(cm.mean_gray, 2),
            "pixel_od_mean": _r(cm.pix_mean),
            "pixel_od_sd": _r(cm.pix_sd),
            "pixel_od_median": _r(cm.pix_median),
            "pixel_od_p5": _r(cm.pix_p5),
            "pixel_od_p95": _r(cm.pix_p95),
            "share_od_above": _r(cm.share_high),
        }
        for k in range(s.i0_fields):
            row[f"i0_field{k + 1}"] = cm.field_means[k] if k < len(cm.field_means) else None
        for k in range(PROFILE_RINGS):
            row[f"profile_r{k + 1}"] = _r(cm.profile[k])
        row.update({
            "dab_od_mean": _r(dab.mean()),
            "dab_iod": _r(dab.sum(), 1),
            "total_od_mean": _r(t.mean()),
            "h_channel_od_mean": _r(h_mean),
            "ring_i0_dev_pct": None if ring_dev is None else round(ring_dev, 2),
            "debris_suspect": bool(s.debris_h > 0 and h_mean >= s.debris_h),
            "threshold_od": thr,
        })
        res.rows.append(row)
    return res
