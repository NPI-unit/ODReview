"""Language, theme and text size switch live (no restart), off-screen.

Opens a study, changes the theme in Rechner › Allgemein: the main window is
rebuilt around the same session (same image, same controller), the settings
window comes back on the same page with unsaved study edits intact, and the
application palette — which also colours combobox popups and menus — follows.
Then English, a bigger text size, and 'Wie System'."""

from __future__ import annotations

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
    from erysreview.persistence import AppSettings
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    for k in (1, 2, 3):
        tifffile.imwrite(folder / f"ErysReview_01_Prä_06.10.26_0{k}.tif", np.full((40, 40), 140, dtype=np.uint8))
    prof = ProfileData(name="Look")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "ignore", "image_num"]
    prof.import_folder = str(folder)
    save_profile(prof)
    AppSettings(last_profile_name="Look", language="de", theme="light").save()

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QPalette
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from erysreview.qt.app import MainWindow, make_app
    from erysreview.qt.settings import SettingsDialog
    from erysreview.theme import PALETTES

    app = make_app(["test"])
    win = MainWindow(); win.resize(1200, 760); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    win.show_image(2)
    ctl = win.ctl
    light_base = app.palette().color(QPalette.Base).name().upper()
    check(light_base == PALETTES["light"]["panel_bg"].upper(), "starts with the light palette")

    seen: list = []

    def drive():
        dlg = next(w for w in QApplication.topLevelWidgets() if isinstance(w, SettingsDialog) and w.isVisible())
        if not seen:
            dlg.work.target_cells = 123          # an unsaved study edit
            dlg._changed()
            dlg.show_page("general")
            seen.append("first")
            dlg._local("theme", "dark")          # rebuilds; the dialog closes and comes back
            QTimer.singleShot(50, drive)
        else:
            seen.append((dlg.current_page(), dlg.work.target_cells, dlg.has_changes()))
            dlg.grab().save(str(OUT / "qt_look_dark_settings.png"))
            dlg.done(0)

    OUT.mkdir(exist_ok=True)
    QTimer.singleShot(50, drive)
    win.open_settings("images")
    QTest.qWait(300)
    wins = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
    check(len(wins) == 1 and wins[0] is not win, "the main window was rebuilt (one window left)")
    new = wins[0]
    check(new.ctl is ctl and new.ctl.idx == 2, "…around the same session, on the same image")
    check(app.palette().color(QPalette.Base).name().upper() == PALETTES["dark"]["panel_bg"].upper()
          and new.P is PALETTES["dark"], "the palette is dark now (popups and menus follow it)")
    check(len(seen) == 2 and seen[1] == ("general", 123, True),
          f"the settings window came back on 'Allgemein' with the unsaved edit ({seen[1:]})")
    check(AppSettings.load().theme == "dark", "the choice is saved")
    new.grab().save(str(OUT / "qt_look_dark.png"))

    new.settings.language = "en"; new.settings.font_scale = 1.25
    base = app._base_point_size
    newer = new.restyle(); QTest.qWait(100)
    check(newer.menuBar().actions()[0].text() == "File", f"English menus at once ({newer.menuBar().actions()[0].text()})")
    check(abs(app.font().pointSizeF() - base * 1.25) < 0.01, "text size applies at once")

    newer.settings.theme = "system"
    newest = newer.restyle(); QTest.qWait(50)
    from erysreview.theme import resolve_theme
    check(resolve_theme("system") in ("light", "dark") and newest.P is PALETTES[resolve_theme("system")],
          "'Wie System' resolves to the system's scheme")
    newest.close(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: language, theme and text size switch without a restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
