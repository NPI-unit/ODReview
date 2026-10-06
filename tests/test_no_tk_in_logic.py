"""Guard for the Qt port: the logic modules must load without Tk.

Anything imported here may be used by a non-Tk front end (and by headless
code paths). If one of them pulls in tkinter or a Tk matplotlib backend
again — usually through an import from canvas/dialogs/extras — this fails.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOGIC = [
    "areas", "batch", "cellpose_runner", "controller", "counts", "db", "editor", "evaluation", "export",
    "grouping", "i18n", "image_state", "nameparser", "overlay", "overview",
    "package", "persistence", "profile", "sizefilter", "stats", "study",
    "summary", "tasks", "theme",
]

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
    if failures:
        raise SystemExit("FAIL:\n  " + "\n  ".join(failures))
    print(f"OK — {len(LOGIC)} logic modules import without tkinter.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
