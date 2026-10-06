"""Sidebar rules shared by the Tk and Qt sidebars, without any UI toolkit:
an image's status, whether it carries a remark, which filter shows it, and a
subject row's counter."""

from __future__ import annotations

from pathlib import Path

from .cellpose_runner import has_cache
from .persistence import ReviewState
from .profile import ProfileData

FILTERS = [("all", "filter_all"), ("unsegmented", "filter_unsegmented"),
           ("unfinished", "filter_unfinished"), ("finished", "filter_finished"),
           ("with_notes", "filter_with_notes")]


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


def passes_filter(mode: str, status: str, has_note: bool) -> bool:
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
