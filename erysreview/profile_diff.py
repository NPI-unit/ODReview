"""What saving an edited study would change — the "Speichern mit
Folgen-Vorschau" sheet, without any UI.

Each change is classified by its consequence, because that is what the user
needs to decide:

  resegment  Cellpose parameters/model — existing masks no longer match
  grouping   filename parser — images may move between groups
  counting   target, size filter, border margin — which cells/images count
  display    colours, outlines, opacity — immediately visible, no data
  export     what an export writes
  study      name, image folder
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .cellpose_runner import has_cache
from .grouping import group_images
from .profile import ProfileData

KIND_ORDER = ["resegment", "grouping", "counting", "study", "export", "display"]


@dataclass
class Change:
    kind: str
    field: str            # i18n-free identifier, e.g. "cellpose.diameter"
    old: object
    new: object


@dataclass
class Impact:
    """Consequences that need the image folder to compute."""
    n_with_masks: int = 0          # images whose masks would be stale
    n_moved: int = 0               # images that change (timepoint, subject)
    n_ungrouped_before: int = 0
    n_ungrouped_after: int = 0


@dataclass
class ProfileDiff:
    changes: list[Change] = field(default_factory=list)
    impact: Impact = field(default_factory=Impact)

    @property
    def kinds(self) -> set[str]:
        return {c.kind for c in self.changes}

    def by_kind(self) -> list[tuple[str, list[Change]]]:
        return [(k, [c for c in self.changes if c.kind == k])
                for k in KIND_ORDER if any(c.kind == k for c in self.changes)]

    def __bool__(self) -> bool:
        return bool(self.changes)


_FIELDS: list[tuple[str, str]] = [
    ("cellpose.model", "resegment"), ("cellpose.diameter", "resegment"),
    ("cellpose.flow_threshold", "resegment"), ("cellpose.cellprob_threshold", "resegment"),
    ("use_simple_parser", "grouping"), ("delimiters", "grouping"),
    ("field_roles", "grouping"), ("image_pattern", "grouping"),
    ("target_cells", "counting"), ("margin_n", "counting"),
    ("size_filter.enabled", "counting"), ("size_filter.min_factor", "counting"),
    ("size_filter.max_factor", "counting"),
    ("name", "study"), ("import_folder", "study"),
    ("images_per_subject", "export"), ("stats_enabled", "export"),
    ("overview_xlsx", "export"), ("overlay_png", "export"),
    ("copy_originals_to_outputs", "export"), ("use_gpu", "export"),
    ("overlay_alpha", "display"), ("type_outlines_enabled", "display"),
    ("show_margin_overlay", "display"),
]


def _get(p: ProfileData, dotted: str):
    obj = p
    for part in dotted.split("."):
        obj = getattr(obj, part)
    return obj


def _types_signature(p: ProfileData):
    return [(t.key, t.name, t.color, t.hotkey, t.outline) for t in p.cell_types]


def diff_profiles(old: ProfileData, new: ProfileData) -> list[Change]:
    out = [Change(kind, f, _get(old, f), _get(new, f))
           for f, kind in _FIELDS if _get(old, f) != _get(new, f)]
    if _types_signature(old) != _types_signature(new):
        keys_old = [t.key for t in old.cell_types]
        keys_new = [t.key for t in new.cell_types]
        # Adding/removing types changes what a cell can be counted as.
        kind = "counting" if keys_old != keys_new else "display"
        out.append(Change(kind, "cell_types", len(old.cell_types), len(new.cell_types)))
    return out


def compute_impact(old: ProfileData, new: ProfileData, folder: Path | None,
                   images: list[Path], changes: list[Change]) -> Impact:
    imp = Impact()
    if folder is None or not images:
        return imp
    kinds = {c.kind for c in changes}
    if "resegment" in kinds:
        imp.n_with_masks = sum(1 for p in images if has_cache(folder, p))
    if "grouping" in kinds:
        a = {i.path.name: (i.timepoint, i.subject, i.is_ungrouped)
             for i in group_images(images, old).flat}
        b = {i.path.name: (i.timepoint, i.subject, i.is_ungrouped)
             for i in group_images(images, new).flat}
        imp.n_moved = sum(1 for n in a if a[n][:2] != b.get(n, a[n])[:2])
        imp.n_ungrouped_before = sum(1 for v in a.values() if v[2])
        imp.n_ungrouped_after = sum(1 for v in b.values() if v[2])
    return imp


def profile_diff(old: ProfileData, new: ProfileData, folder: Path | None = None,
                 images: list[Path] | None = None) -> ProfileDiff:
    changes = diff_profiles(old, new)
    return ProfileDiff(changes, compute_impact(old, new, folder, images or [], changes))
