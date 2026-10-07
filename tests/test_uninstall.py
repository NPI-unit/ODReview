"""Uninstall (Einstellungen › Allgemein) — on a fake app folder and a fake
home in a temp directory, never on the real ones.

The plan lists exactly the known places (model and settings only when
chosen), refuses anything else, the script deletes only after the app's
process has ended, images/reviews and the app folder itself survive, and
the window flow stops at either of the two confirmations."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS = 0
OUT = Path(__file__).resolve().parent / "_out"


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def fake(tmp: Path) -> tuple[Path, Path, Path, Path]:
    root = tmp / "ErysReview"; (root / "erysreview").mkdir(parents=True)
    (root / "erysreview" / "__init__.py").write_text("__version__ = '0'\n")
    (root / ".venv" / "bin").mkdir(parents=True); (root / ".venv" / "bin" / "python").write_text("x")
    (root / "vendor" / "wheels").mkdir(parents=True); (root / "vendor" / "wheels" / "a.whl").write_bytes(b"0" * 2000)
    (root / "install.log").write_text("log")
    home = tmp / "home"
    (home / "Desktop" / "ErysReview.app" / "Contents").mkdir(parents=True)
    (home / ".cellpose" / "models").mkdir(parents=True); (home / ".cellpose" / "models" / "cpsam").write_bytes(b"m" * 3000)
    support = home / "Library" / "Application Support" / "Erys"; support.mkdir(parents=True)
    (support / "settings.json").write_text("{}")
    images = home / "Studie"; (images / ".erys_cache").mkdir(parents=True)
    (images / "Bild_01.tif").write_bytes(b"tif"); (images / ".erys_cache" / "Bild_01_review.json").write_text("{}")
    return root, home, support, images


def main() -> int:
    from erysreview import uninstall as un
    tmp = Path(tempfile.mkdtemp())
    root, home, support, images = fake(tmp)

    items = un.plan(root, home, support, remove_model=False, remove_settings=False)
    check({i.key for i in items} == {"un_venv", "un_vendor", "un_desktop_app", "un_log"},
          "by default: .venv, vendor/, the Desktop app and the log")
    items_all = un.plan(root, home, support, remove_model=True, remove_settings=True)
    check({"un_model", "un_settings"} <= {i.key for i in items_all}, "model and settings only when chosen")
    try:
        un.plan(tmp, home, support, False, False); refused = False
    except ValueError:
        refused = True
    check(refused, "a folder that is not ErysReview is refused")
    try:
        un._check_safe([un.Item("un_settings", home, 0)], root, home); refused = False
    except ValueError:
        refused = True
    check(refused, "never anything outside the known places (e.g. the whole home)")

    # The script waits for the app's process, then deletes.
    sleeper = subprocess.Popen(["sleep", "1.5"])
    script = un.write_script(items_all, sleeper.pid, tmp)
    proc = subprocess.Popen(["/bin/bash", str(script)])
    time.sleep(0.5)
    check((root / ".venv").exists(), "nothing is deleted while the app still runs")
    sleeper.wait(); proc.wait(timeout=10)
    check(not (root / ".venv").exists() and not (root / "vendor").exists()
          and not (home / "Desktop" / "ErysReview.app").exists()
          and not (home / ".cellpose" / "models" / "cpsam").exists() and not support.exists(),
          "after the app quit, everything on the list is gone")
    check((images / "Bild_01.tif").is_file() and (images / ".erys_cache" / "Bild_01_review.json").is_file()
          and (root / "erysreview" / "__init__.py").is_file() and (home / ".cellpose" / "models").is_dir(),
          "images, reviews and the app folder itself stay")
    check(not script.exists(), "the script removes itself")

    # Window flow: two confirmations, either can stop it.
    root, home, support, images = fake(Path(tempfile.mkdtemp()))
    from erysreview.qt import about
    from erysreview.i18n import get_strings
    from PySide6.QtWidgets import QApplication
    from erysreview.qt.app import unhide_qt_plugins
    unhide_qt_plugins()
    QApplication.instance() or QApplication(["t"])
    about.un.app_root = lambda: root
    os.environ["HOME"] = str(home)
    import erysreview.persistence as pers
    pers.app_support_dir = lambda: support
    S = get_strings("de")
    calls = {"launch": [], "quit": 0}
    launch = lambda p: calls["launch"].append(p)
    quit_app = lambda: calls.__setitem__("quit", calls["quit"] + 1)
    texts = []
    def asker(answers):
        it = iter(answers)
        return lambda title, text, yes: (texts.append(text), next(it))[1]
    check(not about.start_uninstall(None, S, False, False, ask=asker([False]), launch=launch, quit_app=quit_app)
          and not calls["launch"], "'Abbrechen' at the warning stops it")
    check("Bilder, Masken, Bewertungen" in texts[0] and ".venv" in texts[0], "the warning lists what goes and what stays")
    check(not about.start_uninstall(None, S, False, False, ask=asker([True, False]), launch=launch, quit_app=quit_app)
          and not calls["launch"], "'Abbrechen' at the second question stops it too")
    check(about.start_uninstall(None, S, True, True, ask=asker([True, True]), launch=launch, quit_app=quit_app)
          and len(calls["launch"]) == 1 and calls["quit"] == 1, "two confirmations: script launched, app quits")
    check(".venv" in calls["launch"][0].read_text() and (root / ".venv").exists(),
          "the app itself deletes nothing — the script does, after quitting")
    print(f"OK — {CHECKS} checks: uninstall removes only what it lists, after the app quit, after two confirmations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
