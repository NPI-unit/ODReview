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
        rows = {r["cell"]: r for r in res.rows}
        assert set(rows) == {1, 2, MANUAL_ID_BASE}
        assert abs(rows[1]["dab_od_mean"] - 0.3) < 0.02
        assert abs(rows[2]["dab_od_mean"] - 0.15) < 0.02 and rows[2]["discarded"]
        assert rows[MANUAL_ID_BASE]["manual"] and abs(rows[MANUAL_ID_BASE]["dab_od_mean"]) < 0.01
        assert rows[1]["dab_iod"] > rows[1]["dab_od_mean"] * rows[1]["area_px"] * 0.99
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


# ── Fiji-conform measurement ─────────────────────────────
def _gray_scene(bg=216, cell_gray=150, h=300, w=400, cy=150, cx=200, r=40):
    img = np.full((h, w, 3), bg, np.float32)
    yy, xx = np.mgrid[:h, :w]
    img[(yy - cy) ** 2 + (xx - cx) ** 2 <= r * r] = cell_gray
    return img


def test_fiji_od_is_od_of_the_mean_with_field_i0():
    img = _gray_scene()
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="threshold")
        res = analyze(folder, image, img, s, Review())
        [row] = res.rows
        assert row["status"] == "kept" and row["i0"] == 216.0
        mean_i = res.gray[res.labels == row["cell"]].mean()          # Ī over the contour
        assert abs(row["od"] - (-np.log10(mean_i / 216))) < 1e-4
        cm = res.cells[row["cell"]]
        assert len(cm.fields) == 3
        dist = ndi_distance_to(res.labels > 0)
        for x, y in cm.fields:                       # in the ring, off the cell
            assert s.i0_ring_inner <= dist[int(y), int(x)] <= s.i0_ring_outer + 1
        assert len(row["i0_field1"].__str__()) and row["profile_r1"] is not None


def ndi_distance_to(mask):
    from scipy import ndimage as ndi
    return ndi.distance_transform_edt(~mask)


def test_i0_out_of_range_warns_and_keeps_the_cell():
    img = _gray_scene(bg=205, cell_gray=140)
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        res = analyze(folder, image, img, Settings(method="threshold"), Review())
        [row] = res.rows
        # a failed I₀ check warns; the cell stays and counts once confirmed
        assert row["status"] == "kept" and row["i0_warning"] and not row["i0_ok"] and row["od"] is not None


def test_i0_fields_reproducible_redraw_and_manual():
    img = _gray_scene()
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="threshold")
        a = analyze(folder, image, img, s, Review()).cells[1].fields
        b = analyze(folder, image, img, s, Review()).cells[1].fields
        c = analyze(folder, image, img, s, Review(i0_seed={"1": 1})).cells[1].fields
        assert a == b and a != c
        img2 = img.copy(); img2[10:30, 10:30] = 212           # a darker background spot
        res = analyze(folder, image, img2, s, Review(i0_fields={"1": [[20, 20]]}))
        assert res.rows[0]["i0"] == 212.0


def test_weighted_gray():
    from nkreview.measure import gray_image
    img = np.zeros((1, 1, 3), np.float32); img[0, 0] = (100, 200, 50)
    assert gray_image(img, "mean")[0, 0] == round(350 / 3)
    assert gray_image(img, "weighted")[0, 0] == round(100 * 0.299 + 200 * 0.587 + 50 * 0.114)


# ── study ────────────────────────────────────────────────
def test_name_roles():
    from nkreview.study import detect_delimiters, guess_roles, parse_name
    names = ["TEST A NK 5hmc 10.02.2026_002.tif", "TEST B NK 5mc 10.02.2026_001.tif"]
    d = detect_delimiters(names)
    roles = guess_roles(names, d)
    assert roles == ["ignorieren", "proband", "zelltyp", "marker", "datum", "bildnr"]
    m = parse_name(names[1], d, roles)
    assert (m["proband"], m["zelltyp"], m["marker"], m["bildnr"]) == ("B", "NK", "5mc", "001")
    t = guess_roles(["TRIBAL_195_Prä_19.11.25_021.tif", "TRIBAL_196_Post_19.11.25_003.tif"], ["_"])
    assert t == ["ignorieren", "proband", "zeitpunkt", "datum", "bildnr"]


def test_dates_stay_one_part():
    from nkreview.study import detect_delimiters, guess_roles, iso_date, parse_name, tokenize
    n = "2026-08-27-C2C12-Ki67-FE01R3-20x_001.tif"
    assert tokenize(n, ["_", "-"]) == ["2026-08-27", "C2C12", "Ki67", "FE01R3", "20x", "001"]
    names = [n, "2026-08-28-C2C12-Ki67-FE02R3-20x_013.tif"]
    d = detect_delimiters(names)
    roles = guess_roles(names, d)
    assert roles == ["datum", "zelltyp", "marker", "proband", "ignorieren", "bildnr"]
    assert parse_name("10.02.26_x.tif", ["_"], ["datum", "proband"])["datum"] == "2026-02-10"
    assert iso_date("31.02.2026") == "" and iso_date("2026-8-7") == "2026-08-07"


def test_study_images_groups_and_summary():
    from nkreview.export import export_study, summarize
    from nkreview.study import Study
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        import tifffile
        for name, g in (("X A NK 5hmc_001.tif", 150), ("X A NK 5hmc_002.tif", 160), ("X B NK 5hmc_001.tif", 170)):
            tifffile.imwrite(folder / name, _gray_scene(cell_gray=g).astype(np.uint8))
        st = Study(name="t", folders=[str(folder)], delimiters=[" ", "_"],
                   roles=["ignorieren", "proband", "zelltyp", "marker", "bildnr"], groups={"A": "Intervention"})
        st.settings.method = "threshold"
        imgs = st.images()
        assert {i.meta["gruppe"] for i in imgs if i.proband == "A"} == {"Intervention"}
        for i in imgs:      # subject B's cell stays unconfirmed: in cells.csv, not in summary.csv
            Review(reviewed=True, confirmed=[1] if i.proband == "A" else []).save(i.folder, i.path)
        info = export_study(st, images=imgs)
        assert info["n_cells"] == 3 and info["n_confirmed"] == 2 and (info["folder"] / "cells.csv").exists()
        import pandas as pd
        summ = pd.read_csv(info["folder"] / "summary.csv")
        cells = pd.read_csv(info["folder"] / "cells.csv")
        a = summ[summ.subject == "A"].iloc[0]
        expect = cells[cells.subject == "A"].od.mean()
        assert a.n_cells == 2 and abs(a.od_mean - expect) < 1e-3 and a.group == "Intervention"
        assert list(summ.subject) == ["A"] and sorted(cells.confirmed) == [False, True, True]


def test_relative_i0_check_follows_each_image():
    s = Settings(method="threshold", i0_check="relativ", i0_rel_pct=3.0)
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        for bg in (190, 228):                          # dark and bright series both pass
            [row] = analyze(folder, image, _gray_scene(bg=bg, cell_gray=bg - 50), s, Review()).rows
            assert row["status"] == "kept" and abs(row["i0_dev_pct"]) < 0.5, row
        img = _gray_scene(bg=216, cell_gray=150)
        img[10:30, 10:30] = 200                             # a field on "cytoplasm": -7 %
        [row] = analyze(folder, image, img, s, Review(i0_fields={"1": [[20, 20]]})).rows
        assert row["status"] == "kept" and row["i0_warning"] and row["i0_dev_pct"] < -3


def test_min_area_and_positive_call():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        img = _gray_scene(bg=216, cell_gray=150)
        cp = np.zeros(img.shape[:2], np.int32)
        yy, xx = np.mgrid[:300, :400]
        cp[(yy - 150) ** 2 + (xx - 200) ** 2 <= 40 ** 2] = 1
        cp[20:28, 20:28] = 2                               # 64 px speck
        s = Settings(method="cellpose", min_cell_area=500, pos_threshold=0.1)
        rows = analyze(folder, image, img, s, Review(), cp=cp).rows
        assert [r["cell"] for r in rows] == [1] and rows[0]["positive"] is True
        s.pos_threshold = 0.5
        assert analyze(folder, image, img, s, Review(), cp=cp).rows[0]["positive"] is False
        s.pos_threshold = 0
        assert analyze(folder, image, img, s, Review(), cp=cp).rows[0]["positive"] is None


def test_max_area_drops_whole_cells():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        img = _gray_scene(bg=216, cell_gray=150)
        cp = np.zeros(img.shape[:2], np.int32)
        cp[100:130, 100:130] = 1                           # 900 px nucleus
        cp[150:250, 200:300] = 2                           # 10000 px whole cell
        s = Settings(method="cellpose", min_cell_area=200, max_cell_area=4000)
        assert [r["cell"] for r in analyze(folder, image, img, s, Review(), cp=cp).rows] == [1]


def test_nucleus_target_uses_instanseg_with_magnification_preset():
    from nkreview.study import MAG_LOW, Study, TARGET_CELL, TARGET_NUCLEUS
    st = Study(name="x", target=TARGET_NUCLEUS)
    st.apply_target(MAG_LOW)
    s = st.settings
    assert s.cellpose.model == segment.MODEL_INSTANSEG and s.method == segment.METHOD_CELLPOSE
    assert (s.cellpose.pixel_size, s.min_cell_area, s.max_cell_area) == (0.45, 100, 1500)
    assert (s.i0_check, s.i0_rel_pct) == ("lokal", 3.0)
    st.target = TARGET_CELL
    st.apply_target()
    assert s.cellpose.model == "cpsam" and s.cellpose.pixel_size == 0 and s.max_cell_area == 0
    assert s.i0_check == "absolut"


def test_run_cellpose_hands_nuclei_to_instanseg():
    calls = []

    class Fake:
        small_image_threshold = 10 ** 9

        def eval_small_image(self, img, pixel_size, target, return_image_tensor):
            import torch
            calls.append((img.shape, pixel_size, target))
            return torch.ones((1, 1) + img.shape[:2], dtype=torch.int32)

    orig = segment._get_instanseg
    segment._get_instanseg = lambda: Fake()
    try:
        out = segment.run_cellpose(np.zeros((40, 60, 3), np.uint8),
                                   CellposeParams(model=segment.MODEL_INSTANSEG, pixel_size=0.25))
    finally:
        segment._get_instanseg = orig
    assert calls == [((40, 60, 3), 0.25, "nuclei")] and out.shape == (40, 60) and out.dtype == np.int32


def test_cache_from_before_a_new_param_stays_valid():
    import json
    from dataclasses import asdict
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        npz, meta = segment._cp_paths(folder, image)
        np.savez_compressed(npz, masks=np.ones((4, 4), np.uint16))
        old = {k: v for k, v in asdict(CellposeParams()).items() if k != "pixel_size"}
        meta.write_text(json.dumps(old), "utf-8")
        assert segment.has_cellpose_cache(folder, image, CellposeParams())
        assert segment.cached_cellpose(folder, image, CellposeParams()) is not None
        assert not segment.has_cellpose_cache(folder, image, CellposeParams(pixel_size=0.25))


def test_closed_dialog_is_destroyed_on_the_gui_thread():
    """A dialog kept alive only by a reference cycle must not be collected by
    the segmentation thread (Qt widgets deleted off the GUI thread crash).
    Without gc_on_gui_thread this collects it in the worker."""
    import gc
    import os
    import threading
    import weakref
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread, QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
    from nkreview.qt.app import gc_on_gui_thread

    class Dialog(QDialog):
        def __init__(self):
            super().__init__()
            self.b = QPushButton(self)
            self.me = self                                # a cycle only the GC can free

    class Busy(QThread):
        def run(self):
            junk = [{"i": i} for i in range(2_000_000)]   # enough to trigger automatic GC
            del junk

    app = QApplication.instance() or QApplication(["test"])
    gc_on_gui_thread(app)
    try:
        main, where = threading.get_ident(), []
        dlg = Dialog()
        ref = weakref.ref(dlg, lambda _: where.append(threading.get_ident() == main))
        del dlg
        t = Busy(); t.start(); t.wait()
        assert where == [] and ref() is not None
        gc.collect()
        assert where == [True]
    finally:
        gc.enable()
        for timer in app.findChildren(QTimer):
            timer.stop()


def test_i0_per_image_one_set_of_fields_for_all_cells():
    from nkreview.analysis import IMAGE_KEY
    from nkreview.state import I0_PER_IMAGE
    img = _scene(h=400, w=600, cells=((100, 120, 25, 0.3), (300, 450, 25, 0.2)))
    cp = np.zeros(img.shape[:2], np.int32)
    yy, xx = np.mgrid[:400, :600]
    cp[(yy - 100) ** 2 + (xx - 120) ** 2 <= 25 ** 2] = 1
    cp[(yy - 300) ** 2 + (xx - 450) ** 2 <= 25 ** 2] = 2
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="cellpose", i0_scope=I0_PER_IMAGE, i0_min=0, i0_max=255)
        res = analyze(folder, image, img, s, Review(), cp=cp)
        assert len(res.image_fields) == 3
        assert res.cells[1].fields == res.cells[2].fields and res.cells[1].i0 == res.cells[2].i0
        assert analyze(folder, image, img, s, Review(), cp=cp).image_fields == res.image_fields   # seeded
        again = analyze(folder, image, img, s, Review(i0_seed={IMAGE_KEY: 1}), cp=cp).image_fields
        assert again != res.image_fields                                                        # R redraws
        hand = [[300.0, 50.0], [500.0, 60.0], [550.0, 150.0]]
        res = analyze(folder, image, img, s, Review(i0_fields={IMAGE_KEY: hand}), cp=cp)
        assert res.cells[1].fields == hand and res.cells[2].fields == hand
        assert Settings(method="cellpose").i0_scope == "zelle"                             # default unchanged


def test_local_i0_check_needs_agreeing_fields_not_a_fixed_range():
    from nkreview.analysis import field_spread
    from nkreview.state import I0_LOCAL
    assert field_spread([200.0]) is None
    assert round(field_spread([200.0, 210.0, 220.0]), 2) == 4.76      # 220 vs mean 210
    img = _gray_scene(bg=240, cell_gray=150)                         # bright, outside 210–220
    cp = np.zeros(img.shape[:2], np.int32)
    yy, xx = np.mgrid[:300, :400]
    cp[(yy - 150) ** 2 + (xx - 200) ** 2 <= 40 ** 2] = 1
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="cellpose", i0_check=I0_LOCAL, i0_rel_pct=3.0)
        [r] = analyze(folder, image, img, s, Review(), cp=cp).rows
        assert r["status"] == "kept" and not r["i0_warning"] and r["i0_field_spread_pct"] < 3
        fields = analyze(folder, image, img, s, Review(), cp=cp).cells[1].fields
        on_dark = [[200.0, 150.0]] + fields[1:]                          # field 1 moved onto a dark spot
        [r] = analyze(folder, image, img, s, Review(i0_fields={"1": on_dark}), cp=cp).rows
        assert r["i0_warning"] and "disagree" in r["reason"]
        s.i0_check = "absolut"
        assert analyze(folder, image, img, s, Review(), cp=cp).rows[0]["i0_warning"]  # 240 > 220


def test_confirmed_only_for_kept_cells_and_notes_marked():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        img = _gray_scene(bg=216, cell_gray=150)
        cp = np.zeros(img.shape[:2], np.int32)
        cp[100:140, 100:140] = 1; cp[200:240, 300:340] = 2
        s = Settings(method="cellpose", i0_min=0, i0_max=255)
        rv = Review(confirmed=[1, 2], discarded=[2], notes={"1": "check field 2"})
        rows = {r["cell"]: r for r in analyze(folder, image, img, s, rv, cp=cp).rows}
        assert rows[1]["confirmed"] and rows[1]["has_note"]
        assert not rows[2]["confirmed"] and not rows[2]["has_note"]        # discarded is never confirmed
        assert Review(**{**Review().__dict__, "note": "x"}).note == "x"


def test_comment_circles_hit_test_and_roundtrip():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from nkreview.qt.view import ImageView
    QApplication.instance() or QApplication(["test"])
    marks = [{"x": 50, "y": 50, "r": 40, "text": "big"}, {"x": 60, "y": 50, "r": 10, "text": "small"}]
    v = ImageView(); v.set_pixmap(np.zeros((200, 200, 3), np.uint8)); v.set_marks(marks)
    assert v.mark_at(60, 52) == 1 and v.mark_at(20, 50) == 0 and v.mark_at(150, 150) == -1
    assert len(v._mark_items) == 6                      # rim, line, badge per circle
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"
        Review(marks=marks).save(folder, image)
        assert Review.load(folder, image).marks == marks


def test_view_fills_kept_cells_only_and_numbers_the_fields():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from nkreview.qt.view import ImageView
    QApplication.instance() or QApplication(["test"])
    labels = np.zeros((60, 60), np.int32); labels[5:20, 5:20] = 1; labels[30:50, 30:50] = 2
    rows = [{"cell": 1, "status": "kept", "confirmed": True}, {"cell": 2, "status": "excluded"}]
    v = ImageView(); v.set_pixmap(np.zeros((60, 60, 3), np.uint8))
    v.set_cells(labels, rows)
    kept, excl = v._cell_items[1][1], v._cell_items[2][1]
    assert kept.brush().style() != Qt.NoBrush and excl.brush().style() == Qt.NoBrush
    v.set_fill(False, 0.35)
    assert kept.brush().style() == Qt.NoBrush
    v.set_cells(labels, [{"cell": 1, "status": "kept"}, {"cell": 2, "status": "excluded", "has_note": True}])
    halo, line = v._cell_items[1][:2]
    assert line.pen().color().name() == "#ffffff" and halo.isVisible()       # open: white with a dark rim
    assert v._cell_items[2][1].pen().style() == Qt.SolidLine and v._cell_items[2][1].pen().widthF() == 3.0
    assert len(v._cell_items[2]) == 3                                          # note dot
    v.set_fields([[10, 40], [50, 10], [40, 5]], 9)
    assert len(v._field_items) == 6                      # a box and its number per field
    assert [it.text() for it in v._field_items if hasattr(it, "text")] == ["1", "2", "3"]


def test_template_roundtrip_keeps_scheme_not_folders():
    from nkreview import study as stm
    from nkreview.study import Study, Template, TARGET_NUCLEUS
    with tempfile.TemporaryDirectory() as d:
        orig = stm.templates_dir
        stm.templates_dir = lambda: Path(d)
        try:
            st = Study(name="Pilot", folders=["/x"], roles=["datum", "proband"], delimiters=["-"],
                       groups={"A": "K"}, target=TARGET_NUCLEUS)
            st.settings.i0_check, st.settings.i0_rel_pct, st.settings.min_cell_area = "relativ", 3.0, 500
            Template.from_study(st, "C2C12 Kern 20×", "Test").save()
            [t] = stm.list_templates()
            new = Study(name="Serie 2", folders=["/y"])
            t.apply_to(new)
            assert (new.name, new.folders, new.groups) == ("Serie 2", ["/y"], {})
            assert new.target == TARGET_NUCLEUS and new.roles == ["datum", "proband"]
            assert new.settings.i0_check == "relativ" and new.settings.min_cell_area == 500
        finally:
            stm.templates_dir = orig


def test_display_adjust_is_view_only():
    from nkreview import display_adjust as da
    x = np.linspace(0, 1, 11, dtype=np.float32)
    assert np.allclose(da.apply(x, None), x) and da.clean(da.DEFAULT) is None
    y = da.apply(x, {"brightness": 0, "contrast": 100, "gamma": 0.5})
    assert y[5] > x[5]                                   # gamma < 1 brightens mid-tones
    assert da.clean({"brightness": 500, "contrast": 1, "gamma": 9}) == {"brightness": 100, "contrast": 50, "gamma": 2.5}
    img = _gray_scene()
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d); image = folder / "a.tif"; image.touch()
        s = Settings(method="threshold")
        a = analyze(folder, image, img, s, Review()).rows
        b = analyze(folder, image, img, s, Review(display={"brightness": -40, "contrast": 300, "gamma": 0.6})).rows
        assert a == b                                    # measurement never sees the display


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


def test_pixel_size_follows_the_objective_in_the_xmp():
    import tifffile
    from nkreview.segment import MODEL_INSTANSEG, CellposeParams, params_for_image
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.tif"
        tifffile.imwrite(f, np.zeros((8, 8, 3), np.uint8),
                         extratags=[(700, 1, None, b"<Custom:UmPerPixX>0.590619</Custom:UmPerPixX>", True)])
        p = CellposeParams(model=MODEL_INSTANSEG, pixel_size=0.293)
        assert params_for_image(f, p).pixel_size == 0.5906
        assert params_for_image(f, CellposeParams(pixel_size=0.293)).pixel_size == 0.293   # Cellpose: unchanged
        tifffile.imwrite(f, np.zeros((8, 8, 3), np.uint8))
        assert params_for_image(f, p).pixel_size == 0.293                                  # no XMP: study value


def test_i0_field_is_dragged_with_the_left_button_and_esc_puts_it_back():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from nkreview.qt.view import TOOL_I0, ImageView
    QApplication.instance() or QApplication(["test"])
    v = ImageView(); v.resize(400, 400); v.set_pixmap(np.zeros((200, 200, 3), np.uint8))
    v.set_fields([[50, 50], [150, 150]], 9); v.set_tool(TOOL_I0); v.resetTransform(); v.centerOn(100, 100)
    moved, clicked = [], []
    v.i0Moved.connect(lambda k, x, y: moved.append((k, round(x), round(y))))
    v.i0Clicked.connect(lambda x, y: clicked.append((round(x), round(y))))
    at = lambda x, y: v.mapFromScene(x, y)
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, at(150, 150))
    QTest.mouseMove(v.viewport(), at(120, 140))
    QTest.mouseRelease(v.viewport(), Qt.LeftButton, Qt.NoModifier, at(120, 140))
    assert moved == [(1, 120, 140)] and not clicked          # field 2 dragged, nothing added
    QTest.mouseClick(v.viewport(), Qt.LeftButton, Qt.NoModifier, at(100, 20))
    assert clicked == [(100, 20)]                            # next to the fields: a click, no jump
    QTest.mousePress(v.viewport(), Qt.LeftButton, Qt.NoModifier, at(50, 50))
    QTest.mouseMove(v.viewport(), at(80, 80))
    assert v.cancel_field_drag() and v._field_items[0].pos().x() == 0   # Esc: back in place
    assert len(moved) == 1


def test_objective_tile_sets_area_in_um_and_counted_default():
    import tifffile
    from nkreview.analysis import area_limits
    from nkreview.segment import MODEL_INSTANSEG
    from nkreview.study import ImageInfo, is_counted
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "x.tif"
        tifffile.imwrite(f, np.zeros((8, 8, 3), np.uint8), extratags=[(700, 1, None,
                         b"<aux:Lens>10x</aux:Lens><Custom:UmPerPixX>0.5</Custom:UmPerPixX>", True)])
        s = Settings(); s.cellpose.model = MODEL_INSTANSEG
        s.objectives = {"10×": {"count": False, "segment": True, "min_d": 5.0, "max_d": 10.0}}
        lo, hi = area_limits(s, f)
        assert round(lo) == round(np.pi * 25 / 4 / 0.25) and round(hi) == round(np.pi * 100 / 4 / 0.25)
        info = ImageInfo(f, Path(d), {})
        assert info.objective == "10×" and not is_counted(info, Review(), s)   # tile default: off
        assert is_counted(info, Review(counted=True), s)                      # E overrides it


def test_segmentation_window_start_button_queues_the_selection():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from nkreview.qt.batch import BatchDialog
    from nkreview.study import Study
    QApplication.instance() or QApplication(["test"])

    class Worker:                                    # stands in for CellposeWorker
        def __init__(self): self.queued = None
        def set_queue(self, items, params, force=False): self.queued = items
        started = finished = failed = type("S", (), {"connect": lambda *a: None})()
    with tempfile.TemporaryDirectory() as d:
        import tifffile
        tifffile.imwrite(Path(d) / "a_1.tif", np.zeros((8, 8, 3), np.uint8))
        st = Study(name="t", folders=[d], delimiters=["_"], roles=["proband", "bildnr"])
        w = Worker(); b = BatchDialog(None, st, st.images(), w)
        b._select("all"); b.start_btn.click()          # the real click, not _start()
        assert w.queued and w.queued[0][1].name == "a_1.tif" and b.pages.currentIndex() == 1
        b._on_finished(str(st.images()[0].path))
        assert b.close_btn.text() == "Close"
        b.close_btn.click()                              # closes – a counter once shadowed QDialog.done
        assert b.result() == 1 and not b.isVisible()


def test_unexpected_error_is_logged_and_shown_once(monkeypatch=None):
    import io
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    import nkreview.qt.app as appmod
    app = QApplication.instance() or QApplication(["test"])
    boxes, old_hook, old_err, old_box = [], sys.excepthook, sys.stderr, appmod.QMessageBox.warning
    appmod.QMessageBox.warning = lambda *a: boxes.append(a[2])
    sys.stderr = buf = io.StringIO()
    try:
        appmod.show_errors(app)
        for _ in range(3):                                 # the same error three times …
            try:
                raise TypeError("'bool' object is not iterable")
            except TypeError:
                sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook, sys.stderr, appmod.QMessageBox.warning = old_hook, old_err, old_box
    assert len(boxes) == 1 and "TypeError" in boxes[0]     # … one box
    assert buf.getvalue().count("Traceback") == 3          # … three tracebacks in the log


def test_default_studies_are_seeded_once():
    import tempfile
    from nkreview.study import Study, _seed_default_studies
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        _seed_default_studies(d)
        names = {p.name for p in d.glob("*.json")}
        assert {"Pilot_C2C12_ICC.json", "Pilot_C2C12_calibration.json"} <= names
        assert Study.load(d / "Pilot_C2C12_ICC.json").settings.objectives["20×"]["count"] is True
        (d / "Pilot_C2C12_ICC.json").unlink()               # deleted by the user …
        _seed_default_studies(d)
        assert not (d / "Pilot_C2C12_ICC.json").exists()   # … stays deleted
