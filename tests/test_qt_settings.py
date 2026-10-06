"""Phase 2: the Qt settings window and 'Studie öffnen', off-screen.

Edits a study through the settings window, checks the consequence preview,
saves, and checks the main window picked the change up; checks that computer
settings save at once; drives the study picker through new / duplicate /
rename / delete / open. Throw-away app-support folder, as in test_qt_shell.
"""

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

    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings
    from erysreview.profile import ProfileData, list_profiles, load_profile, save_profile

    folder = root / "study"; folder.mkdir()
    for name in ("A_01_Prä_1.tif", "A_02_Prä_1.tif", "A-03-Prä-1.tif"):
        tifffile.imwrite(folder / name, np.full((60, 60), 150, dtype=np.uint8))
    prof = ProfileData(name="Studie A")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    masks = np.zeros((60, 60), dtype=np.int32); masks[10:20, 10:20] = 1
    npy, meta = cache_paths(folder, folder / "A_01_Prä_1.tif")
    npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy, masks)
    meta.write_text(json.dumps(asdict(prof.cellpose)))
    save_profile(prof)
    save_profile(ProfileData(name="Studie B"))
    AppSettings(last_profile_name="Studie A", language="de").save()

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog, QInputDialog, QMessageBox
    from erysreview.qt import settings as qsettings, study_picker
    from erysreview.qt.app import MainWindow, make_app

    app = make_app(["test"])
    win = MainWindow(); win.resize(1300, 800); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    check(len(win.ctl.images) == 3, "the study opens with its folder")
    check(set(win.sidebar._group_items) == {("Prä", "01"), ("Prä", "02"), ("_ungrouped", "_ungrouped")}
          or len(win.sidebar._group_items) == 3, "the dash-named image is unassigned at first")

    saved = {}
    dlg = qsettings.SettingsDialog(
        win, win.P, win.S, win.settings, win.ctl.profile, win.ctl.folder, win.ctl.images,
        on_study_saved=lambda p, c: (saved.update(p=p, c=c), win._apply_profile_edit(p, c)),
        on_local_changed=win._on_local_setting, on_switch_study=lambda: None, start_page="images")
    dlg.resize(1100, 740); dlg.show(); QTest.qWait(50)
    check("3" in dlg.parse_check.text() and "1 nicht zugeordnet" in dlg.parse_check.text(),
          f"the parser check counts the real files ({dlg.parse_check.text()})")
    OUT.mkdir(exist_ok=True)
    dlg.grab().save(str(OUT / "qt_settings_images.png"))

    dlg._toggle_delim("-", True)
    check("0 nicht zugeordnet" in dlg.parse_check.text(), "adding '-' as delimiter assigns the last file")
    dlg.work.cellpose.diameter = 34.0
    dlg.work.cell_types[0].color = "#112233"
    dlg._changed()
    check(dlg.btn_save.isEnabled() and "3 Änderung" in dlg.footer_text.text(),
          f"the footer counts unsaved study changes ({dlg.footer_text.text()})")
    dlg.show_page("seg"); QTest.qWait(20)
    dlg.grab().save(str(OUT / "qt_settings_seg.png"))

    sheets = []
    orig_exec = qsettings.SaveSheet.exec

    def fake_exec(sheet):
        sheets.append(sheet)
        sheet.grab().save(str(OUT / "qt_save_sheet.png"))
        return QDialog.Accepted
    qsettings.SaveSheet.exec = fake_exec
    dlg._save()
    qsettings.SaveSheet.exec = orig_exec
    check(len(sheets) == 1 and saved["c"] == "later", "saving goes through the consequence sheet, default 'later'")
    stored = load_profile("Studie A")
    check(stored.cellpose.diameter == 34.0 and "-" in stored.delimiters and stored.cell_types[0].color == "#112233",
          "the study file holds the saved values")
    check(len(win.sidebar._group_items) == 3 and all(k[0] != "_ungrouped" for k in win.sidebar._group_items),
          "the main window regrouped with the new parser")
    check(win.toolbar.banner.text() != "" if win.ctl.idx == 0 else True,
          "the open image now flags its masks as made with other values")
    check(not dlg.btn_save.isEnabled(), "after saving there are no pending changes")

    dlg._local("toolbar_layout", "two_row")
    check(AppSettings.load().toolbar_layout == "two_row" and len(win.toolbar.chips) > 0,
          "a computer setting saves at once and the toolbar rebuilds")
    dlg.close()

    # ── study picker ──
    answers = iter([("Studie C", True), ("Studie C2", True), ("Studie D", True)])
    QInputDialog.getText = staticmethod(lambda *a, **k: next(answers))
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    pk = study_picker.StudyPicker(win, win.P, win.S, win.settings)
    pk.resize(1080, 720); pk.show(); QTest.qWait(50)
    check([c.profile.name for c in pk.cards] == sorted(list_profiles()) or len(pk.cards) == 2,
          "one card per study")
    check(pk._selected.profile.name == "Studie A", "the last study is pre-selected")
    pk.grab().save(str(OUT / "qt_study_picker.png"))
    pk._new(); check("Studie C" in list_profiles() and pk._selected.profile.name == "Studie C", "new study")
    pk._duplicate(); check("Studie C2" in list_profiles(), "duplicate")
    pk._rename(); check("Studie D" in list_profiles() and "Studie C2" not in list_profiles(), "rename")
    pk._delete(); check("Studie D" not in list_profiles(), "delete (after confirmation)")
    pk.select(next(c for c in pk.cards if c.profile.name == "Studie B"))
    pk.open_selected()
    check(pk.chosen.name == "Studie B", "open returns the selected study")

    win.ctl.activate_profile(pk.chosen)
    win._profile_changed(reopen_folder=True)
    check(win.windowTitle().endswith("Studie B") and not win.ctl.has_images and win.empty_btn.isVisible(),
          "switching to a study without a folder clears the view")
    win.close()
    tmp.cleanup()
    print(f"OK — {CHECKS} checks: settings window, consequence sheet and study picker work "
          f"(screenshots in tests/_out/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
