"""Consistency gate for the string tables.

A missing key is invisible until the moment a user opens that one dialog, and
then it is a bare KeyError with no fallback — the dialog simply does not appear.
A placeholder that exists in one language but not the other is the same failure
in slower motion: `.format()` raises only for the language nobody tested in.

Static check, no Tk, no data. Walks every module for `S["…"]`, `self.S["…"]`
and `strings["…"]` subscripts and holds them against both tables.

Runs as a plain script:  ./.venv/bin/python tests/test_i18n_keys.py
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from erysreview.i18n import get_strings

STRING_HOLDERS = {"S", "strings", "STRINGS"}


def collect_used(pkg: pathlib.Path) -> dict[str, list[str]]:
    """key -> where it is referenced (for a useful failure message)."""
    used: dict[str, list[str]] = {}
    for f in sorted(pkg.glob("*.py")):
        if f.stem == "i18n":
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Subscript):
                continue
            if not (isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                continue
            val = node.value
            name = (val.id if isinstance(val, ast.Name)
                    else val.attr if isinstance(val, ast.Attribute) else None)
            if name in STRING_HOLDERS:
                used.setdefault(node.slice.value, []).append(f"{f.name}:{node.lineno}")
    return used


def placeholders(text: object) -> set[str]:
    return set(re.findall(r"\{(\w+)", str(text)))


def main() -> int:
    de, en = get_strings("de"), get_strings("en")
    used = collect_used(pathlib.Path(__file__).resolve().parent.parent / "erysreview")

    problems: list[str] = []
    for key, sites in sorted(used.items()):
        for lang, table in (("de", de), ("en", en)):
            if key not in table:
                problems.append(f"{key!r} missing in {lang} — used at {sites[0]}")

    for lang_a, a, lang_b, b in (("de", de, "en", en), ("en", en, "de", de)):
        for key in sorted(set(a) - set(b)):
            problems.append(f"{key!r} defined in {lang_a} but not in {lang_b}")

    for key in sorted(set(de) & set(en)):
        pa, pb = placeholders(de[key]), placeholders(en[key])
        if pa != pb:
            problems.append(f"{key!r} placeholders differ: de={sorted(pa)} en={sorted(pb)}")

    if problems:
        for p in problems:
            print(f"  FAIL {p}")
        return 1

    print(f"  ok  {len(used)} referenced keys resolve in both languages")
    print(f"  ok  tables have identical key sets ({len(de)} entries)")
    print("  ok  format placeholders match across languages")
    print(f"OK — {len(used)} keys checked: no missing string can reach a dialog "
          f"at runtime.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
