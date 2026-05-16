"""Duplicate-cell detection via centroid distance.

A "duplicate" here means: two Cellpose detections whose centroids sit closer than
`threshold_factor × mean_cell_radius`. The smaller of the pair is excluded (default
policy) or both (policy=remove_both). Duplicates are treated visually identical to
border cells (gray, force-includable via the Remove tool).
"""

from __future__ import annotations

import math
import numpy as np

from .canvas import CellInfo
from .profile import DuplicateDetection


def detect_duplicates(
    cell_infos: dict[int, CellInfo],
    config: DuplicateDetection,
) -> set[int]:
    """Returns the set of cell_ids flagged as duplicates (to be excluded from count)."""
    if not config.enabled or len(cell_infos) < 2:
        return set()

    ids = list(cell_infos.keys())
    centroids = np.array([(cell_infos[i].cx, cell_infos[i].cy) for i in ids], dtype=np.float32)
    areas = np.array([cell_infos[i].area for i in ids], dtype=np.float32)
    if areas.size == 0:
        return set()
    mean_radius = math.sqrt(float(areas.mean()) / math.pi)
    threshold = config.threshold_factor * mean_radius

    # Pairwise via scipy if available; fall back to brute force on small sets
    try:
        from scipy.spatial import cKDTree
        tree = cKDTree(centroids)
        pairs = tree.query_pairs(r=threshold)
    except ImportError:
        pairs = set()
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                d = math.hypot(centroids[i, 0] - centroids[j, 0],
                               centroids[i, 1] - centroids[j, 1])
                if d < threshold:
                    pairs.add((i, j))

    flagged: set[int] = set()
    for i, j in pairs:
        cid_i = ids[i]
        cid_j = ids[j]
        if config.policy == "remove_both":
            flagged.add(cid_i)
            flagged.add(cid_j)
        else:
            # remove_smaller (default): drop the cell with smaller area
            if cell_infos[cid_i].area <= cell_infos[cid_j].area:
                flagged.add(cid_i)
            else:
                flagged.add(cid_j)
    return flagged
