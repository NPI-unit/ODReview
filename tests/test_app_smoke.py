"""End-to-end smoke test of the Tk app on top of the Phase-0 split.

Builds the real main window against a throw-away app-support folder and a
tiny study, then drives it the way a user would: open the folder, step
through images, edit cells on the canvas, undo, mark finished, export, close.
Proves that EryApp, AppController, ReviewEditor and the task runner still
work together after the refactor — and that nothing touches the user's real
settings or studies (app_support_dir is redirected before anything loads).
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
import tifffile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    support = root / "support"
    support.mkdir()

    from erysreview import persistence, profile as profile_mod
    persistence.app_support_dir = lambda: support
    profile_mod.app_support_dir = lambda: support

    from erysreview.cellpose_runner import cache_paths
    from erysreview.persistence import AppSettings, ReviewState
    from erysreview.profile import ProfileData, save_profile

    # Study folder: three images, the first one already segmented.
    folder = root / "study"; folder.mkdir()
    for name in ("A_01_Prä_1.tif", "A_01_Prä_2.tif", "A_02_Prä_1.tif"):
        tifffile.imwrite(folder / name, np.full((60, 60), 120, dtype=np.uint8))
    prof = ProfileData(name="Smoke")
    prof.use_simple_parser, prof.delimiters = True, ["_"]
    prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    prof.import_folder = str(folder)
    prof.margin_n = 2
    prof.size_filter.enabled = False
    masks = np.zeros((60, 60), dtype=np.int32)
    masks[10:22, 10:22] = 1
    masks[30:42, 30:42] = 2
    npy, meta = cache_paths(folder, folder / "A_01_Prä_1.tif")
    npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy, masks)
    meta.write_text(json.dumps(asdict(prof.cellpose)))
    save_profile(prof)
    s = AppSettings(last_profile_name="Smoke", language="de")
    s.save()

    from erysreview.app import EryApp
    import erysreview.app as app_mod
    # Modal info dialogs would block the run; record them instead.
    shown = []
    app_mod.info_dialog = lambda _p, title, msg: shown.append((title, msg))

    app = EryApp()
    app.root.update()
    check(app.profile.name == "Smoke", "the last study is loaded at startup")
    app._open_folder(folder, restore_index=False)
    app.root.update()
    check(len(app.images) == 3 and app.idx == 0, "the study folder opens")
    check(app.canvas.masks is not None and app.canvas.masks.max() == 2,
          "the segmented image shows its masks")
    check("2" in app.status_label.cget("text"), "the status line reports the cells")

    cv = app.canvas
    check(cv._act_remove_toggle(16, 16), "switching a cell off on the canvas works")
    check(ReviewState.load(folder, "A_01_Prä_1").force_excluded == [1],
          "and is saved immediately")
    app._set_active_tool("type")
    check(cv._act_type(36, 36), "setting a type works")
    check(cv._act_add(50, 10), "adding a manual cell works")
    app._on_undo(); app._on_undo()
    rv = ReviewState.load(folder, "A_01_Prä_1")
    check(rv.types == {} and rv.manual_cells == [] and rv.force_excluded == [1],
          "undo reverts the last two edits and saves")

    app._on_toggle_finished()
    check(ReviewState.load(folder, "A_01_Prä_1").is_finished, "f marks the image finished")
    app._step_image(+1); app.root.update()
    check(app.idx == 1 and cv.masks.max() == 0, "stepping on shows the raw next image")
    check(app.ctl.folder_state.last_index == 1, "the folder remembers the position")

    out = app.ctl.export_task([("Prä", "01")])
    from erysreview.tasks import run_sync
    result = run_sync(out)
    check(Path(result).is_dir() and (folder / "outputs").is_dir(), "an export task writes outputs/")

    app._on_close()
    check(AppSettings.load().active_tool == "type", "closing saves the settings")
    check(not (Path.home() / "Library/Application Support/Erys/profiles/Smoke.json").exists(),
          "nothing was written to the real app-support folder")
    tmp.cleanup()
    print(f"OK — {CHECKS} checks: the Tk app runs end to end on controller, editor and tasks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
