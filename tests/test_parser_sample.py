"""Consistency gate for the grouping editor's sample-file pick.

The role rows in the parser editor are built from ONE sample filename, so the
sample decides how many rows there are and which token each role points at.
`_sample_files()` therefore has to apply the same exclusions as
`cellpose_runner.list_images` — otherwise the preview describes files the import
never sees.

The case that bit a real study (Vollblut, exFAT stick): macOS writes an
AppleDouble `._<name>` twin next to every image on FAT/exFAT, "." sorts before
letters, so `._Bedrest BDC-5_A3_VB_7.5.26_001.tif` was picked as the sample.
"_" is a delimiter, so `._Bedrest` splits into "." + "Bedrest" — one token more
than the real name. Every role rendered one row too low (timepoint pointed at
"Bedrest", subject at "BDC-5"), and saving that shifted 7-role list made
len(tokens) < len(field_roles) for all 986 real files: the whole study went to
_ungrouped, while the preview still read 1224/1224 because it counted the twins.

Runs as a plain script:  ./.venv/bin/python tests/test_parser_sample.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erysreview import nameparser

CHECKS = 0
DELIMS = ["_", " "]
ROLES = ["ignore", "timepoint", "subject", "ignore", "ignore", "image_num"]
REAL = "Bedrest BDC-5_A3_VB_7.5.26_001.tif"


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(label)
    print(f"  ok  {label}")


def sample_files(folder: Path) -> list[str]:
    """Mirror of dialogs._sample_files (which closes over the dialog's state)."""
    out: list[str] = []
    for f in sorted(folder.iterdir()):
        if not f.is_file() or f.suffix.lower() not in nameparser._EXTS:
            continue
        if f.name.startswith(".") or f.name.endswith("_overlay.png"):
            continue
        out.append(f.name)
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="erys_sample_"))
    try:
        (tmp / REAL).write_bytes(b"II*\x00")
        (tmp / ("._" + REAL)).write_bytes(b"\x00\x05\x16\x07Mac OS X")
        (tmp / "Bedrest BDC-5_A3_VB_7.5.26_001_overlay.png").write_bytes(b"x")

        files = sample_files(tmp)
        check(files == [REAL], "sample list holds only the real image")

        # The sample decides the row count — it must match the real filename.
        n_real = len(nameparser.tokenize(REAL, DELIMS))
        n_twin = len(nameparser.tokenize("._" + REAL, DELIMS))
        check(n_real == 6, f"real name tokenizes to 6 tokens (got {n_real})")
        check(n_twin == n_real + 1,
              "the AppleDouble twin still yields one token more (the shift)")
        check(len(nameparser.tokenize(files[0], DELIMS)) == len(ROLES),
              "chosen sample yields exactly one row per saved role")

        # Roles land on the tokens the study actually means.
        parsed = nameparser.parse(files[0], DELIMS, ROLES)
        check(parsed is not None, "sample parses")
        check(parsed["timepoint"] == "BDC-5", "timepoint is BDC-5, not 'Bedrest'")
        check(parsed["subject"] == "A3", "subject is A3, not 'BDC-5'")
        check(parsed["image_num"] == 1, "image_num is 1, not the date")

        # The regression itself: one role too many drops every real file.
        check(nameparser.parse(REAL, DELIMS, ROLES + ["ignore"]) is None,
              "a 7-role list would send the real file to _ungrouped")

        print(f"\n{CHECKS} checks passed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
