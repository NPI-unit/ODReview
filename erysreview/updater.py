"""Update check against a public GitHub release repository.

Deliberately narrow: this asks whether a newer release exists and reports the
answer. It never downloads and never replaces anything — an app that rewrites
itself while running is a different, much riskier feature, and nothing here
forecloses building it later.

**Why a separate, public repo.** The source repository is private. An
unauthenticated request to a private repo answers 404, so a check against it
would need a token on every lab machine: plaintext credentials on other
people's computers, with an expiry date that eventually breaks every install at
once. A second repository holding only build artefacts and a version tag is
public without exposing any source, and needs no credentials at all.

**No new dependency.** urllib and json are stdlib, so the installers stay as
they are. Nothing here may raise: a lab machine is often offline, and an update
check must never be the reason the app fails to start.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass

# The public repository holding releases. Must exist for the check to find
# anything; until it does, every call simply reports "no information", which is
# the same path a missing network takes.
# Das Quellcode-Repo. Es ist derzeit PRIVAT: eine unangemeldete Anfrage
# bekommt 404 (geprüft — dieselbe Anfrage an ein öffentliches Repo liefert
# 200), also meldet jede Prüfung "keine Information". Die Anschlussstellen
# in app.py sind deshalb auskommentiert.
#
# Verdrahtet in der Qt-App (Hilfe › Nach Updates suchen, optional täglich beim
# Start). Solange dieses Repo privat ist, antwortet GitHub unangemeldet mit 404:
# die Prüfung meldet dann "nicht prüfbar" (nie fälschlich "aktuell").
# Damit sie antwortet: Repo öffentlich schalten und ein Release taggen
# (z. B. v17.6.0) — oder ein separates öffentliches Repo nur für Releases
# anlegen und hier eintragen; dann bleibt der Quellcode privat.
RELEASE_REPO = "finn-dreisigacker/ErysReview"

API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT_S = 4.0


@dataclass(frozen=True)
class Release:
    version: str          # normalised, e.g. "17.6.0"
    tag: str              # as published, e.g. "v17.6.0"
    url: str              # human page to open in a browser
    notes: str            # release body, may be empty


def parse_version(text: str) -> tuple[int, ...]:
    """`v17.5.0` / `17.5` / `17.5.1-beta` -> comparable tuple of ints.

    Only the leading numeric run counts. A suffix like `-beta` is dropped
    rather than guessed at: ordering pre-releases correctly needs rules this
    project has no use for, and a wrong guess would offer a downgrade.
    """
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(text))
    if not m:
        return ()
    return tuple(int(p) for p in m.group(1).split("."))


def is_newer(candidate: str, current: str) -> bool:
    """True only when `candidate` is a strictly higher version than `current`.

    Unparseable input is never newer — a malformed tag must not nag every user
    of every install forever.
    """
    a, b = parse_version(candidate), parse_version(current)
    if not a or not b:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def fetch_latest(repo: str = RELEASE_REPO, timeout: float = TIMEOUT_S) -> Release | None:
    """Latest published release, or None if that cannot be determined.

    None covers every failure alike — offline, DNS, 404 (repo absent or still
    private), rate limit, malformed JSON. The caller cannot act differently on
    any of them, so they are not distinguished.
    """
    req = urllib.request.Request(
        API.format(repo=repo),
        headers={"Accept": "application/vnd.github+json",
                 "User-Agent": "ErysReview-update-check"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            ValueError, TimeoutError):
        return None
    tag = str(data.get("tag_name") or "")
    if not tag:
        return None
    v = parse_version(tag)
    return Release(
        version=".".join(str(p) for p in v) if v else tag,
        tag=tag,
        url=str(data.get("html_url") or f"https://github.com/{repo}/releases"),
        notes=str(data.get("body") or "").strip(),
    )


def check_status(current_version: str, repo: str = RELEASE_REPO) -> tuple[str, Release | None]:
    """("newer", release) | ("current", release) | ("unknown", None).

    "unknown" = no answer (offline, repository private / no release yet) —
    kept apart from "current" so the app never claims to be up to date when
    it simply could not ask."""
    rel = fetch_latest(repo)
    if rel is None:
        return "unknown", None
    return ("newer" if is_newer(rel.tag, current_version) else "current"), rel


def check(current_version: str, repo: str = RELEASE_REPO) -> Release | None:
    """The whole check: returns a Release only when it is newer than `current`."""
    rel = fetch_latest(repo)
    if rel is None or not is_newer(rel.tag, current_version):
        return None
    return rel
