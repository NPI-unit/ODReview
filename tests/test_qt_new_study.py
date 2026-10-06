"""'Neue Studie' assistant (mockup v5), logic and Qt, off-screen.

Files named like ErysReview_01_Prä_06.10.26_02.tif (project, proband,
timepoint, date, image number) plus one with dashes. Logic: delimiters and
roles are guessed, the dash file is offered as a one-click fix. Qt: walk the
four steps with a template study, fix the dash file, take the measured
diameter from a (fake) test run, create the study — it is saved with the
template's types, opens in the picker's caller, and 'Jetzt im Hintergrund'
starts the batch beside the main window."""

from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
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
    from erysreview import study_setup as setup
    from erysreview.persistence import AppSettings, TypeDef
    from erysreview.profile import ProfileData, list_profiles, load_profile, save_profile

    today = dt.date.today().strftime("%d.%m.%y")
    folder = root / "ErysReview 2026"; folder.mkdir()
    names = [f"ErysReview_{s:02d}_{tp}_{today}_{k:02d}.tif"
             for s in (1, 2, 3) for tp in ("Prä", "Post") for k in (1, 2)]
    names.append(f"ErysReview-04-Prä-{today}-01.tif")
    for n in names:
        tifffile.imwrite(folder / n, np.full((48, 48), 140, dtype=np.uint8))

    # ── logic ──
    d = setup.detect_delimiters(names)
    check(d == ["_"], f"'_' is detected; the dots of the date are not a delimiter ({d})")
    roles = setup.guess_roles(names, d)
    check(roles == ["ignore", "subject", "timepoint", "ignore", "image_num"],
          f"roles: project ignored, proband, timepoint, date ignored, image number ({roles})")
    imgs = sorted(folder.glob("*.tif"))
    r = setup.read_result(imgs, d, roles)
    check(r.ok == 12 and r.unassigned == 1 and r.subjects == 3, f"12 of 13 read, 1 unassigned ({r})")
    check(setup.suggest_delimiter(imgs, d, roles) == "-", "adding '-' is suggested")
    rows = setup.preview_rows(imgs, d, roles)
    check(any(not x.ok for x in rows) and rows[0].ok, "the sample shows the unassigned file too")
    m = np.zeros((60, 60), np.int32); m[5:25, 5:25] = 1; m[30:50, 30:50] = 2
    check(abs(setup.measured_diameter(m) - 22.6) < 0.5, "the measured diameter is the equivalent circle")

    # ── Qt ──
    tpl = ProfileData(name="Vorlage")
    tpl.cell_types = [TypeDef(key="normal", name="Normal", color="#3B7DDD"),
                      TypeDef(key="echino", name="Echinozyt", color="#D9534F", hotkey="2")]
    tpl.cellpose.diameter = 30.0
    tpl.target_cells = 300
    save_profile(tpl)
    AppSettings(last_profile_name="Vorlage", language="de", theme="light").save()

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from erysreview.qt import new_study, study_picker
    from erysreview.qt.app import MainWindow, make_app
    app = make_app(["test"])
    win = MainWindow(); win.resize(1200, 760); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(150)
    OUT.mkdir(exist_ok=True)

    wiz = new_study.NewStudyWizard(win, win.P, win.S, win.settings, on_import=lambda: None)
    wiz.resize(1100, 720); wiz.show(); QTest.qWait(20)
    check(not wiz.btn_next.isEnabled(), "step 1: 'Weiter' waits for a folder")
    wiz.set_folder(folder)
    check(wiz.name_edit.text() == "ErysReview 2026" and "13" in wiz.scan_line.text(),
          f"choosing the folder counts the images and proposes its name ({wiz.scan_line.text()})")
    check(wiz.rb_template.isChecked() and wiz.template_combo.currentText() == "Vorlage",
          "the last study is the proposed template")
    wiz.grab().save(str(OUT / "qt_wiz_1.png"))
    wiz._next()
    check(wiz.step == 1 and wiz.chip_row.count() >= 5, f"step 2 shows one chip per name part ({wiz.step}, {wiz.chip_row.count()}, {wiz.roles})")
    check("1 nicht zugeordnet" in wiz.read_line.text() and wiz.fix_btn.isVisibleTo(wiz),
          f"the unassigned file is reported with a fix ({wiz.read_line.text()})")
    wiz.grab().save(str(OUT / "qt_wiz_2.png"))
    wiz._apply_fix()
    check("0 nicht zugeordnet" in wiz.read_line.text() and "-" in wiz.delims, "one click assigns it")
    wiz.set_role(3, "subject")                  # a wrong click: the date as proband …
    check(wiz.roles.count("subject") == 1 and wiz.roles[1] == "ignore", "…moves the role, never doubles it")
    wiz.set_role(1, "subject"); wiz.set_role(3, "ignore")
    wiz._next()
    check(wiz.step == 2 and [t.name for t in wiz.work.cell_types] == ["Normal", "Echinozyt"],
          "step 3 starts from the template's cell types")
    wiz.show_probe(imgs[0], np.full((60, 60), 140, np.uint8), m, 3.2)
    check(wiz.take_diam.isVisibleTo(wiz), "the test run offers the measured diameter")
    wiz._take_diameter()
    check(wiz.work.cellpose.diameter == 23, f"…and takes it ({wiz.work.cellpose.diameter})")
    wiz._add_type(); QTest.qWait(20)
    wiz.grab().save(str(OUT / "qt_wiz_3.png"))
    wiz._next()
    check(wiz.step == 3 and "4 Probanden" in wiz.sum_rows["wiz_sum_groups"].text()
          and "Echinozyt" in wiz.sum_rows["wiz_sum_types"].text(), f"the summary lists what was chosen ({wiz.step} {wiz.sum_rows['wiz_sum_groups'].text()} | {wiz.sum_rows['wiz_sum_types'].text()})")
    wiz.grab().save(str(OUT / "qt_wiz_4.png"))
    wiz._go(1)
    check(wiz.step == 1 and wiz.btn_next.isEnabled(), "'ändern' / the step list jumps back")
    wiz._go(3)
    wiz._finish()
    p = load_profile("ErysReview 2026")
    check(p is not None and p.import_folder == str(folder) and p.delimiters == ["_", "-"]
          and len(p.cell_types) == 3 and p.cellpose.diameter == 23 and p.target_cells == 300,
          "the study is saved with folder, reading, types and values")
    check(load_profile("Vorlage").cellpose.diameter == 30.0 and len(load_profile("Vorlage").cell_types) == 2,
          "the template itself is untouched")

    # picker → wizard → main window, segmenting in the background
    started = []
    orig_exec = new_study.NewStudyWizard.exec

    def fake_exec(w):
        w.set_folder(folder); w.name_edit.setText("Zweite")
        w._next(); w._apply_fix(); w._next(); w._next(); w._finish()
        return QDialog.Accepted
    new_study.NewStudyWizard.exec = fake_exec
    orig_picker = study_picker.StudyPicker.exec

    def picker_exec(pk):
        pk._new()
        return pk.result()
    study_picker.StudyPicker.exec = picker_exec
    win.ctl.check_model = lambda: None
    from erysreview.qt import processing
    processing.BatchDialog._start = lambda d: started.append(d)
    win.switch_study()
    new_study.NewStudyWizard.exec = orig_exec; study_picker.StudyPicker.exec = orig_picker
    QTest.qWait(50)
    check(win.ctl.profile.name == "Zweite" and len(win.ctl.images) == 13, "the new study opens with its folder")
    check(started and started[0].isVisible() and not started[0].isModal(),
          "'Jetzt im Hintergrund' starts the batch beside the main window")
    started[0].close()

    win.close()
    from erysreview.qt.tasks import shutdown
    shutdown(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: the 'Neue Studie' assistant reads the names, takes over a template "
          f"and opens the study (screenshots in tests/_out/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
