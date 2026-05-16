"""Filename → (timepoint, subject, image_num) mapping via profile regex.

Hierarchy is fixed at Zeitpunkt > Proband (per design Q2c.i). Files that don't match
the regex are bundled into a special "Ungrouped" bucket.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .profile import ProfileData

UNGROUPED_TIMEPOINT = "_ungrouped"
UNGROUPED_SUBJECT   = "_ungrouped"


@dataclass
class ImageGroupInfo:
    path: Path
    timepoint: str
    subject: str
    image_num: int | None
    extras: dict[str, str] = field(default_factory=dict)
    is_ungrouped: bool = False


@dataclass
class GroupedFolder:
    """All images in a folder organized as timepoint -> subject -> [images]."""
    profile_name: str
    # tree[timepoint][subject] -> list of ImageGroupInfo sorted by image_num
    tree: dict[str, dict[str, list[ImageGroupInfo]]] = field(default_factory=dict)
    # flat: keep the original list order for index-based navigation
    flat: list[ImageGroupInfo] = field(default_factory=list)

    def group_key(self, info: ImageGroupInfo) -> str:
        """Stable identifier for a (timepoint, subject) group."""
        return f"{info.timepoint}/{info.subject}"

    def n_total(self) -> int:
        return len(self.flat)

    def timepoints(self) -> list[str]:
        # Sort so that ungrouped goes last
        keys = list(self.tree.keys())
        keys.sort(key=lambda k: (k == UNGROUPED_TIMEPOINT, k))
        return keys

    def subjects_in(self, timepoint: str) -> list[str]:
        keys = list(self.tree.get(timepoint, {}).keys())
        keys.sort(key=lambda k: (k == UNGROUPED_SUBJECT, k))
        return keys


def parse_filename(filename: str, profile: ProfileData) -> ImageGroupInfo | None:
    """Returns None if the filename doesn't match the profile regex."""
    try:
        r = re.compile(profile.image_pattern)
    except re.error:
        return None
    m = r.match(filename)
    if not m:
        return None
    groups = m.groupdict()
    image_num_raw = groups.get("image_num", "")
    try:
        image_num = int(image_num_raw) if image_num_raw else None
    except ValueError:
        image_num = None
    extras = {k: v for k, v in groups.items()
              if k not in {"subject", "timepoint", "image_num"} and v is not None}
    return ImageGroupInfo(
        path=Path(filename),
        timepoint=groups.get("timepoint", UNGROUPED_TIMEPOINT) or UNGROUPED_TIMEPOINT,
        subject=groups.get("subject", UNGROUPED_SUBJECT) or UNGROUPED_SUBJECT,
        image_num=image_num,
        extras=extras,
        is_ungrouped=False,
    )


def group_images(image_paths: list[Path], profile: ProfileData) -> GroupedFolder:
    grouped = GroupedFolder(profile_name=profile.name)
    for p in image_paths:
        info = parse_filename(p.name, profile)
        if info is None:
            info = ImageGroupInfo(
                path=p, timepoint=UNGROUPED_TIMEPOINT, subject=UNGROUPED_SUBJECT,
                image_num=None, is_ungrouped=True,
            )
        else:
            info.path = p  # set the real full path

        grouped.tree.setdefault(info.timepoint, {}).setdefault(info.subject, []).append(info)
        grouped.flat.append(info)

    # Sort each subject's image list by image_num (None last)
    for tp in grouped.tree:
        for sub in grouped.tree[tp]:
            grouped.tree[tp][sub].sort(key=lambda x: (x.image_num is None, x.image_num or 0, x.path.name))
    # Sort flat the same way for stable navigation
    grouped.flat.sort(key=lambda x: (x.timepoint, x.subject, x.image_num is None, x.image_num or 0, x.path.name))
    return grouped
