"""App state and workflows, without any UI toolkit.

Owns what the main window used to hold next to its widgets: the active study
(profile), the open folder, its images and grouping, the current image and its
review, the folder's last position, and the time spent per image. Every
workflow that changes that state lives here — the front end (Tk today, Qt next)
asks, then redraws. Nothing in this module opens a dialog or touches a widget;
decisions that need the user (confirm a re-segment, pick a folder) stay in the
front end, which calls in with the answer.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .cellpose_runner import (
    discard_cache, has_cache, list_images, load_cached_masks, load_image,
    params_match, resolve_model, run_for_image,
)
from .export import export_folder_structured, export_training_data
from .grouping import GroupedFolder, group_images
from .persistence import (
    AppSettings, FolderState, ReviewState, TOOLS_ORDER, review_namespace, set_review_namespace,
)
from .profile import ProfileData, save_profile
from .tasks import Task, simple


@dataclass
class LoadedImage:
    """Everything the view needs to show one image."""
    path: Path
    image: np.ndarray
    masks: np.ndarray
    review: ReviewState
    unsegmented: bool       # no masks yet — shown raw, Cellpose on demand
    mismatch: bool          # masks exist but were made with other parameters


@dataclass
class ImageCounts:
    total: int
    active: int
    changed: int            # active cells whose type is not "normal"
    excluded: int           # switched off by hand
    inactive_auto: int      # border / size filter, not switched back on
    by_type: dict = None    # active cells per type key
    n_border: int = 0
    n_size: int = 0         # size-filter rejects (the "duplicate" column)

    @property
    def pct_changed(self) -> float:
        return 100.0 * self.changed / self.active if self.active else 0.0


@dataclass
class ProfileChange:
    margin_changed: bool
    cellpose_changed: bool


def status_counts(cell_ids, review: ReviewState, profile: ProfileData,
                 border_ids: set[int], duplicate_ids: set[int]) -> ImageCounts:
    """The status-line numbers for one image — the same activity rule as
    OverlayColors.is_active, counted."""
    excluded = set(review.force_excluded)
    included = set(review.force_included)
    default_key = profile.default_type_key()
    total = active = changed = n_excl = auto = n_b = n_s = 0
    by_type: dict[str, int] = {}
    for cid in cell_ids:
        total += 1
        n_b += cid in border_ids
        n_s += cid in duplicate_ids
        is_auto = cid in border_ids or cid in duplicate_ids
        if cid in excluded:
            n_excl += 1
            continue
        if is_auto and cid not in included:
            auto += 1
            continue
        active += 1
        t = review.types.get(str(cid), default_key)
        by_type[t] = by_type.get(t, 0) + 1
        if t != "normal":
            changed += 1
    return ImageCounts(total, active, changed, n_excl, auto, by_type, n_b, n_s)


def format_status(S: dict, idx: int, n_images: int, name: str,
                  c: ImageCounts, finished: bool) -> str:
    """The status line under the image, shared by both front ends."""
    done = f"  ·  {S['status_finished']}" if finished else ""
    return (f"[{idx + 1}/{n_images}]  {name}  ·  "
            f"{c.active} {S['status_active']}  ·  "
            f"{c.changed} {S['status_changed']} ({c.pct_changed:.1f}%)  ·  "
            f"{c.excluded} {S['status_excluded']}  ·  "
            f"{c.inactive_auto} {S['status_border']}  ·  "
            f"{c.total} {S['status_total']}{done}")


class AppController:
    MAX_RECENT_FOLDERS = 5
    TIME_IDLE_S = 60.0       # no input for this long -> stop counting time
    TIME_TICK_S = 5.0

    def __init__(self, settings: AppSettings, profile: ProfileData):
        self.settings = settings
        self.profile = profile
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.grouped: GroupedFolder | None = None
        self.idx: int = 0
        self.folder_state = FolderState()
        self.review = ReviewState()
        # Which file the review in memory belongs to: (folder, stem, rating).
        # Nothing is written unless it is still the current image of the same
        # folder and rating — opening a folder or a second rating used to save
        # the review in memory onto image 0 of the new place (data loss).
        self._review_key: tuple | None = None
        now = time.monotonic()
        self._last_activity = now
        self._last_tick = now

    # ── basics ──
    @property
    def has_images(self) -> bool:
        return self.folder is not None and bool(self.images)

    @property
    def current_path(self) -> Path | None:
        if self.has_images and 0 <= self.idx < len(self.images):
            return self.images[self.idx]
        return None

    # ── time spent per image ──
    def mark_activity(self, now: float | None = None) -> None:
        self._last_activity = time.monotonic() if now is None else now

    def tick(self, now: float | None = None) -> None:
        """Called every TIME_TICK_S. Adds the elapsed time to the current image
        while the user is active; a missed tick (sleep, debugger) is capped so
        it cannot dump a huge delta onto one image."""
        now = time.monotonic() if now is None else now
        delta = now - self._last_tick
        self._last_tick = now
        if self.has_images and (now - self._last_activity) < self.TIME_IDLE_S:
            self.review.time_spent_s += min(delta, self.TIME_TICK_S + 1.0)

    def _key_for(self, path: Path) -> tuple:
        return (str(self.folder), path.stem, review_namespace(self.folder))

    def _owns(self, path: Path | None) -> bool:
        return path is not None and self._review_key == self._key_for(path)

    def flush_time(self) -> None:
        """Persist the current image's accumulated time — only onto the image
        the review in memory was loaded for."""
        path = self.current_path
        if self._owns(path):
            try:
                self.review.save(self.folder, path.stem)
            except Exception:  # noqa: BLE001 — time is best-effort
                pass

    # ── tool / type ──
    def set_active_tool(self, key: str) -> bool:
        if key not in TOOLS_ORDER:
            return False
        self.settings.active_tool = key
        self.settings.save()
        return True

    def set_active_type(self, type_key: str) -> None:
        """Profile field; the caller debounces save_profile on this hot path."""
        self.profile.active_type_key = type_key

    # ── folders ──
    def push_recent_folder(self, path: str) -> None:
        recents = [p for p in self.profile.recent_folders if p != path]
        recents.insert(0, path)
        self.profile.recent_folders = recents[: self.MAX_RECENT_FOLDERS]

    def forget_recent_folder(self, path: str) -> None:
        self.profile.recent_folders = [p for p in self.profile.recent_folders if p != path]
        save_profile(self.profile)

    def remember_folder(self, path: str) -> None:
        """Make `path` the study's image folder and the most recent one."""
        self.profile.import_folder = path
        self.push_recent_folder(path)
        save_profile(self.profile)

    def _scan(self, folder: Path) -> list[Path]:
        return list_images(folder, exclude={folder / "outputs"})

    def open_folder(self, path: Path, restore_index: bool) -> int | None:
        """Load a folder. Returns the index to show first, or None if the
        folder holds no images (state is left unchanged then)."""
        images = self._scan(path)
        if not images:
            return None
        self.folder = path
        # a second rating reads and writes its own reviews (same masks)
        set_review_namespace(path, self.profile.rater or None)
        self.review, self._review_key = ReviewState(), None   # belongs to nothing here yet
        self.images = images
        self.grouped = group_images(images, self.profile)
        self.folder_state = FolderState.load(path)
        last = self.folder_state.last_index
        return last if restore_index and 0 <= last < len(images) else 0

    def rescan(self) -> bool:
        """Re-read the folder from disk (F5). False if it is now empty."""
        if self.folder is None:
            return False
        self.flush_time()
        images = self._scan(self.folder)
        if not images:
            return False
        self.images = images
        self.grouped = group_images(images, self.profile)
        self.idx = max(0, min(self.idx, len(images) - 1))
        return True

    def regroup(self) -> None:
        if self.images:
            self.grouped = group_images(self.images, self.profile)

    # ── images ──
    def load_image_at(self, idx: int) -> LoadedImage | None:
        """Switch to image `idx` (clamped). Saves the time of the image being
        left and the folder position. Raises if the image file cannot be read;
        returns None if its cached masks are unreadable."""
        if not self.has_images:
            return None
        self.flush_time()
        self._last_tick = time.monotonic()
        idx = max(0, min(idx, len(self.images) - 1))
        self.idx = idx
        self.folder_state.last_index = idx
        self.folder_state.save(self.folder)
        path = self.images[idx]
        img = load_image(path)
        unsegmented = not has_cache(self.folder, path)
        if unsegmented:
            # Never auto-run Cellpose — the raw image with an empty mask.
            masks = np.zeros(img.shape[:2], dtype=np.int32)
            mismatch = False
        else:
            masks = load_cached_masks(self.folder, path)
            mismatch = not params_match(self.folder, path, self.profile.cellpose)
        if masks is None:
            return None
        self.review = ReviewState.load(self.folder, path.stem)
        self._review_key = self._key_for(path)
        return LoadedImage(path, img, masks, self.review, unsegmented, mismatch)

    def reload_review(self) -> None:
        """Re-read the current review after something else wrote it."""
        path = self.current_path
        if path is not None:
            self.review = ReviewState.load(self.folder, path.stem)
            self._review_key = self._key_for(path)

    def save_review(self) -> None:
        """Raises on a write error, so the front end can say so. Writes only
        onto the image the review was loaded for (see _review_key)."""
        path = self.current_path
        if self._owns(path):
            self.review.save(self.folder, path.stem)

    # ── review edits outside the canvas ──
    def toggle_finished(self) -> bool:
        self.review.is_finished = not self.review.is_finished
        return self.review.is_finished

    def current_group(self) -> list | None:
        """The images of the current image's subject/timepoint group (also the
        "not assigned" one), or None."""
        path = self.current_path
        if self.grouped is None or path is None:
            return None
        for subs in self.grouped.tree.values():
            for infos in subs.values():
                if any(i.path == path for i in infos):
                    return infos
        return None

    def is_counted(self) -> bool:
        """Does the current image count toward the evaluation right now?"""
        from .evaluation import effective_eval_stems
        infos = self.current_group()
        if self.folder is None or infos is None:
            return self.review.eval_include is not False
        self.save_review()          # the cap reads the reviews from disk
        return self.current_path.stem in effective_eval_stems(self.folder, infos, self.profile)

    def toggle_eval(self) -> str:
        """E: counts <-> does not count. Returns "in" | "out".

        Taking an image out stores False (nothing moves up in its place).
        Putting it back returns it to automatic if it is in the automatic set,
        otherwise it is added explicitly (True)."""
        if self.is_counted():
            self.review.eval_include = False
            return "out"
        self.review.eval_include = None
        if not self.is_counted():
            self.review.eval_include = True
        return "in"

    def set_note(self, target: str | None, text: str) -> bool:
        """Image remark (target None) or a cell's. Empty text deletes it.
        Returns False when nothing changed (debounce without an edit)."""
        rv = self.review
        if target is None:
            if rv.note == text:
                return False
            rv.note = text
            return True
        if rv.notes.get(target, "") == text:
            return False
        if text:
            rv.notes[target] = text
        else:
            rv.notes.pop(target, None)
        return True

    def set_subject_status(self, tp: str, sub: str, done: bool) -> int:
        """Finished flag for every segmented image of one subject. Returns how
        many images changed."""
        if self.folder is None or self.grouped is None:
            return 0
        n = 0
        for info in self.grouped.tree.get(tp, {}).get(sub, []):
            if not has_cache(self.folder, info.path):
                continue          # nothing reviewed yet — leave it alone
            rv = ReviewState.load(self.folder, info.path.stem)
            if rv.is_finished != done:
                rv.is_finished = done
                rv.save(self.folder, info.path.stem)
                n += 1
        self.reload_review()
        return n

    # ── profile (study) ──
    def activate_profile(self, profile: ProfileData) -> None:
        self.profile = profile
        if self.folder is not None:
            set_review_namespace(self.folder, profile.rater or None)
        self.settings.last_profile_name = profile.name
        self.settings.save()

    def apply_edited_profile(self, new: ProfileData) -> ProfileChange:
        old = self.profile
        a, b = old.cellpose, new.cellpose
        change = ProfileChange(
            margin_changed=new.margin_n != old.margin_n,
            cellpose_changed=(a.diameter, a.flow_threshold, a.cellprob_threshold, a.model)
            != (b.diameter, b.flow_threshold, b.cellprob_threshold, b.model),
        )
        save_profile(new, replaces=old.name)    # raises ProfileNameClash before anything changes
        self.profile = new
        self.regroup()     # the filename parser may have changed
        return change

    def any_masks(self) -> bool:
        return self.folder is not None and any(has_cache(self.folder, p) for p in self.images)

    # ── Cellpose ──
    def check_model(self) -> None:
        """Raises ModelUnavailable when this machine cannot supply the study's
        model. A dead stop on purpose: falling back to cpsam would mix two
        models' counts in one study without anything in the numbers showing it."""
        resolve_model(self.profile.cellpose.model, self.settings.cellpose_models)

    def discard_for_resegment(self, path: Path) -> None:
        """Drop masks and review of `path` before a forced re-run."""
        discard_cache(self.folder, path)
        try:
            ReviewState.review_path(self.folder, path.stem).unlink()
        except FileNotFoundError:
            pass
        self.review = ReviewState()

    def stale_images(self) -> list[Path]:
        """Images whose masks were made with other parameters than the study's."""
        from .sidebar_model import is_stale
        if self.folder is None:
            return []
        return [p for p in self.images if is_stale(self.folder, p, self.profile.cellpose)]

    def discard_image(self, path: Path) -> None:
        """Masks + review of any image (the batch re-run calls this per image,
        on its worker thread — it touches only files)."""
        discard_cache(self.folder, path)
        try:
            ReviewState.review_path(self.folder, path.stem).unlink()
        except FileNotFoundError:
            pass

    def segment_task(self, path: Path) -> Task:
        folder, cp, gpu = self.folder, self.profile.cellpose, self.profile.use_gpu
        registry = self.settings.cellpose_models
        return simple(lambda: run_for_image(folder, path, load_image(path), cp, force=True,
                                            use_gpu=gpu, model_registry=registry))

    # ── export ──
    def all_groups(self) -> list[tuple[str, str]]:
        if self.grouped is None:
            return []
        return [(tp, sub) for tp in self.grouped.timepoints()
                for sub in self.grouped.subjects_in(tp)]

    def under_target(self, groups: list[tuple[str, str]]) -> list[tuple[str, str, int, int]]:
        """(timepoint, subject, counted cells, target) for every chosen group
        that stays below the study's target — the export warns about them."""
        from .evaluation import group_eval_summary
        out = []
        for tp, sub in groups:
            infos = self.grouped.tree.get(tp, {}).get(sub, []) if self.grouped else []
            if not infos:
                continue
            s = group_eval_summary(self.folder, infos, self.profile)
            if s["n_active"] < s["target"]:
                out.append((tp, sub, s["n_active"], s["target"]))
        return out

    def export_task(self, groups: list[tuple[str, str]]) -> Task:
        """None to the exporter means "everything" and keeps the cross-group
        overview workbook in the run; a real subset skips it (see export.py)."""
        sel = None if len(groups) == len(self.all_groups()) else groups
        folder, profile, grouped = self.folder, self.profile, self.grouped
        return simple(lambda: export_folder_structured(folder, profile, grouped, groups=sel))

    def mark_exported_finished(self, groups) -> int:
        """Mark the exported groups' segmented images finished. Images without
        masks are left alone, so an untouched image is not declared reviewed."""
        n = 0
        wanted = set(groups)
        for info in self.grouped.flat:
            if (info.timepoint, info.subject) not in wanted:
                continue
            if not has_cache(self.folder, info.path):
                continue
            rv = ReviewState.load(self.folder, info.path.stem)
            if not rv.is_finished:
                rv.is_finished = True
                rv.save(self.folder, info.path.stem)
                n += 1
        self.reload_review()
        return n

    def training_task(self, dest: Path) -> Task:
        folder, profile, grouped = self.folder, self.profile, self.grouped
        return simple(lambda: export_training_data(folder, profile, grouped, dest))

    def areas_task(self) -> Task:
        from .areas import compute_areas
        folder, profile, grouped = self.folder, self.profile, self.grouped
        return simple(lambda: compute_areas(folder, profile, grouped))

    def overview_task(self, dest: Path) -> Task:
        from .overview import write_overview_xlsx
        folder, profile, grouped = self.folder, self.profile, self.grouped
        return simple(lambda: write_overview_xlsx(folder, profile, grouped, dest))

    # ── shutdown ──
    def close(self) -> None:
        self.flush_time()
        self.settings.save()
        save_profile(self.profile)
