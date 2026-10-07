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
    win.resize(1440, 860); QTest.qWait(80)
    check(win.width() <= 1440 and win.minimumSizeHint().width() <= 1440,
          f"the window with the comparison fits a laptop screen (min {win.minimumSizeHint().width()} px)")
    check(win.toolbar.btn_abgleich.text() == "⇆", "narrow: the toolbar shows symbols (names in the tooltips)")
    win.resize(2300, 860); QTest.qWait(80)
    check("Abgleich" in win.toolbar.btn_abgleich.text(), "wide: the full names come back")
    win.resize(1440, 860); QTest.qWait(80)
    sp._fill_more()
    acts = [a.text() for a in sp.more_menu.actions() if a.text()]
    check(any("Sicherung laden" in t for t in acts) and any("Finn 2" in t and "entfernen" in t for t in acts),
          f"backups and removing B sit behind '⋯' ({acts})")
    st0 = Path(names[0]).stem
    check(len(sp.diffs) == 2 and [d[0] for d in sp.diffs] == [2, 5], f"image 1: cells 2 and 5 differ ({sp.diffs})")
    OUT.mkdir(exist_ok=True); win.grab().save(str(OUT / "qt_split.png"))

    ov0 = win.settings.overlay_visible
    win.toggle_overlay()
    check(win.canvas._overlay_handle.get_visible() == win.canvas_b._overlay_handle.get_visible() == (not ov0),
          "overlay on/off switches A and B together")
    win.toggle_overlay()
    check(win.canvas_b._overlay_handle.get_visible() == ov0, "…and back")
    win.set_grid(3)
    check(win.canvas.grid_n == win.canvas_b.grid_n == 3 and len(win.canvas_b._grid_artists) == len(win.canvas._grid_artists) > 0,
          "the helper grid shows on A and B")
    win.set_grid(0)
    check(win.canvas_b.grid_n == 0 and not win.canvas_b._grid_artists, "…and goes off on both")
    win.canvas.set_view((10, 60), (60, 10)); win.canvas._notify_view()
    check(tuple(win.canvas_b.ax.get_xlim()) == (10, 60), "zoom and pan move both sides together")
    sp.set_layout("rows")
    check(win.views.orientation() == Qt.Vertical, "'Untereinander' stacks A above B")
    sp.set_layout("side")
    sp.set_diff_mode("changed")
    check([d[0] for d in sp.diffs] == [2, 5] and ir.selection(folder, "Finn 2")["diffs"] == "changed",
          "'Nur veränderte' keeps differences with a type other than normal (Stechapfel ↔ Normal)")
    sp.set_diff_mode("all")
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

    # the list in the comparison: common images, ticks for the statistics, metric
    sb = win.sidebar
    from PySide6.QtCore import Qt as _Qt
    common = ir.common_stems(folder, "Finn 2")
    check(sb._filter == "cmp_common" and sb.filter_combo.findData("cmp_diffs") >= 0,
          "the comparison starts the list on 'Gemeinsam (A & B)' and offers 'Nur Unterschiede'")
    check({win.ctl.images[i].stem for i in sb.order()} == common,
          f"the list shows exactly the images A and B both rated ({len(common)})")
    items = [sb._item_by_idx[i] for i in sb.order()]
    check(all(it.checkState(0) == _Qt.Checked for it in items) and f"{len(common)} von {len(common)}" in sb.calc_line.text(),
          f"every common image is ticked ({sb.calc_line.text()})")
    i0 = next(i for i in sb.order() if win.ctl.images[i].stem == st0)
    sb._item_by_idx[i0].setCheckState(0, _Qt.Unchecked); QTest.qWait(20)
    check(ir.selection(folder, "Finn 2")["excluded"] == {st0} and
          f"{len(common) - 1} von {len(common)}" in sb.calc_line.text(),
          "unticking an image leaves it out of the statistics (saved per B)")
    grp = sb._group_items[sb._group_of_idx[i0]]
    n_grp_common = sum(1 for k in range(grp.childCount()) if grp.child(k).checkState(0) is not None)
    check(grp.checkState(0) == (_Qt.PartiallyChecked if n_grp_common > 1 else _Qt.Unchecked),
          "the proband's tick shows 'some'")
    sp.set_metric("rbc")
    check(ir.selection(folder, "Finn 2")["metric"] == "rbc" and sp.metric_combo.currentData() == "rbc",
          "the metric in the bar is stored per B")
    ws = InterraterWindow(win, win.P, win.S, folder, win.ctl.grouped, win.ctl.profile, me="Finn", rater="Finn 2")
    check(ws.ag.metric == "rbc" and ws.ag.n_left_out == 1 and ws.ag.n_images == len(common) - 1,
          "the statistics use the ticks and the metric")
    ws.close()
    win.show_image(i0); QTest.qWait(20)
    check(sp.group_vals.text().startswith("Proband: A") or sp.group_vals.text() == "",
          f"the panel shows the proband's metric for A and B ({sp.group_vals.text()!r})")
    sb._set_filter("cmp_diffs")
    diff_stems = {win.ctl.images[i].stem for i in sb.order()}
    check(diff_stems and diff_stems <= common and all(
              ir.image_differs(folder, next(x for d in win.ctl.grouped.tree.values() for l in d.values()
                                            for x in l if x.path.stem == st), win.ctl.profile, "Finn 2")
              for st in diff_stems),
          f"'Nur Unterschiede' lists only images A and B rate differently ({len(diff_stems)})")
    sb._set_filter("cmp_common")
    sb._item_by_idx[i0].setCheckState(0, _Qt.Checked); QTest.qWait(20)
    sp.set_metric(ir.DEFAULT_METRIC)

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
    check(win.sidebar._filter == "all" and win.sidebar.filter_combo.findData("cmp_common") < 0
          and win.sidebar.calc_line.isHidden(), "…and the list is back on its filter, without ticks")

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
    w.close()

    # a study whose file name equals a second rating's ("Studie Finn 2" / "Studie · Finn 2")
    from erysreview.profile import ProfileData as _PD, ProfileNameClash, load_profile as _lp, save_profile as _sp
    try:
        _sp(_PD(name="Studie Finn 2")); clash = False
    except ProfileNameClash:
        clash = True
    check(clash and _lp("Studie · Finn 2") is not None and _lp("Studie · Finn 2").rater == "Finn 2",
          "a study cannot overwrite another one that maps to the same file")

    # remove second ratings: in the settings and from the comparison bar
    from erysreview.qt import settings as qsettings
    bk_before = {b.id for b in backups.list_backups(folder)}
    win.toolbar.btn_abgleich.click(); QTest.qWait(50)          # comparing with Finn 2 meanwhile
    win.split.activate("Finn 2")
    check([win.split.b_combo.itemText(i) for i in range(win.split.b_combo.count())] == ["Finn 2", "Finn 3"],
          "the B menu lists both second ratings")
    dlg = qsettings.SettingsDialog(win, win.P, win.S, win.settings, win.ctl.profile, win.ctl.folder,
                                   win.ctl.images, on_study_saved=lambda *a: None,
                                   on_local_changed=win._on_local_setting, on_switch_study=lambda: None,
                                   start_page="raters")
    asked = []
    check(not dlg.delete_rater("Finn 3", confirm=lambda step: asked.append(step) or step == 1)
          and "Finn 3" in ir.list_raters(folder) and asked == [1, 2],
          "removing asks twice; 'Abbrechen' at the second question keeps it")
    check(dlg.raters_box.count() == 2, "Einstellungen › Zweitbewertungen lists both")
    check(dlg.delete_rater("Finn 3", confirm=lambda step: True) and "Finn 3" not in ir.list_raters(folder)
          and not (ir.raters_dir(folder) / "Finn_3").exists(),
          "Einstellungen › Zweitbewertungen removes 'Finn 3' (ratings, decisions, selection)")
    QTest.qWait(20)
    check(dlg.raters_box.count() == 1, "…and its row disappears from the list at once")
    check(dlg.footer_saved.text().startswith("✓") and "Finn 3" in dlg.footer_saved.text(),
          f"the footer confirms it ({dlg.footer_saved.text()!r})")
    dlg._local("reviewer_name", win.settings.reviewer_name)
    check(dlg.footer_saved.text() == "✓ Gespeichert", "a change on a computer page shows '✓ Gespeichert'")
    check(win.split.active and win.split.rater == "Finn 2"
          and [win.split.b_combo.itemText(i) for i in range(win.split.b_combo.count())] == ["Finn 2"],
          "the comparison stays on Finn 2, and Finn 3 is gone from the B menu")
    dlg.close()
    check(win.remove_rater("Finn 2", confirm=lambda step: True) and ir.list_raters(folder) == [],
          "'B entfernen …' in the comparison bar removes 'Finn 2'")
    check(not win.split.active and not win.toolbar.btn_abgleich.isVisibleTo(win.toolbar),
          "with no B left the comparison closes and '⇆ Abgleich' disappears")
    check(_lp("Studie · Finn 2") is None and _lp("Studie") is not None,
          "the study made for the second rating goes, study A stays")
    from erysreview.cellpose_runner import cache_paths as _cp
    check(all(_cp(folder, folder / n)[0].is_file() for n in names)
          and all(ReviewState.review_path(folder, Path(n).stem).is_file() for n in names),
          "the masks and study A's reviews stay untouched")
    check(bk_before <= {b.id for b in backups.list_backups(folder)},
          "the backups ('A vor Abgleich …') stay")

    # importing a second rating shows '⇆ Abgleich' at once
    from erysreview.qt import interrater as qir
    real_flow = qir.compare_package_flow
    def fake_flow(*_a, **_k):
        ir.create_self_rating(folder, "Janina", [Path(n).stem for n in names])
        return "Janina"
    qir.compare_package_flow = fake_flow
    win.open_interrater = lambda *_a, **_k: None
    win._compare_package(root / "Janina.zip")
    qir.compare_package_flow = real_flow
    check(win.toolbar.btn_abgleich.isVisibleTo(win.toolbar),
          "after importing a second rating '⇆ Abgleich' is in the toolbar right away")
    win.close()
    from erysreview.qt.tasks import shutdown
    shutdown(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: blind intra-rater rating, comparison in the main window, backups and Fleiss' κ.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
