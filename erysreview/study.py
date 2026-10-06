"""Study progress as the toolbar's study button reports it.

Counted per proband ID, not per image: an ID is done once every image of it,
at every timepoint, is marked finished AND each of its timepoint groups has
been exported (the group's `<tp>_<sub>_summary.csv` exists under outputs/).
Images that never parsed into a group (`_ungrouped`) belong to no ID.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .cellpose_runner import has_cache
from .grouping import GroupedFolder, UNGROUPED_TIMEPOINT
from .persistence import ReviewState


@dataclass
class StudyProgress:
    total: int = 0
    done: int = 0        # finished and exported
    wip: int = 0         # something happened, not done yet
    open: int = 0        # nothing segmented, finished or exported

    @property
    def fraction(self) -> float:
        return self.done / self.total if self.total else 0.0


def _group_exported(folder: Path, tp: str, sub: str) -> bool:
    # The path export._group_folder writes to — rebuilt here instead of
    # calling outputs_root(), which would create outputs/ as a side effect.
    name = f"{tp}_{sub}"
    return (folder / "outputs" / tp / name / f"{name}_summary.csv").is_file()


def study_progress(folder: Path, grouped: GroupedFolder) -> StudyProgress:
    # subject -> [done?, touched?] accumulated over its timepoints
    per_id: dict[str, list[bool]] = {}
    for tp, subjects in grouped.tree.items():
        if tp == UNGROUPED_TIMEPOINT:
            continue
        for sub, infos in subjects.items():
            finished = sum(ReviewState.load(folder, i.path.stem).is_finished for i in infos)
            exported = _group_exported(folder, tp, sub)
            group_done = bool(infos) and finished == len(infos) and exported
            touched = (finished > 0 or exported
                       or any(has_cache(folder, i.path) for i in infos))
            state = per_id.setdefault(sub, [True, False])
            state[0] = state[0] and group_done
            state[1] = state[1] or touched
    p = StudyProgress(total=len(per_id))
    for done, touched in per_id.values():
        if done:
            p.done += 1
        elif touched:
            p.wip += 1
        else:
            p.open += 1
    return p
