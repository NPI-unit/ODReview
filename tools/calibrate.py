"""Calibrate the nucleus settings per objective against what a reviewer marked.

    ./.venv/bin/python tools/calibrate.py "Pilot C2C12 – calibration"
    ./.venv/bin/python tools/calibrate.py "Pilot C2C12 – calibration" --write

Truth per image = the nuclei the reviewer confirmed (C) plus the ones drawn by
hand (tool 2); discarded and unconfirmed objects are not nuclei. Only images
marked reviewed (Enter) count – an image with an unmarked nucleus would teach
the wrong answer. For each objective a small grid is tried: InstanSeg's pixel
size × {0.8 … 1.25} (how large the model expects a nucleus) and the kept
nucleus Ø range. Score: F1 of matched nuclei (IoU ≥ 0.5) over all images of
that objective. --write stores the best Ø range in the study (the pixel-size
factor is only reported).
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nkreview.segment import cached_cellpose, image_pixel_size, polygon_mask, run_instanseg  # noqa: E402
from nkreview.state import MANUAL_ID_BASE, Review, load_image  # noqa: E402
from nkreview.study import Study, list_studies  # noqa: E402

PX_FACTORS = (0.8, 0.9, 1.0, 1.1, 1.25)
MIN_D = (3.0, 4.0, 5.0, 6.0)
MAX_D = (15.0, 20.0, 25.0, 30.0)
IOU = 0.5


def truth_masks(study: Study, info) -> list[np.ndarray] | None:
    rv = Review.load(info.folder, info.path)
    if not rv.reviewed:
        return None
    cp = cached_cellpose(info.folder, info.path, study.settings.cellpose)
    out = [cp == cid for cid in rv.confirmed if cid < MANUAL_ID_BASE and cp is not None and (cp == cid).any()]
    shape = cp.shape if cp is not None else load_image(info.path).shape[:2]
    out += [polygon_mask(shape, poly) for poly in rv.manual]
    return out


def match_f1(pred: list[np.ndarray], truth: list[np.ndarray]) -> tuple[int, int, int]:
    """(true positives, predicted, truth): greedy one-to-one matching at IoU ≥ 0.5."""
    used, tp = set(), 0
    for t in truth:
        best, bi = 0.0, -1
        for k, p in enumerate(pred):
            if k in used:
                continue
            inter = np.logical_and(t, p).sum()
            if not inter:
                continue
            iou = inter / np.logical_or(t, p).sum()
            if iou > best:
                best, bi = iou, k
        if best >= IOU:
            used.add(bi); tp += 1
    return tp, len(pred), len(truth)


def objects(labels: np.ndarray, px: float, lo_d: float, hi_d: float) -> list[np.ndarray]:
    sizes = np.bincount(labels.ravel())
    disc = lambda d: np.pi * (d / 2) ** 2 / px ** 2
    keep = [i for i in range(1, len(sizes)) if disc(lo_d) <= sizes[i] <= disc(hi_d)]
    return [labels == i for i in keep]


def f1(tp: int, n_pred: int, n_truth: int) -> float:
    return 2 * tp / (n_pred + n_truth) if n_pred + n_truth else 0.0


def calibrate(study: Study) -> dict:
    by_obj: dict = {}
    for info in study.images():
        truth = truth_masks(study, info)
        if truth is not None and info.objective:
            by_obj.setdefault(info.objective, []).append((info, truth))
    results = {}
    for obj, items in by_obj.items():
        scores: dict = {}
        for info, truth in items:
            img = load_image(info.path); px = image_pixel_size(info.path)
            for fac in PX_FACTORS:
                lab = run_instanseg(img, replace(study.settings.cellpose, pixel_size=round(px * fac, 4)))
                for lo, hi in itertools.product(MIN_D, MAX_D):
                    t = scores.setdefault((fac, lo, hi), [0, 0, 0])
                    for k, v in enumerate(match_f1(objects(lab, px, lo, hi), truth)):
                        t[k] += v
        ranked = sorted(scores.items(), key=lambda kv: -f1(*kv[1]))
        results[obj] = {"images": len(items), "nuclei": sum(len(t) for _, t in items), "ranked": ranked}
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("study"); ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    study = next((s for s in list_studies() if s.name == a.study), None)
    if study is None:
        print(f"No study named “{a.study}”."); return 1
    res = calibrate(study)
    if not res:
        print("No reviewed image yet – confirm, discard and draw the nuclei, then press Enter on each image.")
        return 1
    for obj, r in res.items():
        (fac, lo, hi), (tp, n_p, n_t) = r["ranked"][0]
        base = next(v for k, v in r["ranked"] if k == (1.0, 5.0, 20.0))
        print(f"{obj}: {r['images']} images, {r['nuclei']} marked nuclei")
        print(f"  now (×1.0, Ø 5–20 µm): F1 {f1(*base):.2f}  ·  found {base[0]}/{base[2]}, {base[1] - base[0]} wrong")
        print(f"  best (×{fac}, Ø {lo:g}–{hi:g} µm): F1 {f1(tp, n_p, n_t):.2f}  ·  found {tp}/{n_t}, {n_p - tp} wrong")
        if a.write:
            study.settings.objectives.setdefault(obj, {}).update(min_d=lo, max_d=hi)
    if a.write:
        study.save(); print(f"Saved Ø ranges to {study.path.name}.")
    return 0


def _selftest() -> None:
    a = np.zeros((40, 40), bool); a[5:15, 5:15] = True
    b = np.zeros((40, 40), bool); b[6:16, 6:16] = True
    c = np.zeros((40, 40), bool); c[30:35, 30:35] = True
    assert match_f1([b, c], [a]) == (1, 2, 1) and round(f1(1, 2, 1), 2) == 0.67
    lab = np.zeros((40, 40), np.int32); lab[a] = 1; lab[c] = 2
    assert len(objects(lab, 1.0, 8.0, 20.0)) == 1          # the 5×5 speck is below Ø 8 px


if __name__ == "__main__":
    _selftest()
    sys.exit(main())
