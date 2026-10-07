"""Uninstalling ErysReview (Einstellungen › Allgemein) — without any UI.

What goes, always: the app's Python environment (.venv), the packages kept for
offline installs (vendor/), ErysReview.app on the Desktop, install.log.
Optional: the Cellpose model (~/.cellpose/models/cpsam — other Cellpose
programs may use it) and the settings & studies (app support folder).
Never: image folders, masks, reviews, exports — they live with the images —
and the app folder itself (the code), which the user moves to the Trash.

A running app cannot delete the environment it runs from, so the deleting is
done by a small shell script that waits until the app has quit.
"""

from __future__ import annotations

import os
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Item:
    key: str            # i18n key of the label
    path: Path
    size: int           # bytes (0 if missing)


def _size(p: Path) -> int:
    if p.is_symlink() or p.is_file():
        try:
            return p.lstat().st_size if p.is_symlink() else p.stat().st_size
        except OSError:
            return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).lstat().st_size
            except OSError:
                pass
    return total


def app_root() -> Path:
    return Path(__file__).resolve().parent.parent


def plan(root: Path, home: Path, support: Path, remove_model: bool, remove_settings: bool) -> list[Item]:
    """What would be deleted — only paths that exist."""
    if not (root / "erysreview" / "__init__.py").is_file():
        raise ValueError(f"not an ErysReview folder: {root}")
    cand = [("un_venv", root / ".venv"), ("un_vendor", root / "vendor"),
            ("un_desktop_app", home / "Desktop" / "ErysReview.app"), ("un_log", root / "install.log")]
    if remove_model:
        cand.append(("un_model", home / ".cellpose" / "models" / "cpsam"))
    if remove_settings:
        cand.append(("un_settings", support))
    items = []
    for key, p in cand:
        if p.exists() or p.is_symlink():
            items.append(Item(key, p, _size(p)))
    _check_safe(items, root, home)
    return items


def _check_safe(items: list[Item], root: Path, home: Path) -> None:
    """Refuse anything that is not one of the known places (guard against a
    wrong root or home ever reaching rm -rf)."""
    allowed = {root / ".venv", root / "vendor", root / "install.log",
               home / "Desktop" / "ErysReview.app", home / ".cellpose" / "models" / "cpsam"}
    for it in items:
        p = it.path
        if p in allowed:
            continue
        if it.key == "un_settings" and p.name.lower() in ("erys",) and home in p.parents:
            continue
        raise ValueError(f"refusing to delete {p}")


def write_script(items: list[Item], wait_pid: int, script_dir: Path | None = None) -> Path:
    """A shell script that waits for `wait_pid` to end, deletes the items,
    and says so (macOS notification). Returns its path."""
    lines = ["#!/bin/bash", "# ErysReview uninstall — written by the app, runs after it quit.",
             f"while kill -0 {int(wait_pid)} 2>/dev/null; do sleep 0.5; done"]
    for it in items:
        lines.append(f"rm -rf {shlex.quote(str(it.path))}")
    lines.append('osascript -e \'display notification "ErysReview wurde deinstalliert." '
                 'with title "ErysReview"\' >/dev/null 2>&1 || true')
    lines.append('rm -f "$0"')
    d = script_dir or Path(tempfile.gettempdir())
    p = d / f"erysreview-uninstall-{wait_pid}.sh"
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o755)
    return p


def fmt_size(n: int, decimal_sep: str = ",") -> str:
    if n < 1e6:
        return "< 1 MB"
    if n < 1e9:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e9:.1f} GB".replace(".", decimal_sep)
