"""Phase 1: the Qt main window, driven like a user, off-screen.

Throw-away app-support folder and a tiny study (as in test_app_smoke): open
the folder, step with the arrow keys, edit cells, undo, mark finished, switch
tools by key — and check that a `1` typed into the remark box stays text (K9).
Also runs a Task through the QThread runner and renders screenshots of both
toolbar layouts to tests/_out/ for a visual check.
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
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, save_profile

    folder = root / "study"; folder.mkdir()
    rng = np.random.default_rng(0)
    for name in ("A_01_Prä_1.tif", "A_01_Prä_2.tif", "A_02_Prä_1.tif"):
        tifffile.imwrite(folder / name, (rng.random((80, 80)) * 60 + 150).astype(np.uint8))
    prof = ProfileData(name="QtTest")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    prof.margin_n = 2
    prof.size_filter.enabled = False
    masks = np.zeros((80, 80), dtype=np.int32)
    masks[10:24, 10:24] = 1
    masks[40:54, 40:54] = 2
    npy, meta = cache_paths(folder, folder / "A_01_Prä_1.tif")
    npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy, masks)
    meta.write_text(json.dumps(asdict(prof.cellpose)))
    save_profile(prof)
    AppSettings(last_profile_name="QtTest", language="de", theme="light").save()

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from erysreview.qt.app import MainWindow, make_app
    from erysreview.qt.tasks import run_task_modal
    from erysreview.tasks import Task

    app = make_app(["test"])
    win = MainWindow()
    win.resize(1400, 820)
    win.show()
    QTest.qWaitForWindowExposed(win)
    QTest.qWait(200)                      # the deferred folder open
    ctl = win.ctl

    check(ctl.profile.name == "QtTest" and len(ctl.images) == 3, "the last study and its folder open")
    check(win.canvas.masks is not None and win.canvas.masks.max() == 2, "the segmented image shows its masks")
    cv0 = win.canvas
    cv0._is_zoomed_in = lambda: True
    check(not win.settings.show_cell_numbers and not cv0._numbers_shown(),
          "cell numbers are off by default, even zoomed in")
    win.settings.show_cell_numbers = True; win._on_local_setting("show_cell_numbers")
    check(cv0._numbers_shown() and any(a.get_visible() for a in cv0._number_artists.values()),
          "Einstellungen › Bedienung turns them on at once")
    win.settings.show_cell_numbers = False; win._on_local_setting("show_cell_numbers")
    check(not any(a.get_visible() for a in cv0._number_artists.values()), "…and off again")
    del cv0._is_zoomed_in
    check(win.empty_btn.isHidden(), "the empty-state button is gone once images show")
    check("[1/3]" in win.status_label.text(), "the status line counts the images")
    from PySide6.QtGui import QAction, QShortcut
    bare = {sc.key().toString() for sc in win.findChildren(QShortcut)}
    menu_keys = {k.toString() for a in win.findChildren(QAction) for k in a.shortcuts()}
    check(not (bare & menu_keys), f"no menu shortcut collides with a tool key ({bare & menu_keys})")
    groups = win.sidebar._group_items
    check(set(groups) == {("Prä", "01"), ("Prä", "02")}, "the sidebar shows timepoint › subject")

    QTest.keyClick(win, "2")
    check(win.settings.active_tool == "remove", "key 2 switches to Zählen ein/aus")
    check(win.canvas._act_remove_toggle(16, 16), "a click switches a cell off")
    check(ReviewState.load(folder, "A_01_Prä_1").force_excluded == [1], "and it is saved at once")
    QTest.keyClick(win, "z")
    check(ReviewState.load(folder, "A_01_Prä_1").force_excluded == [], "z undoes it")

    QTest.keyClick(win, "f")
    check(ReviewState.load(folder, "A_01_Prä_1").is_finished, "f marks the image finished")
    QTest.keyClick(win, Qt.Key_Right)
    check(ctl.idx == 1 and win.canvas.masks.max() == 0, "→ steps to the next (raw) image")
    QTest.keyClick(win, Qt.Key_Left)
    check(ctl.idx == 0, "← steps back")

    win._on_cell_note(2)
    check(win.notebox.visible and win.settings.active_tool == "remove",
          "a remark opens the floating box")
    win.notebox.text.setFocus()
    QTest.keyClicks(win.notebox.text, "1 Zelle")
    check(win.notebox.text.toPlainText() == "1 Zelle" and win.settings.active_tool == "remove",
          "typing '1' into the remark stays text and does not switch tools (K9)")
    win.notebox._on_primary()
    check(ReviewState.load(folder, "A_01_Prä_1").notes == {"2": "1 Zelle"}, "the remark is saved")
    win.notebox.request_close()

    OUT.mkdir(exist_ok=True)
    win.grab().save(str(OUT / "qt_grouped.png"))
    win.settings.toolbar_layout = "two_row"
    win.toolbar.rebuild()
    QTest.qWait(50)
    check(len(win.toolbar.chips) == len(prof.cell_types), "layout C shows the cell types as chips")
    win.grab().save(str(OUT / "qt_two_row.png"))
    win._show_study_card(win.toolbar.btn_study)
    QTest.qWait(50)
    cards = [w for w in app.topLevelWidgets() if type(w).__name__ == "StudyCard" and w.isVisible()]
    check(len(cards) == 1, "the study button opens the study card")
    cards[0].grab().save(str(OUT / "qt_study_card.png"))
    cards[0].close()

    seen = []

    def work(progress, cancelled):
        for i in range(5):
            progress(i, f"{i}")
            seen.append(i)
        return "fertig"
    res = run_task_modal(win, Task(work=work), "Test", "läuft", maximum=5)
    check(res == "fertig" and seen == list(range(5)),
          f"a Task runs on a QThread and returns its result ({res!r}, {seen})")
    try:
        run_task_modal(win, Task(work=lambda p, c: 1 / 0), "Test", "läuft")
        check(False, "errors propagate")
    except ZeroDivisionError:
        check(True, "a worker exception is re-raised on the GUI thread")

    # right button: drag moves the image, a right click opens no remark
    from types import SimpleNamespace
    cv = win.canvas
    cv.reset_zoom()
    ax = cv.ax
    (px, py) = ax.transData.transform((30, 30))
    ev = lambda kind, x, y: SimpleNamespace(button=3, inaxes=ax, x=x, y=y,
                                            xdata=ax.transData.inverted().transform((x, y))[0],
                                            ydata=ax.transData.inverted().transform((x, y))[1])
    notes, menus = [], []
    cv.note_callback = notes.append
    cv.context_callback = lambda: menus.append(1)        # the image menu (brightness) — not opened here
    before = ax.get_xlim()
    cv._on_button_press(ev("press", px, py))
    cv._on_motion(ev("move", px - 40, py))
    cv._on_button_release(ev("release", px - 40, py))
    check(ax.get_xlim() != before and not notes and not win.notebox.visible,
          "right-drag moves the image; no remark opens")
    check(not menus, "…and a drag opens no menu")
    cv._on_button_press(ev("press", px, py)); cv._on_button_release(ev("release", px, py))
    check(not notes and menus == [1], "a plain right click opens the image menu, never a remark")
    cv.reset_zoom()

    # helper grid (exploratory): G cycles, choice is kept per computer
    win.cycle_grid()
    from erysreview.persistence import AppSettings as _AS
    check(win.canvas.grid_n == 2 and _AS.load().grid_n == 2 and len(win.canvas._grid_artists) == 2
          and win._grid_actions[2].isChecked(), "the helper grid shows 2 × 2 fields (lines only, no labels over cells)")
    win.grab().save(str(OUT / "qt_grid.png"))
    win.show_image(win.ctl.idx)
    check(len(win.canvas._grid_artists) == 2, "it stays when the image changes")
    win.set_grid(0)
    check(not win.canvas._grid_artists and not _AS.load().grid_n, "…and switches off")

    win.close()
    check(AppSettings.load().active_tool == "remove", "closing saves the settings")
    tmp.cleanup()
    print(f"OK — {CHECKS} checks: the Qt main window works end to end "
          f"(screenshots in tests/_out/).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
