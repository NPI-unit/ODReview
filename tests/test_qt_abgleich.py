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

    from PySide6.QtCore import Qt
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

    w.close()

    # ── the comparison in the main window (split) ──
    from erysreview import backups
    from erysreview.qt.backups_dialog import BackupsDialog
    win.settings.abgleich_hint_shown = True              # the first-take note is tested below
    win.show_image(0)
    win._update_abgleich_ui()
    check(win.toolbar.btn_abgleich.isVisibleTo(win.toolbar) and win.toolbar.btn_stats.isVisibleTo(win.toolbar),
          "study A with a comparison: '⇆ Abgleich' and 'Statistik' appear in the toolbar")
    win.toolbar.btn_abgleich.click(); QTest.qWait(80)
    sp = win.split
    check(sp.active and win.canvas_b.isVisible() and win.canvas_b.read_only and sp.panel.isVisible(),
          "the view splits: A and B (view only), decision panel on the right")
    bks = backups.list_backups(folder)
    check(any(b.kind == "abgleich" and "Finn 2" in b.name for b in bks), "A was backed up when the comparison started")
    st0 = Path(names[0]).stem
    check(len(sp.diffs) == 2 and [d[0] for d in sp.diffs] == [2, 5], f"image 1: cells 2 and 5 differ ({sp.diffs})")
    OUT.mkdir(exist_ok=True); win.grab().save(str(OUT / "qt_split.png"))

    win.canvas.set_view((10, 60), (60, 10)); win.canvas._notify_view()
    check(tuple(win.canvas_b.ax.get_xlim()) == (10, 60), "zoom and pan move both sides together")
    sp.set_layout("rows")
    check(win.views.orientation() == Qt.Vertical, "'Untereinander' stacks A above B")
    sp.set_layout("side")
    sp.only_cb.setChecked(True)
    check(win.canvas.only_ids == {2, 5}, "'Nur Unterschiede' colours only the differing cells")
    sp.only_cb.setChecked(False)

    from types import SimpleNamespace
    ax = win.canvas_b.ax
    x, y = ax.transData.transform((60, 30))
    types_b_before = dict(sp.rb.types)
    win.canvas_b._on_button_press(SimpleNamespace(button=1, inaxes=ax, x=x, y=y, xdata=60, ydata=30))
    check(sp.rb.types == types_b_before, "B cannot be edited from the split")

    # cell 2: A Stechapfel, B normal → B is right → take B
    sp.pos = 0; sp.take_b()
    a_now = ReviewState.load(folder, st0)
    check("2" not in a_now.types and ir.decisions(folder, "Finn 2")[st0]["2"] == "taken",
          "'B → A' changes cell 2 in study A itself (normal now) and records the decision")
    # cell 5: A normal, B Stechapfel → A is right → keep
    sp.pos = next(k for k, d in enumerate(sp.diffs) if d[0] == 5); sp.keep_a()
    check("5" not in ReviewState.load(folder, st0).types and ir.decisions(folder, "Finn 2")[st0]["5"] == "kept",
          "'A behalten' leaves A as it is")
    check(sp.prog_text.text().startswith("2 von 2"), f"progress shows both decided ({sp.prog_text.text()})")
    sp.undo()
    check("5" not in ir.decisions(folder, "Finn 2")[st0], "the last decision can be undone")
    sp.undo()
    check(ReviewState.load(folder, st0).types.get("2") == "stechapfel", "undoing 'B → A' restores A's cell")
    sp.pos = 0; sp.take_b()

    # first take shows the note (backup can be loaded any time) — once
    win.settings.abgleich_hint_shown = False
    shown = []
    from PySide6.QtWidgets import QMessageBox
    real_exec = QMessageBox.exec
    def fake_exec(box):
        shown.append(box.text())
        box.checkBox().setChecked(True)
        box._clicked = box.buttons()[0]
        return 0
    QMessageBox.exec = fake_exec
    QMessageBox.clickedButton = lambda box: getattr(box, "_clicked", None)
    check(sp._first_take_hint() and shown and "Sicherung" in shown[0] and win.settings.abgleich_hint_shown,
          "the first 'B → A' explains: study A changes, the backup can be loaded any time")
    QMessageBox.exec = real_exec
    del QMessageBox.clickedButton

    # statistics: with the backup A is unchanged → the original 8 differences
    ws = InterraterWindow(win, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, me="Finn", rater="Finn 2")
    check(ws.snap_cb.isChecked() and ws.ag.n_cells - ws.ag.n_same == 8,
          "the statistics use 'A vor Abgleich' — the taken correction does not flatter κ")
    ws.snap_cb.setChecked(False)
    check(ws.ag.n_cells - ws.ag.n_same == 7, "…and can compare the current A instead (one difference fewer)")
    ws.close()

    # backups: load 'A vor Abgleich' back
    bd = BackupsDialog(win, win.P, win.S, folder)
    target = next(i for i in range(bd.tree.topLevelItemCount())
                  if "Finn 2" in bd.tree.topLevelItem(i).text(0))
    bd.tree.setCurrentItem(bd.tree.topLevelItem(target)); bd.tree.topLevelItem(target).setSelected(True)
    bd.load(confirm=lambda: True)
    check(bd.restored and ReviewState.load(folder, st0).types.get("2") == "stechapfel",
          "loading the backup brings A's original rating back")
    check(any(b.kind == "auto" for b in backups.list_backups(folder)), "…after saving the state before as an automatic backup")
    bd.close()
    sp.deactivate()
    check(not win.canvas_b.isVisible() and win.canvas.only_ids is None, "'⇆ Abgleich' off: back to the single view")

    # backups travel in a package only when asked
    from erysreview import package as pkg
    import zipfile
    zp1, zp2 = root / "ohne.zip", root / "mit.zip"
    pkg.create_package(folder, win.ctl.grouped, win.ctl.profile, zp1, include_images=False)
    pkg.create_package(folder, win.ctl.grouped, win.ctl.profile, zp2, include_images=False, include_backups=True)
    has = lambda z: any(n.startswith("sicherungen/") for n in zipfile.ZipFile(z).namelist())
    check(not has(zp1) and has(zp2), "backups go into a package only with 'Sicherungen mitnehmen'")

    # three ratings for Fleiss' κ: a second self rating
    ir.create_self_rating(folder, "Finn 3", [Path(n).stem for n in names])
    for n in names:
        rv = ReviewState.load(folder, Path(n).stem); rv.is_finished = True
        (ir.rater_dir(folder, "Finn 3") / f"{Path(n).stem}_review.json").write_text(json.dumps(rv.to_dict()))
    w = InterraterWindow(win, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, me="Finn", rater="Finn 2")
    w.all_cb.setEnabled(True); w.all_cb.setChecked(True); QTest.qWait(30)
    titles = [w.tiles.itemAt(i).widget().findChildren(type(w.title))[0].text() for i in range(w.tiles.count())]
    check(any("Fleiss" in t and "3" in t for t in titles), f"'Alle Bewertungen' shows Fleiss' κ over 3 ratings ({titles})")
    check("Shrout" in w.findChildren(type(w.title))[-3].text() or any("Shrout" in l.text() for l in w.findChildren(type(w.title))),
          "the window lists the references")
    w.close(); win.close()
    from erysreview.qt.tasks import shutdown
    shutdown(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: blind intra-rater rating, comparison in the main window, backups and Fleiss' κ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
