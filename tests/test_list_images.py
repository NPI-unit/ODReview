"""Consistency gate for folder scanning.

Guards one property that is easy to break and expensive when broken: only real
image files are listed. On any FAT/exFAT volume — every USB stick — macOS writes
an AppleDouble `._<name>` twin next to each file. Those carry the image
extension but are 4 KB of binary metadata, and `._` sorts before letters, so
including them meant a folder opened on a blob: load_image failed, the canvas
stayed empty, and every mouse click died silently at the `image is None` guard.
It also doubled the apparent image count (238 photos -> 476 "images").

Runs as a plain script:  ./.venv/bin/python tests/test_list_images.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.cellpose_runner import list_images

CHECKS = 0
TIFF_MAGIC = b"II*\x00"
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07Mac OS X" + b"\x00" * 64


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="erys_scan_"))
    sub = tmp / "tag2"
    sub.mkdir()
    hidden_dir = tmp / ".erys_cache"
    hidden_dir.mkdir()

    for i in range(4):
        (tmp / f"P_{i:03d}.tif").write_bytes(TIFF_MAGIC)
        (tmp / f"._P_{i:03d}.tif").write_bytes(APPLEDOUBLE_MAGIC)
    (sub / "P_100.tif").write_bytes(TIFF_MAGIC)
    (sub / "._P_100.tif").write_bytes(APPLEDOUBLE_MAGIC)
    (hidden_dir / "P_900.tif").write_bytes(TIFF_MAGIC)          # cache stays out
    (tmp / "P_200_overlay.png").write_bytes(b"\x89PNG")          # export artifact
    (tmp / ".DS_Store").write_bytes(b"junk")

    got = list_images(tmp)
    names = sorted(p.name for p in got)

    check(len(got) == 5, f"only the 5 real images are listed (got {len(got)})")
    check(not any(p.name.startswith(".") for p in got),
          "no dot-file reaches the list")
    check("P_100.tif" in names, "images in sub-folders are still found")
    check(not any(p.name.endswith("_overlay.png") for p in got),
          "export overlays stay out")
    check(not any("erys_cache" in p.parts for p in got),
          "the cache directory stays out")

    # The flat fallback (recursion empty) must filter identically.
    flat = Path(tempfile.mkdtemp(prefix="erys_flat_"))
    (flat / "._only.tif").write_bytes(APPLEDOUBLE_MAGIC)
    check(list_images(flat) == [],
          "a folder holding only AppleDouble twins yields nothing, not phantoms")
    (flat / "only.tif").write_bytes(TIFF_MAGIC)
    check([p.name for p in list_images(flat)] == ["only.tif"],
          "the real image next to a twin is still found")

    shutil.rmtree(tmp)
    shutil.rmtree(flat)
    print(f"OK — {CHECKS} checks: only real images are scanned; AppleDouble twins "
          f"on FAT volumes cannot become phantom images.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
