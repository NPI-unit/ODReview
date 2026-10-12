"""Core maths without Cellpose and without a display.

    ./.venv/bin/python tests/test_core.py        (pytest works too)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nkreview import od as odm  # noqa: E402
from nkreview import segment  # noqa: E402
from nkreview.analysis import analyze  # noqa: E402
from nkreview.segment import (  # noqa: E402
    CellposeParams, polygon_mask, refine_hybrid, threshold_segment,
)
from nkreview.state import MANUAL_ID_BASE, Review, Settings  # noqa: E402

I0 = np.array([218.0, 217.0, 218.0])


def _dab_rgb(od_dab: float) -> np.ndarray:
    """RGB of a pure DAB pixel with the given DAB OD on background I0."""
    v = odm.stain_matrix(odm.STAIN_PRESETS["H DAB"])[odm.DAB]
    return I0 * 10 ** (-od_dab * v)


def _scene(h=300, w=400, cells=((150, 120, 30, 0.3),), noise=0.0, seed=0):
    """Background I0 with round pure-DAB cells (cy, cx, r, od)."""
    rng = np.random.default_rng(seed)
    img = np.tile(I0, (h, w, 1)).astype(np.float64)
    yy, xx = np.mgrid[:h, :w]
    for cy, cx, r, od in cells:
        img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = _dab_rgb(od)
    img += rng.normal(0, noise, img.shape)
    return np.clip(img, 0, 255).astype(np.float32)


def test_od_of_background_is_zero():
    img = _scene(cells=())
    assert abs(odm.od_rgb(img, I0).mean()) < 1e-6


def test_deconvolution_recovers_dab_od():
    img = _scene(cells=((150, 120, 30, 0.3),))
    st = odm.deconvolve(odm.od_rgb(img, I0), odm.STAIN_PRESETS["H DAB"])
    assert abs(st[150, 120, odm.DAB] - 0.3) < 0.01
    assert abs(st[150, 120, 0]) < 0.01              # nothing leaks into H


def test_background_ignores_cells_and_honours_roi():
    img = _scene(cells=((150, 200, 120, 0.8),))     # a large dark cell
    mask = threshold_segment(odm.smoothed_total_od(img, I0), 0.05) > 0
    bg = odm.estimate_background(img, mask)
    assert np.allclose(bg.i0, I0, atol=0.5) and bg.source == "auto"
    roi = odm.estimate_background(img, mask, roi=(0, 0, 20, 20))
    assert roi.source == "roi" and np.allclose(roi.i0, I0, atol=0.5)


def test_threshold_segment_drops_small_debris():
    img = _scene(cells=((150, 120, 30, 0.3), (40, 330, 6, 0.3)))
    lab = threshold_segment(odm.smoothed_total_od(img, I0), 0.05, min_area=500)
    assert lab.max() == 1 and lab[150, 120] == 1 and lab[40, 330] == 0


def test_hybrid_grows_cellpose_to_the_stained_edge():
    img = _scene(cells=((150, 120, 30, 0.3),))
    cp = np.zeros(img.shape[:2], np.int32)
    yy, xx = np.mgrid[:300, :400]
    cp[(yy - 150) ** 2 + (xx - 120) ** 2 <= 24 ** 2] = 7      # Cellpose cut the rim
    out = refine_hybrid(cp, odm.smoothed_total_od(img, I0), 0.05, grow=10)
    true_area = ((yy - 150) ** 2 + (xx - 120) ** 2 <= 30 ** 2).sum()
    assert set(np.unique(out)) == {0, 7}                       # id kept
    assert abs((out == 7).sum() - true_area) / true_area < 0.08


def test_hybrid_keeps_cellpose_for_a_pale_cell():
    img = _scene(cells=((150, 120, 30, 0.02),))                 # below threshold
    cp = np.zeros(img.shape[:2], np.int32)
    cp[130:170, 100:140] = 1
    out = refine_hybrid(cp, odm.smoothed_total_od(img, I0), 0.05)
    assert (out == 1).sum() == (cp == 1).sum()


def test_crop_padding_to_multiple_of_32(monkeypatch=None):
    seen = []

    def fake_eval(img, p):
        seen.append(img.shape[:2])
        return np.ones(img.shape[:2], np.int32)
    orig = segment._eval
    segment._eval = fake_eval
    try:
        out = segment._eval_padded(np.zeros((300, 357, 3), np.uint8), CellposeParams(), I0)
    finally:
        segment._eval = orig
    assert seen == [(320, 384)] and out.shape == (300, 357)


def test_dab_regions_find_stained_cells_only():
    img = _scene(h=600, w=800, cells=((150, 150, 30, 0.3), (450, 600, 30, 0.3)))
    # unstained grey debris (H-ish colour) must not become a region
    yy, xx = np.mgrid[:600, :800]
    h_vec = odm.stain_matrix(odm.STAIN_PRESETS["H DAB"])[0]
    img[(yy - 450) ** 2 + (xx - 150) ** 2 <= 30 ** 2] = I0 * 10 ** (-0.3 * h_vec)
    boxes, _ = segment.dab_regions(img, CellposeParams())
    assert len(boxes) == 2
    for sl in boxes:
        assert (sl[0].stop - sl[0].start) >= segment.MIN_CROP or sl[0].start == 0


def test_analyze_end_to_end_with_manual_and_discard():
    img = _scene(cells=((150, 120, 30, 0.3), (150, 300, 25, 0.15)))
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="threshold")
        rv = Review(discarded=[2], manual=[[[10, 10], [60, 10], [60, 60], [10, 60]]])
        res = analyze(folder, image, img, s, rv)
        rows = {r["zelle"]: r for r in res.rows}
        assert set(rows) == {1, 2, MANUAL_ID_BASE}
        assert abs(rows[1]["dab_od_mean"] - 0.3) < 0.02
        assert abs(rows[2]["dab_od_mean"] - 0.15) < 0.02 and rows[2]["verworfen"]
        assert rows[MANUAL_ID_BASE]["manuell"] and abs(rows[MANUAL_ID_BASE]["dab_od_mean"]) < 0.01
        assert rows[1]["dab_iod"] > rows[1]["dab_od_mean"] * rows[1]["flaeche_px"] * 0.99
        # no Cellpose cache yet → hybrid falls back to the threshold, and says so
        res2 = analyze(folder, image, img, Settings(), Review())
        assert res2.method == "threshold" and res2.note


def test_state_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "sub" / "b.tif"
        image.parent.mkdir(); image.touch()
        s = Settings(threshold=0.07); s.cellpose.roi_pad = 64; s.save(folder)
        s2 = Settings.load(folder)
        assert s2.threshold == 0.07 and s2.cellpose.roi_pad == 64
        Review(discarded=[3], roi=[1, 2, 30, 40], reviewed=True).save(folder, image)
        r = Review.load(folder, image)
        assert r.discarded == [3] and r.roi == [1, 2, 30, 40] and r.reviewed


def test_polygon_mask():
    m = polygon_mask((50, 50), [[10, 10], [40, 10], [40, 40], [10, 40]])
    assert 800 < m.sum() < 1000


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                fails += 1; print(f"FAIL  {name}: {type(e).__name__}: {e}")
    sys.exit(1 if fails else 0)
