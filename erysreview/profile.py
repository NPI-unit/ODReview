"""Study profile module.

A profile bundles everything study-specific: how filenames map to (timepoint, subject),
the cell types, Cellpose parameters, target cell count, size-filter rules,
and the persisted Treeview-open-state.

Profiles live globally at:
    ~/Library/Application Support/Erys/profiles/<slug>.json

Globals (language, window size, last_profile_name, active_tool) stay in settings.json.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .persistence import (
    CellposeParams, TypeDef, app_support_dir, read_json, write_json_atomic,
)


# ─────────────────────────────────────────────
# Default profile content (cell types as in the first study it served)
# ─────────────────────────────────────────────
DEFAULT_PROFILE_NAME = "Standard"
# Earlier name of the default study; renamed once at startup (migrate_default_name).
LEGACY_DEFAULT_NAMES = ("TRIBAL (Default)",)

DEFAULT_IMAGE_PATTERN = (
    r"^(?P<study>[A-Za-z]+)_(?P<subject>\d+)_(?P<timepoint>[^_]+)"
    r"_(?P<date>[^_]+)_(?P<image_num>\d+)\.[Tt][Ii][Ff][Ff]?$"
)

DEFAULT_TYPES_TRIBAL = [
    TypeDef(key="normal",       name="Normal",               color="#FFD000"),
    TypeDef(key="stechapfel",   name="Stechapfel",           color="#FF6B6B"),
    TypeDef(key="auszug",       name="Auszüge Membran",      color="#4ECDC4"),
    TypeDef(key="verschmolzen", name="Verschmolzen Membran", color="#9D4EDD"),
    TypeDef(key="elongiert",    name="Elongierte RBC",       color="#F77F00"),
]


# Valid ranges for the numeric study settings. Spinbox arrows bound a field
# but typed input does not, so an out-of-range value (how the "diameter
# 200/2000" values got in) is snapped before it reaches Cellpose — and a
# nonsense size-filter factor must not silently exclude every cell (EH-3).
LIMITS: dict[str, tuple[float, float]] = {
    "diameter": (5.0, 500.0),
    "flow_threshold": (0.0, 3.0),
    "cellprob_threshold": (-6.0, 6.0),
    "size_min_factor": (0.05, 0.95),
    "size_max_factor": (1.05, 5.0),
    "overlay_alpha": (0.15, 1.0),
}


def clamp_field(field: str, raw, default: float) -> float:
    """`raw` as float, clamped to LIMITS[field]; `default` if not a number."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    if v != v:          # NaN
        return default
    lo, hi = LIMITS[field]
    return max(lo, min(hi, v))


@dataclass
class SizeFilter:
    """Flags cells whose area is implausible for a single erythrocyte.

    Both bounds are factors of the **median** cell area *of that image*, so the
    filter adapts to magnification and needs no absolute pixel values. Median,
    not mean: the fragments we are looking for drag a mean down and would raise
    their own detection threshold.

      area <  min_factor * median  -> segmentation fragment / spurious detection
      area >  max_factor * median  -> two cells merged into one label

    Flagged cells render gray and drop out of the count exactly like border
    cells, and a click with the *Zählen ein/aus* tool puts any of them back in.

    Replaces the former centroid-distance "duplicate detection" (see
    docs/consistency-audit.md PF-13): Cellpose masks are a pixel partition, so
    two labels can never overlap, and measured on real data no threshold existed
    that flagged anything before it started flagging ordinary neighbours.
    """
    enabled: bool = True
    min_factor: float = 0.35
    max_factor: float = 1.70


@dataclass
class ProfileData:
    """Everything that defines how a study is reviewed."""
    name: str = DEFAULT_PROFILE_NAME
    import_folder: str | None = None  # path to the images folder for this study
    image_pattern: str = DEFAULT_IMAGE_PATTERN
    # Friendly filename parser (alternative to regex). When use_simple_parser is
    # True the filename is split on any character in `delimiters` (extension
    # stripped first) and each resulting token is mapped to a role via
    # `field_roles` (one role per token position): "subject" | "timepoint" |
    # "image_num" | "ignore". The regex above is still kept for the advanced tab.
    use_simple_parser: bool = False
    delimiters: list[str] = field(default_factory=lambda: ["_"])
    field_roles: list[str] = field(default_factory=list)
    target_cells: int = 2000
    # Batch default: compute the first N images per parsed (timepoint, subject)
    # group; images beyond N are left raw. Pre-selects the batch dialog.
    images_per_subject: int = 13
    # Show the in-app Statistics tab for this study.
    stats_enabled: bool = True
    # Generate the cross-group overview workbook (Uebersicht.xlsx) on export.
    overview_xlsx: bool = True
    # Export: write per-image overlay PNGs (off by default — large + slow).
    overlay_png: bool = False
    # Export: copy original TIFs into outputs/ (off by default — avoids
    # duplicating the source images; masks already live compressed in the cache).
    copy_originals_to_outputs: bool = False
    cell_types: list[TypeDef] = field(default_factory=lambda: list(DEFAULT_TYPES_TRIBAL))
    cellpose: CellposeParams = field(default_factory=CellposeParams)
    use_gpu: bool = True
    margin_n: int = 0
    show_margin_overlay: bool = True
    size_filter: SizeFilter = field(default_factory=SizeFilter)
    # Opacity of the type-colour fill over the image. Raised from the original
    # 0.42 because at that value the colours wash out against a bright field
    # (measured: min ΔE between types 21.6 -> 27.6 going from 0.42 to 0.55).
    overlay_alpha: float = 0.55
    # Draw each type's contour (TypeDef.outline) so the type is not conveyed by
    # colour alone — WCAG 1.4.1. Off restores the pure colour rendering.
    type_outlines_enabled: bool = True
    # Per-profile UI state (persisted)
    sidebar_open_groups: list[str] = field(default_factory=list)  # "timepoint/subject" keys
    details_collapsed: bool = False
    # Recently used image folders (most recent first, capped at recent_folders_max)
    recent_folders: list[str] = field(default_factory=list)
    # Currently-selected type for the "Modify" tool (per-profile so each study keeps its own)
    active_type_key: str = "normal"
    # Metadata
    created_at: str = ""
    updated_at: str = ""

    # ─────────────────────────────────────────
    # Serialization
    # ─────────────────────────────────────────
    @classmethod
    def from_dict(cls, raw: dict) -> "ProfileData":
        cp = CellposeParams(**raw.get("cellpose", {}))
        types_raw = raw.get("cell_types") or []
        # Drop unknown keys instead of raising: a profile written by a NEWER
        # version (or arriving inside a shared package) must not break the app
        # with a TypeError over a field this build doesn't know yet.
        known = {f.name for f in fields(TypeDef)}
        types = ([TypeDef(**{k: v for k, v in t.items() if k in known})
                  for t in types_raw]
                 if types_raw else list(DEFAULT_TYPES_TRIBAL))
        # Back-compat: profiles written before the size filter carry a
        # `duplicate_detection` block. Its thresholds are meaningless now (a
        # different criterion), but the user's on/off choice is honoured.
        sf_raw = raw.get("size_filter")
        if sf_raw is None:
            legacy = raw.get("duplicate_detection") or {}
            sf = SizeFilter(enabled=bool(legacy.get("enabled", True)))
        else:
            sf = SizeFilter(
                enabled=bool(sf_raw.get("enabled", True)),
                min_factor=float(sf_raw.get("min_factor", 0.35)),
                max_factor=float(sf_raw.get("max_factor", 1.70)),
            )
        return cls(
            name=raw.get("name", DEFAULT_PROFILE_NAME),
            import_folder=raw.get("import_folder"),
            image_pattern=raw.get("image_pattern", DEFAULT_IMAGE_PATTERN),
            use_simple_parser=bool(raw.get("use_simple_parser", False)),
            delimiters=list(raw.get("delimiters", ["_"])) or ["_"],
            field_roles=list(raw.get("field_roles", [])),
            target_cells=int(raw.get("target_cells", 2000)),
            images_per_subject=int(raw.get("images_per_subject", 13)),
            stats_enabled=bool(raw.get("stats_enabled", True)),
            overview_xlsx=bool(raw.get("overview_xlsx", True)),
            overlay_png=bool(raw.get("overlay_png", False)),
            copy_originals_to_outputs=bool(raw.get("copy_originals_to_outputs", False)),
            cell_types=types,
            cellpose=cp,
            use_gpu=bool(raw.get("use_gpu", True)),
            margin_n=int(raw.get("margin_n", 0)),
            show_margin_overlay=bool(raw.get("show_margin_overlay", True)),
            size_filter=sf,
            overlay_alpha=float(raw.get("overlay_alpha", 0.55)),
            type_outlines_enabled=bool(raw.get("type_outlines_enabled", True)),
            sidebar_open_groups=list(raw.get("sidebar_open_groups", [])),
            details_collapsed=bool(raw.get("details_collapsed", False)),
            recent_folders=list(raw.get("recent_folders", [])),
            active_type_key=raw.get("active_type_key", "normal"),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "import_folder": self.import_folder,
            "image_pattern": self.image_pattern,
            "use_simple_parser": self.use_simple_parser,
            "delimiters": self.delimiters,
            "field_roles": self.field_roles,
            "target_cells": self.target_cells,
            "images_per_subject": self.images_per_subject,
            "stats_enabled": self.stats_enabled,
            "overview_xlsx": self.overview_xlsx,
            "overlay_png": self.overlay_png,
            "copy_originals_to_outputs": self.copy_originals_to_outputs,
            "cell_types": [asdict(t) for t in self.cell_types],
            "cellpose": asdict(self.cellpose),
            "use_gpu": self.use_gpu,
            "margin_n": self.margin_n,
            "show_margin_overlay": self.show_margin_overlay,
            "size_filter": asdict(self.size_filter),
            "overlay_alpha": self.overlay_alpha,
            "type_outlines_enabled": self.type_outlines_enabled,
            "sidebar_open_groups": self.sidebar_open_groups,
            "details_collapsed": self.details_collapsed,
            "recent_folders": self.recent_folders,
            "active_type_key": self.active_type_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # ─────────────────────────────────────────
    # Type helpers
    # ─────────────────────────────────────────
    def type_by_key(self, key: str) -> TypeDef | None:
        for t in self.cell_types:
            if t.key == key:
                return t
        return None

    def default_type_key(self) -> str:
        for t in self.cell_types:
            if t.key == "normal":
                return "normal"
        return self.cell_types[0].key if self.cell_types else "normal"

    # ─────────────────────────────────────────
    # Validation
    # ─────────────────────────────────────────
    def validate_regex(self) -> tuple[bool, str]:
        """Returns (ok, error_message)."""
        try:
            r = re.compile(self.image_pattern)
        except re.error as e:
            return (False, f"Regex error: {e}")
        names = set(r.groupindex.keys())
        required = {"subject", "timepoint", "image_num"}
        missing = required - names
        if missing:
            return (False, f"Missing named groups: {sorted(missing)}")
        return (True, "")


# ─────────────────────────────────────────────
# Profile file system
# ─────────────────────────────────────────────
def profiles_dir() -> Path:
    d = app_support_dir() / "profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_")
    return s or "profile"


def profile_path(name: str) -> Path:
    return profiles_dir() / f"{_slugify(name)}.json"


def list_profiles() -> list[str]:
    """Returns the names of all stored profiles."""
    out = []
    for p in sorted(profiles_dir().glob("*.json")):
        raw = read_json(p)
        if raw and "name" in raw:
            out.append(raw["name"])
    return out


def load_profile(name: str) -> ProfileData | None:
    raw = read_json(profile_path(name))
    if not raw:
        return None
    try:
        return ProfileData.from_dict(raw)
    except (TypeError, ValueError):
        return None


def save_profile(profile: ProfileData) -> None:
    if not profile.created_at:
        profile.created_at = _now_iso()
    profile.updated_at = _now_iso()
    write_json_atomic(profile_path(profile.name), profile.to_dict())


def delete_profile(name: str) -> bool:
    try:
        profile_path(name).unlink()
        return True
    except FileNotFoundError:
        return False


def migrate_default_name(settings=None) -> bool:
    """Rename the default study from its old name ('TRIBAL (Default)') to
    DEFAULT_PROFILE_NAME, once. Folder, types and reviews stay — the reviews
    live in the image folder, not in the study file. If it was the last
    opened study, `settings` follows (and is saved). Leaves everything alone
    when a study with the new name already exists. Returns True if renamed."""
    if load_profile(DEFAULT_PROFILE_NAME) is not None:
        return False
    for old in LEGACY_DEFAULT_NAMES:
        p = load_profile(old)
        if p is None:
            continue
        rename_profile(p, DEFAULT_PROFILE_NAME)
        if settings is not None and settings.last_profile_name == old:
            settings.last_profile_name = DEFAULT_PROFILE_NAME
            settings.save()
        return True
    return False


def ensure_default_profile() -> ProfileData:
    """Idempotent: creates the default profile on disk if it does not exist."""
    existing = load_profile(DEFAULT_PROFILE_NAME)
    if existing is not None:
        return existing
    p = ProfileData()
    save_profile(p)
    return p


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def duplicate_profile(src: ProfileData, new_name: str) -> ProfileData:
    """A copy of `src` under `new_name`, saved. Folder and settings carry over;
    the per-profile UI state (open groups, recents) starts fresh."""
    import copy
    p = copy.deepcopy(src)
    p.name = new_name.strip()
    p.created_at = p.updated_at = ""
    p.sidebar_open_groups = []
    p.recent_folders = []
    save_profile(p)
    return p


def rename_profile(p: ProfileData, new_name: str) -> ProfileData:
    """Save `p` under `new_name` and remove the old file. The caller updates
    settings.last_profile_name if this was the active study."""
    old = p.name
    p.name = new_name.strip()
    save_profile(p)
    if profile_path(old) != profile_path(p.name):
        delete_profile(old)
    return p
