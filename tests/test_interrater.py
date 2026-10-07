"""Inter-rater agreement (mockup v6), logic and Qt, off-screen.

Statistics against published values (ICC(2,1): Shrout & Fleiss 1979, table 2;
Cohen's κ by hand). Then a real round trip: I make a package, my reviews
change afterwards (so 'Anna' = the package differs from me), one image is
re-segmented locally so its masks differ — that image must be left out
(identical masks only). The package is stored as a second rater without
touching my reviews; the window shows κ, ICC, the heatmap and both plots."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS = 0
OUT = Path(__file__).resolve().parent / "_out"


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    from erysreview import interrater as ir
    i, lo, hi = ir.icc_2_1_matrix([[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8], [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]])
    check(round(i, 2) == 0.29 and round(lo, 2) == 0.02 and round(hi, 2) == 0.76,
          f"ICC(2,1) = 0.29 [0.02, 0.76] as in Shrout & Fleiss ({i:.3f} [{lo:.3f}, {hi:.3f}])")
    k, _, khi = ir.cohen_kappa(list("aaaabbbbbb"), list("aaabbbbbba"))
    check(abs(k - 0.5833) < 1e-3 and khi <= 1.0, f"Cohen's κ by hand: 0.583 ({k:.3f}), CI capped at 1")
    check(ir.icc_2_1([5, 10, 15, 20], [5, 10, 15, 20])[0] == 1.0, "identical ratings: ICC = 1")
    md, l2, h2 = ir.bland_altman([1, 2, 3, 4], [2, 3, 4, 5])
    check(md == 1.0 and l2 == h2 == 1.0, "Bland-Altman: constant offset → mean 1, no spread")

    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    support = root / "support"; support.mkdir()
    from erysreview import persistence, profile as profile_mod
    persistence.app_support_dir = lambda: support
    profile_mod.app_support_dir = lambda: support
    from erysreview import package as pkg
    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    prof = ProfileData(name="IR")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "ignore", "image_num"]
    prof.import_folder = str(folder)
    prof.size_filter.enabled = False
    rng = np.random.default_rng(3)
    names = []
    for sub in ("01", "02", "03"):
        for tp in ("Prä", "Post"):
            for kimg in (1, 2):
                name = f"ErysReview_{sub}_{tp}_06.10.26_0{kimg}.tif"
                names.append(name)
                tifffile.imwrite(folder / name, np.full((120, 120), 140, dtype=np.uint8))
                m = np.zeros((120, 120), dtype=np.int32)
                for c in range(16):
                    y, x = 10 + 25 * (c // 4), 10 + 25 * (c % 4)
                    m[y:y + 15, x:x + 15] = c + 1
                npy, meta = cache_paths(folder, folder / name)
                npy.parent.mkdir(parents=True, exist_ok=True)
                np.save(npy, m); meta.write_text(json.dumps(asdict(prof.cellpose)))
                rv = ReviewState(is_finished=True)
                rv.types = {str(c): "stechapfel" for c in rng.choice(np.arange(1, 17), 4, replace=False)}
                rv.save(folder, Path(name).stem)
    save_profile(prof)
    AppSettings(last_profile_name="IR", language="de", theme="light", reviewer_name="Finn").save()

    from erysreview.grouping import group_images
    from erysreview.cellpose_runner import list_images
    grouped = group_images(list_images(folder), prof)
    zip_path = root / "Anna.eryspkg.zip"
    pkg.create_package(folder, grouped, prof, zip_path, include_images=False, reviewer="Anna")

    # After the package left: my reviews change on two images, one image is re-segmented.
    s0 = Path(names[0]).stem
    rv = ReviewState.load(folder, s0); rv.types = {"1": "stechapfel", "2": "stechapfel"}; rv.force_excluded = [3]
    rv.save(folder, s0)
    s_resegmented = Path(names[5]).stem
    npy, _ = cache_paths(folder, folder / names[5])
    m = np.load(npy); m[m == 16] = 0; np.save(npy, m)
    mine_before = ReviewState.review_path(folder, s0).read_text()

    status = ir.check_package(zip_path, folder)
    check(status[s_resegmented] == "different" and sum(v == "same" for v in status.values()) == 11,
          "the re-segmented image is recognised as 'other masks'")
    res = ir.import_rater(zip_path, folder)
    check(res["name"] == "Anna" and len(res["imported"]) == 11 and s_resegmented in res["rejected"],
          "only images with identical masks are taken over")
    check(ReviewState.review_path(folder, s0).read_text() == mine_before, "my reviews are untouched")
    check(ir.list_raters(folder) == ["Anna"], "Anna is listed as second rater")

    ag = ir.compare(folder, grouped, prof, "Anna", "Finn")
    check(ag.n_images == 11 and ag.n_cells == 11 * 16, f"11 images × 16 cells compared ({ag.n_cells})")
    check(0 < ag.n_cells - ag.n_same <= 6 and ag.kappa[0] < 1, f"the changed image shows up as disagreement "
          f"({ag.n_cells - ag.n_same} cells, κ = {ag.kappa[0]:.3f})")
    check(len(ag.groups) == 6 and ir.EXCLUDED in ag.categories, "one pair per proband × timepoint, excluded as category")

    # selection for the statistics: common images, unticked ones, the metric
    common = ir.common_stems(folder, "Anna")
    check(len(common) == 11 and s_resegmented not in common, f"common images = rated by A and B ({len(common)})")
    check(ir.selection(folder, "Anna") == {"excluded": set(), "metric": "pct_changed_2000", "diffs": "all"},
          "default: every image, % verändert pro 2000, all differences")
    X = ir.EXCLUDED
    check(all(ir.is_shown_diff(a, b, "all") for a, b in (("normal", X), ("normal", "stechapfel"))),
          "'Alle': every difference")
    check(ir.is_shown_diff("normal", "stechapfel", "changed") and ir.is_shown_diff("stechapfel", X, "changed")
          and ir.is_shown_diff("elongiert", "stechapfel", "changed")
          and not ir.is_shown_diff("normal", X, "changed") and not ir.is_shown_diff(X, "normal", "changed"),
          "'Nur veränderte': a type other than normal on either side; normal ↔ excluded is skipped")
    ir.set_diff_mode(folder, "Anna", "changed")
    check(ir.selection(folder, "Anna")["diffs"] == "changed", "the difference mode is stored per B")
    ir.set_diff_mode(folder, "Anna", "all")
    ir.set_excluded(folder, "Anna", [s0], True)
    sel = ir.selection(folder, "Anna")
    ag_x = ir.compare(folder, grouped, prof, "Anna", "Finn", excluded=sel["excluded"], metric=sel["metric"])
    check(ag_x.n_images == 10 and ag_x.n_left_out == 1 and ag_x.n_cells == 10 * 16,
          "an unticked image leaves κ, matrix and ICC")
    check(ag_x.n_same == ag_x.n_cells, "…here exactly the one that differed")
    g = next(g for g in ag.groups if g.pct_a != g.pct_b)
    check(abs(g.val_a - g.pct_a * prof.target_cells / g.active_a) < 1e-9,
          "default metric = % verändert × target / counted")
    ir.set_metric(folder, "Anna", "rbc")
    ag_r = ir.compare(folder, grouped, prof, "Anna", "Finn", metric=ir.selection(folder, "Anna")["metric"])
    check(all(gr.val_a == gr.active_a and gr.val_b == gr.active_b for gr in ag_r.groups) and ag_r.metric == "rbc",
          "metric 'RBC gesamt' compares the counted cells")
    check(ir.image_differs(folder, next(i for d in grouped.tree.values() for l in d.values() for i in l if i.path.stem == s0), prof, "Anna"),
          "image_differs finds the image A and B rate differently")
    ir.set_excluded(folder, "Anna", [s0], False); ir.set_metric(folder, "Anna", ir.DEFAULT_METRIC)

    # broken package: every mask different → refused
    saved = {}
    for nm in names:
        npy, _ = cache_paths(folder, folder / nm)
        saved[npy] = npy.read_bytes()
        m = np.load(npy); m[0, 0] = 99; np.save(npy, m)
    try:
        ir.import_rater(zip_path, folder, name="Bob")
        refused = False
    except ValueError:
        refused = True
    check(refused and ir.list_raters(folder) == ["Anna"], "a package with other masks everywhere is refused")
    ag2 = ir.compare(folder, grouped, prof, "Anna", "Finn")
    check(ag2.n_images == 0 and ag2.n_masks_changed == 11,
          "masks changed after Anna's rating: those images drop out of the comparison")
    for npy, data in saved.items():
        npy.write_bytes(data)

    # ── Qt window ──
    from PySide6.QtTest import QTest
    from erysreview.qt.app import make_app
    from erysreview.qt.interrater import InterraterWindow
    from erysreview.theme import palette
    from erysreview.i18n import get_strings
    app = make_app(["test"])
    w = InterraterWindow(None, palette("light"), get_strings("de"), folder, grouped, prof, me="Finn")
    w.show(); QTest.qWait(80)
    check(w.rater_combo.currentText() == "Anna" and w.tiles.count() == 4 and w.table.rowCount() == len(ag.categories),
          "the window shows four key figures and the heatmap")
    diag = w.table.item(0, 0).background().color()
    off = [w.table.item(r, c) for r in range(w.table.rowCount()) for c in range(w.table.columnCount())
           if r != c and w.table.item(r, c).text() != "0"]
    check(diag.blue() > diag.red() and off and off[0].background().color().red() > off[0].background().color().blue(),
          "agreement is blue, disagreement red")
    axes = w.fig.axes
    check(len(axes) == 2 and axes[0].get_xlabel() and any(l.get_linestyle() == "--" for l in axes[0].lines)
          and len(axes[1].lines) >= 3, "A-vs-B with 45° line, Bland-Altman with mean and ±1.96 SD")
    OUT.mkdir(exist_ok=True)
    w.grab().save(str(OUT / "qt_interrater.png"))
    csv_path = root / "ir.csv"
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    w._export(str(csv_path))
    text = csv_path.read_text(encoding="utf-8-sig")
    check("cohen_kappa" in text and "icc_2_1_pct_changed_2000" in text and csv_path.with_suffix(".png").is_file(),
          "export writes cells, groups, summary and the figure")
    w.close(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: second ratings only with identical masks; κ, ICC, heatmap and plots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
