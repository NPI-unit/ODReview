"""Consistency gate for the cell-area outcome.

The one property that matters: the sample is exactly the one every other metric
uses. If areas were computed over a different set of cells than RBC and
%verändert_2000, two numbers for the same subject would rest on different
denominators and nobody would notice until the numbers were already published.

Synthetic masks, no images needed.  ./.venv/bin/python tests/test_areas.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.areas import AreaStats, compute_areas, to_rows
from erysreview.cellpose_runner import cache_paths, save_mask
from erysreview.grouping import GroupedFolder, ImageGroupInfo
from erysreview.persistence import ReviewState
from erysreview.profile import ProfileData, SizeFilter

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _mask(side: int, k: int, H: int = 80, W: int = 80) -> np.ndarray:
    """k interior square cells of `side`x`side` px — so every area is known."""
    m = np.zeros((H, W), dtype=np.int32)
    idx = 1
    step = side + 4
    for r in range(4, H - side - 4, step):
        for c in range(4, W - side - 4, step):
            if idx > k:
                return m
            m[r:r + side, c:c + side] = idx
            idx += 1
    if idx <= k:
        raise ValueError("grid too small")
    return m


def _add(folder: Path, tp: str, sub: str, stem: str, side: int, k: int) -> ImageGroupInfo:
    info = ImageGroupInfo(path=folder / f"{stem}.tif", timepoint=tp, subject=sub,
                          image_num=None)
    npy, _ = cache_paths(folder, info.path)
    save_mask(npy, _mask(side, k))
    return info


def main() -> int:
    # ── pure statistics ────────────────────────────────────────────────
    st = AreaStats.of([10.0, 20.0, 30.0])
    check(st.n == 3 and st.mean == 20.0 and st.median == 20.0, "mean and median")
    check(st.minimum == 10.0 and st.maximum == 30.0, "min and max")
    check(AreaStats.of([5.0]).sd == 0.0, "a single value reports no spread, not a crash")
    check(AreaStats.of([]).n == 0, "an empty set yields an empty stat block")

    tmp = Path(tempfile.mkdtemp(prefix="erys_area_"))
    profile = ProfileData()
    profile.margin_n = 0
    profile.size_filter = SizeFilter(enabled=False)
    profile.target_cells = 10_000            # cap does not bite: all images count

    # 6x6 = 36 px² cells, 4 of them; and 8x8 = 64 px², 4 of them.
    tree = {"T0": {"S1": [_add(tmp, "T0", "S1", "a", 6, 4),
                          _add(tmp, "T0", "S1", "b", 8, 4)]}}
    flat = tree["T0"]["S1"]
    grouped = GroupedFolder(profile_name=profile.name, tree=tree, flat=flat)

    res = compute_areas(tmp, profile, grouped)
    check(len(res) == 1, "one group in, one group out")
    g = res[0]
    check(g.stats.n == 8, "all 8 cells counted")
    check(abs(g.stats.mean - 50.0) < 1e-6, "mean of 36 and 64 px² cells is 50")
    per = dict(g.per_image)
    check(abs(per["a"].mean - 36.0) < 1e-6, "per-image mean matches the known 36 px²")
    check(abs(per["b"].mean - 64.0) < 1e-6, "per-image mean matches the known 64 px²")

    # ── excluded cells must not count ──────────────────────────────────
    rv = ReviewState.load(tmp, "a")
    rv.force_excluded = [1, 2]
    rv.save(tmp, "a")
    res = compute_areas(tmp, profile, grouped)
    per = dict(res[0].per_image)
    check(per["a"].n == 2, "manually excluded cells drop out of the area sample")
    check(res[0].stats.n == 6, "and out of the group total")

    # ── the eval cap decides which images count ────────────────────────
    # "a" now counts 2 cells (two were excluded above), so a target of 2 is
    # reached by "a" alone and "b" must drop out of the evaluation.
    profile.target_cells = 2
    res = compute_areas(tmp, profile, grouped)
    stems = {s for s, _ in res[0].per_image}
    check(stems == {"a"}, "an image outside the evaluation contributes nothing")

    # ── rows ───────────────────────────────────────────────────────────
    rows = to_rows(res)
    check(rows and rows[0]["ebene"] == "Gruppe", "the group row comes first")
    check(any(r["ebene"] == "Bild" for r in rows), "image rows follow")
    check("flaeche_mittel_px2" in rows[0], "the unit is named in the column")

    print(f"OK — {CHECKS} checks: areas use exactly the sample behind RBC and "
          f"%verändert_2000.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
