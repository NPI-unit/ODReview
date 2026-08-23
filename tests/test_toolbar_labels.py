"""Consistency gate for the toolbar's two label sets.

The toolbar relabels itself when the window gets narrow: every button carries a
full label and a compact one. The compact label is the only thing the user sees
at small widths, so a wrong one is not cosmetic — it is the entire button.

This exists because of a real defect: a search-and-replace meant for the tool
loop also hit the action loop, so all five action buttons took `str(i)` as their
compact label, where `i` was the tool loop's leftover counter. Every one of
⟳ ◉ ⛶ ↺ ⓘ rendered as "4". Distinctness alone catches that whole family.

Needs a Tk display but no data.  ./.venv/bin/python tests/test_toolbar_labels.py
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.i18n import get_strings
from erysreview.persistence import AppSettings, TOOLS_ORDER
from erysreview.profile import ProfileData
from erysreview.theme import palette
from erysreview.toolbar import GLYPHS, Toolbar

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def main() -> int:
    root = tk.Tk()
    root.withdraw()
    noop = lambda *a, **k: None
    settings = AppSettings()
    tb = Toolbar(root, palette(settings.theme), get_strings("de"), settings,
                 ProfileData(),
                 on_tool_change=noop, on_type_change=noop, on_resegment=noop,
                 on_toggle_overlay=noop, on_reset_zoom=noop, on_undo=noop,
                 on_info=noop, on_export=noop)

    compact = [c for _w, c, _f in tb._labels]
    full = [f for _w, _c, f in tb._labels]

    check(len(set(compact)) == len(compact),
          f"every compact label is distinct ({compact})")
    check(all(f.strip() for f in full), "no button has an empty full label")

    # Two families of button, distinguished by what their compact label is.
    # The numbered tools come first and are labelled by their hotkey digit; the
    # glyph buttons — the two remaining modes plus every action — follow.
    digits = [c for c in compact if c.isdigit()]
    glyphs = set(GLYPHS.values())
    check(digits == [str(i) for i in range(1, len(digits) + 1)],
          f"the numbered tools run 1..{len(digits)} without a gap ({digits})")
    check(compact[:len(digits)] == digits,
          "and they sit at the front of the toolbar")
    check(all(c in glyphs for c in compact[len(digits):]),
          f"every remaining button carries a glyph ({compact[len(digits):]})")
    check(all(f.startswith(c) for c, f in zip(compact[len(digits):], full[len(digits):])),
          "and its full label starts with that same glyph")
    check(all(f.startswith(f"{i}   ") for i, f in enumerate(full[:len(digits)], start=1)),
          "the numbered labels start with their digit")

    # Every mode must be reachable: each entry of TOOLS_ORDER needs a button,
    # whether it is labelled by digit or by glyph.
    check(set(tb.tool_buttons) == set(TOOLS_ORDER),
          f"every tool in TOOLS_ORDER has a button ({sorted(tb.tool_buttons)})")
    for tool, glyph_key in (("note", "note"), ("cursor", "cursor")):
        if tool in TOOLS_ORDER:
            label = tb.tool_buttons[tool].cget("text")
            check(GLYPHS[glyph_key] in label,
                  f"the {tool} tool shows its glyph ({label!r})")

    root.destroy()
    print(f"OK — {CHECKS} checks: compact and full labels stay in sync; no button "
          f"can inherit another's label.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
