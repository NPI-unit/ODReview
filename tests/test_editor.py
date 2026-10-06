"""ReviewEditor: the three tools and undo, headless.

Every edit must be exactly reversible — after undoing everything, the review
JSON and the live mask are what they were on load. Runs without any UI.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.editor import ReviewEditor
from erysreview.persistence import ReviewState
from erysreview.profile import ProfileData

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def masks() -> np.ndarray:
    m = np.zeros((100, 100), dtype=np.int32)
    m[40:50, 40:50] = 1          # interior cell
    m[0:8, 60:70] = 2            # touches the top edge -> border with margin 5
    m[70:80, 20:30] = 3
    return m


def main() -> int:
    prof = ProfileData()
    prof.margin_n = 5
    prof.size_filter.enabled = False
    ed = ReviewEditor(prof, new_circle_radius=4)
    rv = ReviewState()
    ed.load(masks(), rv)
    before_review = copy.deepcopy(rv.__dict__)
    before_mask = ed.masks.copy()

    check(ed.border_ids == {2}, "cell touching the margin is a border cell")
    check(ed.find_cell_at(45, 45) == 1 and ed.find_cell_at(5, 95) is None,
          "find_cell_at hits cells and misses background")

    check(ed.toggle_active(45, 45) == 1 and rv.force_excluded == [1],
          "toggle on an active cell excludes it")
    check(ed.toggle_active(65, 3) == 2 and rv.force_included == [2],
          "toggle on a border cell force-includes it")
    check(ed.set_type(25, 75, "stechapfel") == 3 and rv.types == {"3": "stechapfel"},
          "set_type records the type")
    new = ed.add(85, 85)
    check(new == 4 and ed.find_cell_at(85, 85) == 4 and rv.manual_cells[-1].cell_id == 4,
          "add burns a manual cell into the mask and the review")
    check(rv.next_manual_id == 5, "next_manual_id advances")
    check(ed.colors().is_active(2) and not ed.colors().is_active(1),
          "colours follow the edits (included border on, excluded off)")

    kinds = []
    while (u := ed.undo()) is not None:
        kinds.append(u.kind)
    check(kinds == ["add", "type", "remove_toggle", "remove_toggle"],
          "undo walks the history backwards")
    check(rv.__dict__ == {**before_review, "next_manual_id": rv.next_manual_id}
          and rv.next_manual_id == before_review["next_manual_id"],
          "after undoing everything the review is unchanged")
    check(np.array_equal(ed.masks, before_mask), "and the live mask is unchanged")
    check(ed.undo() is None, "undo on an empty history is a no-op")

    rv2 = ReviewState()
    ed2 = ReviewEditor(prof, new_circle_radius=4)
    ed2.load(masks(), rv2)
    ed2.add(85, 85)
    rv2_reloaded = ReviewState(manual_cells=list(rv2.manual_cells),
                               next_manual_id=rv2.next_manual_id)
    ed3 = ReviewEditor(prof, new_circle_radius=4)
    ed3.load(masks(), rv2_reloaded)
    check(ed3.find_cell_at(85, 85) == 4 and ed3.next_id == 5,
          "a saved manual cell is restored on reload")

    print(f"OK — {CHECKS} checks: every tool edit is recorded and exactly reversible.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
