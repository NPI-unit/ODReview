"""Menu-bar commands must run after the native menu closed (macOS).

On aqua a menu command that opens a modal dialog otherwise runs inside AppKit's
menu tracking session; on macOS 27 / Tk 9.0.3 the first Combobox dropdown in
that dialog then hangs the app. tkfixes.defer_menu_commands re-schedules every
command with `after 0` so the menu action returns first.
"""

from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview import tkfixes

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    root = tk.Tk(); root.withdraw()
    aqua = root.tk.call("tk", "windowingsystem") == "aqua"
    ran: list[str] = []
    bar = tk.Menu(root)
    sub = tk.Menu(bar, tearoff=0)
    sub.add_command(label="Settings", command=lambda: ran.append("settings"))
    sub.add_separator()
    sub.add_command(label="disabled", state="disabled")
    empty = tk.Menu(sub, tearoff=0)
    sub.add_cascade(label="Recent", menu=empty)
    bar.add_cascade(label="File", menu=sub)

    tkfixes.defer_menu_commands(bar)
    tkfixes.defer_menu_commands(bar)     # idempotent: no `after 0 after 0 …`
    cmd = sub.entrycget(0, "command")
    if not aqua:
        check(not str(cmd).startswith("after"), "non-aqua: commands untouched")
        root.destroy(); print(f"OK — {CHECKS} checks (non-aqua)"); return 0

    parts = [str(x) for x in root.tk.splitlist(cmd)]
    check(parts[:2] == ["after", "0"] and len(parts) == 3,
          "nested cascade command is wrapped exactly once in `after 0`")

    sub.invoke(0)
    check(ran == [], "invoking the menu entry does not run the command synchronously")
    root.update()
    check(ran == ["settings"], "it runs once the event loop is back")

    empty.add_command(label="folder", command=lambda: ran.append("recent"))
    tkfixes.defer_menu_commands(empty)
    empty.invoke(0); check(ran == ["settings"], "entries added later can be wrapped too")
    root.update(); check(ran[-1] == "recent", "and still run")

    root.destroy()
    print(f"OK — {CHECKS} checks: menu-bar commands run after the menu closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
