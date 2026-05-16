"""Three persistence layers (Frage 7, design consensus):

1. Global settings  – ~/Library/Application Support/Erys/settings.json
2. Per-folder state – <folder>/.erys_cache/folder_state.json
3. Per-image review – <folder>/.erys_cache/<image>_review.json

All writes are atomic (tmp file + rename). Per-image review auto-saves on every click.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


# ─────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────
def app_support_dir() -> Path:
    """Platform-appropriate location for global settings."""
    if sys.platform == "darwin":
        d = Path.home() / "Library" / "Application Support" / "Erys"
    elif sys.platform == "win32":
        d = Path(os.environ.get("APPDATA", Path.home())) / "Erys"
    else:
        d = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "erys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_dir_for(folder: Path) -> Path:
    d = folder / ".erys_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─────────────────────────────────────────────
# Atomic JSON
# ─────────────────────────────────────────────
def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


# ─────────────────────────────────────────────
# Global settings
# ─────────────────────────────────────────────
@dataclass
class CellposeParams:
    diameter: float = 30.0
    flow_threshold: float = 0.4
    cellprob_threshold: float = 0.0
    model: str = "cpsam"


@dataclass
class TypeDef:
    """One Ery type. `name` is per-language for built-ins via i18n key,
    or stored verbatim for user-custom types."""
    key: str                   # internal id, e.g. "normal", "sickle"
    i18n_key: str | None = None  # if set: lookup in i18n strings; else use `name`
    name: str = ""             # display name (used when i18n_key is None)
    color: str = "#FFD000"     # hex
    hotkey: str = "1"          # "1".."5"


# Built-in default types (translated via i18n_key)
DEFAULT_TYPES = [
    TypeDef(key="normal",      i18n_key="type_normal",      color="#FFD000", hotkey="1"),
    TypeDef(key="sickle",      i18n_key="type_sickle",      color="#FF6B6B", hotkey="2"),
    TypeDef(key="target",      i18n_key="type_target",      color="#4ECDC4", hotkey="3"),
    TypeDef(key="schistocyte", i18n_key="type_schistocyte", color="#9D4EDD", hotkey="4"),
    TypeDef(key="spherocyte",  i18n_key="type_spherocyte",  color="#F77F00", hotkey="5"),
]


# Tool identifiers (hotkey 1..3 mapping)
TOOL_ADD     = "add"
TOOL_REMOVE  = "remove"
TOOL_TYPE    = "type"
TOOLS_ORDER  = [TOOL_ADD, TOOL_REMOVE, TOOL_TYPE]


@dataclass
class AppSettings:
    """Global app preferences. All study-specific config lives in a ProfileData
    (see erysreview.profile), not here."""
    language: str = "de"
    theme: str = "dark"               # "dark" or "light"
    window_w: int = 1400
    window_h: int = 900
    sidebar_width: int = 270
    overlay_visible: bool = True
    active_tool: str = TOOL_ADD
    last_profile_name: str | None = None

    @classmethod
    def load(cls) -> "AppSettings":
        path = app_support_dir() / "settings.json"
        raw = read_json(path)
        if not raw:
            return cls()
        try:
            active_tool = raw.get("active_tool", TOOL_ADD)
            if active_tool not in TOOLS_ORDER:
                active_tool = TOOL_ADD
            theme = raw.get("theme", "dark")
            if theme not in ("dark", "light"):
                theme = "dark"
            return cls(
                language=raw.get("language", "de"),
                theme=theme,
                window_w=int(raw.get("window_w", 1400)),
                window_h=int(raw.get("window_h", 900)),
                sidebar_width=int(raw.get("sidebar_width", 270)),
                overlay_visible=bool(raw.get("overlay_visible", True)),
                active_tool=active_tool,
                last_profile_name=raw.get("last_profile_name"),
            )
        except (TypeError, ValueError):
            return cls()

    def save(self) -> None:
        path = app_support_dir() / "settings.json"
        write_json_atomic(path, {
            "language": self.language,
            "theme": self.theme,
            "window_w": self.window_w,
            "window_h": self.window_h,
            "sidebar_width": self.sidebar_width,
            "overlay_visible": self.overlay_visible,
            "active_tool": self.active_tool,
            "last_profile_name": self.last_profile_name,
        })


# ─────────────────────────────────────────────
# Per-folder state
# ─────────────────────────────────────────────
@dataclass
class FolderState:
    last_index: int = 0

    @classmethod
    def load(cls, folder: Path) -> "FolderState":
        raw = read_json(cache_dir_for(folder) / "folder_state.json")
        if not raw:
            return cls()
        try:
            return cls(last_index=int(raw.get("last_index", 0)))
        except (TypeError, ValueError):
            return cls()

    def save(self, folder: Path) -> None:
        write_json_atomic(
            cache_dir_for(folder) / "folder_state.json",
            {"last_index": self.last_index},
        )


# ─────────────────────────────────────────────
# Per-image review state
# ─────────────────────────────────────────────
@dataclass
class ManualCell:
    cell_id: int
    x: float
    y: float
    radius: float


@dataclass
class ReviewState:
    """All edits made by the user for a single image.
    Auto-saved to <cache>/<image-stem>_review.json on every click.

    Effective active-state per cell:
        active = (id not in force_excluded)
                 and (id not in border_ids or id in force_included)
    """
    force_excluded: list[int] = field(default_factory=list)  # user explicitly removed
    force_included: list[int] = field(default_factory=list)  # user included despite border flag
    types: dict[str, str] = field(default_factory=dict)      # cell_id (str) -> type_key
    manual_cells: list[ManualCell] = field(default_factory=list)
    is_finished: bool = False
    next_manual_id: int = 0

    @staticmethod
    def review_path(folder: Path, image_stem: str) -> Path:
        return cache_dir_for(folder) / f"{image_stem}_review.json"

    @classmethod
    def load(cls, folder: Path, image_stem: str) -> "ReviewState":
        raw = read_json(cls.review_path(folder, image_stem))
        if not raw:
            return cls()
        try:
            manual = [ManualCell(**m) for m in raw.get("manual_cells", [])]
            # Backward-compat: old files used `deactivated_ids` for the same meaning.
            force_excluded = raw.get("force_excluded")
            if force_excluded is None:
                force_excluded = raw.get("deactivated_ids", [])
            return cls(
                force_excluded=list(force_excluded),
                force_included=list(raw.get("force_included", [])),
                types=dict(raw.get("types", {})),
                manual_cells=manual,
                is_finished=bool(raw.get("is_finished", False)),
                next_manual_id=int(raw.get("next_manual_id", 0)),
            )
        except (TypeError, ValueError):
            return cls()

    def save(self, folder: Path, image_stem: str) -> None:
        write_json_atomic(
            self.review_path(folder, image_stem),
            {
                "force_excluded": self.force_excluded,
                "force_included": self.force_included,
                "types": self.types,
                "manual_cells": [asdict(m) for m in self.manual_cells],
                "is_finished": self.is_finished,
                "next_manual_id": self.next_manual_id,
            },
        )

    def has_user_edits(self) -> bool:
        return bool(
            self.force_excluded or self.force_included
            or self.types or self.manual_cells or self.is_finished
        )
