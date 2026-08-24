"""Consistency gate for the single-cell blit fast path.

Toggling a cell used to redraw the whole figure, which re-resamples all three
full-size image layers: ~510 ms per click on 1200x1920 review data, ~97 % of
the click. `EryCanvas._blit_cell()` instead rebuilds only the rectangle around
the edited cell and copies that to the screen.

Cutting a redraw down to a rectangle is only allowed if nobody can tell. So
this compares the actual pixels: after an edit taken through the fast path, the
canvas buffer must equal the one a full `canvas.draw()` produces from the same
state. Exactly equal — not "close enough", because the failure mode here is a
stale rectangle that looks plausible.

Cases that matter, all exercised:

  * a plain toggle, then a second toggle whose rectangle OVERLAPS the first
    (the reason the cached region is re-taken after every blit)
  * a cell right at the image border, where the rectangle gets clipped
  * a cell with neighbours close enough that their numbers reach into the
    rectangle and have to be redrawn with it
  * after a zoom, where the cached region belongs to a viewport that is gone

Needs a Tk display. Synthetic data only, nothing is written to disk.
    ./.venv/bin/python tests/test_blit_equivalence.py

WINDOWS: run it the way run.bat runs the app, from an ACTIVATED conda env:

    call %USERPROFILE%\\miniforge3\\Scripts\\activate.bat erys
    python tests\\test_blit_equivalence.py

Calling the env's python.exe directly leaves %CONDA_PREFIX%\\Library\\bin off
PATH, and matplotlib's _tkagg then kills the process on its first real draw
(exit 0xC06D007F, no traceback). That hits every test that renders, not just
this one — it is an environment trap, not a defect in the code under test.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.canvas import EryCanvas
from erysreview.persistence import AppSettings, ReviewState
from erysreview.profile import ProfileData

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _synthetic(h: int = 420, w: int = 560) -> tuple[np.ndarray, np.ndarray]:
    """A grid of round cells, plus two that touch the border and two that nearly
    touch each other — the geometries the rectangle logic has to survive."""
    from skimage.draw import disk
    img = np.random.default_rng(7).random((h, w)).astype(np.float32)
    masks = np.zeros((h, w), dtype=np.int32)
    cid = 0
    centres = [(cy, cx) for cy in range(40, h - 30, 90) for cx in range(40, w - 30, 90)]
    centres += [(4, 120), (h - 5, 300)]          # clipped at the border
    centres += [(200, 300), (200, 322)]          # close enough to crowd each other
    for cy, cx in centres:
        cid += 1
        rr, cc = disk((cy, cx), 16, shape=(h, w))
        masks[rr, cc] = cid
    return img, masks


def _buffer(canvas) -> np.ndarray:
    return np.asarray(canvas.buffer_rgba()).copy()


def _full_draw_buffer(cv) -> np.ndarray:
    """What a normal, complete redraw of the current state looks like."""
    cv.canvas.draw()
    cv.update_idletasks()
    return _buffer(cv.canvas)


def _report(fast: np.ndarray, truth: np.ndarray) -> str:
    if fast.shape != truth.shape:
        return f"buffer shape {fast.shape} != {truth.shape}"
    diff = np.any(fast != truth, axis=-1)
    n = int(diff.sum())
    if not n:
        return ""
    ys, xs = np.nonzero(diff)
    return (f"{n} pixels differ, first at (x={xs.min()}, y={ys.min()}), "
            f"bbox x {xs.min()}-{xs.max()} y {ys.min()}-{ys.max()}")


def _toggle_and_compare(cv, cid: int, label: str) -> None:
    """Take one edit through the fast path, then prove it is indistinguishable."""
    info = cv.cell_infos[cid]
    ok = cv._act_remove_toggle(float(info.cx), float(info.cy))
    check(ok, f"{label}: the toggle landed on cell {cid}")
    if not cv.BLIT_ENABLED:
        # Where the fast path is switched off (EryCanvas.BLIT_ENABLED — it is
        # Windows-only because the rebuilt rectangle was measured to render the
        # edited cell visibly darker on macOS), pin what matters there instead:
        # the edit lands and the full path draws it. Skipping outright would let
        # the platform branch rot unnoticed.
        check(cv._blit_bg is None,
              f"{label}: the fast path stays off on this platform")
        return
    check(cv._blit_bg is not None, f"{label}: the fast path was actually used")
    fast = _buffer(cv.canvas)
    truth = _full_draw_buffer(cv)
    problem = _report(fast, truth)
    check(not problem, f"{label}: pixels identical to a full redraw"
                       + (f"\n      {problem}" if problem else ""))


def main() -> int:
    img, masks = _synthetic()
    root = tk.Tk()
    root.geometry("900x640+0+0")
    profile = ProfileData()
    cv = EryCanvas(root, AppSettings(), profile, save_callback=lambda: None)
    cv.pack(fill="both", expand=True)
    root.update()
    cv.set_image(img, masks, ReviewState())
    root.update()
    cv.canvas.draw()
    root.update()

    ids = sorted(cv.cell_infos)
    check(len(ids) > 8, f"the synthetic image has enough cells ({len(ids)})")

    # 1. A plain toggle in the middle of the field.
    middle = [c for c in ids if 60 < cv.cell_infos[c].cy < 350][len(ids) // 4]
    _toggle_and_compare(cv, middle, "plain toggle")

    # 2. The same cell again — the rectangle overlaps the previous one exactly.
    _toggle_and_compare(cv, middle, "same cell twice")

    # 3. A crowded pair: the second cell's rectangle covers the first cell's
    #    number, which the rebuild must put back.
    crowded = sorted(ids, key=lambda c: (abs(cv.cell_infos[c].cy - 200)
                                         + abs(cv.cell_infos[c].cx - 300)))[:2]
    for c in crowded:
        _toggle_and_compare(cv, c, f"crowded neighbour {c}")

    # 4. Cells clipped by the image border.
    h, w = masks.shape
    edge = [c for c in ids
            if cv.cell_infos[c].bbox_y0 <= 1 or cv.cell_infos[c].bbox_y1 >= h - 1]
    check(bool(edge), "the fixture really has a cell on the border")
    _toggle_and_compare(cv, edge[0], "border cell")

    # 5. After a zoom the cached region describes a viewport that no longer
    #    exists; the fast path has to notice and re-take it.
    cv.ax.set_xlim(80, 380)
    cv.ax.set_ylim(300, 60)
    cv._full_redraw()
    cv.canvas.draw()
    root.update()
    _toggle_and_compare(cv, middle, "after a zoom")

    # 6. With the overlay switched off the rectangle must still come out right.
    cv.reset_zoom()
    cv.canvas.draw()
    root.update()
    cv.set_overlay_visible(False)
    cv.canvas.draw()
    root.update()
    _toggle_and_compare(cv, middle, "overlay hidden")
    cv.set_overlay_visible(True)
    cv.canvas.draw()
    root.update()

    # 7. Adding a cell takes the same path, and brings a brand-new number
    #    artist into the rectangle with it.
    empty_y, empty_x = 370.0, 500.0
    check(cv._find_cell_at(empty_x, empty_y) is None,
          "the spot picked for a new cell is empty")
    before = set(cv.cell_infos)
    check(cv._act_add(empty_x, empty_y), "a new cell was added")
    new_id = (set(cv.cell_infos) - before).pop()
    if cv.BLIT_ENABLED:
        fast = _buffer(cv.canvas)
        problem = _report(fast, _full_draw_buffer(cv))
        check(not problem, f"new cell {new_id}: pixels identical to a full redraw"
                           + (f"\n      {problem}" if problem else ""))
    else:
        check(cv._blit_bg is None,
              f"new cell {new_id}: drawn by the full path on this platform")

    root.destroy()
    from erysreview.canvas import EryCanvas as _EC
    how = ("pixel-identical to a full redraw" if _EC.BLIT_ENABLED
           else "off on this platform; edits go through the full redraw")
    print(f"OK — {CHECKS} checks: the single-cell blit is {how}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
