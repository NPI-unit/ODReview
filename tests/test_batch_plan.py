"""Dialog rules moved out of the UI: batch selection and runs, eval inclusion,
export-scope presets. Headless; Cellpose is replaced by a stub segmenter.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview import batch
from erysreview.evaluation import inclusion_with_overrides
from erysreview.export import scope_preset
from erysreview.grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_TIMEPOINT
from erysreview.profile import ProfileData

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def grouped(folder: Path) -> tuple[GroupedFolder, list[Path]]:
    g = GroupedFolder(profile_name="t")
    paths = []
    for tp in ("Prä", "Post"):
        for sub in ("01", "02"):
            for k in range(3):
                p = folder / f"{tp}_{sub}_{k}.tif"
                info = ImageGroupInfo(path=p, timepoint=tp, subject=sub, image_num=k)
                g.tree.setdefault(tp, {}).setdefault(sub, []).append(info)
                g.flat.append(info); paths.append(p)
    return g, paths


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        g, paths = grouped(folder)
        stray = folder / "notparsed.tif"
        plan = batch.BatchPlan.build(paths + [stray], g)
        check(plan.tp_order == g.timepoints() + [UNGROUPED_TIMEPOINT],
              "timepoints keep the app's natural order; unparsed images go last, ungrouped")
        check(len(plan.info_by_name) == 13 and plan.subjects[:2] == ["01", "02"],
              "every image appears once")
        checked = plan.default_checked(folder)
        check(all(checked.values()), "nothing has masks yet, so everything is pre-selected")

        visible = list(plan.groups(tp_filter="Prä"))
        batch.keep_first_n(checked, visible, 2)
        check(sum(checked[i.path.name] for _t, _s, infos in visible for i in infos) == 4,
              "first N per group narrows only the visible groups (2 × 2)")
        check(checked["Post_01_2.tif"], "groups outside the filter are untouched")
        batch.invert(checked, [i for _t, _s, infos in visible for i in infos])
        check(not checked["Prä_01_0.tif"] and checked["Prä_01_2.tif"], "invert flips the visible set")
        check(batch.estimate_minutes(4) == 2 and batch.estimate_minutes(0) == 1,
              "time estimate rounds and never says 0")

        seen, progress_log = [], []
        infos = [plan.info_by_name[n] for n in ("Prä_01_0.tif", "Prä_01_1.tif")]
        res = batch.run_normal(folder, infos, lambda i: seen.append(i.path.name),
                               lambda v, t=None: progress_log.append(v), lambda: False,
                               lambda i, n, name: f"{i}/{n} {name}")
        check(res[:2] == (2, 0) and seen == ["Prä_01_0.tif", "Prä_01_1.tif"],
              "run_normal segments each selected image once")

        def boom(info):
            raise RuntimeError("GPU weg")
        res = batch.run_normal(folder, infos[:1], boom, lambda *a: None, lambda: False,
                               lambda *a: "")
        check(res[2] == [("Prä_01_0.tif", "GPU weg")], "errors are collected, not raised")

        calls = iter([False, True])
        res = batch.run_normal(folder, infos, lambda i: None, lambda *a: None,
                               lambda: next(calls), lambda *a: "")
        check(res[0] == 1, "cancel stops after the current image")

        prof = ProfileData(); prof.target_cells = 1
        res = batch.run_to_target(folder, [("Prä", "01", g.tree["Prä"]["01"])], prof,
                                  lambda i: None, lambda *a: None, lambda: False,
                                  lambda *a: "", lambda n: f"{n} Gruppen")
        check(res[0] == 3 and res[3] == "1 Gruppen",
              "run_to_target keeps going while no cells are counted yet")

    imgs = [ImageGroupInfo(path=Path(f"{k}.tif"), timepoint="t", subject="s", image_num=k)
            for k in range(4)]
    counts = {"0": 900, "1": 900, "2": 900, "3": 900}
    inc, cum = inclusion_with_overrides(imgs, counts, {}, 2000)
    check(inc == {"0", "1", "2"} and cum == 2700, "auto-fill stops once the target is reached")
    inc, cum = inclusion_with_overrides(imgs, counts, {"1": False, "3": True}, 2000)
    check(inc == {"0", "2", "3"} and cum == 2700, "overrides force images in and out")
    inc, cum = inclusion_with_overrides(imgs, counts, {"0": False}, 2000)
    check(inc == {"1", "2"} and cum == 1800, "taking one out only subtracts — nothing moves up")
    # BDC-5 · G3 from the bug report: 001 out gave 2118 (011 moved up), must be 2054 − 191
    real = [191, 203, 201, 202, 204, 207, 210, 218, 204, 214, 255, 248, 229, 267, 218]
    imgs15 = [ImageGroupInfo(path=Path(f"{k:03d}.tif"), timepoint="t", subject="s", image_num=k)
              for k in range(1, 16)]
    c15 = {f"{k:03d}": n for k, n in enumerate(real, 1)}
    check(inclusion_with_overrides(imgs15, c15, {}, 2000)[1] == 2054, "BDC-5 G3: automatic = 2054")
    check(inclusion_with_overrides(imgs15, c15, {"001": False}, 2000)[1] == 1863,
          "BDC-5 G3: 001 out = 1863, not 2118")
    check(inclusion_with_overrides(imgs15, c15, {"001": False, "011": True}, 2000)[1] == 2118,
          "…and 011 counts only when added by hand")

    rows = [("Prä", "01", 3, 3, True), ("Prä", "02", 3, 1, False), ("Post", "01", 3, 0, False)]
    check(scope_preset(rows, "edited") == {("Prä", "01"): True, ("Prä", "02"): False, ("Post", "01"): False},
          "export preset 'edited' picks groups with user edits")
    check(scope_preset(rows, "computed")[("Prä", "02")] and not scope_preset(rows, "computed")[("Post", "01")],
          "preset 'computed' picks groups with any masks")

    print(f"OK — {CHECKS} checks: batch, eval and export rules work without a UI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
