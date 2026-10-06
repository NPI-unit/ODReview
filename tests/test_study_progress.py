"""Study button: progress counted per proband ID, and layout C's type chips.

An ID is done only when every image of it, at every timepoint, is finished
AND each timepoint group was exported. Layout C shows the cell types as
chips up to CHIP_MAX_TYPES and falls back to the dropdown above that.
"""

from __future__ import annotations

import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.grouping import GroupedFolder, ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from erysreview.i18n import get_strings
from erysreview.persistence import AppSettings, ReviewState, TypeDef
from erysreview.profile import ProfileData
from erysreview.study import study_progress
from erysreview.theme import palette
from erysreview.toolbar import CHIP_MAX_TYPES, Toolbar

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def _grouped(folder: Path, spec: dict) -> GroupedFolder:
    g = GroupedFolder(profile_name="t")
    for (tp, sub), n in spec.items():
        for i in range(n):
            info = ImageGroupInfo(path=folder / f"{tp}_{sub}_{i}.tif",
                                  timepoint=tp, subject=sub, image_num=i)
            g.tree.setdefault(tp, {}).setdefault(sub, []).append(info)
            g.flat.append(info)
    return g


def _finish(folder: Path, g: GroupedFolder, tp: str, sub: str) -> None:
    for info in g.tree[tp][sub]:
        ReviewState(is_finished=True).save(folder, info.path.stem)


def _export(folder: Path, tp: str, sub: str) -> None:
    d = folder / "outputs" / tp / f"{tp}_{sub}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tp}_{sub}_summary.csv").write_text("x\n")


def progress_checks() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        g = _grouped(folder, {("Prä", "01"): 2, ("Post", "01"): 2,
                              ("Prä", "02"): 2, ("Post", "02"): 2,
                              ("Prä", "03"): 1,
                              (UNGROUPED_TIMEPOINT, UNGROUPED_SUBJECT): 3})
        p = study_progress(folder, g)
        check((p.total, p.done, p.wip, p.open) == (3, 0, 0, 3),
              "three IDs, all open; _ungrouped is no ID")

        _finish(folder, g, "Prä", "01"); _export(folder, "Prä", "01")
        p = study_progress(folder, g)
        check((p.done, p.wip) == (0, 1),
              "one timepoint done is not the ID done — still in progress")

        _finish(folder, g, "Post", "01")
        p = study_progress(folder, g)
        check(p.done == 0, "finished but not exported does not count as done")

        _export(folder, "Post", "01")
        p = study_progress(folder, g)
        check((p.done, p.wip, p.open) == (1, 0, 2),
              "every timepoint finished and exported: the ID is done")

        _export(folder, "Prä", "02")
        p = study_progress(folder, g)
        check((p.done, p.wip, p.open) == (1, 1, 1),
              "an exported but unfinished group counts as in progress")
        check(not (folder / "outputs" / UNGROUPED_TIMEPOINT).exists(),
              "counting never creates folders")


def chip_checks() -> None:
    root = tk.Tk(); root.withdraw()
    noop = lambda *a, **k: None
    settings = AppSettings(toolbar_layout="two_row")

    def make(n_types: int) -> Toolbar:
        prof = ProfileData()
        prof.cell_types = [TypeDef(key=f"t{i}", name=f"Typ {i}", color="#3366AA")
                           for i in range(n_types)]
        prof.active_type_key = "t0"
        return Toolbar(root, palette(settings.theme), get_strings("de"), settings, prof,
                       on_tool_change=noop, on_type_change=noop, on_resegment=noop,
                       on_toggle_overlay=noop, on_reset_zoom=noop, on_undo=noop,
                       on_info=noop, on_export=noop)

    tb = make(CHIP_MAX_TYPES)
    check(len(tb._chips) == CHIP_MAX_TYPES and tb.type_combo is None,
          f"{CHIP_MAX_TYPES} types: chips, no dropdown")
    picked = []
    tb._cb_type = picked.append
    tb._chips["t2"].invoke()
    check(picked == ["t2"] and tb.profile.active_type_key == "t2"
          and str(tb._chips["t2"].cget("style")) == "ErysChipActive.TButton",
          "a chip click selects the type and highlights it")

    tb2 = make(CHIP_MAX_TYPES + 1)
    check(not tb2._chips and tb2.type_combo is not None,
          f"{CHIP_MAX_TYPES + 1} types: back to the dropdown")

    tb.profile.cell_types.append(TypeDef(key="extra", name="Extra"))
    tb.refresh_type_combo(tb.profile)
    check(not tb._chips and tb.type_combo is not None,
          "growing past the limit later switches too")

    settings.toolbar_layout = "grouped"
    tb.rebuild()
    check(tb.type_combo is not None and len(tb._rows) == 1,
          "rebuild to the grouped layout: one row with the dropdown")
    root.destroy()


def main() -> int:
    progress_checks()
    chip_checks()
    print(f"OK — {CHECKS} checks: progress counts finished & exported IDs; "
          f"chips fall back to the dropdown above {CHIP_MAX_TYPES} types.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
