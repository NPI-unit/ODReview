"""Study profile module.

A profile bundles everything study-specific: how filenames map to (timepoint, subject),
the cell types, Cellpose parameters, target cell count, duplicate detection rules,
and the persisted Treeview-open-state.

Profiles live globally at:
    ~/Library/Application Support/Erys/profiles/<slug>.json

Globals (language, window size, last_profile_name, active_tool) stay in settings.json.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .persistence import (
    CellposeParams, TypeDef, app_support_dir, read_json, write_json_atomic,
)


# ─────────────────────────────────────────────
# Default TRIBAL-prefilled profile content
# ─────────────────────────────────────────────
DEFAULT_PROFILE_NAME = "TRIBAL (Default)"

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


@dataclass
class DuplicateDetection:
    enabled: bool = True
    threshold_factor: float = 0.7   # multiplied by mean cell radius
    policy: str = "remove_smaller"  # or "remove_both"


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
    # Show the in-app Statistics tab for this study.
    stats_enabled: bool = True
    cell_types: list[TypeDef] = field(default_factory=lambda: list(DEFAULT_TYPES_TRIBAL))
    cellpose: CellposeParams = field(default_factory=CellposeParams)
    use_gpu: bool = True
    margin_n: int = 0
    show_margin_overlay: bool = True
    duplicate_detection: DuplicateDetection = field(default_factory=DuplicateDetection)
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
        types = [TypeDef(**t) for t in types_raw] if types_raw else list(DEFAULT_TYPES_TRIBAL)
        dup_raw = raw.get("duplicate_detection") or {}
        dup = DuplicateDetection(
            enabled=bool(dup_raw.get("enabled", True)),
            threshold_factor=float(dup_raw.get("threshold_factor", 0.7)),
            policy=str(dup_raw.get("policy", "remove_smaller")),
        )
        return cls(
            name=raw.get("name", DEFAULT_PROFILE_NAME),
            import_folder=raw.get("import_folder"),
            image_pattern=raw.get("image_pattern", DEFAULT_IMAGE_PATTERN),
            use_simple_parser=bool(raw.get("use_simple_parser", False)),
            delimiters=list(raw.get("delimiters", ["_"])) or ["_"],
            field_roles=list(raw.get("field_roles", [])),
            target_cells=int(raw.get("target_cells", 2000)),
            stats_enabled=bool(raw.get("stats_enabled", True)),
            cell_types=types,
            cellpose=cp,
            use_gpu=bool(raw.get("use_gpu", True)),
            margin_n=int(raw.get("margin_n", 0)),
            show_margin_overlay=bool(raw.get("show_margin_overlay", True)),
            duplicate_detection=dup,
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
            "stats_enabled": self.stats_enabled,
            "cell_types": [asdict(t) for t in self.cell_types],
            "cellpose": asdict(self.cellpose),
            "use_gpu": self.use_gpu,
            "margin_n": self.margin_n,
            "show_margin_overlay": self.show_margin_overlay,
            "duplicate_detection": asdict(self.duplicate_detection),
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
