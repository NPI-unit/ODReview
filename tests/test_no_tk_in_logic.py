"""Guard: the app has no Tk any more (Phase 5) — nothing may load tkinter.

Every module of the package, Qt front end included, is imported on its own;
if one of them pulls in tkinter or a Tk matplotlib backend, this fails. A
second check: no file of the package mentions tkinter at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PKG = ROOT / "erysreview"
LOGIC = sorted(
    ".".join(p.relative_to(PKG).with_suffix("").parts)
    for p in PKG.rglob("*.py")
    if p.name != "__init__.py" and " " not in p.name and "__pycache__" not in p.parts
)

PROBE = """
import sys, importlib
sys.path.insert(0, {root!r})
importlib.import_module("erysreview.{mod}")
bad = sorted(m for m in sys.modules
             if m == "tkinter" or m.startswith("tkinter.") or m.endswith("backend_tkagg"))
print(",".join(bad))
"""


def main() -> int:
    failures = []
    for mod in LOGIC:
        out = subprocess.run([sys.executable, "-c", PROBE.format(root=str(ROOT), mod=mod)],
                             capture_output=True, text=True)
        if out.returncode != 0:
            failures.append(f"{mod}: import failed\n{out.stderr.strip().splitlines()[-1]}")
        elif out.stdout.strip():
            failures.append(f"{mod}: loads {out.stdout.strip()}")
        else:
            print(f"  ok  erysreview.{mod} loads without Tk")
    for p in PKG.rglob("*.py"):
        if " " not in p.name and "tkinter" in p.read_text(encoding="utf-8"):
            failures.append(f"{p.relative_to(ROOT)} mentions tkinter")
    if failures:
        raise SystemExit("FAIL:\n  " + "\n  ".join(failures))
    print(f"OK — {len(LOGIC)} modules import without tkinter; no file mentions it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
