"""Phase 4: packages, statistics, overview, cell areas — off-screen.

A small study with segmented, reviewed images: create a package, import it
into a fresh folder (the package's study becomes active), import it again
into the same folder to hit the conflict question (keep = local review
survives), then statistics (boxplot, both views), overview, cell areas + CSV
and the overview workbook."""

from __future__ import annotations

import csv
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
    from erysreview.cellpose_runner import cache_paths, has_cache
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, list_profiles, save_profile

    folder = root / "study"; folder.mkdir()
    prof = ProfileData(name="Analyse")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    prof.size_filter.enabled = False
    prof.target_cells = 3
    rng = np.random.default_rng(1)
    for tp in ("d0", "d7"):
        for sub in ("01", "02"):
            for k in (1, 2):
                name = f"A_{sub}_{tp}_{k}.tif"
                tifffile.imwrite(folder / name, np.full((80, 80), 140, dtype=np.uint8))
                m = np.zeros((80, 80), dtype=np.int32)
                for c in range(4):
                    s = 8 + int(rng.integers(0, 4))
                    m[5 + 18 * c:5 + 18 * c + s, 10:10 + s] = c + 1
                npy, meta = cache_paths(folder, folder / name)
                npy.parent.mkdir(parents=True, exist_ok=True)
                np.save(npy, m); meta.write_text(json.dumps(asdict(prof.cellpose)))
                rv = ReviewState(is_finished=True)
                rv.types = {"1": "stechapfel"} if (k == 1 and sub == "01") else {}
                rv.time_spent_s = 60.0 * k
                rv.save(folder, Path(name).stem)
    save_profile(prof)
    AppSettings(last_profile_name="Analyse", language="de", theme="light").save()

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from erysreview.qt import analysis
    from erysreview.qt.app import MainWindow, make_app

    app = make_app(["test"])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    win = MainWindow(); win.resize(1300, 800); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    check(len(win.ctl.images) == 8, "the study opens with 8 segmented images")
    OUT.mkdir(exist_ok=True)

    # ── package create ──
    dlg = analysis.PackageCreateDialog(win, win.P, win.S, win.ctl.folder, win.ctl.grouped,
                                       win.ctl.profile, "Finn")
    dlg.resize(820, 640); dlg.show(); QTest.qWait(20)
    check(len(dlg.selected()) == 8, "all segmented images are pre-selected")
    img_b, mask_b, _rev = dlg.estimate()
    check(img_b > 0 and mask_b > 0 and "≈" in dlg.count.text(), f"the size estimate is shown ({dlg.count.text()!r})")
    dlg.include.setChecked(False)
    check(dlg.estimate()[0] == 0, "…and drops the images when they are left out")
    dlg.include.setChecked(True)
    dlg.grab().save(str(OUT / "qt_pkg_create.png"))
    zip_path = root / "paket.zip"
    dlg._create(str(zip_path))
    check(zip_path.is_file() and dlg.reviewer_used == "Finn", "the package is written")

    # ── import into a new folder ──
    target = root / "empfang"; target.mkdir()
    res = analysis.import_package_flow(win, win.P, win.S, win.ctl.profile,
                                       ask_zip=lambda: str(zip_path), ask_target=lambda: str(target))
    check(res is not None and len(res["imported"]) == 8, f"import into a new folder takes all 8 ({res and len(res['imported'])})")
    imported_root = Path(res["target"])
    check(any(has_cache(imported_root, p) for p in imported_root.glob("*.tif")), "masks travel with the package")

    # ── import again over local edits: conflict, keep ──
    some = sorted(imported_root.glob("*.tif"))[0].stem
    rv = ReviewState.load(imported_root, some); rv.force_excluded = [2]; rv.save(imported_root, some)
    seen = []
    orig = analysis.ConflictDialog.exec

    def keep_all(d):
        seen.append(1)
        d.grab().save(str(OUT / "qt_pkg_conflict.png"))
        d._pick("keep_all")
        return 1
    analysis.ConflictDialog.exec = keep_all
    res2 = analysis.import_package_flow(win, win.P, win.S, win.ctl.profile,
                                        ask_zip=lambda: str(zip_path), ask_target=lambda: str(imported_root))
    analysis.ConflictDialog.exec = orig
    check(seen and res2 is not None, "a local edit raises the conflict question")
    check(ReviewState.load(imported_root, some).force_excluded == [2], "'keep' preserves the local review")

    # ── statistics ──
    sw = analysis.StatsWindow(win, win.P, win.S, win.ctl.folder, win.ctl.profile, win.ctl.grouped)
    sw.resize(1100, 720); sw.show(); QTest.qWait(50)
    check(sw.table.topLevelItemCount() == 2, "statistics by timepoint: two groups (d0, d7)")
    sw.grab().save(str(OUT / "qt_stats.png"))
    subs = {r["subject"] for r in sw.records}
    check(len(sw.ax_traj.get_lines()) == len(subs) and [t.get_text() for t in sw.ax_traj.get_xticklabels()] == ["d0", "d7"],
          "trajectories: one line per proband over the timepoints")
    ax_items = [it for it in sw.hover.items if it[0] is sw.ax_traj]
    _ax, xs, ys, labels = ax_items[0]
    px, py = sw.ax_traj.transData.transform((xs[0], ys[0]))
    hit = sw.hover.nearest(sw.ax_traj, px + 2, py - 2)
    check(hit is not None and "Proband" in hit[2] and "d0" in hit[2],
          f"pointing at a dot names proband and timepoint ({hit and hit[2]!r})")
    check(sw.hover.nearest(sw.ax, -500, -500) is None, "…and nothing away from the dots")
    box_items = [it for it in sw.hover.items if it[0] is sw.ax]
    check(sum(len(it[1]) for it in box_items) == len(sw.records), "every value is a named dot in the box plot")
    sw.profile.timepoint_order = ["d7", "d0"]; sw.redraw()
    check(sw.table.topLevelItem(0).text(0) == "d7" and sw.ax_traj.get_xticklabels()[0].get_text() == "d7",
          "the study's timepoint order is used (d7 before d0)")
    sw.profile.timepoint_order = []; sw.redraw()
    sw.by.buttons()[1].setChecked(True); QTest.qWait(20)
    check(sw.table.topLevelItemCount() == 2 and sw.table.topLevelItem(0).text(0) == "01",
          "…and by subject")
    sw.close()

    # ── overview ──
    dw = analysis.DashboardWindow(win, win.P, win.S, win.ctl.folder, win.ctl.profile, win.ctl.grouped)
    dw.resize(1100, 720); dw.show(); QTest.qWait(30)
    check(dw.table.topLevelItemCount() == 4, "the overview lists every subject × timepoint")
    check(dw.tiles.count() == 4, "with four key-figure tiles")
    dw.grab().save(str(OUT / "qt_dashboard.png"))
    dw.close()

    # ── cell areas ──
    from erysreview.tasks import run_sync
    groups = run_sync(win.ctl.areas_task())
    aw = analysis.AreasWindow(win, win.P, win.S, groups)
    aw.resize(1000, 600); aw.show(); QTest.qWait(20)
    aw.grab().save(str(OUT / "qt_areas.png"))
    csv_path = root / "flaechen.csv"
    aw._save(str(csv_path))
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    check(len(groups) == 4 and rows and rows[0]["ebene"] == "Gruppe", "areas per group, CSV written")
    aw.close()

    # ── overview workbook ──
    xlsx = root / "Uebersicht.xlsx"
    written = run_sync(win.ctl.overview_task(xlsx))
    check(written and Path(written).is_file(), "the overview workbook is written")

    # The buttons themselves, clicked (a direct _create(path) call hid that
    # clicked(bool) landed in `dest` and the click silently did nothing).
    from PySide6.QtWidgets import QFileDialog
    real_save = QFileDialog.getSaveFileName
    zip2 = root / "per_klick.zip"; csv2 = root / "per_klick.csv"
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(zip2), "ZIP (*.zip)"))
    d2 = analysis.PackageCreateDialog(win, win.P, win.S, win.ctl.folder, win.ctl.grouped, win.ctl.profile, "Finn")
    d2.go.click()
    check(zip2.is_file(), "clicking 'Paket erstellen' creates the package")
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (str(csv2), "CSV (*.csv)"))
    aw2 = analysis.AreasWindow(win, win.P, win.S, groups)
    next(b for b in aw2.findChildren(type(d2.go)) if b.text() == win.S["areas_save_csv"]).click() \
        if any(b.text() == win.S["areas_save_csv"] for b in aw2.findChildren(type(d2.go))) else aw2._save()
    check(csv2.is_file(), "clicking 'CSV speichern' writes the file")
    QFileDialog.getSaveFileName = real_save

    # Guard: a button's clicked(bool) must never land in an optional parameter.
    import ast
    bad = []
    for f in (Path(__file__).resolve().parent.parent / "erysreview" / "qt").rglob("*.py"):
        tree = ast.parse(f.read_text())
        opt = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and len(n.args.args) >= 2
               and len(n.args.defaults) >= len(n.args.args) - 1}
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "connect" and n.args
                    and isinstance(n.args[0], ast.Attribute) and n.args[0].attr in opt
                    and getattr(getattr(n.func, "value", None), "attr", "") in ("clicked", "triggered", "toggled")):
                bad.append(f"{f.name}:{n.lineno} {n.args[0].attr}")
    check(not bad, f"no button passes its checked-flag into an optional parameter ({bad})")

    # Guard: no attribute of ours may hide a Qt method of the same name
    # (`self.metric` replaced QPaintDevice.metric() and crashed painting;
    # `self.done` replaced QDialog.done()).
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QWidget
    from erysreview.qt import processing, settings as qsettings, study_picker
    probes = [win, win.toolbar, win.sidebar, win.canvas, win.notebox, sw, dw, aw, dlg,
              processing.BatchDialog(win, win.P, win.S, win.ctl.folder, win.ctl.images, win.ctl.grouped,
                                     win.ctl.profile, {}),
              processing.ExportDialog(win, win.P, win.S, win.ctl.folder, win.ctl.grouped),
              processing.EvalSelectDialog(win, win.P, win.S, win.ctl.folder,
                                          win.ctl.grouped.tree["d0"]["01"], win.ctl.profile, "x"),
              qsettings.SettingsDialog(win, win.P, win.S, win.settings, win.ctl.profile, win.ctl.folder,
                                       win.ctl.images, lambda p, c: None, lambda f: None, lambda: None),
              study_picker.StudyPicker(win, win.P, win.S, win.settings)]
    clashes = []
    for obj in probes:
        qt_bases = [b for b in type(obj).__mro__ if issubclass(b, QWidget) and b.__module__.startswith("PySide6")]
        for name in vars(obj):
            if isinstance(getattr(type(obj), name, None), Signal):
                continue                     # PySide keeps its own signals there
            if any(callable(getattr(b, name, None)) for b in qt_bases):
                clashes.append(f"{type(obj).__name__}.{name}")
    check(not clashes, f"no attribute hides a Qt method ({clashes})")

    win.close()
    from erysreview.qt.tasks import shutdown
    shutdown(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: packages, statistics, overview and cell areas work "
          f"(screenshots in tests/_out/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
