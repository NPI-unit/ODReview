"""Consistency gate for the update check.

No network: the version comparison is pure, and every failure path is forced by
substituting urlopen. A gate that needs the internet fails on the lab machines
this software runs on, which is exactly where it would matter least.

The feature ships INACTIVE (release repo not created yet) — the Qt app does not
call it yet. This gate keeps the code compiling and correct so that turning it
on later is a small step and not an archaeology project.

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

    # ── shipped inactive ───────────────────────────────────────────────
    from erysreview.persistence import AppSettings
    check(AppSettings().update_check_enabled is False,
          "the startup check is off by default while the repo is private")
    app_src = (Path(__file__).resolve().parent.parent / "erysreview" / "qt" / "app.py").read_text()
    check("updater" not in app_src and "menu_check_updates" not in app_src,
          "the Qt app has no update menu entry and no startup check yet (wiring follows with the release repo)")

    print(f"OK — {CHECKS} checks: version comparison is correct and every network "
          f"failure degrades to None; feature ships inactive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
