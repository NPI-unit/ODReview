"""Cell edits and undo for one image, without any UI toolkit.

The review data of an image (`ReviewState`) only ever changes through the
three tools — add a cell, switch it on/off, set its type — and through undo.
This used to live inside the Tk canvas widget, interleaved with drawing; here
it is the model, and a view (Tk today, Qt next) only asks it to act and then
redraws what the returned cell id says changed.

Derived per-image state lives here too, because every edit depends on it:
the live label image with manual cells burnt in, the per-cell geometry, and
which cells are auto-inactive (border zone, size filter).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from skimage.draw import disk as sk_disk

from .overlay import CellInfo, OverlayColors, compute_cell_infos, is_border_cell
from .persistence import ManualCell, ReviewState
from .profile import ProfileData
from .sizefilter import detect_size_outliers


@dataclass
class Undone:
    """What an undo reverted, so the view knows how much to redraw."""
    kind: str        # "add" | "remove_toggle" | "type"
    cell_id: int


class ReviewEditor:
    def __init__(self, profile: ProfileData, new_circle_radius: int = 17):
        self.profile = profile
        self.new_circle_radius = new_circle_radius
        self.clear()

    def clear(self) -> None:
        self.masks: np.ndarray | None = None
        self.review: ReviewState | None = None
        self.shape: tuple[int, int] | None = None
        self.cell_infos: dict[int, CellInfo] = {}
        self.border_ids: set[int] = set()
        # Auto-excluded by the size filter. The name stays `duplicate_ids`
        # because it maps 1:1 to the `is_duplicate` column of the exported
        # stats CSV and erys.db, which downstream analysis relies on.
        self.duplicate_ids: set[int] = set()
        self.next_id: int = 1
        self.history: list[tuple] = []

    # ── loading / derived state ──
    def load(self, masks: np.ndarray, review: ReviewState) -> None:
        """Take a new image's masks and review; burn manual cells into the mask."""
        self.masks = masks.astype(np.int32)
        self.review = review
        self.shape = self.masks.shape[:2]
        self.history.clear()
        manual_ids: set[int] = set()
        max_id = int(self.masks.max()) if self.masks.size else 0
        for mc in review.manual_cells:
            max_id = max(max_id, mc.cell_id)
            rr, cc = sk_disk((int(mc.y), int(mc.x)), int(mc.radius), shape=self.shape)
            self.masks[rr, cc] = mc.cell_id
            manual_ids.add(mc.cell_id)
        self.next_id = max_id + 1
        if review.next_manual_id < self.next_id:
            review.next_manual_id = self.next_id
        self.cell_infos = compute_cell_infos(self.masks, manual_ids)
        self.recompute_borders()
        self.recompute_duplicates()

    def set_profile(self, profile: ProfileData) -> None:
        self.profile = profile
        self.recompute_borders()
        self.recompute_duplicates()      # the profile carries the size filter

    def set_margin_n(self, n: int) -> None:
        self.profile.margin_n = int(n)
        self.recompute_borders()

    def recompute_borders(self) -> None:
        if self.shape is None:
            self.border_ids = set()
            return
        h, w = self.shape
        n = self.profile.margin_n
        self.border_ids = {cid for cid, info in self.cell_infos.items()
                           if is_border_cell(info, h, w, n)}

    def recompute_duplicates(self) -> None:
        self.duplicate_ids = detect_size_outliers(self.cell_infos, self.profile.size_filter)

    def colors(self) -> OverlayColors:
        """Colour/activity resolver for the current state. Cheap — rebuilt per
        operation so it never holds a stale view of the review."""
        return OverlayColors(self.review, self.profile, self.border_ids, self.duplicate_ids)

    def find_cell_at(self, x: float, y: float) -> int | None:
        if self.masks is None:
            return None
        yi, xi = int(round(y)), int(round(x))
        if 0 <= yi < self.masks.shape[0] and 0 <= xi < self.masks.shape[1]:
            cid = int(self.masks[yi, xi])
            return cid if cid > 0 else None
        return None

    # ── the three tools ──
    def add(self, x: float, y: float) -> int | None:
        """New manual cell centred on (x, y). Returns its id."""
        if self.masks is None or self.review is None:
            return None
        r = self.new_circle_radius
        new_id = self.next_id
        self.next_id += 1
        self.review.next_manual_id = self.next_id
        rr, cc = sk_disk((int(y), int(x)), r, shape=self.masks.shape)
        self.masks[rr, cc] = new_id
        self.review.manual_cells.append(
            ManualCell(cell_id=new_id, x=float(x), y=float(y), radius=float(r)))
        self.cell_infos[new_id] = CellInfo(
            cell_id=new_id, cy=float(y), cx=float(x), area=int(np.pi * r ** 2),
            bbox_y0=int(y) - r, bbox_x0=int(x) - r,
            bbox_y1=int(y) + r + 1, bbox_x1=int(x) + r + 1,
            is_manual=True,
        )
        h, w = self.shape
        if is_border_cell(self.cell_infos[new_id], h, w, self.profile.margin_n):
            self.border_ids.add(new_id)
        self.history.append(("add", new_id))
        return new_id

    def toggle_active(self, x: float, y: float) -> int | None:
        """Flip the effective active state of the cell under (x, y).

          - active normal          -> force_excluded
          - force_excluded         -> back to default
          - border (inactive)      -> force_included
          - force_included         -> back to border
        """
        cid = self.find_cell_at(x, y)
        if cid is None or self.review is None:
            return None
        rv = self.review
        prev = (cid in rv.force_excluded, cid in rv.force_included)
        if cid in rv.force_excluded:
            rv.force_excluded.remove(cid)
        elif cid in rv.force_included:
            rv.force_included.remove(cid)
        elif cid in self.border_ids:
            rv.force_included.append(cid)
        else:
            rv.force_excluded.append(cid)
        self.history.append(("remove_toggle", cid, prev))
        return cid

    def set_type(self, x: float, y: float, type_key: str) -> int | None:
        cid = self.find_cell_at(x, y)
        if cid is None or self.review is None:
            return None
        prev = self.review.types.get(str(cid))
        self.review.types[str(cid)] = type_key
        self.history.append(("type", cid, prev))
        return cid

    def undo(self) -> Undone | None:
        if not self.history or self.review is None or self.masks is None:
            return None
        action = self.history.pop()
        kind = action[0]
        rv = self.review
        if kind == "remove_toggle":
            _, cid, (was_excluded, was_included) = action
            if cid in rv.force_excluded:
                rv.force_excluded.remove(cid)
            if cid in rv.force_included:
                rv.force_included.remove(cid)
            if was_excluded:
                rv.force_excluded.append(cid)
            if was_included:
                rv.force_included.append(cid)
        elif kind == "add":
            _, cid = action
            self.masks[self.masks == cid] = 0
            rv.manual_cells = [m for m in rv.manual_cells if m.cell_id != cid]
            self.cell_infos.pop(cid, None)
            self.border_ids.discard(cid)
            self.next_id = max(1, self.next_id - 1)
            rv.next_manual_id = self.next_id
        else:  # "type"
            _, cid, prev = action
            if prev is None:
                rv.types.pop(str(cid), None)
            else:
                rv.types[str(cid)] = prev
        return Undone(kind, cid)
