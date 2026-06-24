"""Friendly, regex-free filename parser.

The user found the regex too fiddly. Instead a study can describe its filenames
declaratively: pick one or more delimiter characters and assign a role to each
resulting token. Example for ``TRIBAL_195_Prä_19.11.25_021.tif`` with delimiter
``_`` and roles ``[ignore, subject, timepoint, ignore, image_num]``:

    TRIBAL → ignore
    195    → subject
    Prä    → timepoint
    19.11.25 → ignore
    021    → image_num   (the extension .tif is stripped before splitting)

Multiple delimiters are allowed (e.g. ``_`` and space), so a name may mix them.
The same routine powers both the live preview in the editor and the actual
grouping at load time, so what you preview is exactly what you get.
"""

from __future__ import annotations

import re

ROLE_SUBJECT = "subject"
ROLE_TIMEPOINT = "timepoint"
ROLE_IMAGE_NUM = "image_num"
ROLE_IGNORE = "ignore"
ROLES = [ROLE_SUBJECT, ROLE_TIMEPOINT, ROLE_IMAGE_NUM, ROLE_IGNORE]

# Image extensions stripped before tokenizing (kept in sync with cellpose_runner).
_EXTS = (".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp")


def strip_ext(filename: str) -> str:
    low = filename.lower()
    for ext in _EXTS:
        if low.endswith(ext):
            return filename[: -len(ext)]
    return filename


def tokenize(filename: str, delimiters: list[str]) -> list[str]:
    """Split a filename (extension stripped) on any of the delimiter chars.

    Empty tokens are dropped. With no delimiters the whole stem is one token.
    """
    stem = strip_ext(filename)
    delims = [d for d in (delimiters or []) if d]
    if not delims:
        return [stem] if stem else []
    charclass = "[" + re.escape("".join(delims)) + "]+"
    return [t for t in re.split(charclass, stem) if t]


def parse(filename: str, delimiters: list[str], field_roles: list[str]
          ) -> dict | None:
    """Return {'subject','timepoint','image_num','extras'} or None on no match.

    Roles are matched against the leading tokens by position; the filename must
    have *at least* as many tokens as there are roles. Any extra trailing tokens
    are ignored, so a stray suffix (e.g. ``Tribal_123_prä_5.6.24_001_edited``)
    still groups correctly instead of silently dropping into ``_ungrouped``.
    A match still requires the subject + timepoint roles to be present and
    non-empty; image_num is optional (None if its token isn't an integer).
    """
    if not field_roles:
        return None
    tokens = tokenize(filename, delimiters)
    if len(tokens) < len(field_roles):
        return None

    subject = timepoint = None
    image_num: int | None = None
    extras: dict[str, str] = {}
    for tok, role in zip(tokens, field_roles):
        if role == ROLE_SUBJECT:
            subject = tok
        elif role == ROLE_TIMEPOINT:
            timepoint = tok
        elif role == ROLE_IMAGE_NUM:
            try:
                image_num = int(tok)
            except ValueError:
                image_num = None

    if not subject or not timepoint:
        return None
    return {
        "subject": subject,
        "timepoint": timepoint,
        "image_num": image_num,
        "extras": extras,
    }


def validate_roles(field_roles: list[str]) -> tuple[bool, str]:
    """Returns (ok, message). Requires subject + timepoint roles assigned once."""
    if not field_roles:
        return (False, "no_roles")
    counts = {r: field_roles.count(r) for r in ROLES}
    if counts[ROLE_SUBJECT] != 1:
        return (False, "need_subject")
    if counts[ROLE_TIMEPOINT] != 1:
        return (False, "need_timepoint")
    if counts[ROLE_IMAGE_NUM] > 1:
        return (False, "too_many_image_num")
    return (True, "")
