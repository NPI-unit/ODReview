"""Helligkeit & Kontrast (display only), off-screen.

The formula and the automatic stretch; then a study of bright images: select
two in the list, apply through the big window — reviews get `display`, the open
image darkens, ☀︎ marks them, the counts and masks do not change, the value
survives the next save of the open image (in-memory review), it travels in a
package, scope 'whole proband' / 'all' reach the right images, and a right
click without dragging opens the image menu (a drag does not)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

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
    from erysreview import display_adjust as da
    check(da.clean({"brightness": 0, "contrast": 100, "gamma": 1}) is None, "default values count as 'nothing changed'")
    check(da.clean({"brightness": 500, "contrast": 1, "gamma": 9}) == {"brightness": 100, "contrast": 50, "gamma": 2.5},
          "values are clamped to their ranges")
    bright = np.linspace(0.85, 0.98, 4000, dtype=np.float32).reshape(40, 100)
    a = da.auto(bright); r = da.apply(bright, a)
    check(float(r.min()) < 0.05 and float(r.max()) > 0.95, f"'Automatisch' stretches an over-exposed image ({a})")

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
    prof = ProfileData(name="Hell")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "ignore", "image_num"]
    prof.import_folder = str(folder); prof.size_filter.enabled = False
    names = []
    rng = np.random.default_rng(4)
    for sub in ("01", "02"):
        for k in (1, 2):
            n = f"ErysReview_{sub}_Prä_07.10.26_0{k}.tif"; names.append(n)
            img = (225 + rng.integers(0, 25, (90, 90))).astype(np.uint8)
            img[20:40, 20:40] = 205
            tifffile.imwrite(folder / n, img)
            m = np.zeros((90, 90), np.int32); m[20:40, 20:40] = 1; m[50:70, 50:70] = 2
            npy, meta = cache_paths(folder, folder / n); npy.parent.mkdir(parents=True, exist_ok=True)
            np.save(npy, m); meta.write_text(json.dumps(asdict(prof.cellpose)))
            ReviewState(is_finished=True).save(folder, Path(n).stem)
    save_profile(prof)
    AppSettings(last_profile_name="Hell", language="de", theme="light").save()

    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialog
    from erysreview.qt import brightness
    from erysreview.qt.app import MainWindow, make_app
    from erysreview.controller import status_counts
    app = make_app(["t"])
    win = MainWindow(); win.resize(1300, 800); win.show()
    QTest.qWaitForWindowExposed(win); QTest.qWait(200)
    win.show_image(0)
    counts_before = status_counts(win.canvas.cell_infos, win.ctl.review, win.ctl.profile,
                                  win.canvas.border_ids, win.canvas.duplicate_ids)
    mean_before = float(win.canvas.image_norm.mean())
    masks_before = win.canvas.masks.copy()

    # select two images in the list
    sb = win.sidebar
    for i in (0, 1):
        sb._item_by_idx[i].setSelected(True)
    check(sb.selected_indices() == [0, 1], "two images selected in the list")

    seen = {}
    real_exec = brightness.BrightnessDialog.exec

    def drive(dlg):
        seen["dlg"] = dlg
        scr = dlg.screen().availableGeometry()
        seen["big"] = dlg.width() >= scr.width() * 0.5 and dlg.height() >= scr.height() * 0.5
        dlg.sliders["brightness"][0].setValue(-40)
        dlg.sliders["contrast"][0].setValue(220)
        QTest.qWait(60)
        seen["pix"] = not dlg.pix.pixmap().isNull()
        dlg.grab().save(str(OUT / "qt_brightness.png"))
        dlg.apply()
        return QDialog.Accepted
    brightness.BrightnessDialog.exec = drive
    OUT.mkdir(exist_ok=True)
    win.adjust_display(sb.selected_indices())
    check(seen.get("big") and seen.get("pix"), "the window is at least half the screen and shows the preview")
    d0 = ReviewState.load(folder, Path(names[0]).stem).display
    d1 = ReviewState.load(folder, Path(names[1]).stem).display
    d2 = ReviewState.load(folder, Path(names[2]).stem).display
    check(d0 == d1 == {"brightness": -40, "contrast": 220, "gamma": 1.0} and d2 is None,
          "applied to exactly the two selected images")
    check(float(win.canvas.image_norm.mean()) < mean_before - 0.05, "the open image is shown darker at once")
    rows = [sb._item_by_idx[i].text(0) for i in (0, 1, 2)]
    check("☀︎" in rows[0] and "☀︎" in rows[1] and "☀︎" not in rows[2], "☀︎ marks the adjusted images")
    counts_after = status_counts(win.canvas.cell_infos, win.ctl.review, win.ctl.profile,
                                 win.canvas.border_ids, win.canvas.duplicate_ids)
    check(counts_after == counts_before and (win.canvas.masks == masks_before).all(),
          "counts and masks are unchanged (display only)")
    win.ctl.flush_time(); win._save_review()
    check(ReviewState.load(folder, Path(names[0]).stem).display == d0,
          "the next save of the open image keeps it (in-memory review updated)")

    # whole proband / all images
    def drive_scope(scope):
        def f(dlg):
            for b in dlg.scope.buttons():
                if b.property("scope") == scope:
                    b.setChecked(True)
            dlg.sliders["gamma"][0].setValue(150)
            dlg.apply()
            return QDialog.Accepted
        return f
    brightness.BrightnessDialog.exec = drive_scope("all")
    win.adjust_display([2])
    check(all(ReviewState.load(folder, Path(n).stem).display for n in names), "'alle Bilder' reaches every image")
    win.reset_display(list(range(4)))
    check(not any(ReviewState.load(folder, Path(n).stem).display for n in names), "'Anzeige zurücksetzen' clears it")
    brightness.BrightnessDialog.exec = drive_scope("group")
    win.adjust_display([2])
    got = [bool(ReviewState.load(folder, Path(n).stem).display) for n in names]
    check(got == [False, False, True, True], f"'ganzer Proband' reaches only that proband ({got})")
    brightness.BrightnessDialog.exec = real_exec

    # travels in a package
    from erysreview import package as pkg
    zp = root / "p.zip"
    pkg.create_package(folder, win.ctl.grouped, prof, zp, include_images=False, reviewer="Finn")
    with zipfile.ZipFile(zp) as z:
        rv = json.loads(z.read(f"cache/{Path(names[3]).stem}_review.json"))
    check(rv.get("display", {}).get("gamma") == 1.5, "the setting travels in the package")

    # right click without dragging opens the image menu; a drag does not
    hits = []
    win.canvas.context_callback = lambda: hits.append(1)
    ax = win.canvas.ax
    px, py = ax.transData.transform((30, 30))
    ev = lambda x, y: SimpleNamespace(button=3, inaxes=ax, x=x, y=y,
                                      xdata=ax.transData.inverted().transform((x, y))[0],
                                      ydata=ax.transData.inverted().transform((x, y))[1])
    win.canvas._on_button_press(ev(px, py)); win.canvas._on_button_release(ev(px, py))
    check(hits == [1], "a right click (no drag) on the image opens its menu")
    win.canvas._on_button_press(ev(px, py)); win.canvas._on_motion(ev(px - 50, py))
    win.canvas._on_button_release(ev(px - 50, py))
    check(hits == [1], "a right-drag only moves the image")

    win.close(); tmp.cleanup()
    print(f"OK — {CHECKS} checks: brightness & contrast change the display only, per image, and travel in packages.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
