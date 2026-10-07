"""Backups of study A's ratings (the review files) — "Sicherungen".

A backup is a complete copy of the study's main review files at one moment:
which cell has which type, which is counted, remarks, finished flags. Images
and masks are never copied (they exist once). About 0.6 KB per image, so
even many backups of a large study stay small.

Kinds:
  "abgleich" — made when a comparison starts ("A vor Abgleich mit Anna");
               the agreement statistics are computed against it. Never pruned.
  "manual"   — "Jetzt sichern". Never pruned.
  "auto"     — made before loading another backup. Only the newest KEEP_AUTO
               are kept.

Loading a backup replaces the study's review files with it — after saving
the current state as an "auto" backup, so loading can always be undone.

Layout:  <folder>/.erys_cache/sicherungen/<YYYY-MM-DD_HHMMSS>_<slug>/
             meta.json, <stem>_review.json …
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from .persistence import cache_dir_for, rater_slug, review_dir, write_json_atomic

KEEP_AUTO = 20
KINDS = ("abgleich", "manual", "auto")


@dataclass
class Backup:
    id: str              # folder name
    path: Path
    name: str
    kind: str
    created: str         # ISO timestamp
    n_images: int


def backups_dir(folder: Path) -> Path:
    return cache_dir_for(folder) / "sicherungen"


def _reviews_in(d: Path) -> list[Path]:
    """Review files — not macOS' "._…" companions on exFAT/FAT drives."""
    return sorted(f for f in d.glob("*_review.json") if not f.name.startswith("._"))


def _main_reviews(folder: Path) -> list[Path]:
    return _reviews_in(review_dir(folder, None))


def create(folder: Path, name: str, kind: str = "manual") -> Backup:
    """Copy every main review file of the study into a new backup."""
    assert kind in KINDS
    now = _dt.datetime.now()
    bid = f"{now:%Y-%m-%d_%H%M%S}_{rater_slug(name)}"
    d = backups_dir(folder) / bid
    k = 1
    while d.exists():                       # two in the same second
        k += 1
        d = backups_dir(folder) / f"{bid}_{k}"
    d.mkdir(parents=True)
    files = _main_reviews(folder)
    for f in files:
        shutil.copyfile(f, d / f.name)      # copyfile: new mtime, the count cache re-reads it
    write_json_atomic(d / "meta.json", {"name": name, "kind": kind,
                                         # microseconds: several backups in one second sort right
                                         "created": now.isoformat(timespec="microseconds"),
                                         "n_images": len(files)})
    if kind == "auto":
        prune(folder)
    return _read(d)


def _read(d: Path) -> Backup | None:
    try:
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Backup(d.name, d, str(meta.get("name", d.name)), str(meta.get("kind", "manual")),
                  str(meta.get("created", "")), int(meta.get("n_images", 0)))


def list_backups(folder: Path) -> list[Backup]:
    """Newest first."""
    d = backups_dir(folder)
    if not d.is_dir():
        return []
    out = [b for b in (_read(x) for x in d.iterdir() if x.is_dir()) if b is not None]
    return sorted(out, key=lambda b: b.created, reverse=True)


def get(folder: Path, bid: str) -> Backup | None:
    d = backups_dir(folder) / bid
    return _read(d) if d.is_dir() else None


def prune(folder: Path) -> list[str]:
    """Keep only the newest KEEP_AUTO automatic backups; returns removed ids."""
    autos = [b for b in list_backups(folder) if b.kind == "auto"]
    gone = []
    for b in autos[KEEP_AUTO:]:
        shutil.rmtree(b.path, ignore_errors=True)
        gone.append(b.id)
    return gone


def delete(folder: Path, bid: str) -> None:
    b = get(folder, bid)
    if b is not None:
        shutil.rmtree(b.path, ignore_errors=True)


def restore(folder: Path, bid: str, auto_name: str = "A vor dem Laden") -> Backup:
    """Replace the study's review files with backup `bid`. The current state
    is saved first as an automatic backup; returns that backup (to undo)."""
    b = get(folder, bid)
    if b is None:
        raise FileNotFoundError(bid)
    before = create(folder, auto_name, "auto")
    target = review_dir(folder, None)
    keep = {f.name for f in _reviews_in(b.path)}
    for f in _main_reviews(folder):
        if f.name not in keep:              # rated after the backup → not in that state
            f.unlink()
    for f in _reviews_in(b.path):
        shutil.copyfile(f, target / f.name)
    return before


def review_path_in(backup: Backup, stem: str) -> Path:
    return backup.path / f"{stem}_review.json"
