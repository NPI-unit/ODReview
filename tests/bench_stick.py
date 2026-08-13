"""Does per-file cost grow with the number of files in the directory?

    ./.venv/bin/python tests/bench_stick.py /Volumes/YOUR_STICK
    ./.venv/bin/python tests/bench_stick.py /Volumes/YOUR_STICK 50,150,400

Not a test — a measuring instrument. It checks ONE claim behind the reported
"app gets slower the more files I import, especially on Windows":

    exFAT/FAT32 (typical USB stick) has no directory index, so every file open
    scans the directory linearly. `.erys_cache` holds 4 files per image, so at
    780 images ~3120 files sit in one flat directory — and the per-operation
    cost would then grow WITH the folder size, making the app super-linearly
    slower rather than just linearly.

The number that decides it is the last column, **µs per operation**:

    grows with folder size  -> directory-scan effect confirmed; the O(N)-per-click
                               rework targets the real bottleneck
    stays flat              -> no such effect on this medium; the cost is "only"
                               linear and the exFAT thesis does NOT explain the
                               Windows symptom — measure on Windows before
                               spending the rework

Calibration: on APFS (B-tree directory index) this must print a flat line. It
does — 4.2–4.5 µs across 270…2340 files, factor 0.95. If a run on the SSD ever
reports growth, the instrument is broken, not the filesystem.

What is measured: the CLICK path with warm caches — the constant friction. Today
one click costs ~29 filesystem operations *per image in the folder*, because
`app._save_review()` -> `sidebar.refresh_statuses()` walks every image, and
`_group_counters()`'s uncached n_done loop then reads every review JSON again.

Masks are deliberately tiny: the click path never reads a mask, it only stats
its mtime. The file COUNT per directory (4 per image) is exactly as in
production, and that is what this measures. The cold import path (which does
read masks) is out of scope here.

Synthetic data only, written to its own `_erys_sticktest_<n>` subfolder and
removed afterwards. Real images are never touched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# Allow running the file directly (python tests/bench_stick.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

# ── Count filesystem operations by wrapping Path (standalone script: global
# monkeypatching is fine, nothing else runs in this process). ──
CNT: Counter = Counter()
_ORIG = {n: getattr(Path, n) for n in ("exists", "stat", "open", "mkdir", "is_dir")}


def _wrap(name: str):
    fn = _ORIG[name]

    def inner(self, *a, **k):
        CNT[name] += 1
        return fn(self, *a, **k)

    return inner


for _name in _ORIG:
    setattr(Path, _name, _wrap(_name))

from erysreview.cellpose_runner import has_cache            # noqa: E402
from erysreview.evaluation import effective_eval_stems      # noqa: E402
from erysreview.grouping import group_images                # noqa: E402
from erysreview.persistence import ReviewState              # noqa: E402
from erysreview.profile import ProfileData                  # noqa: E402

WORK_PREFIX = "_erys_sticktest_"


def _fstype(path: Path) -> str:
    """Filesystem type via `df -Y` (macOS). Cosmetic — never fails the run."""
    try:
        out = subprocess.run(["df", "-Y", str(path)], capture_output=True,
                             text=True, timeout=10).stdout.splitlines()
        if len(out) > 1:
            return " ".join(out[1].split()[:2])
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _profile() -> ProfileData:
    """T1_P02_003 -> timepoint / subject / image_num via the delimiter parser."""
    p = ProfileData(name="sticktest")
    p.use_simple_parser = True
    p.delimiters = ["_"]
    p.field_roles = ["timepoint", "subject", "image_num"]
    return p


def build(root: Path, n: int) -> tuple[Path, list[Path]]:
    """Create n synthetic images + 3 cache files each, as in production."""
    folder = root / f"{WORK_PREFIX}{n}"
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    cache = folder / ".erys_cache"
    cache.mkdir(parents=True)

    mask = np.zeros((8, 8), dtype=np.int32)   # tiny: never read on the click path
    mask[0:3, 0:3] = 1
    mask[4:7, 4:7] = 2
    params = json.dumps({"diameter": 30.0, "flow_threshold": 0.4,
                         "cellprob_threshold": 0.0, "model": "cpsam"})

    n_tp, n_sub = 3, 10
    per_group = max(1, n // (n_tp * n_sub))
    made = 0
    for tp in range(1, n_tp + 1):
        for sub in range(1, n_sub + 1):
            for k in range(1, per_group + 1):
                if made >= n:
                    break
                stem = f"T{tp}_P{sub:02d}_{k:03d}"
                (folder / f"{stem}.png").write_bytes(b"x")
                np.save(cache / f"{stem}_masks.npy", mask)
                (cache / f"{stem}_masks.json").write_text(params)
                ReviewState(types={"1": "sickle"}).save(folder, stem)
                made += 1
    return folder, sorted(folder.glob("*.png"))


def measure(folder: Path, images: list[Path], profile: ProfileData,
            clicks: int = 5) -> dict:
    """Replay what app._save_review() does per click, with warm caches."""
    grouped = group_images(images, profile)

    # Warm up counts.json + the group caches — the steady state during review.
    for _tp, subs in grouped.tree.items():
        for _sub, imgs in subs.items():
            effective_eval_stems(folder, imgs, profile)

    CNT.clear()
    t0 = time.perf_counter()
    for c in range(clicks):
        cur = grouped.flat[c % len(grouped.flat)]

        # (1) review.save() — every click, per persistence.py's contract
        rv = ReviewState.load(folder, cur.path.stem)
        rv.types[str(c)] = "target"
        rv.save(folder, cur.path.stem)

        # (2) refresh_current_group_counter() — re-evaluates the current group
        effective_eval_stems(folder, grouped.tree[cur.timepoint][cur.subject], profile)

        # (3) refresh_statuses() — _image_status() for every row ...
        for info in grouped.flat:
            if has_cache(folder, info.path):
                ReviewState.load(folder, info.path.stem)
        # ... then _group_counters()'s n_done loop reads them all a second time
        for _tp, subs in grouped.tree.items():
            for _sub, imgs in subs.items():
                for info in imgs:
                    if has_cache(folder, info.path):
                        ReviewState.load(folder, info.path.stem)

    dt = (time.perf_counter() - t0) / clicks
    ops = sum(CNT.values()) / clicks
    return {"ms": dt * 1000, "ops": ops, "us_per_op": (dt * 1e6 / ops) if ops else 0.0}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"ERROR: {root} is not a directory.")
        return 2
    try:
        sizes = ([int(x) for x in sys.argv[2].split(",")]
                 if len(sys.argv) > 2 else [100, 300, 780])
    except ValueError:
        print("ERROR: sizes must be comma-separated integers, e.g. 50,150,400")
        return 2

    print(f"Target:      {root}")
    print(f"Filesystem:  {_fstype(root)}")
    print(f"Sizes:       {sizes}")
    print("\nBuilding synthetic folders (takes a few minutes on a stick) ...\n")

    print(f"{'images':>7} | {'files in cache':>14} | {'ops/click':>9} | "
          f"{'ms/click':>8} | {'µs per operation':>16}")
    print("-" * 70)

    rows: list[tuple[int, dict]] = []
    for n in sizes:
        folder, images = build(root, n)
        try:
            r = measure(folder, images, _profile())
            rows.append((len(images), r))
            print(f"{len(images):>7} | {len(images) * 3:>14} | {r['ops']:>9.0f} | "
                  f"{r['ms']:>8.0f} | {r['us_per_op']:>16.1f}")
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    print("\n" + "=" * 70)
    if len(rows) < 2:
        print("Need at least two sizes to compare. Pass e.g. 100,300,780.")
        return 0

    first, last = rows[0][1]["us_per_op"], rows[-1][1]["us_per_op"]
    growth = (last / first) if first else 0.0
    print(f"Cost per single operation: {first:.1f} µs at {rows[0][0]} images"
          f"  ->  {last:.1f} µs at {rows[-1][0]} images   (factor {growth:.2f})")
    print()
    if growth >= 1.5:
        print("VERDICT: per-operation cost GROWS with the file count.")
        print("         Directory-scan thesis confirmed — the app gets super-linearly")
        print("         slower, which is exactly the reported symptom. The O(N)-per-click")
        print("         rework targets the real bottleneck.")
    else:
        print("VERDICT: per-operation cost stays roughly FLAT.")
        print("         No directory-scan effect on this medium. Cost is 'only' linear.")
        print("         The rework still helps (it removes the per-image work entirely),")
        print("         but the exFAT thesis does NOT explain the Windows symptom —")
        print("         measure on a Windows box before spending the rework.")
    print()
    print(f"For scale: the app does this on EVERY click today — {rows[-1][1]['ops']:.0f}")
    print(f"operations at {rows[-1][0]} images, growing with every imported file.")
    print("After the rework a click touches a handful of files at most, and that")
    print("number no longer depends on how many images are in the folder.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
