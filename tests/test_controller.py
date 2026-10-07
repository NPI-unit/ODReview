"""AppController: the main window's state and workflows, headless.

A real (tiny) study folder on disk — images, a fake Cellpose cache, reviews —
driven only through the controller, the way a Qt front end will drive it.
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

from erysreview import persistence
from erysreview.cellpose_runner import cache_paths
from erysreview.controller import AppController, status_counts
from erysreview.persistence import AppSettings, ReviewState
from erysreview.profile import ProfileData
from erysreview.tasks import CancelFlag, Task, run_sync

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def make_study(root: Path, prof: ProfileData) -> Path:
    folder = root / "study"
    folder.mkdir()
    for name in ("A_01_Prä_1.tif", "A_01_Prä_2.tif", "A_02_Prä_1.tif"):
        tifffile.imwrite(folder / name, np.full((40, 40), 100, dtype=np.uint8))
    # A Cellpose cache for the first image, made with the profile's parameters.
    masks = np.zeros((40, 40), dtype=np.int32)
    masks[5:15, 5:15] = 1
    masks[20:30, 20:30] = 2
    npy, meta = cache_paths(folder, folder / "A_01_Prä_1.tif")
    npy.parent.mkdir(parents=True, exist_ok=True)
    np.save(npy, masks)
    meta.write_text(json.dumps(asdict(prof.cellpose)))
    return folder


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Keep settings.json / profiles out of the real app-support folder.
        persistence.app_support_dir = lambda: root / "support"
        (root / "support").mkdir()
        import erysreview.profile as profile_mod
        if hasattr(profile_mod, "app_support_dir"):
            profile_mod.app_support_dir = persistence.app_support_dir

        prof = ProfileData()
        prof.use_simple_parser = True
        prof.delimiters = ["_"]
        prof.field_roles = ["ignore", "subject", "timepoint", "image_num"]
        folder = make_study(root, prof)
        ctl = AppController(AppSettings(), prof)

        check(ctl.open_folder(root / "nothing-here", restore_index=False) is None
              if (root / "nothing-here").mkdir() is None else False,
              "an empty folder is refused and leaves the state alone")
        check(ctl.folder is None, "…state untouched")

        # Regression (data loss): opening a folder and showing an image must
        # not write the review held in memory onto image 0 of that folder.
        st0 = sorted(p.stem for p in folder.glob("*.tif"))[0]
        rv0 = ReviewState(is_finished=True); rv0.types = {"1": "x"}; rv0.save(folder, st0)
        before0 = ReviewState.review_path(folder, st0).read_bytes()
        ctl.open_folder(folder, restore_index=False)
        ctl.idx = 0
        ctl.load_image_at(2)
        check(ReviewState.review_path(folder, st0).read_bytes() == before0,
              "opening a folder never overwrites image 0's review (regression)")
        ReviewState.review_path(folder, st0).unlink()
        ctl.folder_state.last_index = 0; ctl.folder_state.save(folder)

        start = ctl.open_folder(folder, restore_index=True)
        check(start == 0 and len(ctl.images) == 3, "a folder opens with its three images")
        check(set(ctl.grouped.timepoints()) == {"Prä"} and ctl.grouped.subjects_in("Prä") == ["01", "02"],
              "the filename parser groups by subject and timepoint")

        img = ctl.load_image_at(0)
        check(not img.unsegmented and not img.mismatch and img.masks.max() == 2,
              "a cached image loads with its masks; parameters match")
        img2 = ctl.load_image_at(1)
        check(img2.unsegmented and img2.masks.max() == 0, "an uncached image loads raw")
        check(ctl.load_image_at(99).path.name == "A_02_Prä_1.tif" and ctl.idx == 2,
              "the index is clamped")

        ctl.load_image_at(0)
        c = status_counts(range(1, 3), ctl.review, prof, border_ids={2}, duplicate_ids=set())
        check((c.total, c.active, c.inactive_auto) == (2, 1, 1), "status counts follow border rules")
        ctl.review.force_included.append(2)
        ctl.review.types["2"] = "stechapfel"
        c = status_counts(range(1, 3), ctl.review, prof, border_ids={2}, duplicate_ids=set())
        check((c.active, c.changed, round(c.pct_changed)) == (2, 1, 50), "…and edits")

        # time: active ticks count, idle ones do not, and the total is saved
        ctl.mark_activity(now=1000.0); ctl._last_tick = 1000.0
        ctl.tick(now=1005.0)
        ctl.tick(now=1005.0 + ctl.TIME_IDLE_S + 10)
        check(abs(ctl.review.time_spent_s - 5.0) < 1e-6, "only active time is counted")
        ctl.save_review()
        check(ReviewState.load(folder, "A_01_Prä_1").time_spent_s == 5.0, "and it is saved with the review")

        check(ctl.toggle_finished() is True, "toggle_finished flips the flag")
        check([ctl.toggle_eval() for _ in range(3)] == ["out", "in", "out"], "E: counts ↔ does not count")
        check(ctl.review.eval_include is False and not ctl.is_counted(), "out is stored and leaves the group")
        ctl.toggle_eval()
        check(ctl.review.eval_include is None and ctl.is_counted(), "back in = automatic, not 'always'")
        check(ctl.set_note("2", "Membran?") and not ctl.set_note("2", "Membran?"),
              "a note change is reported once, a repeat is not")
        check(ctl.set_note("2", "") and "2" not in ctl.review.notes, "empty text deletes the note")
        ctl.save_review()

        ctl.load_image_at(2)
        n = ctl.set_subject_status("Prä", "01", True)
        check(n == 0, "the subject is already finished — nothing changes")
        n = ctl.set_subject_status("Prä", "01", False)
        check(n == 1 and not ReviewState.load(folder, "A_01_Prä_1").is_finished,
              "set_subject_status touches segmented images only")

        check(ctl.all_groups() == [("Prä", "01"), ("Prä", "02")], "all groups in display order")
        check(ctl.mark_exported_finished([("Prä", "01")]) == 1, "export marks only segmented images")

        changed = ProfileData(**{**prof.__dict__})
        changed.cellpose = type(prof.cellpose)(**{**asdict(prof.cellpose), "diameter": 99.0})
        ch = ctl.apply_edited_profile(changed)
        check(ch.cellpose_changed and not ch.margin_changed and ctl.any_masks(),
              "editing Cellpose values is detected; masks exist")
        check(ctl.load_image_at(0).mismatch, "and the cached masks now count as mismatched")

        ctl.discard_for_resegment(folder / "A_01_Prä_1.tif")
        check(not ctl.any_masks() and not ReviewState.review_path(folder, "A_01_Prä_1").exists(),
              "discard_for_resegment drops masks and review")

        (folder / "A_03_Prä_1.tif").write_bytes((folder / "A_02_Prä_1.tif").read_bytes())
        check(ctl.rescan() and len(ctl.images) == 4, "rescan picks up new files")

        ctl.push_recent_folder("/a"); ctl.push_recent_folder("/b"); ctl.push_recent_folder("/a")
        check(ctl.profile.recent_folders[:2] == ["/a", "/b"], "recent folders dedupe, newest first")

    flag = CancelFlag()
    seen = []
    t = Task(work=lambda progress, cancelled: [progress(i) or seen.append(i)
                                               for i in range(3) if not cancelled()])
    run_sync(t, progress=lambda v, text=None: None, is_cancelled=flag)
    flag.cancel()
    run_sync(t, progress=lambda v, text=None: None, is_cancelled=flag)
    check(seen == [0, 1, 2], "a task runs to the end, and a cancelled one does nothing")

    print(f"OK — {CHECKS} checks: folder, images, edits, time, profile and export "
          f"workflows run without a UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
