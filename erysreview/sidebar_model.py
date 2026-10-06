"""Sidebar rules shared by the Tk and Qt sidebars, without any UI toolkit:
an image's status, whether it carries a remark, which filter shows it, and a
subject row's counter."""

from __future__ import annotations

from pathlib import Path

from .cellpose_runner import cached_params, has_cache, params_match
from .persistence import ReviewState
from .profile import ProfileData

FILTERS = [("all", "filter_all"), ("unsegmented", "filter_unsegmented"),
           ("unfinished", "filter_unfinished"), ("finished", "filter_finished"),
           ("with_notes", "filter_with_notes")]
# Qt only (the Tk sidebar keeps its five): masks made with other parameters.
FILTER_STALE = ("stale", "qt_filter_stale")


def image_status(folder: Path | None, image_path: Path) -> tuple[str, bool]:
    """("unsegmented" | "in_progress" | "finished", has_remark).

    The remark flag rides along deliberately: the review is already loaded
    here, and a second ReviewState.load per row would double the disk work of a
    sidebar refresh — the hot path this project has had to optimise before.
    """
    if folder is None or not has_cache(folder, image_path):
        return "unsegmented", False
    review = ReviewState.load(folder, image_path.stem)
    has_note = bool(review.note.strip() or any(t.strip() for t in review.notes.values()))
    return ("finished" if review.is_finished else "in_progress"), has_note


def is_stale(folder: Path | None, image_path: Path, params) -> bool:
    """Masks exist but were made with other Cellpose parameters/model than the
    study now asks for — counted with values that no longer apply."""
    return (folder is not None and has_cache(folder, image_path)
            and not params_match(folder, image_path, params))


def stale_reason(folder: Path, image_path: Path, params) -> list[tuple[str, object, object]]:
    """[(field, made with, study wants)] for a stale image — what the tooltip names."""
    cp = cached_params(folder, image_path) or {}
    out = []
    for f in ("model", "diameter", "flow_threshold", "cellprob_threshold"):
        want = getattr(params, f)
        have = cp.get(f)
        if have is None or (str(have) != str(want) if f == "model" else float(have) != float(want)):
            out.append((f, have, want))
    return out


def passes_filter(mode: str, status: str, has_note: bool, stale: bool = False) -> bool:
    if mode == "stale":
        return stale
    if mode == "with_notes":
        return has_note
    if mode == "unsegmented":
        return status == "unsegmented"
    if mode == "unfinished":
        return status != "finished"
    if mode == "finished":
        return status == "finished"
    return True


def subject_counter(folder: Path, images, profile: ProfileData) -> tuple[int, int, set[str]]:
    """(counted cells, target, stems that count) for one subject/timepoint group."""
    from .evaluation import group_eval
    target = max(1, profile.target_cells)
    try:
        summary, stems = group_eval(folder, images, profile)
        return summary["n_active"], target, stems
    except Exception:  # noqa: BLE001 — a broken group must not break the tree
        return 0, target, set()


def group_details(folder: Path, images, profile: ProfileData) -> dict:
    """Everything the detail panel shows for one subject/timepoint group:
    the evaluation summary (counted cells, target, per type, % changed) plus
    how many images are finished and how many there are."""
    from .evaluation import group_eval
    try:
        summary, _stems = group_eval(folder, images, profile)
    except Exception:  # noqa: BLE001
        summary = {"n_active": 0, "n_images": 0, "by_type": {}, "pct_changed": 0.0,
                   "target": max(1, profile.target_cells), "reached": False}
    finished = sum(1 for i in images if image_status(folder, i.path)[0] == "finished")
    return {**summary, "n_finished": finished, "n_total": len(images)}

