"""Sicherungen (backups.py): a backup holds exactly the review files of that
moment; loading restores that state (removing ratings made later) after
saving the current one; automatic backups are kept to the newest 20, the
comparison and own backups never pruned."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def main() -> int:
    from erysreview import backups
    from erysreview.persistence import ReviewState
    folder = Path(tempfile.mkdtemp())
    for k in (1, 2):
        rv = ReviewState(); rv.types = {"1": "stechapfel"}; rv.save(folder, f"img{k}")
    b = backups.create(folder, "A vor Abgleich mit Anna", "abgleich")
    check(b.n_images == 2 and (b.path / "img1_review.json").is_file() and b.kind == "abgleich",
          "a backup copies the review files of that moment")
    check(not list(b.path.glob("*masks*")), "…and nothing else (no images, no masks)")
    rv = ReviewState.load(folder, "img1"); rv.types = {"1": "normal"}; rv.save(folder, "img1")
    ReviewState(is_finished=True).save(folder, "img3")              # rated after the backup
    before = backups.restore(folder, b.id, "A vor dem Laden")
    check(ReviewState.load(folder, "img1").types == {"1": "stechapfel"}, "loading brings the old ratings back")
    check(not ReviewState.review_path(folder, "img3").exists(), "…and the state of that moment (later ratings go)")
    check(before.kind == "auto" and (before.path / "img3_review.json").is_file(),
          "…which are in the automatic backup made just before")
    own = backups.create(folder, "Eigene", "manual")
    for k in range(25):
        backups.create(folder, f"auto {k}", "auto")
    lst = backups.list_backups(folder)
    autos = [x for x in lst if x.kind == "auto"]
    check(len(autos) == backups.KEEP_AUTO and autos[0].name == "auto 24",
          f"only the newest {backups.KEEP_AUTO} automatic backups are kept")
    check(any(x.id == b.id for x in lst) and any(x.id == own.id for x in lst),
          "the comparison backup and own backups are never pruned")
    size = sum(f.stat().st_size for f in b.path.iterdir())
    check(size < 10_000, f"a backup is small ({size} bytes for 2 images)")
    backups.delete(folder, own.id)
    check(backups.get(folder, own.id) is None, "a backup can be deleted")
    print(f"OK — {CHECKS} checks: backups hold the ratings of one moment, load safely, prune automatics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
