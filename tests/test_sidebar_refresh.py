"""Consistency gate for the group-local sidebar refresh.

`app._save_review()` runs on every single click. It used to call
`refresh_statuses()`, which re-reads one review JSON per image in the whole
FOLDER, plus `refresh_current_group_counter()`, which walked the current group
a second time. Measured on Windows/NTFS that was 617 ms per click at 780
images; the group-local `refresh_current_group()` does it in 88 ms and, more
importantly, no longer scales with the folder.

Cutting work is only allowed if the result is identical, so that is what this
pins: after an edit, the tree that `refresh_current_group()` leaves behind must
equal the one the folder-wide sweep produces from disk. Two cases matter and
both are exercised:

  1. the eval cap does NOT move  -> only the edited row may be repainted
  2. the eval cap DOES move      -> the ⊘ markers of its siblings must follow

Case 2 is the one a naive "just repaint the edited row" would get wrong.

Needs a Tk display. Synthetic data only, in a temp folder.
    ./.venv/bin/python tests/test_sidebar_refresh.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import tkinter as tk
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.grouping import group_images
from erysreview.i18n import get_strings
from erysreview.persistence import ReviewState
from erysreview.profile import ProfileData
from erysreview.sidebar import Sidebar
from erysreview.theme import palette

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _profile() -> ProfileData:
    """T1_P01_003 -> timepoint / subject / image_num, with a tiny target so a
    handful of two-cell images is enough to move the evaluation cap."""
    p = ProfileData(name="refreshtest")
    p.use_simple_parser = True
    p.delimiters = ["_"]
    p.field_roles = ["timepoint", "subject", "image_num"]
    p.target_cells = 3
    return p


def _build_folder(root: Path, n_subjects: int, per_subject: int) -> tuple[Path, list[Path]]:
    """Images with two cells each, all segmented, in n_subjects groups."""
    folder = root / "study"
    cache = folder / ".erys_cache"
    cache.mkdir(parents=True)
    mask = np.zeros((8, 8), dtype=np.int32)
    mask[0:3, 0:3] = 1
    mask[4:7, 4:7] = 2
    params = json.dumps({"diameter": 30.0, "flow_threshold": 0.4,
                         "cellprob_threshold": 0.0, "model": "cpsam"})
    for s in range(1, n_subjects + 1):
        for k in range(1, per_subject + 1):
            stem = f"T1_P{s:02d}_{k:03d}"
            (folder / f"{stem}.png").write_bytes(b"x")
            np.save(cache / f"{stem}_masks.npy", mask)
            (cache / f"{stem}_masks.json").write_text(params)
            ReviewState().save(folder, stem)
    return folder, sorted(folder.glob("*.png"))


def _snapshot(sb: Sidebar) -> dict[str, tuple[str, tuple]]:
    """Every row the tree shows, by iid: its text and its tags."""
    out: dict[str, tuple[str, tuple]] = {}

    def walk(iid: str) -> None:
        for child in sb.tree.get_children(iid):
            out[child] = (sb.tree.item(child, "text"),
                          tuple(sb.tree.item(child, "tags")))
            walk(child)

    walk("")
    return out


def _ground_truth(sb: Sidebar) -> dict[str, tuple[str, tuple]]:
    """What the folder-wide sweep produces, read fresh from disk."""
    sb.invalidate_group_cache()
    sb.refresh_statuses()
    sb.refresh_current_group_counter()
    return _snapshot(sb)


def _diff(a: dict, b: dict) -> list[str]:
    keys = sorted(set(a) | set(b))
    return [f"{k}: {a.get(k)!r} != {b.get(k)!r}" for k in keys if a.get(k) != b.get(k)]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="erysrefresh_"))
    try:
        folder, images = _build_folder(tmp, n_subjects=3, per_subject=4)
        profile = _profile()
        grouped = group_images(images, profile)

        root = tk.Tk()
        root.withdraw()
        noop = lambda *a, **k: None
        sb = Sidebar(root, palette("light"), get_strings("de"), profile,
                     on_open_folder=noop, on_image_select=noop,
                     on_subject_select=noop, on_open_changed=noop)
        sb.populate(folder, images, grouped)
        sb.set_idx(0)                       # T1_P01_001, first of its group
        root.update_idletasks()

        edited = images[0]
        siblings = [p for p in images if p.name.startswith("T1_P01_")]
        check(len(siblings) == 4, "the edited image has three siblings in its group")

        # ── 1. An edit that leaves the cap where it is ───────────────────────
        # Two cells per image, target 3: images 1+2 fill it either way.
        rv = ReviewState.load(folder, edited.stem)
        rv.types["1"] = "sichel"
        rv.save(folder, edited.stem)

        sb.refresh_current_group()
        fast = _snapshot(sb)
        check(_ground_truth(sb) == fast,
              "cap unchanged: the group-local refresh matches the folder-wide sweep")

        # ── 2. An edit that moves the cap ───────────────────────────────────
        # Emptying image 1 pushes image 3 into the evaluation, so a SIBLING's
        # ⊘ marker has to change — the case a row-only refresh would miss.
        before = set(sb.eval_stems("T1", "P01"))
        rv = ReviewState.load(folder, edited.stem)
        rv.force_excluded = [1, 2]
        rv.save(folder, edited.stem)

        sb.refresh_current_group()
        fast = _snapshot(sb)
        after = set(sb.eval_stems("T1", "P01"))
        check(after != before,
              f"the edit really did move the cap ({sorted(before)} -> {sorted(after)})")
        truth = _ground_truth(sb)
        check(truth == fast,
              "cap moved: the group-local refresh still matches the sweep"
              + ("" if truth == fast else "\n    " + "\n    ".join(_diff(truth, fast))))

        # ── 3. Other groups are left exactly alone ──────────────────────────
        rv = ReviewState.load(folder, edited.stem)
        rv.force_excluded = []
        rv.save(folder, edited.stem)
        untouched_before = {k: v for k, v in _snapshot(sb).items()
                            if "P01" not in k}
        sb.refresh_current_group()
        untouched_after = {k: v for k, v in _snapshot(sb).items()
                           if "P01" not in k}
        check(untouched_before == untouched_after,
              "rows outside the edited group are untouched")

        # ── 4. No group context -> the safe fallback still runs ─────────────
        sb.idx = 10_000
        sb.refresh_current_group()          # must not raise
        check(True, "an out-of-range index falls back to the sweep instead of failing")

        root.destroy()
        print(f"OK — {CHECKS} checks: the group-local refresh is indistinguishable "
              f"from the folder-wide one.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
