"""Implausible-cell-size detection.

Erythrocytes in one field of view are strikingly uniform — measured over 3164
cells in 20 TRIBAL images the interquartile range of the cell area is 0.90-1.09x
the median. That makes area a sharp signal: anything far below the median is a
segmentation fragment, anything far above is two cells Cellpose merged into one
label. Both are excluded from the count (rendered gray, re-includable with the
*Zählen ein/aus* tool), exactly like border cells.

Replaces the previous centroid-distance duplicate detection, which was measured
to be inert — see docs/consistency-audit.md, PF-13. Short version: a Cellpose
mask is a pixel partition, so two labels can never overlap; two *touching* cells
sit ~2 radii apart, and the old rule needed them within 0.7 radii.

Bounds are relative to the median of THAT image, so magnification and exposure
differences between images take care of themselves.
"""

from __future__ import annotations

import numpy as np

from .overlay import CellInfo
from .profile import SizeFilter


def detect_size_outliers(
    cell_infos: dict[int, CellInfo],
    config: SizeFilter,
) -> set[int]:
    """Cell ids whose area is implausible for a single cell (to be excluded).

    Uses the median, not the mean: fragments pull a mean downward and would
    partly mask themselves.
    """
    if not config.enabled or len(cell_infos) < 2:
        return set()

    areas = np.fromiter((i.area for i in cell_infos.values()),
                        dtype=np.float64, count=len(cell_infos))
    median = float(np.median(areas))
    if median <= 0:
        return set()

    lo = float(config.min_factor) * median
    hi = float(config.max_factor) * median
    return {
        cid for cid, info in cell_infos.items()
        if info.area < lo or info.area > hi
    }
