"""Profile-editor rules that now live outside the dialog: value ranges and the
sample filenames the parser is configured against."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.nameparser import sample_filenames
from erysreview.profile import LIMITS, clamp_field

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    check(clamp_field("diameter", "2000", 30.0) == 500.0, "typed-in diameter 2000 is snapped to 500")
    check(clamp_field("diameter", "abc", 30.0) == 30.0, "garbage falls back to the old value")
    check(clamp_field("size_min_factor", 0.0, 0.35) == 0.05,
          "a size factor of 0 cannot exclude every cell")
    check(clamp_field("overlay_alpha", float("nan"), 0.55) == 0.55, "NaN falls back too")
    check(all(lo < hi for lo, hi in LIMITS.values()), "every range is non-empty")

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        for n in ("b_1.tif", "a_1.TIF", "._a_1.tif", "a_1_overlay.png", "notes.txt"):
            (d / n).write_bytes(b"x")
        (d / "sub").mkdir(); (d / "sub" / "c_1.tif").write_bytes(b"x")
        names = sample_filenames(d)
        check(names == ["a_1.TIF", "b_1.tif"],
              f"samples skip AppleDouble twins, overlays, non-images and sub-folders ({names})")
    check(sample_filenames(None) == [] and sample_filenames("/no/such/dir") == [],
          "no folder, no samples")
    print(f"OK — {CHECKS} checks: value ranges and parser samples are UI-free.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
