"""Mockup v4: the hint for masks made with old values, end to end (off-screen).

Study wants diameter 34; one image has masks made with 30, one has masks made
with 34, one is raw. Checks every place the state shows, then re-runs the
stale image after the confirmation and checks its review was reset."""

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
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    support = root / "support"; support.mkdir()
    from erysreview import batch, persistence, profile as profile_mod
    persistence.app_support_dir = lambda: support
    profile_mod.app_support_dir = lambda: support
    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    names = ["A_01_Prä_1.tif", "A_01_Prä_2.tif", "A_01_Prä_3.tif"]
    for n in names:
        tifffile.imwrite(folder / n, np.full((60, 60), 140, dtype=np.uint8))
    prof = ProfileData(name="Stale")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    prof.size_filter.enabled = False
    prof.cellpose.diameter = 34.0

    def write_cache(name, diameter, n_cells):
        m = np.zeros((60, 60), dtype=np.int32)
        for k in range(n_cells):
            m[5 + 15 * k:13 + 15 * k, 5:13] = k + 1
        npy, meta = cache_paths(folder, folder / name)
        npy.parent.mkdir(parents=True, exist_ok=True)
        np.save(npy, m)
        meta.write_text(json.dumps({**asdict(prof.cellpose), "diameter": diameter}))
    write_cache(names[0], 30.0, 2)        # stale
    write_cache(names[1], 34.0, 2)        # current
    rv = ReviewState(); rv.force_excluded = [1]; rv.types = {"2": "stechapfel"}
    rv.save(folder, "A_01_Prä_1")
    save_profile(prof)
    AppSettings(last_profile_name="Stale", language="de", theme="light").save()

    def fake_segment(fold, info, profile, registry):
        m = np.zeros((60, 60), dtype=np.int32); m[30:40, 30:40] = 1; m[45:55, 45:55] = 2; m[5:12, 40:47] = 3
        npy, meta = cache_paths(fold, info.path)
        npy.parent.mkdir(parents=True, exist_ok=True)
        np.save(npy, m); meta.write_text(json.dumps(asdict(profile.cellpose)))
    batch.segment_one = fake_segment

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from erysreview.qt import processing
    from erysreview.qt.app import MainWindow, make_app

    app = make_app(["test"])
    asked = []
    QMessageBox.question = staticmethod(lambda *a, **k: (asked.append(a[2] if len(a) > 2 else ""), QMessageBox.Yes)[1])
    win = MainWindow(); win.resize(1300, 800); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    tb, sb = win.toolbar, win.sidebar

    win.show_image(0)
    check(tb.btn_resegment.property("cpstate") == "stale" and tb._cp_dot.isVisibleTo(tb),
          "old values: the Cellpose button turns orange with a dot")
    check("30" in tb.btn_resegment.toolTip() and "34" in tb.btn_resegment.toolTip(),
          f"the tooltip names the difference ({tb.btn_resegment.toolTip()!r})")
    check(win.S["qt_status_stale"] in win.status_label.text(), "the status line says so")
    check(not tb.banner.isVisible(), "the old text banner is gone from the toolbar")
    check(len(sb.stale_paths()) == 1 and "(1)" in sb.filter_combo.itemText(sb.filter_combo.findData("stale")),
          "the filter offers 'Alte Werte (1)'")
    check(not hasattr(sb, "det_rerun") and "Zellen" in sb.det_cells.text(),
          "the detail panel shows only cells vs. target (old values live in tree, filter and study card)")
    OUT.mkdir(exist_ok=True)
    win.grab().save(str(OUT / "qt_stale_main.png"))

    win.show_image(1)
    check(tb.btn_resegment.property("cpstate") == "ok" and not tb._cp_dot.isVisibleTo(tb),
          "current values: the button is quiet")
    win.show_image(2)
    check(tb.btn_resegment.property("cpstate") == "unsegmented" and win.S["qt_compute"] in tb.btn_resegment.text(),
          "raw image: the button becomes the blue 'Rechnen' action")

    sb._set_filter("stale")
    shown = [sb._item_by_idx[i].text(0) for i in sb._item_by_idx]
    check(len(shown) == 1 and "A_01_Prä_1.tif" in shown[0] and "⟳" not in shown[0],
          "filter 'Alte Werte' shows only the stale image (⟳ only on the proband row)")
    sb._set_filter("all")

    def run_and_wait(dlg):
        dlg.awake_cb.setChecked(False)
        check("1 von 3" in dlg.count_label.text(), "the re-run window pre-selects only the stale image")
        dlg._start()
        for _ in range(100):
            QTest.qWait(30)
            if dlg.ran:
                break
        return 1
    orig = processing.BatchDialog.exec
    processing.BatchDialog.exec = run_and_wait
    win.rerun(sb.stale_paths())
    processing.BatchDialog.exec = orig
    check(asked and "zurückgesetzt" in asked[-1], "the user is told that the review is reset, and confirms")
    rv = ReviewState.load(folder, "A_01_Prä_1")
    check(rv.force_excluded == [] and rv.types == {}, "the stale image's old review was reset")
    check(sb.stale_paths() == [] and win.ctl.stale_images() == [], "no image has old values any more")
    check(sb.filter_combo.itemText(sb.filter_combo.findData("stale")).endswith("(0)"), "the filter count is back to 0")

    win.close(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: old values show on the button, in the tree, filter, details and "
          f"status line, and a confirmed re-run clears them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
