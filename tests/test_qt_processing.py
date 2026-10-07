"""Phase 3 + theme: batch, export, counted images, image info — and the light
theme staying light while the system palette is dark. Off-screen; Cellpose is
replaced by a stub that writes a real mask cache."""

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
    from erysreview.cellpose_runner import cache_paths, has_cache
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    names = [f"A_{s}_Prä_{k}.tif" for s in ("01", "02") for k in (1, 2)]
    for n in names:
        tifffile.imwrite(folder / n, np.full((60, 60), 140, dtype=np.uint8))
    prof = ProfileData(name="P3")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    prof.size_filter.enabled = False
    save_profile(prof)
    AppSettings(last_profile_name="P3", language="de", theme="light").save()

    def fake_segment(fold, info, profile, registry):
        m = np.zeros((60, 60), dtype=np.int32)
        m[10:20, 10:20] = 1; m[30:40, 30:40] = 2
        npy, meta = cache_paths(fold, info.path)
        npy.parent.mkdir(parents=True, exist_ok=True)
        np.save(npy, m); meta.write_text(json.dumps(asdict(profile.cellpose)))
    batch.segment_one = fake_segment

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
    from erysreview.qt import processing
    from erysreview.qt.app import MainWindow, make_app

    # Simulate macOS in dark mode: a dark *system* palette before the app styles itself.
    QApplication.setPalette(QPalette(QColor("#1E1E1E")))
    app = make_app(["test"])
    check(app.palette().color(QPalette.Base).name().upper() == "#FFFFFF"
          and app.palette().color(QPalette.Text).name().upper() == "#1A2233",
          "light theme overrides a dark system palette (base white, text dark)")
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)

    win = MainWindow(); win.resize(1300, 800); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    ctl = win.ctl
    check(len(ctl.images) == 4 and not ctl.any_masks(), "four raw images open")

    combo = win.sidebar.filter_combo
    combo.showPopup(); QTest.qWait(50)
    view = combo.view()
    check(view.palette().color(QPalette.Base).name().upper() == "#FFFFFF",
          "the filter list pops up light, not black")
    OUT.mkdir(exist_ok=True)
    view.window().grab().save(str(OUT / "qt_filter_popup_light.png"))
    combo.hidePopup()

    win._on_cell_note(None)
    check(win.notebox.width() >= 420 and win.notebox.height() >= 320
          and win.notebox.text.height() >= 140, "the remark box is large enough to write in")
    win.grab().save(str(OUT / "qt_notebox.png"))
    win.notebox.request_close()

    # ── batch ──
    dlg = processing.BatchDialog(win, win.P, win.S, ctl.folder, ctl.images, ctl.grouped,
                                 ctl.profile, {})
    dlg.resize(1000, 720); dlg.show(); QTest.qWait(30)
    check("4 von 4" in dlg.count_label.text(), f"all unsegmented images are pre-selected ({dlg.count_label.text()})")
    dlg.grab().save(str(OUT / "qt_batch.png"))
    dlg._select("firstn") if dlg.n_spin.setValue(1) is None else None
    check("2 von 4" in dlg.count_label.text(), "first N per subject narrows to 1 × 2 subjects")
    dlg.awake_cb.setChecked(False)
    dlg._start()
    for _ in range(100):
        QTest.qWait(30)
        if dlg.ran:
            break
    check(dlg.ran and "2 Bilder verarbeitet" in dlg.summary.text(), f"the batch ran ({dlg.summary.text()!r})")
    dlg.grab().save(str(OUT / "qt_batch_done.png"))
    check(has_cache(folder, folder / "A_01_Prä_1.tif") and not has_cache(folder, folder / "A_01_Prä_2.tif"),
          "exactly the selected images were segmented")
    dlg.accept()
    win.sidebar.refresh_all(); win.show_image(0)
    check(win.canvas.masks.max() == 2, "the main window shows the new masks")

    # ── counted images ──
    infos = ctl.grouped.tree["Prä"]["01"]
    ev = processing.EvalSelectDialog(win, win.P, win.S, folder, infos, ctl.profile, "Prä / 01")
    ev.show(); QTest.qWait(20)
    check(ev.tree.topLevelItemCount() == 2 and ev.counts["A_01_Prä_1"] == 2, "the picker lists the group with counts")
    ev._toggle_eval(ev.tree.topLevelItem(0), 0)
    ev._set_done(True)
    ev.grab().save(str(OUT / "qt_eval_select.png"))
    ev._save()
    rv = ReviewState.load(folder, "A_01_Prä_1")
    check(ev.changed and rv.eval_include is False and rv.is_finished, "eval override and finished flag are saved")
    check(not ReviewState.load(folder, "A_01_Prä_2").is_finished, "an unsegmented image is never marked done")
    win.sidebar.refresh_all()
    sub_item = win.sidebar._group_items[("Prä", "01")]
    rows = [sub_item.child(i).text(0) for i in range(sub_item.childCount())]
    check(rows[-1].endswith("⊘") and "A_01_Prä_1" in rows[-1] and "⊘" not in rows[0],
          f"a not counted image is marked ⊘ and moves to the end of its proband ({rows})")
    i_unseg = next(i for i, p in enumerate(ctl.images) if p.name == "A_01_Prä_2.tif")
    i_off = next(i for i, p in enumerate(ctl.images) if p.name == "A_01_Prä_1.tif")
    win.show_image(i_unseg); win.step(+1)
    check(ctl.idx == i_off, "→ follows the list: from the unsegmented image to the ⊘ image below it")
    win.step(-1)
    check(ctl.idx == i_unseg, "← goes back up the list")

    # ── image info ──
    win.show_image(0)
    win.show_info.__func__  # exists
    from erysreview.controller import status_counts
    c = status_counts(win.canvas.cell_infos, ctl.review, ctl.profile, win.canvas.border_ids, win.canvas.duplicate_ids)
    info = processing.ImageInfoDialog(win, win.P, win.S, "A_01_Prä_1.tif", c, ctl.profile, ctl.review)
    info.show(); QTest.qWait(20); info.grab().save(str(OUT / "qt_image_info.png"))
    check(c.active == 2 and c.by_type.get("normal") == 2, "image info counts cells per type")
    info.accept()
    shown = []
    orig_info_exec = processing.ImageInfoDialog.exec

    def grab_info(d):
        shown.append(d); d.grab().save(str(OUT / "qt_image_info_group.png")); return 1
    import erysreview.qt.app as qapp
    qapp.ImageInfoDialog.exec = grab_info
    win.show_info()
    qapp.ImageInfoDialog.exec = orig_info_exec
    heads = [r[0] for r in shown[0].rows] if shown else []
    check(shown and shown[0].rows[0][1] is not None and "Zellen" in heads[0]
          and shown[0].rows[-2][1] == "–",
          "Info shows proband × timepoint beside the image (border/size/excluded only for the image)")
    check(win.sidebar.det_cells.property("reached") is False and "/" in win.sidebar.det_cells.text(),
          f"the sidebar line shows cells vs. target, red below it ({win.sidebar.det_cells.text()})")

    # ── the progress window is big enough to read ──
    from erysreview.qt.tasks import progress_dialog
    pd = progress_dialog(win, "Export", "Export läuft … die gezählten Zellen jeder Gruppe werden geschrieben "
                                        "(Prä · Proband 01)")
    pd.setCancelButton(None); pd.show(); QTest.qWait(20)
    pd.grab().save(str(OUT / "qt_progress.png"))
    check(pd.width() >= 520 and pd.height() >= 150, f"the progress window is readable ({pd.width()}×{pd.height()})")
    pd.close()

    # ── export ──
    orig_exec = processing.ExportDialog.exec

    def accept_with_mark(d):
        d._preset("computed")
        d.mark.setChecked(True)
        d.grab().save(str(OUT / "qt_export.png"))
        return QDialog.Accepted
    processing.ExportDialog.exec = accept_with_mark
    warned = []
    win.export(confirm_under=lambda rows: warned.append(rows) or False)
    check(warned and all(n < t for _tp, _sub, n, t in warned[0]) and not (folder / "outputs").is_dir(),
          f"groups below the target are named before exporting; 'Abbrechen' exports nothing ({warned and warned[0][:2]})")
    win.export(confirm_under=lambda rows: True)
    processing.ExportDialog.exec = orig_exec
    check((folder / "outputs").is_dir() and any((folder / "outputs").rglob("*_summary.csv")),
          "export writes the group summaries")
    check(ReviewState.load(folder, "A_02_Prä_1").is_finished, "exported segmented images are marked finished")
    d2 = processing.ExportDialog(win, win.P, win.S, win.ctl.folder, win.ctl.grouped)
    exported = [d2.tree.topLevelItem(i).text(4) for i in range(d2.tree.topLevelItemCount())]
    check(any(t.startswith("✓") for t in exported) and d2.tree.headerItem().text(4) == "Exportiert",
          f"the 'Exportiert' column shows when a group was exported ({exported})")
    from erysreview.export import pending_preset
    pend = pending_preset(d2.rows, d2.states)
    check(not any(pend[k] for k, (_n, when) in d2.states.items() if when is not None)
          and all(d2.checked.values()),
          "'Fertig, nicht exportiert' skips exported groups; with none pending, all are pre-selected")

    win.close()
    tmp.cleanup()
    print(f"OK — {CHECKS} checks: batch, export, counted images, image info and the light theme "
          f"work (screenshots in tests/_out/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
