"""Consistency gate for the update check.

No network: the version comparison is pure, and every failure path is forced by
substituting urlopen. A gate that needs the internet fails on the lab machines
this software runs on, which is exactly where it would matter least.

Wired in the Qt app (Hilfe › Nach Updates suchen; daily start-up check opt-in). While
the repository is private, GitHub answers 404 and the app says "nicht prüfbar" —
never a false "up to date".

Runs as a plain script:  ./.venv/bin/python tests/test_updater.py
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview import updater

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _serving(payload: dict):
    return lambda req, timeout=None: _Resp(json.dumps(payload).encode("utf-8"))


def _raising(exc):
    def boom(req, timeout=None):
        raise exc
    return boom


def qt_checks() -> None:
    """The menu action end to end, off-screen, with a stubbed GitHub."""
    import os
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from erysreview import persistence, profile as profile_mod
    tmp = Path(tempfile.mkdtemp())
    persistence.app_support_dir = lambda: tmp
    profile_mod.app_support_dir = lambda: tmp
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QMessageBox
    from erysreview.persistence import AppSettings
    from erysreview.profile import ProfileData, save_profile
    save_profile(ProfileData(name="U"))
    AppSettings(last_profile_name="U", language="de").save()
    from erysreview.qt.app import MainWindow, make_app
    from erysreview.qt.tasks import shutdown
    make_app(["t"])
    shown: list[str] = []
    QMessageBox.information = staticmethod(lambda _p, _t, text, *a, **k: (shown.append(text), QMessageBox.Ok)[1])
    win = MainWindow()
    real = updater.fetch_latest
    for answer, expect in ((None, "Keine Update-Information"),
                           (updater.Release(tag="v99.0.0", version="99.0.0", url="https://x", notes=""), "99.0.0")):
        updater.fetch_latest = lambda repo=None, timeout=None, a=answer: a
        shown.clear()
        win.check_updates(silent=False)
        for _ in range(40):
            QTest.qWait(25)
            if shown:
                break
        check(shown and expect in shown[0], f"menu check shows: {expect!r}")
    updater.fetch_latest = lambda repo=None, timeout=None: None
    shown.clear(); win.check_updates(silent=True); QTest.qWait(400)
    check(not shown, "the silent start-up check says nothing when there is no news")
    updater.fetch_latest = real
    win.close(); shutdown()


def main() -> int:
    # ── pure comparison ────────────────────────────────────────────────
    cases = [
        ("v17.6.0", "17.5.0", True,  "a higher minor is newer, leading v ignored"),
        ("17.5.0",  "17.5.0", False, "the same version is not newer"),
        ("17.4.9",  "17.5.0", False, "an older version is never offered"),
        ("17.5.1",  "17.5",   True,  "different segment counts compare correctly"),
        ("17.5",    "17.5.1", False, "and the reverse, too"),
        ("v18.0",   "17.5.0", True,  "a major bump is newer"),
        ("quatsch", "17.5.0", False, "an unparseable tag never nags"),
        ("",        "17.5.0", False, "an empty tag never nags"),
    ]
    for cand, cur, want, label in cases:
        check(updater.is_newer(cand, cur) is want, label)

    # ── network shapes, all substituted ────────────────────────────────
    real = updater.urllib.request.urlopen
    try:
        updater.urllib.request.urlopen = _serving({
            "tag_name": "v17.9.1",
            "html_url": "https://example.invalid/r/v17.9.1",
            "body": "  Notizen  ",
        })
        rel = updater.fetch_latest("x/y")
        check(rel is not None and rel.version == "17.9.1", "a normal release parses")
        check(rel.notes == "Notizen", "release notes are trimmed")
        check(updater.check("17.5.0", "x/y") is not None, "a newer release is reported")
        check(updater.check("17.9.1", "x/y") is None, "the current version reports nothing")
        check(updater.check("18.0.0", "x/y") is None, "a newer local build reports nothing")

        updater.urllib.request.urlopen = _serving({"html_url": "https://example.invalid"})
        check(updater.fetch_latest("x/y") is None, "a release without a tag is ignored")

        updater.urllib.request.urlopen = _serving({"tag_name": "v1.0"})
        rel = updater.fetch_latest("x/y")
        check(rel is not None and rel.url.startswith("https://github.com/x/y"),
              "a missing html_url falls back to the repo's releases page")

        for exc, label in (
            (urllib.error.HTTPError("u", 404, "Not Found", {}, None),
             "404 (repo private or absent) yields None, not an exception"),
            (urllib.error.URLError("offline"), "no network yields None"),
            (TimeoutError(), "a timeout yields None"),
            (OSError("reset"), "a dropped connection yields None"),
        ):
            updater.urllib.request.urlopen = _raising(exc)
            check(updater.fetch_latest("x/y") is None, label)

        updater.urllib.request.urlopen = lambda req, timeout=None: _Resp(b"<html>nope")
        check(updater.fetch_latest("x/y") is None, "a non-JSON body yields None")
    finally:
        updater.urllib.request.urlopen = real

    # ── three outcomes, never a false "up to date" ──────────────────────
    real_fetch = updater.fetch_latest
    try:
        updater.fetch_latest = lambda repo=None, timeout=None: None
        check(updater.check_status("17.5.0") == ("unknown", None),
              "no answer (offline / private repo) is 'unknown', not 'current'")
        rel = updater.Release(tag="v17.6.0", version="17.6.0", url="u", notes="")
        updater.fetch_latest = lambda repo=None, timeout=None: rel
        check(updater.check_status("17.5.0") == ("newer", rel), "a newer tag is 'newer'")
        check(updater.check_status("17.6.0")[0] == "current", "the same version is 'current'")
    finally:
        updater.fetch_latest = real_fetch

    # ── wired in the Qt app; the start-up check is opt-in ───────────────
    from erysreview.persistence import AppSettings
    check(AppSettings().update_check_enabled is False, "the daily start-up check is off by default")
    app_src = (Path(__file__).resolve().parent.parent / "erysreview" / "qt" / "app.py").read_text()
    check('S["menu_check_updates"]' in app_src and "_maybe_check_updates" in app_src,
          "Hilfe › Nach Updates suchen and the start-up check are wired in the Qt app")
    qt_checks()

    print(f"OK — {CHECKS} checks: version comparison is correct and every network "
          f"failure degrades to None; wired in Qt, start-up check opt-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
