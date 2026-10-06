"""Batch segmentation without any UI toolkit: grouping, selection rules, the
two run modes, and keeping the machine awake.

The dialog (Tk today, Qt next) owns only the widgets: it shows the groups
from `BatchPlan`, keeps the `checked` map, and runs `run_normal` /
`run_to_target` on a worker thread, forwarding their `progress` calls to the
screen. Both runners are plain functions: `progress(value, text=None)` and
`is_cancelled()` are the only ways they talk back, and neither may touch UI.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .cellpose_runner import has_cache, load_image, run_for_image
from .counts import image_counts
from .grouping import ImageGroupInfo, UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from .profile import ProfileData

# Rough per-image Cellpose time used for the "~N min" estimate.
AVG_SECONDS_PER_IMAGE = 30.0

Progress = Callable[..., None]          # progress(value, text=None)
IsCancelled = Callable[[], bool]
# (done, skipped, errors[(name, message)], extra_summary_line)
BatchResult = tuple[int, int, list[tuple[str, str]], str]


@dataclass
class BatchPlan:
    """All images, grouped timepoint -> [(subject, [infos])], in display order.
    Images the grouping did not cover land in the ungrouped bucket."""
    tp_order: list[str] = field(default_factory=list)
    subs_by_tp: dict[str, list[tuple[str, list[ImageGroupInfo]]]] = field(default_factory=dict)
    info_by_name: dict[str, ImageGroupInfo] = field(default_factory=dict)

    @classmethod
    def build(cls, images: list[Path], grouped=None) -> "BatchPlan":
        plan = cls()
        if grouped is not None and getattr(grouped, "tree", None):
            for tp in grouped.timepoints():
                for sub in grouped.subjects_in(tp):
                    plan._add(tp, sub, list(grouped.tree[tp][sub]))
        leftover = [p for p in images if p.name not in plan.info_by_name]
        if leftover:
            plan._add(UNGROUPED_TIMEPOINT, UNGROUPED_SUBJECT, [
                ImageGroupInfo(path=p, timepoint=UNGROUPED_TIMEPOINT,
                               subject=UNGROUPED_SUBJECT, image_num=None,
                               is_ungrouped=True) for p in leftover])
        return plan

    def _add(self, tp: str, sub: str, infos: list[ImageGroupInfo]) -> None:
        if tp not in self.subs_by_tp:
            self.subs_by_tp[tp] = []
            self.tp_order.append(tp)
        self.subs_by_tp[tp].append((sub, infos))
        for info in infos:
            self.info_by_name[info.path.name] = info

    @property
    def subjects(self) -> list[str]:
        return list(dict.fromkeys(sub for tp in self.tp_order for sub, _ in self.subs_by_tp[tp]))

    def groups(self, tp_filter: str | None = None, sub_filter: str | None = None):
        """(tp, sub, infos) in order; None means 'all' for either filter."""
        for tp in self.tp_order:
            if tp_filter is not None and tp != tp_filter:
                continue
            for sub, infos in self.subs_by_tp[tp]:
                if sub_filter is not None and sub != sub_filter:
                    continue
                yield tp, sub, infos

    def default_checked(self, folder: Path) -> dict[str, bool]:
        """Pre-selection: everything that has no masks yet."""
        return {nm: not has_cache(folder, info.path) for nm, info in self.info_by_name.items()}


# ── selection rules (operate on the visible groups only) ──
def select_where(checked: dict[str, bool], visible: list[ImageGroupInfo],
                 rule: Callable[[ImageGroupInfo], bool]) -> None:
    for i in visible:
        checked[i.path.name] = bool(rule(i))


def invert(checked: dict[str, bool], visible: list[ImageGroupInfo]) -> None:
    for i in visible:
        checked[i.path.name] = not checked[i.path.name]


def keep_first_n(checked: dict[str, bool], visible_groups, n: int) -> None:
    """Narrow the current selection to the first n images of each visible
    group (image order). Never adds images that were not selected."""
    n = max(0, int(n))
    for _tp, _sub, infos in visible_groups:
        for k, info in enumerate(infos):
            if k >= n:
                checked[info.path.name] = False


def estimate_minutes(n_selected: int) -> int:
    return max(1, int(round(n_selected * AVG_SECONDS_PER_IMAGE / 60.0)))


# ── the two run modes ──
def segment_one(folder: Path, info: ImageGroupInfo, profile: ProfileData,
                model_registry: dict | None) -> None:
    run_for_image(folder, info.path, load_image(info.path), profile.cellpose,
                  force=False, use_gpu=profile.use_gpu, model_registry=model_registry)


def run_normal(folder: Path, infos: list[ImageGroupInfo], segment: Callable[[ImageGroupInfo], None],
               progress: Progress, is_cancelled: IsCancelled,
               label: Callable[[int, int, str], str]) -> BatchResult:
    """Segment every selected image that has no masks yet."""
    errors: list[tuple[str, str]] = []
    skipped = done = 0
    n = len(infos)
    for idx, info in enumerate(infos, start=1):
        if is_cancelled():
            break
        progress(idx - 1, label(idx, n, info.path.name))
        if has_cache(folder, info.path):
            skipped += 1
        else:
            try:
                segment(info); done += 1
            except Exception as e:  # noqa: BLE001 — collected for the summary
                errors.append((info.path.name, str(e)))
        progress(idx)
    return done, skipped, errors, ""


def run_to_target(folder: Path, groups: list[tuple[str, str, list[ImageGroupInfo]]],
                  profile: ProfileData, segment: Callable[[ImageGroupInfo], None],
                  progress: Progress, is_cancelled: IsCancelled,
                  label: Callable[[str, str, int, int], str],
                  summary: Callable[[int], str]) -> BatchResult:
    """Per group, segment images in image order until the counted cells reach
    the profile target; the rest of the group is left raw."""
    target = max(1, profile.target_cells)
    errors: list[tuple[str, str]] = []
    skipped = done = touched = n_groups = 0
    for tp, sub, infos in groups:
        if is_cancelled():
            break
        n_groups += 1
        cumulative = 0
        for info in infos:
            if is_cancelled() or cumulative >= target:
                break
            touched += 1
            progress(touched, label(tp, sub, cumulative, target))
            if has_cache(folder, info.path):
                skipped += 1
            else:
                try:
                    segment(info); done += 1
                except Exception as e:  # noqa: BLE001
                    errors.append((info.path.name, str(e)))
                    continue
            cumulative += (image_counts(folder, info, profile) or {}).get("rbc", 0) or 0
    return done, skipped, errors, summary(n_groups)


# ── keep the machine awake during a run ──
class KeepAwake:
    """macOS: `caffeinate -i -w <pid>` (dies with us, so a crash cannot leave
    it running). Windows: SetThreadExecutionState. Anything else: no-op."""

    def __init__(self) -> None:
        self._token = None

    def start(self) -> None:
        try:
            if sys.platform == "darwin":
                self._token = ("mac", subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())]))
            elif sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x1 | 0x2)
                self._token = ("win", None)
        except Exception:  # noqa: BLE001 — staying awake is best-effort
            self._token = None

    def stop(self) -> None:
        token, self._token = self._token, None
        if not token:
            return
        kind, obj = token
        try:
            if kind == "mac" and obj is not None:
                obj.terminate()
            elif kind == "win":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        except Exception:  # noqa: BLE001
            pass


def run_rerun(folder: Path, infos: list[ImageGroupInfo], discard: Callable[[ImageGroupInfo], None],
              segment: Callable[[ImageGroupInfo], None], progress: Progress,
              is_cancelled: IsCancelled, label: Callable[[int, int, str], str]) -> BatchResult:
    """Re-segment images whose masks are stale. Each image's masks and review
    are discarded right before it is segmented, so a cancel leaves every image
    not yet reached exactly as it was. The caller has confirmed the reset."""
    errors: list[tuple[str, str]] = []
    done = 0
    n = len(infos)
    for idx, info in enumerate(infos, start=1):
        if is_cancelled():
            break
        progress(idx - 1, label(idx, n, info.path.name))
        try:
            discard(info)
            segment(info)
            done += 1
        except Exception as e:  # noqa: BLE001 — collected for the summary
            errors.append((info.path.name, str(e)))
        progress(idx)
    return done, 0, errors, ""

