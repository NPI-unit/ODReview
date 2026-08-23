"""Consistency gate for the sidebar's automatic width.

The canvas draws with aspect='equal', so an image of ratio r occupies at most
`height * r` pixels of width. On a wide window the rest is grey and unusable by
the image, so it is handed to the sidebar instead.

Two properties are worth pinning. The sash must never move left of the width the
user chose — taking space away overrides a deliberate choice. And the surplus
must never be written back as that chosen width, or the sidebar would keep its
bonus and grow again the next session, and again, until it had eaten the window.
Neither failure raises anything; both are silent.

Pure arithmetic, no Tk.  ./.venv/bin/python tests/test_sash_fit.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.app import desired_sash

CHECKS = 0
IMG_W, IMG_H = 1920, 1200          # the study's images: ratio 1.600


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def main() -> int:
    # Laptop: the canvas is narrower than the image could use, so there is no
    # surplus and the chosen width stands untouched.
    got = desired_sash(1507, 833, IMG_W, IMG_H, floor=432)
    check(got == 432, f"no surplus on a narrow window: sash stays at the floor ({got})")

    # Wide monitor: image capped at 833*1.6 = 1332 px, the rest is surplus.
    got = desired_sash(2555, 833, IMG_W, IMG_H, floor=432)
    check(got == 2555 - 1332, f"the whole surplus goes to the sidebar ({got})")
    check(2555 - got == 1332, "and the canvas keeps exactly what the image can use")

    # Wider still — the canvas must not grow past the cap.
    check(3400 - desired_sash(3400, 833, IMG_W, IMG_H, 432) == 1332,
          "an even wider window adds nothing to the canvas")

    # A dragged sidebar is a floor, never a ceiling.
    check(desired_sash(1507, 833, IMG_W, IMG_H, floor=700) == 700,
          "a wide chosen sidebar is not narrowed to enlarge the image")
    check(desired_sash(2555, 833, IMG_W, IMG_H, floor=700) == 2555 - 1332,
          "but surplus still applies on top of a wide choice")

    # No ratchet: feeding a computed sash back as the floor must not creep.
    w = desired_sash(2555, 833, IMG_W, IMG_H, floor=432)
    check(desired_sash(2555, 833, IMG_W, IMG_H, floor=432) == w,
          "the same window yields the same sash — the result is a function, not a step")
    check(desired_sash(1507, 833, IMG_W, IMG_H, floor=432) == 432,
          "and returning to the small window returns to the chosen width")

    # Both panes keep their minimum.
    check(desired_sash(700, 833, IMG_W, IMG_H, floor=600) == 300,
          "the canvas keeps its 400 px minimum even against a wide floor")
    check(desired_sash(500, 400, IMG_W, IMG_H, floor=50) == 200,
          "the sidebar keeps its 200 px minimum")

    # A tall image inverts the situation: it can never use the full width.
    check(desired_sash(2000, 800, 800, 1200, floor=200) == 2000 - 533,
          "a portrait image leaves a correspondingly larger surplus")

    print(f"OK — {CHECKS} checks: surplus is given, the chosen width is never taken, "
          f"and the sidebar cannot ratchet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
