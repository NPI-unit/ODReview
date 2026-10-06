"""Own shortcuts (Rechner › Tastenkürzel): registry rules and the Qt page.

Logic: defaults are unique, spellings normalise, only differences are stored,
a broken settings file falls back to unique bindings. Qt (off-screen): bind by
click + key press, refuse a key another action owns, bind the mouse's Back
button, and check the new bindings act at once in the main window."""

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

from erysreview import shortcuts as sc

CHECKS = 0
OUT = Path(__file__).resolve().parent / "_out"


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def logic() -> None:
    d = sc.effective({})
    check(not sc.conflicts(d), "the default bindings are unique")
    check(sc.normalize("shift+cmd+o") == "Ctrl+Shift+O" and sc.normalize("escape") == "Esc",
          "spellings normalise (cmd → Ctrl, escape → Esc)")
    m = sc.effective({"resegment": ["X"]})
    check(m["resegment"] == ["X"] and sc.overrides_from(m) == {"resegment": ["X"]},
          "only the differences from the defaults are stored")
    check(sc.owner_of(d, "f") == "toggle_finished" and sc.owner_of(d, "F", except_id="toggle_finished") is None,
          "owner_of finds who has a key")
    mapping, reset = sc.resolve({"resegment": ["F"]})       # F already belongs to 'fertig'
    check(not sc.conflicts(mapping) and "resegment" in reset and mapping["resegment"] == ["R"],
          "a clashing stored binding falls back to its default")
    check(sc.effective({"x_unknown": ["Q"], "undo": "nope"}) == d, "junk in the settings file is ignored")


def qt() -> None:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    support = root / "support"; support.mkdir()
    from erysreview import persistence, profile as profile_mod
    persistence.app_support_dir = lambda: support
    profile_mod.app_support_dir = lambda: support
    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    for k in (1, 2, 3):
        tifffile.imwrite(folder / f"A_01_d0_{k}.tif", np.full((40, 40), 140, dtype=np.uint8))
    prof = ProfileData(name="Keys")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    save_profile(prof)
    # A broken settings file: R for 'fertig' collides with 'Cellpose'.
    AppSettings(last_profile_name="Keys", language="de", theme="light",
                shortcuts={"toggle_finished": ["R"]}).save()

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from erysreview.qt import settings as qsettings
    from erysreview.qt.app import MainWindow, _ActivityFilter, make_app

    app = make_app(["test"])
    win = MainWindow(); win.resize(1200, 760); win.show()
    filt = _ActivityFilter(win.ctl, win); app.installEventFilter(filt)
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    check(win._mapping["toggle_finished"] == ["F"] and AppSettings.load().shortcuts == {},
          "a clashing binding from the settings file is reset at startup")

    dlg = qsettings.SettingsDialog(win, win.P, win.S, win.settings, win.ctl.profile, win.ctl.folder,
                                   win.ctl.images, lambda p, c: None, win._on_local_setting, lambda: None,
                                   start_page="keys")
    dlg.resize(1100, 740); dlg.show(); QTest.qWait(30)
    OUT.mkdir(exist_ok=True)
    dlg.grab().save(str(OUT / "qt_shortcuts.png"))

    btn = dlg.key_buttons[("resegment", 0)]
    btn.click(); check(btn.capturing, "clicking a field waits for a key")
    QTest.keyClick(btn, Qt.Key_F)
    check("F" not in dlg._keys["resegment"] and "fertig" in dlg.keys_msg.text().lower(),
          f"a key another action owns is refused, naming the owner ({dlg.keys_msg.text()!r})")
    btn = dlg.key_buttons[("resegment", 0)]
    btn.click(); QTest.keyClick(btn, Qt.Key_N, Qt.ControlModifier)
    check(dlg._keys["resegment"] == ["Ctrl+N"] and AppSettings.load().shortcuts.get("resegment") == ["Ctrl+N"],
          "Ctrl+N is captured and saved for this computer")

    # mouse Back → 'Cellpose' slot 2 is free; bind Back to 'vorheriges Bild' is default already:
    btn2 = dlg.key_buttons[("toggle_finished", 1)]
    btn2.click()
    ev = QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), QPointF(5, 5), Qt.BackButton,
                     Qt.BackButton, Qt.NoModifier)
    btn2.mousePressEvent(ev)
    check("Mouse:Back" not in dlg._keys["toggle_finished"] and "Vorheriges" in dlg.keys_msg.text(),
          "the mouse Back button is refused while 'Vorheriges Bild' owns it")
    dlg._assign("prev_image", 1, "")                      # free Back
    btn2 = dlg.key_buttons[("toggle_finished", 1)]
    btn2.click(); btn2.mousePressEvent(ev)
    check(dlg._keys["toggle_finished"] == ["F", "Mouse:Back"], "…and accepted once it is free")
    check(not sc.conflicts(dlg._keys), "after all edits every binding is still unique")
    dlg.close()

    # The main window follows at once.
    win.activateWindow(); QTest.qWait(30)
    from erysreview.persistence import ReviewState
    win.show_image(0)
    before = ReviewState.load(folder, win.ctl.current_path.stem).is_finished
    app.sendEvent(win, QMouseEvent(QEvent.MouseButtonPress, QPointF(50, 50), QPointF(50, 50),
                                   Qt.BackButton, Qt.BackButton, Qt.NoModifier)) \
        if QApplication.activeWindow() is win else win.run_action(win._mouse_map["Mouse:Back"])
    check(ReviewState.load(folder, win.ctl.current_path.stem).is_finished != before,
          "the mouse Back button now toggles 'fertig'")
    keys_now = {k.toString() for a in win._qactions.values() for k in a.shortcuts()} | \
        {o.key().toString() for o in win._shortcut_objs}
    check("R" not in keys_now and "Ctrl+N" in keys_now,
          "the main window rebound at once: R is free, Ctrl+N re-runs Cellpose")

    win.close(); tmp.cleanup()


def main() -> int:
    logic()
    qt()
    print(f"OK — {CHECKS} checks: shortcuts are unique, rebindable per computer, mouse side "
          f"buttons included.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
