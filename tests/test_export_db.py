"""Consistency gate for the SQLite reporting store (Step 1).

Proves the erys.db SQL views return the SAME numbers as group_eval_summary()
(the existing canonical aggregation), so the DB can safely become the source of
truth in Step 2. Runs as a plain script:  python -m tests.test_export_db
(or  ./.venv/bin/python tests/test_export_db.py).

Synthetic data only — no real images needed: masks are written straight into
the .erys_cache as label arrays, reviews are set in-memory and saved.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

# Allow running the file directly (python tests/test_export_db.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview import db
from erysreview.cellpose_runner import cache_paths, save_mask
from erysreview.evaluation import group_eval_summary
from erysreview.grouping import GroupedFolder, ImageGroupInfo
from erysreview.persistence import ReviewState
from erysreview.profile import ProfileData
from erysreview.profile import DuplicateDetection


def _make_mask(k: int, H: int = 60, W: int = 60) -> np.ndarray:
    """A label image with k well-separated interior 6×6 cells (no border touch)."""
    m = np.zeros((H, W), dtype=np.int32)
    idx = 1
    for r in range(5, H - 10, 12):
        for c in range(5, W - 10, 12):
            if idx > k:
                return m
            m[r:r + 6, c:c + 6] = idx
            idx += 1
    if idx <= k:
        raise ValueError(f"grid too small for {k} cells")
    return m


def _add_image(folder: Path, stem: str, n_cells: int,
               types: dict[int, str] | None = None) -> None:
    """Write a cached mask + (optional) review for one synthetic image."""
    info = ImageGroupInfo(path=folder / f"{stem}.tif", timepoint="x", subject="x",
                          image_num=None)
    npy, _meta = cache_paths(folder, info.path)
    save_mask(npy, _make_mask(n_cells))
    if types:
        rv = ReviewState()
        rv.types = {str(cid): t for cid, t in types.items()}
        rv.save(folder, stem)


def _approx(a: float, b: float, tol: float = 1e-6) -> bool:
    return abs(float(a) - float(b)) <= tol


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="erysdb_"))
    profile = ProfileData()
    profile.target_cells = 5                       # small, so the eval-cap kicks in
    profile.margin_n = 0
    profile.duplicate_detection = DuplicateDetection(enabled=False)

    # Two groups under one timepoint, plus a second timepoint.
    # Group (T0,S1): 3 images; cap=5 should select the first two only.
    _add_image(tmp, "T0_S1_a", 4, {1: "stechapfel"})       # rbc 4, 1 changed
    _add_image(tmp, "T0_S1_b", 3, {})                       # rbc 3, all normal
    _add_image(tmp, "T0_S1_c", 5, {1: "elongiert", 2: "elongiert"})  # excluded by cap
    # Group (T0,S2): 1 image, all normal.
    _add_image(tmp, "T0_S2_a", 6, {})
    # Group (T1,S1): 2 images with several typed cells.
    _add_image(tmp, "T1_S1_a", 5, {1: "stechapfel", 2: "elongiert"})
    _add_image(tmp, "T1_S1_b", 4, {1: "stechapfel"})

    def info(stem: str, tp: str, sub: str, num: int) -> ImageGroupInfo:
        return ImageGroupInfo(path=tmp / f"{stem}.tif", timepoint=tp, subject=sub,
                              image_num=num)

    tree = {
        "T0": {
            "S1": [info("T0_S1_a", "T0", "S1", 1),
                   info("T0_S1_b", "T0", "S1", 2),
                   info("T0_S1_c", "T0", "S1", 3)],
            "S2": [info("T0_S2_a", "T0", "S2", 1)],
        },
        "T1": {
            "S1": [info("T1_S1_a", "T1", "S1", 1),
                   info("T1_S1_b", "T1", "S1", 2)],
        },
    }
    flat = [i for subs in tree.values() for imgs in subs.values() for i in imgs]
    grouped = GroupedFolder(profile_name=profile.name, tree=tree, flat=flat)

    dest = tmp / "erys.db"
    assert db.write_db(tmp, profile, grouped, dest) == dest, "write_db returned None"

    import sqlite3
    conn = sqlite3.connect(str(dest))
    conn.row_factory = sqlite3.Row

    target = profile.target_cells
    checked = 0
    for tp, subs in tree.items():
        for sub, imgs in subs.items():
            want = group_eval_summary(tmp, imgs, profile)
            row = conn.execute(
                "SELECT * FROM v_group_summary WHERE timepoint=? AND subject=?",
                (tp, sub)).fetchone()
            assert row is not None, f"missing v_group_summary row for {tp}/{sub}"

            assert row["n_images"] == want["n_images"], \
                f"{tp}/{sub} n_images {row['n_images']} != {want['n_images']}"
            assert row["sum_rbc"] == want["n_active"], \
                f"{tp}/{sub} sum_rbc {row['sum_rbc']} != {want['n_active']}"
            assert _approx(row["pct_changed"], want["pct_changed"]), \
                f"{tp}/{sub} pct_changed {row['pct_changed']} != {want['pct_changed']}"
            assert _approx(row["pct_changed_per_target"], want["pct_changed_2000"]), \
                f"{tp}/{sub} per_target {row['pct_changed_per_target']} != {want['pct_changed_2000']}"
            assert _approx(row["pct_target"], 100.0 * want["n_active"] / target), \
                f"{tp}/{sub} pct_target mismatch"

            # Per-type counts (long view) must equal by_type for active types.
            type_rows = conn.execute(
                "SELECT type_key, n FROM v_group_type WHERE timepoint=? AND subject=?",
                (tp, sub)).fetchall()
            db_by_type = {r["type_key"]: r["n"] for r in type_rows}
            for k, v in want["by_type"].items():
                if v == 0:
                    assert db_by_type.get(k, 0) == 0, f"{tp}/{sub} type {k} should be 0"
                else:
                    assert db_by_type.get(k, 0) == v, \
                        f"{tp}/{sub} type {k} {db_by_type.get(k, 0)} != {v}"
            assert sum(db_by_type.values()) == want["n_active"], \
                f"{tp}/{sub} Σ per-type != sum_rbc"
            checked += 1

    # Atom counts sanity: every cached image is stored; cells == labels.
    n_images_db = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    assert n_images_db == len(flat), f"images {n_images_db} != {len(flat)}"
    # The legend is populated.
    n_defs = conn.execute("SELECT COUNT(*) FROM metric_definitions").fetchone()[0]
    assert n_defs >= 5, "metric_definitions not seeded"

    # Idempotency / incremental: a second write must not change the numbers.
    db.write_db(tmp, profile, grouped, dest)
    row2 = conn.execute(
        "SELECT sum_rbc, pct_changed FROM v_group_summary "
        "WHERE timepoint='T1' AND subject='S1'").fetchone()
    assert row2["sum_rbc"] == group_eval_summary(tmp, tree["T1"]["S1"], profile)["n_active"]

    conn.close()
    print(f"OK — {checked} groups: v_group_summary / v_group_type == group_eval_summary; "
          f"{n_images_db} images, {n_defs} metric definitions; incremental re-write stable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
