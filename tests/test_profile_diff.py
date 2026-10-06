"""profile_diff: what saving an edited study changes, and its consequences."""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.cellpose_runner import cache_paths
from erysreview.profile import ProfileData
from erysreview.profile_diff import profile_diff

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    old = ProfileData(name="S")
    old.use_simple_parser, old.delimiters = True, ["_"]
    old.field_roles = ["ignore", "subject", "timepoint", "image_num"]
    check(not profile_diff(old, copy.deepcopy(old)), "an unchanged copy has no changes")

    new = copy.deepcopy(old)
    new.cellpose.diameter = 34.0
    new.overlay_alpha = 0.7
    new.cell_types[0].color = "#000000"
    d = profile_diff(old, new)
    check(d.kinds == {"resegment", "display"}, f"diameter → re-run, colours → display ({d.kinds})")
    check([k for k, _ in d.by_kind()] == ["resegment", "display"], "consequences are ordered by weight")

    new2 = copy.deepcopy(old)
    new2.cell_types = new2.cell_types[:-1]
    check(profile_diff(old, new2).kinds == {"counting"}, "removing a cell type changes counting")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        images = [folder / n for n in ("A_01_Prä_1.tif", "A_02_Prä_1.tif", "A-03-Prä-1.tif")]
        for p in images:
            p.write_bytes(b"x")
        npy, meta = cache_paths(folder, images[0])
        npy.parent.mkdir(parents=True, exist_ok=True)
        np.save(npy, np.zeros((2, 2), dtype=np.int32))
        meta.write_text(json.dumps(asdict(old.cellpose)))

        d = profile_diff(old, new, folder, images)
        check(d.impact.n_with_masks == 1, "the re-run impact counts images with masks")
        g = copy.deepcopy(old)
        g.delimiters = ["_", "-"]
        d = profile_diff(old, g, folder, images)
        check(d.kinds == {"grouping"}, "a new delimiter is a grouping change")
        check((d.impact.n_ungrouped_before, d.impact.n_ungrouped_after, d.impact.n_moved) == (1, 0, 1),
              "…which moves the dash-named image out of 'unassigned'")
    print(f"OK — {CHECKS} checks: every study change is classified by its consequence.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
