"""Studies: a name, the image folders, how file names are read, and the
measurement settings. One JSON file per study in the app's support folder.

File names are read without regex, as in ErysReview: pick delimiter
characters and give each resulting part a role. ODReview's roles go beyond
ErysReview's proband/timepoint/image number: group, cell type and marker,
because 5mC and 5hmC are quantified separately and groups are compared.

Example `TEST A NK 5hmc 10.02.2026_002.tif` with delimiters " " and "_":
    TEST → ignorieren, A → proband, NK → zelltyp, 5hmc → marker,
    10.02.2026 → datum, 002 → bildnr

A date is always one part, even when its own separator is a delimiter:
`2026-08-27-C2C12-Ki67-FE01R3-20x_001` with "-" and "_" reads
2026-08-27 | C2C12 | Ki67 | FE01R3 | 20x | 001. The "datum" role is stored as
JJJJ-MM-TT.

Templates (Vorlagen) keep what a kind of experiment shares — target,
file-name scheme and every measurement setting — without the name, folders
and groups that belong to one experiment.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import __version__
from .paths import app_support_dir
from .state import I0_ABSOLUTE, I0_LOCAL, IMG_EXTS, Review, Settings, list_images

ROLE_PROBAND = "proband"
ROLE_GROUP = "gruppe"
ROLE_TIMEPOINT = "zeitpunkt"
ROLE_CELLTYPE = "zelltyp"
ROLE_MARKER = "marker"
ROLE_IMAGE_NUM = "bildnr"
ROLE_DATE = "datum"
ROLE_IGNORE = "ignorieren"
ROLES = [ROLE_PROBAND, ROLE_GROUP, ROLE_TIMEPOINT, ROLE_CELLTYPE, ROLE_MARKER,
         ROLE_DATE, ROLE_IMAGE_NUM, ROLE_IGNORE]
ROLE_LABELS = {ROLE_PROBAND: "Subject", ROLE_GROUP: "Group", ROLE_TIMEPOINT: "Timepoint",
               ROLE_CELLTYPE: "Cell type", ROLE_MARKER: "Marker", ROLE_DATE: "Date",
               ROLE_IMAGE_NUM: "Image no.", ROLE_IGNORE: "ignore"}
META_ROLES = [r for r in ROLES if r != ROLE_IGNORE]
# Role keys are stored in study files (German); exports and the UI use these.
ROLE_COLUMNS = {ROLE_PROBAND: "subject", ROLE_GROUP: "group", ROLE_TIMEPOINT: "timepoint",
                ROLE_CELLTYPE: "cell_type", ROLE_MARKER: "marker", ROLE_DATE: "date",
                ROLE_IMAGE_NUM: "image_no"}
CANDIDATE_DELIMS = [" ", "_", "-", "."]

TARGET_CELL = "zelle"          # stored value (kept German); whole cell (NK): Cellpose on RGB
TARGET_NUCLEUS = "kern"         # nucleus (C2C12 Ki67): Cellpose on the DAB map
TARGET_LABELS = {TARGET_CELL: "whole cell", TARGET_NUCLEUS: "nucleus"}

# Nucleus presets per magnification (stored values kept German). Pixel size is
# InstanSeg's input, estimated from the nucleus size on the pilot images
# (median ~37 / ~22 px across, ~10 µm nuclei); replace it once the microscope
# calibration is known. Areas in px: nuclei kept, specks and cells dropped.
MAG_HIGH = "hoch"
MAG_LOW = "niedrig"
MAG_LABELS = {MAG_HIGH: "high magnification", MAG_LOW: "low magnification"}
MAGNIFICATIONS = {MAG_HIGH: {"pixel_size": 0.25, "min_cell_area": 200, "max_cell_area": 4000},
                  MAG_LOW: {"pixel_size": 0.45, "min_cell_area": 100, "max_cell_area": 1500}}


def apply_magnification(settings: Settings, magnification: str) -> None:
    preset = MAGNIFICATIONS[magnification]
    settings.cellpose.pixel_size = preset["pixel_size"]
    settings.min_cell_area = preset["min_cell_area"]
    settings.max_cell_area = preset["max_cell_area"]

NO_TIMEPOINT = "no timepoint"
NO_PROBAND = "no subject"


# ─────────────────────────────────────────────
# File names
# ─────────────────────────────────────────────
def strip_ext(name: str) -> str:
    low = name.lower()
    for ext in IMG_EXTS:
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


# Dates that stay one part: 2026-08-27, 2026.08.27, 27.08.2026, 10.02.26
_DATE_IN_NAME = re.compile(
    r"(?<![0-9])(?:(?:19|20)\d{2}[-.](?:0?[1-9]|1[0-2])[-.](?:0?[1-9]|[12]\d|3[01])"
    r"|(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])\.(?:\d{4}|\d{2}))(?![0-9])")


def iso_date(text: str) -> str:
    """'2026-08-27', '27.08.2026', '10.02.26' → 'JJJJ-MM-TT' ('' if not a date)."""
    m = re.fullmatch(r"((?:19|20)\d{2})[-.](\d{1,2})[-.](\d{1,2})", text)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})", text)
        if not m:
            return ""
        d, mo, y = m.groups()
        if len(y) == 2:
            y = "20" + y
    try:
        return _dt.date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return ""


def tokenize(name: str, delimiters: list[str]) -> list[str]:
    stem = strip_ext(name)
    delims = [d for d in delimiters if d]
    if not delims:
        return [stem]
    # Protect dates: swap each for a placeholder free of delimiters, split,
    # then put the dates back.
    dates: list[str] = []

    def keep(m):
        dates.append(m.group(0))
        return f"\x00{len(dates) - 1}\x00"
    protected = _DATE_IN_NAME.sub(keep, stem)
    parts = [t for t in re.split("|".join(re.escape(d) for d in delims), protected) if t]
    return [re.sub("\x00(\\d+)\x00", lambda m: dates[int(m.group(1))], t) for t in parts]


def parse_name(name: str, delimiters: list[str], roles: list[str]) -> dict:
    """{role: value} for the meta roles; missing ones are ''. A name with a
    different number of parts than roles is read as far as it goes."""
    toks = tokenize(name, delimiters)
    out = {r: "" for r in META_ROLES}
    for tok, role in zip(toks, roles):
        if role == ROLE_DATE:
            tok = iso_date(tok) or tok
        if role in out:
            out[role] = f"{out[role]} {tok}".strip() if out[role] else tok
    return out


def detect_delimiters(names: list[str]) -> list[str]:
    """Delimiters that occur in most names (the date's dots are not one)."""
    stems = [strip_ext(n) for n in names] or [""]
    out = []
    for d in CANDIDATE_DELIMS:
        if d == ".":
            continue
        if sum(d in s for s in stems) >= 0.8 * len(stems):
            out.append(d)
    return out


_DATE = re.compile(r"^\d{1,4}[.\-/]\d{1,2}([.\-/]\d{1,4})?$|^\d{6}$|^\d{8}$")
_MARKER = re.compile(r"^(5h?mc|ki-?67|foxp3|h3k\w+|dna|brdu|pcna)$", re.I)
_CELLTYPE = re.compile(r"^(nk|treg|t-?reg|cd\d+|pbmc|c2c12|hela|mono\w*|lympho\w*)$", re.I)
_TIMEPOINT = re.compile(r"^(pr[äa]e?|post|pre|t\d+|d\d+|w\d+|baseline|bl|fu\d*)$", re.I)
_MAGNIFICATION = re.compile(r"^\d{1,3}x$", re.I)


def guess_roles(names: list[str], delimiters: list[str]) -> list[str]:
    """One role per part position, guessed from the real names."""
    rows = [tokenize(n, delimiters) for n in names]
    if not rows:
        return []
    n = Counter(len(r) for r in rows).most_common(1)[0][0]
    cols = [[r[i] for r in rows if len(r) == n] for i in range(n)]
    roles: list = []
    open_cols = []
    for i, col in enumerate(cols):
        if all(iso_date(v) for v in col):
            roles.append(ROLE_DATE if ROLE_DATE not in roles else ROLE_IGNORE)
        elif all(_DATE.match(v) or _MAGNIFICATION.match(v) for v in col):
            roles.append(ROLE_IGNORE)
        elif i == n - 1 and all(v.isdigit() for v in col):
            roles.append(ROLE_IMAGE_NUM)
        elif all(_MARKER.match(v) for v in col):
            roles.append(ROLE_MARKER)
        elif all(_CELLTYPE.match(v) for v in col):
            roles.append(ROLE_CELLTYPE)
        elif all(_TIMEPOINT.match(v) for v in col):
            roles.append(ROLE_TIMEPOINT)
        elif len(rows) > 1 and len(set(col)) == 1:
            roles.append(ROLE_IGNORE)               # constant: project name etc.
        else:
            roles.append(ROLE_IGNORE)
            open_cols.append(i)
    if open_cols:
        # the proband is the shortest remaining part ("A", "195"), not "TEST"
        best = min(open_cols, key=lambda i: (sum(map(len, cols[i])) / len(cols[i]), i))
        roles[best] = ROLE_PROBAND
    return roles


# ─────────────────────────────────────────────
# Study
# ─────────────────────────────────────────────
def studies_dir() -> Path:
    d = app_support_dir() / "studien"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß_-]+", "_", name).strip("_")
    return s or "studie"


@dataclass
class ImageInfo:
    path: Path
    folder: Path
    meta: dict                       # role -> value (proband, gruppe, …)

    @property
    def timepoint(self) -> str:
        return self.meta.get(ROLE_TIMEPOINT) or NO_TIMEPOINT

    @property
    def proband(self) -> str:
        return self.meta.get(ROLE_PROBAND) or NO_PROBAND

    @property
    def marker(self) -> str:
        return self.meta.get(ROLE_MARKER) or ""


@dataclass
class Study:
    name: str
    folders: list = field(default_factory=list)
    delimiters: list = field(default_factory=lambda: [" ", "_"])
    roles: list = field(default_factory=list)
    groups: dict = field(default_factory=dict)        # proband -> group (when not in the name)
    target: str = TARGET_CELL
    settings: Settings = field(default_factory=Settings)
    created_at: str = ""
    file: str = ""                                    # json file name in studies_dir()

    # ── persistence ──────────────────────────────────────
    @property
    def path(self) -> Path:
        return studies_dir() / (self.file or f"{_slug(self.name)}.json")

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("file", None)
        d["app_version"] = __version__
        return d

    def save(self) -> None:
        if not self.file:
            base, n = _slug(self.name), 1
            self.file = f"{base}.json"
            while (studies_dir() / self.file).exists():
                n += 1
                self.file = f"{base}_{n}.json"
        if not self.created_at:
            self.created_at = _dt.datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)

    @classmethod
    def from_dict(cls, d: dict, file: str = "") -> "Study":
        return cls(
            name=d.get("name", "Study"),
            folders=list(d.get("folders", [])),
            delimiters=list(d.get("delimiters", [" ", "_"])),
            roles=list(d.get("roles", [])),
            groups=dict(d.get("groups", {})),
            target=d.get("target", TARGET_CELL),
            settings=Settings.from_dict(d.get("settings", {})),
            created_at=d.get("created_at", ""),
            file=file,
        )

    @classmethod
    def load(cls, path: Path) -> "Study":
        return cls.from_dict(json.loads(Path(path).read_text("utf-8")), Path(path).name)

    def apply_target(self, magnification: str = MAG_HIGH) -> None:
        """Defaults that follow from what is measured."""
        from .segment import METHOD_CELLPOSE, METHOD_HYBRID, MODEL_INSTANSEG, CellposeParams
        nucleus = self.target == TARGET_NUCLEUS
        self.settings.cellpose.model = MODEL_INSTANSEG if nucleus else CellposeParams.model
        self.settings.cellpose.input = "dab" if nucleus else "rgb"
        self.settings.debris_h = 0.04 if nucleus else 0.0
        # Nuclei: the contour is the model's own; an OD-threshold edge grows
        # into neighbouring debris.
        self.settings.method = METHOD_CELLPOSE if nucleus else METHOD_HYBRID
        # I₀ validity: the fields of a nucleus must agree (± 3 %) – the pilot images are
        # vignetted, so a fixed 210–220 excluded 98 of 147 nuclei, the local check 23.
        self.settings.i0_check = I0_LOCAL if nucleus else I0_ABSOLUTE
        if nucleus:
            self.settings.i0_rel_pct = 3.0
            apply_magnification(self.settings, magnification)
        else:
            self.settings.cellpose.pixel_size = 0.0
            self.settings.max_cell_area = 0

    # ── images ───────────────────────────────────────────
    def images(self) -> list[ImageInfo]:
        out = []
        for f in self.folders:
            folder = Path(f)
            if not folder.is_dir():
                continue
            for p in list_images(folder):
                meta = parse_name(p.name, self.delimiters, self.roles)
                if not meta[ROLE_GROUP] and meta[ROLE_PROBAND] in self.groups:
                    meta[ROLE_GROUP] = self.groups[meta[ROLE_PROBAND]]
                out.append(ImageInfo(p, folder.resolve(), meta))
        return out

    def markers(self, images: list[ImageInfo] | None = None) -> list[str]:
        return sorted({i.marker for i in (images or self.images()) if i.marker}, key=str.lower)


# ─────────────────────────────────────────────
# Templates (Vorlagen)
# ─────────────────────────────────────────────
def templates_dir() -> Path:
    d = app_support_dir() / "vorlagen"
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass
class Template:
    """What a kind of experiment shares: target, file-name scheme and every
    measurement setting. Name, folders and groups belong to each study."""
    name: str
    target: str = TARGET_CELL
    delimiters: list = field(default_factory=lambda: [" ", "_"])
    roles: list = field(default_factory=list)
    settings: Settings = field(default_factory=Settings)
    description: str = ""
    created_at: str = ""
    file: str = ""

    @property
    def path(self) -> Path:
        return templates_dir() / (self.file or f"{_slug(self.name)}.json")

    @classmethod
    def from_study(cls, study: "Study", name: str, description: str = "") -> "Template":
        import copy
        return cls(name=name, target=study.target, delimiters=list(study.delimiters),
                   roles=list(study.roles), settings=copy.deepcopy(study.settings),
                   description=description)

    def apply_to(self, study: "Study") -> None:
        import copy
        study.target = self.target
        study.delimiters = list(self.delimiters)
        study.roles = list(self.roles)
        study.settings = copy.deepcopy(self.settings)

    def save(self) -> None:
        self.file = self.file or f"{_slug(self.name)}.json"
        if not self.created_at:
            self.created_at = _dt.datetime.now().isoformat(timespec="seconds")
        d = asdict(self)
        d.pop("file", None)
        d["app_version"] = __version__
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, path: Path) -> "Template":
        d = json.loads(Path(path).read_text("utf-8"))
        return cls(name=d.get("name", Path(path).stem), target=d.get("target", TARGET_CELL),
                   delimiters=list(d.get("delimiters", [" ", "_"])), roles=list(d.get("roles", [])),
                   settings=Settings.from_dict(d.get("settings", {})),
                   description=d.get("description", ""), created_at=d.get("created_at", ""),
                   file=Path(path).name)


def list_templates() -> list[Template]:
    out = []
    for p in sorted(templates_dir().glob("*.json")):
        try:
            out.append(Template.load(p))
        except (OSError, ValueError, TypeError):
            continue
    return out


def template_file_for(name: str) -> Path:
    return templates_dir() / f"{_slug(name)}.json"


def list_studies() -> list[Study]:
    out = []
    for p in sorted(studies_dir().glob("*.json")):
        try:
            out.append(Study.load(p))
        except (OSError, ValueError, TypeError):
            continue
    return out


# ─────────────────────────────────────────────
# Progress
# ─────────────────────────────────────────────
def group_key(info: ImageInfo) -> tuple[str, str, str]:
    return info.proband, info.timepoint, info.marker


def progress(images: list[ImageInfo]) -> dict:
    """(proband, zeitpunkt, marker) -> [reviewed images, images]."""
    out: dict = {}
    for info in images:
        k = group_key(info)
        done = Review.load(info.folder, info.path).reviewed
        v = out.setdefault(k, [0, 0])
        v[0] += int(done)
        v[1] += 1
    return out


def proband_progress(images: list[ImageInfo]) -> tuple[int, int]:
    """(probands with every image reviewed, probands)."""
    per: dict = {}
    for (prob, _tp, _m), (done, n) in progress(images).items():
        a = per.setdefault(prob, [True])
        a[0] = a[0] and done == n
    return sum(v[0] for v in per.values()), len(per)
