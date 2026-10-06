"""Interrater, part 2 (mockup v6): intra-rater blind rating, the comparison
view with consensus, and Fleiss' κ over three ratings — off-screen.

A study with masks and reviews (A). 'Abgleich' creates B = "Finn 2" (blind)
in the same folder: opening it shows the same images on the same masks with
empty reviews; edits go to B's namespace, A's files stay byte-identical;
recomputing is refused. The agreement stays locked until B is finished. Then
the comparison view lists differences, N jumps, 'B übernehmen' writes the
consensus (a new rating + study), and 'Alle Bewertungen' gives Fleiss' κ."""

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
    from erysreview import persistence, profile as profile_mod
    persistence.app_support_dir = lambda: support
    profile_mod.app_support_dir = lambda: support
    from erysreview import interrater as ir
    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, load_profile, save_profile

    folder = root / "study"; folder.mkdir()
    prof = ProfileData(name="Studie")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "ignore", "image_num"]
    prof.import_folder = str(folder)
    prof.size_filter.enabled = False
    names = []
    for sub in ("01", "02"):
        for k in (1, 2):
            name = f"ErysReview_{sub}_Prä_07.10.26_0{k}.tif"
            names.append(name)
            tifffile.imwrite(folder / name, np.full((120, 120), 140, dtype=np.uint8))
            m = np.zeros((120, 120), dtype=np.int32)
            for c in range(9):
                y, x = 15 + 30 * (c // 3), 15 + 30 * (c % 3)
                m[y:y + 18, x:x + 18] = c + 1
            npy, meta = cache_paths(folder, folder / name)
            npy.parent.mkdir(parents=True, exist_ok=True)
            np.save(npy, m); meta.write_text(json.dumps(asdict(prof.cellpose)))
            rv = ReviewState(is_finished=True); rv.types = {"1": "stechapfel", "2": "stechapfel"}
            rv.save(folder, Path(name).stem)
    save_profile(prof)
    AppSettings(last_profile_name="Studie", language="de", theme="light", reviewer_name="Finn").save()
    a_before = {}
    for f in (folder / ".erys_cache").glob("*_review.json"):
        d = json.loads(f.read_text()); d.pop("time_spent_s", None); d.pop("next_manual_id", None); a_before[f.name] = d

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QMessageBox
    from erysreview.qt import abgleich, study_picker
    from erysreview.qt.app import MainWindow, make_app
    app = make_app(["test"])
    msgs = []
    QMessageBox.information = staticmethod(lambda *a, **k: (msgs.append(a[2] if len(a) > 2 else ""), QMessageBox.Ok)[1])
    win = MainWindow(); win.resize(1200, 760); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(150)

    # ── Abgleich › hier neu bewerten, blind ──
    dlg = abgleich.AbgleichDialog(win, win.P, win.S, win.settings, current="Studie")
    check(dlg.name.text() == "Finn 2" and "4 Bilder" in dlg.base_info.text() and dlg.go.isEnabled(),
          f"the dialog proposes 'Finn 2' on 4 images with masks ({dlg.base_info.text()})")
    OUT.mkdir(exist_ok=True); dlg.grab().save(str(OUT / "qt_abgleich.png"))
    dlg._create()
    b = dlg.result_profile
    check(b.rater == "Finn 2" and b.blind and b.rater_base == "Studie" and load_profile("Studie · Finn 2") is not None,
          "the second rating is a study of its own: 'Studie · Finn 2', blind")

    win.ctl.flush_time(); win.ctl.activate_profile(b); win._profile_changed(reopen_folder=True)
    QTest.qWait(50)
    check(len(win.ctl.images) == 4 and not win.ctl.review.types and not win.ctl.review.is_finished,
          "B sees the same images, but none of A's ratings (blind)")
    check(win.canvas.masks is not None and int(win.canvas.masks.max()) == 9, "…on A's masks")
    win.ctl.review.types = {"1": "stechapfel", "5": "stechapfel"}; win._save_review()
    def a_ratings():     # A's ratings — not time spent / next free id, which A keeps while open
        out = {}
        for f in (folder / ".erys_cache").glob("*_review.json"):
            d = json.loads(f.read_text()); d.pop("time_spent_s", None); d.pop("next_manual_id", None); out[f.name] = d
        return out
    check((folder / ".erys_cache" / "raters" / "Finn_2" / f"{Path(names[0]).stem}_review.json").is_file()
          and a_ratings() == a_before,
          f"B's edits go to its own folder; A's ratings are unchanged")
    msgs.clear(); win.resegment()
    check(msgs and "nicht neu gerechnet" in msgs[-1], "recomputing is refused in a second rating")

    from erysreview.qt.interrater import InterraterWindow
    w = InterraterWindow(win, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, me="Finn",
                         rater="Finn 2", base_profile=load_profile("Studie"))
    check(w.lock.isVisibleTo(w) and w.ag is None and "0 von 4" in w.lock.text(),
          f"blind: the comparison stays locked until B is finished ({w.lock.text()[:60]}…)")
    w.close()
    for i in range(4):                     # B finishes every image
        win.show_image(i)
        if i == 0:
            pass
        win.ctl.review.is_finished = True; win._save_review()

    # back to A
    win.ctl.activate_profile(load_profile("Studie")); win._profile_changed(reopen_folder=True); QTest.qWait(30)
    check(win.ctl.review.types.get("1") == "stechapfel", "back in study A, A's ratings are there")

    w = InterraterWindow(win, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, me="Finn", rater="Finn 2")
    # A: cells 1+2 Stechapfel on all 4 images; B: cells 1+5 on image 1 only → 2 + 3×2 = 8
    check(not w.lock.isVisibleTo(w) and w.ag is not None and w.ag.n_cells - w.ag.n_same == 8,
          f"finished: the comparison opens, 8 cells differ ({w.ag and w.ag.n_cells - w.ag.n_same})")

    # ── comparison view + consensus ──
    from erysreview.qt.compare_view import CompareWindow
    made = []
    cv = CompareWindow(w, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, "Finn 2", "Finn",
                       on_consensus=lambda: (made.append(1), ir.rater_study(load_profile("Studie"), ir.CONSENSUS)))
    cv.resize(1300, 800); cv.show(); QTest.qWait(80)
    check(cv.list.count() == 4 and len(cv.diffs) == 2 and cv.pos == 0, "4 images listed; the first has 2 differences, shown")
    ax = cv.fig.axes
    check(len(ax) == 2 and ax[0].get_shared_x_axes().joined(ax[0], ax[1]), "two views that zoom and pan together")
    first = cv.diffs[0].cid
    cv.grab().save(str(OUT / "qt_compare_view.png"))
    cv.take("b")
    cons = ir.load_review(folder, ir.CONSENSUS, Path(names[0]).stem)
    check(first == 2 and cons is not None and cons.types == {"1": "stechapfel"},
          f"'B übernehmen' on cell 2: consensus = A with cell 2 as B rated it ({cons and cons.types})")
    check(cv.pos == 1 and made and load_profile("Studie · Konsens") is not None,
          "…jumps to the next difference and creates the study 'Studie · Konsens'")
    cv.set_mode("flip"); cv.flip()
    check(len(cv.fig.axes) == 1 and cv.flip_b, "Umschalten: one view, space flips A/B")
    cv.close()

    w.all_cb.setEnabled(True); w.all_cb.setChecked(True); QTest.qWait(30)
    titles = [w.tiles.itemAt(i).widget().findChildren(type(w.title))[0].text() for i in range(w.tiles.count())]
    check(any("Fleiss" in t and "3" in t for t in titles), f"'Alle Bewertungen' shows Fleiss' κ over 3 ratings ({titles})")
    check("Shrout" in w.findChildren(type(w.title))[-3].text() or any("Shrout" in l.text() for l in w.findChildren(type(w.title))),
          "the window lists the references")
    w.close(); win.close()
    from erysreview.qt.tasks import shutdown
    shutdown(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: blind intra-rater rating, comparison view, consensus and Fleiss' κ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
