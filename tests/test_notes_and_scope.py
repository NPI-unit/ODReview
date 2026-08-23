"""Consistency gate for the remarks + export-scope block.

Two things worth pinning, both correctness rather than cosmetics:

  1. Remarks survive a save/load round-trip. They live in the same
     `<stem>_review.json` sidecar as the rest of the review, so a schema slip
     would silently drop notes a reviewer typed — with no error anywhere.
  2. A scoped export touches ONLY the selected groups, and skips the
     cross-group workbook. Getting this wrong would either leak stale numbers
     into an untouched group or replace a complete Uebersicht.xlsx with a
     partial one.

Synthetic data only — masks are written straight into `.erys_cache`. Runs as a
plain script:  ./.venv/bin/python tests/test_notes_and_scope.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview.cellpose_runner import cache_paths, save_mask
from erysreview.export import export_folder_structured
from erysreview.grouping import GroupedFolder, ImageGroupInfo
from erysreview.persistence import ReviewState
from erysreview.profile import ProfileData, SizeFilter

CHECKS = 0


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def _make_mask(k: int, H: int = 60, W: int = 60) -> np.ndarray:
    m = np.zeros((H, W), dtype=np.int32)
    idx = 1
    for r in range(5, H - 10, 12):
        for c in range(5, W - 10, 12):
            if idx > k:
                return m
            m[r:r + 6, c:c + 6] = idx
            idx += 1
    if idx <= k:
        raise ValueError(f"grid too small for {k} cells")
    return m


def _add_image(folder: Path, tp: str, sub: str, stem: str, n: int) -> ImageGroupInfo:
    info = ImageGroupInfo(path=folder / f"{stem}.tif", timepoint=tp, subject=sub,
                          image_num=None)
    npy, _meta = cache_paths(folder, info.path)
    save_mask(npy, _make_mask(n))
    return info


def test_notes_roundtrip(tmp: Path) -> None:
    rv = ReviewState()
    rv.notes = {"3": "Rand unscharf", "7": "zwei Zellen?"}
    rv.note = "ganze Aufnahme leicht defokussiert"
    rv.types = {"3": "stechapfel"}
    rv.save(tmp, "note_img")

    back = ReviewState.load(tmp, "note_img")
    check(back.notes == rv.notes, "cell remarks survive save/load")
    check(back.note == rv.note, "image remark survives save/load")
    check(back.types == rv.types, "remarks do not disturb the existing type map")

    # Umlauts and newlines are what a reviewer actually types.
    rv.notes["3"] = "Zelle größer als\nder Rest — prüfen"
    rv.save(tmp, "note_img")
    check(ReviewState.load(tmp, "note_img").notes["3"] == rv.notes["3"],
          "umlauts and newlines round-trip unchanged")

    # A remark alone must count as a user edit, otherwise the app would offer to
    # discard it as if nothing had been entered.
    only_note = ReviewState()
    only_note.note = "nur eine Anmerkung"
    check(only_note.has_user_edits(), "an image remark alone counts as a user edit")
    only_cell = ReviewState()
    only_cell.notes = {"1": "x"}
    check(only_cell.has_user_edits(), "a cell remark alone counts as a user edit")
    check(not ReviewState().has_user_edits(), "an untouched review still counts as empty")


def test_export_scope(tmp: Path) -> None:
    profile = ProfileData()
    profile.target_cells = 100
    profile.margin_n = 0
    profile.size_filter = SizeFilter(enabled=False)
    profile.overview_xlsx = True

    tree: dict[str, dict[str, list[ImageGroupInfo]]] = {"T0": {"S1": [], "S2": []}}
    for stem in ("T0_S1_a", "T0_S1_b"):
        tree["T0"]["S1"].append(_add_image(tmp, "T0", "S1", stem, 4))
    for stem in ("T0_S2_a", "T0_S2_b"):
        tree["T0"]["S2"].append(_add_image(tmp, "T0", "S2", stem, 3))
    flat = [i for subs in tree.values() for imgs in subs.values() for i in imgs]
    grouped = GroupedFolder(profile_name=profile.name, tree=tree, flat=flat)

    root = export_folder_structured(tmp, profile, grouped, groups={("T0", "S1")})
    d1, d2 = root / "T0" / "T0_S1", root / "T0" / "T0_S2"
    check(len(list(d1.glob("*_stats.csv"))) == 2, "selected group is fully exported")
    check(not d2.exists() or not list(d2.glob("*_stats.csv")),
          "unselected group is not written at all")
    check(len(list(d1.glob("*_summary.csv"))) == 1, "selected group gets its summary")
    check(not (root / "Uebersicht.xlsx").exists(),
          "cross-group workbook is skipped on a scoped run")

    # Second scoped run: the first group must stay, the second appear.
    export_folder_structured(tmp, profile, grouped, groups={("T0", "S2")})
    check(len(list(d1.glob("*_stats.csv"))) == 2, "earlier group survives a later run")
    check(len(list(d2.glob("*_stats.csv"))) == 2, "second group exported on its own run")

    # erys.db is upserted per image, so it must hold both groups after two runs.
    import sqlite3
    con = sqlite3.connect(root / "erys.db")
    n = con.execute("select count(*) from images").fetchone()[0]
    con.close()
    check(n == 4, "erys.db accumulates across scoped runs (4 images)")

    # A full run writes the workbook again.
    export_folder_structured(tmp, profile, grouped, groups=None)
    check((root / "Uebersicht.xlsx").exists(), "full run writes the workbook")


def test_package_roundtrip(tmp: Path) -> None:
    """Remarks must survive create -> import, and old packages must not break.

    They ride inside `<stem>_review.json`, which the package already carries
    whole, so this is a guard rather than a feature: anyone narrowing that file
    to a field list would silently drop remarks somebody typed, and no error
    would be raised anywhere.
    """
    import json
    import zipfile
    from erysreview import package as pkg
    from erysreview.grouping import GroupedFolder

    src = tmp / "pkg_src"
    src.mkdir()
    profile = ProfileData()
    profile.margin_n = 0
    profile.size_filter = SizeFilter(enabled=False)
    info = _add_image(src, "T0", "S1", "pkg_a", 4)
    tree = {"T0": {"S1": [info]}}
    grouped = GroupedFolder(profile_name=profile.name, tree=tree, flat=[info])

    rv = ReviewState.load(src, "pkg_a")
    rv.notes = {"2": "Fragment — größer als der Rest"}
    rv.note = "Aufnahme leicht defokussiert"
    rv.save(src, "pkg_a")

    zp = tmp / "paket.zip"
    pkg.create_package(src, grouped, profile, zp, include_images=False)
    with zipfile.ZipFile(zp) as z:
        raw = json.loads(z.read("cache/pkg_a_review.json").decode("utf-8"))
    check(raw.get("notes") == rv.notes, "cell remarks are written into the package")
    check(raw.get("note") == rv.note, "the image remark is written into the package")

    dst = tmp / "pkg_dst"
    dst.mkdir()
    pkg.import_package(zp, dst, profile, lambda _s: "import", lambda *a: None)
    back = ReviewState.load(dst, "pkg_a")
    check(back.notes == rv.notes, "cell remarks survive the import")
    check(back.note == rv.note, "the image remark survives the import")

    # A package built before remarks existed: the fields are simply absent.
    old_zip = tmp / "alt.zip"
    with zipfile.ZipFile(zp) as zin, zipfile.ZipFile(old_zip, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename.endswith("_review.json"):
                d = json.loads(data.decode("utf-8"))
                d.pop("notes", None)
                d.pop("note", None)
                data = json.dumps(d).encode("utf-8")
            zout.writestr(item, data)
    old_dst = tmp / "pkg_old"
    old_dst.mkdir()
    pkg.import_package(old_zip, old_dst, profile, lambda _s: "import", lambda *a: None)
    old = ReviewState.load(old_dst, "pkg_a")
    check(old.notes == {} and old.note == "",
          "a package without the fields imports as empty, not as an error")


def test_vanished_volume() -> None:
    """A folder that is no longer there must not throw out of a Tk callback.

    The study folder lives on a USB stick. Ejecting it mid-session made every
    image switch call FolderState.save -> mkdir on a path that no longer
    existed, and the FileNotFoundError escaped into the Tk callback — one
    traceback per click, forever.

    The split matters: the remembered index is a convenience and may be lost
    quietly, but a review is the user's work and must still raise so the app can
    say the save failed instead of pretending it worked.
    """
    from erysreview.persistence import FolderState

    gone = Path("/Volumes/definitely-not-mounted-xyz/study")
    FolderState(last_index=7).save(gone)          # must not raise
    check(True, "saving the remembered index on a vanished volume is not fatal")
    FolderState(last_index=8).save(gone)          # reported once, then quiet
    check(True, "and repeating it stays quiet")

    rv = ReviewState()
    rv.notes = {"1": "would be lost"}
    raised = False
    try:
        rv.save(gone, "img")
    except OSError:
        raised = True
    check(raised, "but a review still reports failure rather than vanishing quietly")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="erysnotes_"))
    test_vanished_volume()
    test_notes_roundtrip(tmp)
    test_export_scope(tmp)
    test_package_roundtrip(tmp)
    print(f"OK — {CHECKS} checks: remarks persist, travel in packages, and a "
          f"scoped export stays inside its selection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
