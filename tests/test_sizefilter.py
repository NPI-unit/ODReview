"""Gate for the implausible-size filter (sizefilter.py).

The filter decides which cells drop out of the count, so it is lab-relevant:
a wrong bound silently changes every percentage in the export. Pins the
behaviour that matters — including the median-vs-mean choice, which is the
reason the previous mean-based rule partly masked its own targets.

    ./.venv/bin/python tests/test_sizefilter.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.canvas import CellInfo
from erysreview.profile import SizeFilter
from erysreview.sizefilter import detect_size_outliers


def _cells(*areas: int) -> dict[int, CellInfo]:
    """Cell infos with the given areas; geometry is irrelevant to this filter."""
    return {
        i + 1: CellInfo(cell_id=i + 1, cy=0.0, cx=0.0, area=a,
                        bbox_y0=0, bbox_x0=0, bbox_y1=1, bbox_x1=1)
        for i, a in enumerate(areas)
    }


def main() -> int:
    cfg = SizeFilter()          # defaults: 0.35 / 1.70
    checks = 0

    def check(label: str, got, want) -> None:
        nonlocal checks
        assert got == want, f"{label}: got {sorted(got)}, want {sorted(want)}"
        checks += 1
        print(f"  ok  {label}")

    # Ten uniform cells (median 100) plus one fragment and one merged pair.
    uniform = [100, 100, 100, 100, 100, 100, 100, 100, 100, 100]
    infos = _cells(*uniform, 20, 250)
    frag_id, merged_id = len(uniform) + 1, len(uniform) + 2
    check("fragment and merged cell flagged, uniform ones untouched",
          detect_size_outliers(infos, cfg), {frag_id, merged_id})

    # Exactly on the bounds: 0.35 and 1.70 must NOT flag (strict comparison).
    check("cells exactly on the bounds stay in",
          detect_size_outliers(_cells(*uniform, 35, 170), cfg), set())
    check("one unit past each bound is flagged",
          detect_size_outliers(_cells(*uniform, 34, 171), cfg),
          {frag_id, merged_id})

    # Disabled -> never flags, whatever the areas.
    check("disabled filter flags nothing",
          detect_size_outliers(infos, SizeFilter(enabled=False)), set())

    # Fewer than two cells -> no meaningful median.
    check("single cell is never an outlier",
          detect_size_outliers(_cells(100), cfg), set())

    # MEDIAN, not mean: a pile of fragments must not drag the reference down and
    # thereby stop being detected. Mean here is 61.7, so a mean-based 0.35 bound
    # would sit at 21.6 and miss the 30-px fragments entirely.
    many_frags = _cells(100, 100, 100, 100, 100, 100, 30, 30, 30, 30, 30)
    got = detect_size_outliers(many_frags, cfg)
    check("median reference still catches fragments in a fragment-heavy image",
          got, {7, 8, 9, 10, 11})

    # Custom bounds are honoured.
    check("custom bounds",
          detect_size_outliers(_cells(*uniform, 60, 140),
                               SizeFilter(True, min_factor=0.7, max_factor=1.3)),
          {frag_id, merged_id})

    print(f"OK — {checks} checks: size filter bounds, median reference, "
          f"disable switch and edge cases behave as specified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
