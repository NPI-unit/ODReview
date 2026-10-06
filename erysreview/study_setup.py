"""The logic behind the 'Neue Studie' assistant (mockup v5), without any UI.

Step 1 looks at the image folder (scan_folder), step 2 reads the file names:
it guesses the delimiters and what each part means from the real files
(detect_delimiters, guess_roles), shows a sample with the unassigned files
first (preview_rows) and offers the one-click fix of an extra delimiter
(suggest_delimiter). Step 3 can measure the cell diameter on a test image
(measured_diameter). build_profile puts the answers together — on top of a
template study, so everything the assistant does not ask about (size filter,
display, export) is taken from there.

Example name used throughout: ErysReview_01_Prä_06.10.26_02.tif
(project, proband, timepoint, date, image number).
"""

from __future__ import annotations

import copy
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from . import nameparser
from .nameparser import ROLE_IGNORE, ROLE_IMAGE_NUM, ROLE_SUBJECT, ROLE_TIMEPOINT
from .profile import ProfileData

CANDIDATE_DELIMS = ["_", "-", " ", "."]
_DATE = re.compile(r"^\d{1,4}[.\-/]\d{1,2}([.\-/]\d{1,4})?$|^\d{6}$|^\d{8}$")


@dataclass
class FolderScan:
    images: list[Path]
    total_bytes: int
    removable: bool             # on a stick / external drive (macOS: under /Volumes)

    @property
    def count(self) -> int:
        return len(self.images)


def scan_folder(folder: Path) -> FolderScan:
    from .cellpose_runner import list_images
    folder = Path(folder)
    images = list_images(folder, exclude={folder / "outputs"})
    total = 0
    for p in images:
        try:
            total += p.stat().st_size
        except OSError:
            pass
    parts = folder.resolve().parts
    return FolderScan(images, total, len(parts) > 2 and parts[1] == "Volumes")


def _stems(names: list[str]) -> list[str]:
    return [nameparser.strip_ext(n) for n in names]


def detect_delimiters(names: list[str]) -> list[str]:
    """The delimiters most names are split by. A '.' only counts when it is
    not just part of a date ('06.10.26'), and '-' likewise."""
    stems = _stems(names)
    if not stems:
        return ["_"]
    found = []
    for d in CANDIDATE_DELIMS:
        hits = 0
        for s in stems:
            if d not in s:
                continue
            if d in ".-":
                # only when it also splits something that is not a date
                others = [x for x in CANDIDATE_DELIMS if x != d and x in s]
                pieces = re.split("|".join(re.escape(x) for x in others), s) if others else [s]
                if all(d not in p or _DATE.match(p) for p in pieces):
                    continue
            hits += 1
        if hits >= max(1, len(stems) // 2):
            found.append(d)
    return found or ["_"]


def guess_roles(names: list[str], delimiters: list[str]) -> list[str]:
    """One role per name part, from how the parts vary across the files:
    a part that never changes is ignored (project name), a date is ignored,
    the last varying number is the image number, the first varying number
    the proband, and a varying part with few values the timepoint."""
    rows = [nameparser.tokenize(n, delimiters) for n in names]
    rows = [r for r in rows if r]
    if not rows:
        return []
    width = Counter(len(r) for r in rows).most_common(1)[0][0]
    rows = [r for r in rows if len(r) == width]
    cols = [[r[i] for r in rows] for i in range(width)]
    roles = [ROLE_IGNORE] * width
    varying = [i for i, c in enumerate(cols) if len(set(c)) > 1 or len(rows) == 1]
    dates = {i for i, c in enumerate(cols) if all(_DATE.match(x) for x in c)}
    numeric = [i for i in varying if i not in dates and all(x.isdigit() for x in cols[i])]
    texty = [i for i in varying if i not in dates and i not in numeric]
    if numeric:
        if len(numeric) >= 2:
            roles[numeric[-1]] = ROLE_IMAGE_NUM
            roles[numeric[0]] = ROLE_SUBJECT
        else:
            roles[numeric[0]] = ROLE_SUBJECT
    if texty:
        # fewest distinct values = the timepoint (Prä / Post / FU)
        tp = min(texty, key=lambda i: len(set(cols[i])))
        roles[tp] = ROLE_TIMEPOINT
    if ROLE_SUBJECT not in roles:
        free = [i for i in varying if roles[i] == ROLE_IGNORE and i not in dates]
        if free:
            roles[free[0]] = ROLE_SUBJECT
    if ROLE_TIMEPOINT not in roles:
        free = [i for i in range(width) if roles[i] == ROLE_IGNORE and i not in dates]
        if free:
            roles[free[0]] = ROLE_TIMEPOINT
    return roles


def example_name(names: list[str], delimiters: list[str]) -> str | None:
    """A typical file name to show the parts of: the first one with the most
    common number of parts (not an odd one out such as a dash-named file)."""
    if not names:
        return None
    widths = Counter(len(nameparser.tokenize(n, delimiters)) for n in names[:500])
    common = widths.most_common(1)[0][0]
    return next(n for n in names if len(nameparser.tokenize(n, delimiters)) == common)


def _profile_for(delimiters: list[str], roles: list[str]) -> ProfileData:
    p = ProfileData(name="_probe")
    p.use_simple_parser, p.delimiters, p.field_roles = True, list(delimiters), list(roles)
    return p


@dataclass
class PreviewRow:
    name: str
    subject: str
    timepoint: str
    image_num: str
    ok: bool


def preview_rows(images: list[Path], delimiters: list[str], roles: list[str], n: int = 4) -> list[PreviewRow]:
    """A few real files with what the reading makes of them — the files that
    could not be assigned first, so a problem is visible at a glance."""
    from .grouping import parse_filename
    prof = _profile_for(delimiters, roles)
    good, bad = [], []
    for p in images:
        info = parse_filename(p.name, prof)
        if info is None or info.is_ungrouped:
            bad.append(PreviewRow(p.name, "—", "—", "—", False))
        else:
            toks = nameparser.tokenize(p.name, delimiters)
            num = toks[roles.index(ROLE_IMAGE_NUM)] if ROLE_IMAGE_NUM in roles and \
                roles.index(ROLE_IMAGE_NUM) < len(toks) else "—"
            good.append(PreviewRow(p.name, info.subject, info.timepoint, num, True))
        if len(good) >= n and len(bad) >= 1:
            break
    take_bad = bad[: max(1, n // 4)] if good else bad[:n]
    return good[: n - len(take_bad)] + take_bad


@dataclass
class ReadResult:
    n: int
    ok: int
    subjects: int
    timepoints: list[str]

    @property
    def unassigned(self) -> int:
        return self.n - self.ok


def read_result(images: list[Path], delimiters: list[str], roles: list[str]) -> ReadResult:
    from .grouping import group_images
    ok, _why = nameparser.validate_roles(roles)
    if not ok:
        return ReadResult(len(images), 0, 0, [])
    g = group_images(images, _profile_for(delimiters, roles))
    good = [i for i in g.flat if not i.is_ungrouped]
    tps = list(dict.fromkeys(i.timepoint for i in good))
    return ReadResult(len(images), len(good), len({i.subject for i in good}), tps)


def suggest_delimiter(images: list[Path], delimiters: list[str], roles: list[str]) -> str | None:
    """A delimiter that, added, assigns more files ('TRIBAL-207-…' among
    '…_…' names) — the assistant's one-click fix. None if none helps."""
    base = read_result(images, delimiters, roles).ok
    best, gain = None, 0
    for d in CANDIDATE_DELIMS:
        if d in delimiters:
            continue
        got = read_result(images, delimiters + [d], roles).ok - base
        if got > gain:
            best, gain = d, got
    return best


def measured_diameter(masks) -> float | None:
    """Median equivalent diameter (px) of the cells in a test segmentation —
    what Cellpose's 'diameter' should be for this magnification."""
    import numpy as np
    m = np.asarray(masks)
    if m.max() <= 0:
        return None
    areas = np.bincount(m.ravel())[1:]
    areas = areas[areas > 0]
    if not len(areas):
        return None
    return float(np.median(np.sqrt(4.0 * areas / np.pi)))


def build_profile(name: str, folder: str, template: ProfileData | None,
                  delimiters: list[str], roles: list[str]) -> ProfileData:
    """The new study: the template's settings (or the defaults), with the
    assistant's name, folder and file-name reading. Per-study UI state
    (open groups, recent folders) starts empty."""
    p = copy.deepcopy(template) if template is not None else ProfileData()
    p.name = name.strip()
    p.import_folder = str(folder)
    p.use_simple_parser = True
    p.delimiters = list(delimiters)
    p.field_roles = list(roles)
    p.created_at = p.updated_at = ""
    p.sidebar_open_groups = []
    p.recent_folders = [str(folder)]
    if p.type_by_key(p.active_type_key) is None:
        p.active_type_key = p.default_type_key()
    return p
