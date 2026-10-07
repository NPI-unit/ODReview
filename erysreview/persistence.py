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


# Memoized per folder: `mkdir(parents=True, exist_ok=True)` costs two syscalls on
# the (overwhelmingly common) already-exists path, and cache_dir_for sits under
# review_path / _counts_path / cache_paths — i.e. under every hot loop that walks
# a folder. Creating it once per folder is enough; write_json_atomic still does
# its own parent mkdir, so a cache dir deleted underneath us can't break writes.
_CACHE_DIRS: dict[Path, Path] = {}


def cache_dir_for(folder: Path) -> Path:
    d = _CACHE_DIRS.get(folder)
    if d is not None:
        return d
    d = folder / ".erys_cache"
    d.mkdir(parents=True, exist_ok=True)
    _CACHE_DIRS[folder] = d
    return d


# ─────────────────────────────────────────────
# Review namespaces (second ratings, interrater.py)
# ─────────────────────────────────────────────
# A second rating of the same images (Anna, "me again", a consensus) lives in
# its own namespace: <cache>/raters/<slug>/ for reviews and their counts, and
# outputs/_rater_<slug>/ for its export. Masks are shared — that is the point.
# While a study with a rater is open, the app reads and writes there.
_REVIEW_NS: dict[str, str] = {}
_TL = __import__("threading").local()


class main_reviews_only:
    """Within this block (on this thread) the main reviews are read whatever
    rating is open — e.g. the study picker counting A's progress."""

    def __enter__(self):
        self._old = getattr(_TL, "main", False)
        _TL.main = True

    def __exit__(self, *exc):
        _TL.main = self._old


def rater_slug(name: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip()).strip("_") or "rater"


def set_review_namespace(folder: Path, rater: str | None) -> None:
    key = str(folder)
    if rater:
        _REVIEW_NS[key] = rater
    else:
        _REVIEW_NS.pop(key, None)


def review_namespace(folder: Path) -> str | None:
    return _REVIEW_NS.get(str(folder))


def review_dir(folder: Path, rater: str | None = "") -> Path:
    """Where reviews (and their counts) live: the cache, or a rater's folder.
    rater "" = the active namespace, None = the main reviews."""
    if rater == "":
        rater = None if getattr(_TL, "main", False) else _REVIEW_NS.get(str(folder))
    base = cache_dir_for(folder)
    return base / "raters" / rater_slug(rater) if rater else base


def outputs_dir(folder: Path) -> Path:
    """The export folder of the active namespace (not created here)."""
    rater = None if getattr(_TL, "main", False) else _REVIEW_NS.get(str(folder))
    return folder / "outputs" / f"_rater_{rater_slug(rater)}" if rater else folder / "outputs"


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


_UNREADABLE_JSON: set[str] = set()
_UNWRITABLE_FOLDERS: set[str] = set()


def read_json(path: Path, default=None):
    """Read a JSON sidecar, or return `default` if it cannot be read.

    `ValueError`, not `json.JSONDecodeError`: a file whose *bytes* are not
    valid UTF-8 raises `UnicodeDecodeError`, which is a ValueError but NOT a
    JSONDecodeError, so it escaped this handler entirely. One such file — from
    a cloud folder handing back a partial read, or a review written by an older
    build under a Windows code page — then propagated out of
    `Sidebar.populate()` and aborted `_open_folder` halfway: folder and images
    set, empty state cleared, but `_show_image_at` never reached. The window
    looked loaded while the canvas held no image, so every click returned at
    the `self.image is None` guard and the app appeared dead to the mouse.

    Reported once per path rather than swallowed: returning the default for a
    review silently discards work somebody typed.
    """
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError) as e:
        key = str(path)
        if key not in _UNREADABLE_JSON:
            _UNREADABLE_JSON.add(key)
            print(f"[JSON unlesbar] {path.name}: {type(e).__name__} — {path}. "
                  f"Datei wird als leer behandelt; gespeicherte Angaben darin "
                  f"sind nicht sichtbar (Cloud-Auslagerung? Fremde Kodierung?).",
                  flush=True)
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
    # Contour drawn around cells of this type so the type is not carried by
    # color alone (WCAG 1.4.1). One of theme.OUTLINE_ORDER; "" = none.
    # Old profiles simply lack the key and fall back to "".
    outline: str = ""


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
# A mode, not an action: it needs the active highlight the other tools have,
# and listing it here is what gives it hotkey 4 (app.py enumerates this).
TOOL_NOTE    = "note"
# The neutral mode: a click changes nothing. Appended rather than put first so
# the digits 1-3 keep meaning what they have always meant — renumbering the
# tools everyone already knows would cost more than the tidier ordering is worth.
TOOL_CURSOR  = "cursor"
TOOLS_ORDER  = [TOOL_ADD, TOOL_REMOVE, TOOL_TYPE, TOOL_NOTE, TOOL_CURSOR]


@dataclass
class AppSettings:
    """Global app preferences. All study-specific config lives in a ProfileData
    (see erysreview.profile), not here."""
    language: str = "de"
    theme: str = "dark"               # "dark" or "light"
    window_w: int = 1400
    window_h: int = 900
    # Screen the size above was measured on. Without it the saved pixel
    # size is meaningless on any other display — see _safe_geometry().
    screen_w: int = 0
    screen_h: int = 0
    # Unix time of the last update check. Throttles the silent startup
    # check to once a day: GitHub allows 60 unauthenticated requests per
    # hour per IP, and a lab shares one NAT address.
    last_update_check: float = 0.0
    # False, solange das Release-Repo nicht existiert. Siehe app._build_menu().
    update_check_enabled: bool = False
    # Position and size of the floating remark editor. One box for the whole
    # app, so one remembered geometry — see notebox.py.
    notebox_x: int = 40
    notebox_y: int = 40
    notebox_w: int = 320
    notebox_h: int = 180
    sidebar_width: int = 270
    overlay_visible: bool = True
    # Reveal a hidden overlay when the user edits a cell (see
    # EryCanvas._autoshow_overlay). Off: the overlay stays as the user left it.
    overlay_autoshow: bool = True
    # Helper grid over the image: n × n fields (0 = off). Per computer, exploratory.
    grid_n: int = 0
    # Toolbar layout: "grouped" (one row) or "two_row" (cell types as chips).
    toolbar_layout: str = "grouped"
    # What happens to existing masks after the study's Cellpose parameters
    # changed: "later" (only the toolbar banner says so), "ask", "batch".
    resegment_default: str = "later"
    # Own key/mouse bindings, only where they differ from the defaults
    # ({action_id: [bindings]}, see shortcuts.py). Per computer.
    shortcuts: dict = field(default_factory=dict)
    active_tool: str = TOOL_ADD
    last_profile_name: str | None = None
    reviewer_name: str = ""           # stamped into shared packages + import logs
    # WCAG 1.4.4: multiplies every font size in the UI. Applied at startup via
    # theme.set_font_scale(); a change takes effect on restart, like the theme.
    font_scale: float = 1.0
    # Cellpose models available ON THIS MACHINE: display name -> model file.
    # Profiles reference a model by NAME only, because a profile travels inside
    # share packages and an absolute path from another machine is meaningless
    # there (Cellpose would silently fall back to cpsam). "cpsam" is built in
    # and never needs an entry.
    cellpose_models: dict = field(default_factory=dict)

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
                screen_w=int(raw.get("screen_w", 0)),
                screen_h=int(raw.get("screen_h", 0)),
                last_update_check=float(raw.get("last_update_check", 0.0)),
                update_check_enabled=bool(raw.get("update_check_enabled", False)),
                notebox_x=int(raw.get("notebox_x", 40)),
                notebox_y=int(raw.get("notebox_y", 40)),
                notebox_w=int(raw.get("notebox_w", 320)),
                notebox_h=int(raw.get("notebox_h", 180)),
                sidebar_width=int(raw.get("sidebar_width", 270)),
                overlay_visible=bool(raw.get("overlay_visible", True)),
                overlay_autoshow=bool(raw.get("overlay_autoshow", True)),
                grid_n=(int(raw.get("grid_n", 0)) if str(raw.get("grid_n", 0)).isdigit()
                        and 0 <= int(raw.get("grid_n", 0)) <= 10 else 0),
                toolbar_layout=(raw.get("toolbar_layout") if raw.get("toolbar_layout")
                                in ("grouped", "two_row") else "grouped"),
                resegment_default=(raw.get("resegment_default")
                                   if raw.get("resegment_default")
                                   in ("later", "ask", "batch") else "later"),
                active_tool=active_tool,
                last_profile_name=raw.get("last_profile_name"),
                reviewer_name=str(raw.get("reviewer_name", "")),
                font_scale=float(raw.get("font_scale", 1.0)),
                cellpose_models=dict(raw.get("cellpose_models") or {}),
                shortcuts=dict(raw.get("shortcuts") or {}),
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
            "screen_w": self.screen_w,
            "screen_h": self.screen_h,
            "last_update_check": self.last_update_check,
            "update_check_enabled": self.update_check_enabled,
            "notebox_x": self.notebox_x, "notebox_y": self.notebox_y,
            "notebox_w": self.notebox_w, "notebox_h": self.notebox_h,
            "sidebar_width": self.sidebar_width,
            "overlay_visible": self.overlay_visible,
            "overlay_autoshow": self.overlay_autoshow,
            "grid_n": self.grid_n,
            "toolbar_layout": self.toolbar_layout,
            "resegment_default": self.resegment_default,
            "active_tool": self.active_tool,
            "last_profile_name": self.last_profile_name,
            "reviewer_name": self.reviewer_name,
            "font_scale": self.font_scale,
            "cellpose_models": self.cellpose_models,
            "shortcuts": self.shortcuts,
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
        """Remember which image was open. Never fatal — this is a convenience.

        Reported once per folder rather than swallowed or raised: when a study
        sits on a removable drive and the drive is ejected mid-session, every
        image switch walked into `mkdir` on a path that no longer exists and
        threw out of the Tk callback. The app kept running but printed a
        traceback per click. Losing the remembered index costs nothing; losing
        a review would, and ReviewState.save deliberately still reports.
        """
        try:
            write_json_atomic(
                cache_dir_for(folder) / "folder_state.json",
                {"last_index": self.last_index},
            )
        except OSError as e:
            key = str(folder)
            if key not in _UNWRITABLE_FOLDERS:
                _UNWRITABLE_FOLDERS.add(key)
                print(f"[Ordner nicht beschreibbar] {folder}: {type(e).__name__} — "
                      f"zuletzt geöffnetes Bild wird nicht gemerkt. Datenträger "
                      f"ausgeworfen oder Ordner verschoben?", flush=True)


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
    # Active review time accumulated on this image (seconds), idle-paused.
    time_spent_s: float = 0.0
    # Free-text remarks for discussion. `notes` is cell_id (str) -> text, `note`
    # belongs to the whole image. Kept in the review JSON only, deliberately NOT
    # exported: the export files get shared, and free text invites content that
    # should not travel. Old review files simply lack both keys.
    notes: dict[str, str] = field(default_factory=dict)
    note: str = ""
    # Whether this image counts toward the per-group target (2000) evaluation:
    #   None  -> automatic (auto-fill by image order until target exceeded)
    #   True  -> user forced INTO the evaluation
    #   False -> user forced OUT of the evaluation
    eval_include: bool | None = None
    # How this image is SHOWN (display_adjust.py): brightness/contrast/gamma.
    # Not a rating — never counted, never compared; travels in packages.
    display: dict | None = None

    @staticmethod
    def review_path(folder: Path, image_stem: str) -> Path:
        return review_dir(folder) / f"{image_stem}_review.json"

    @classmethod
    def load(cls, folder: Path, image_stem: str) -> "ReviewState":
        return cls.load_file(cls.review_path(folder, image_stem))

    @classmethod
    def load_file(cls, path: Path) -> "ReviewState":
        """A review from any file — e.g. a second rater's (interrater.py)."""
        raw = read_json(path)
        if not raw:
            return cls()
        try:
            manual = [ManualCell(**m) for m in raw.get("manual_cells", [])]
            # Backward-compat: old files used `deactivated_ids` for the same meaning.
            force_excluded = raw.get("force_excluded")
            if force_excluded is None:
                force_excluded = raw.get("deactivated_ids", [])
            eval_include = raw.get("eval_include", None)
            if eval_include is not None:
                eval_include = bool(eval_include)
            return cls(
                force_excluded=list(force_excluded),
                force_included=list(raw.get("force_included", [])),
                types=dict(raw.get("types", {})),
                manual_cells=manual,
                is_finished=bool(raw.get("is_finished", False)),
                next_manual_id=int(raw.get("next_manual_id", 0)),
                time_spent_s=float(raw.get("time_spent_s", 0.0)),
                notes={str(k): str(v) for k, v in (raw.get("notes") or {}).items()},
                note=str(raw.get("note", "")),
                eval_include=eval_include,
                display=raw.get("display") if isinstance(raw.get("display"), dict) else None,
            )
        except (TypeError, ValueError):
            return cls()

    def to_dict(self) -> dict:
        return {
            "force_excluded": self.force_excluded,
            "force_included": self.force_included,
            "types": self.types,
            "manual_cells": [asdict(m) for m in self.manual_cells],
            "is_finished": self.is_finished,
            "next_manual_id": self.next_manual_id,
            "time_spent_s": round(self.time_spent_s, 1),
            "notes": self.notes,
            "note": self.note,
            "eval_include": self.eval_include,
            "display": self.display,
        }

    def save(self, folder: Path, image_stem: str) -> None:
        write_json_atomic(self.review_path(folder, image_stem), self.to_dict())

    def has_user_edits(self) -> bool:
        return bool(
            self.force_excluded or self.force_included
            or self.types or self.manual_cells or self.is_finished
            or self.notes or self.note
            or self.eval_include is not None
        )
