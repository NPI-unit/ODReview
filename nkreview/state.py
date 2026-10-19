"""Measurement settings (part of a study) and per-image review state, the
latter as JSON in <folder>/.nk_cache/."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .od import DEFAULT_PRESET, STAIN_PRESETS
from .paths import app_support_dir  # noqa: F401 — re-exported
from .segment import METHOD_HYBRID, CellposeParams, cache_dir

IMG_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp"}
MANUAL_ID_BASE = 10000


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


GRAY_MEAN = "mean"              # ImageJ default: (R+G+B)/3
GRAY_WEIGHTED = "weighted"      # ImageJ "Weighted RGB conversions": 0.299/0.587/0.114
I0_ABSOLUTE = "absolut"
I0_RELATIVE = "relativ"
I0_LOCAL = "lokal"              # the fields of a cell must agree within ± i0_rel_pct
I0_PER_CELL = "zelle"           # stored values (German, like the rest)
I0_PER_IMAGE = "bild"


@dataclass
class Settings:
    """Everything a measurement depends on. Stored inside the study, so an
    export is always reproducible from its einstellungen.json."""
    # Fiji-conform measurement (the method section): grey value, I₀ per cell
    # from random background fields next to it, OD of the mean.
    gray_mode: str = GRAY_MEAN
    i0_scope: str = I0_PER_CELL     # "zelle": fields next to each cell; "bild": one set of
                                    # fields per image, far from every cell, used for all its cells
    i0_fields: int = 3              # background fields per cell (or per image) …
    i0_field_size: int = 9          # … of size × size px (9×9 = 81 ≈ "80 pixels")
    i0_ring_inner: int = 14         # fields lie this far from the contour …
    i0_ring_outer: int = 40         # … and no further
    i0_check: str = "absolut"       # "absolut": I₀ within [i0_min, i0_max];
                                    # "relativ": within ± i0_rel_pct of the image's free background;
                                    # "lokal": each field within ± i0_rel_pct of the fields' mean
                                    # (robust to uneven illumination: catches a field on debris)
    i0_min: float = 210.0           # I₀ outside [min, max] → cell excluded
    i0_max: float = 220.0
    i0_rel_pct: float = 3.0
    min_cell_area: int = 0          # smaller Cellpose objects are dropped (0 = keep all)
    max_cell_area: int = 0          # larger ones too (0 = no limit); nuclei: whole cells, clumps
    pos_threshold: float = 0.0      # OD above this = "positive" (0 = no positive/negative call)
    od_high: float = 0.3            # report the share of pixels above this OD
    debris_h: float = 0.0           # flag "Debris?" if H-channel OD ≥ this (0 = off)
    method: str = METHOD_HYBRID
    threshold: float = 0.05         # total OD above I₀ for the contour
    grow: int = 10                  # hybrid: px the edge may move out of Cellpose
    min_area: int = 500             # threshold backup: smallest cell in px
    ring_inner: int = 15            # local I₀ control ring, px from the contour
    ring_outer: int = 60
    ring_warn_pct: float = 5.0      # flag a cell if ring-I₀ differs more than this
    stain_preset: str = DEFAULT_PRESET
    stain_vectors: list = field(default_factory=lambda: [list(r) for r in STAIN_PRESETS[DEFAULT_PRESET]])
    cellpose: CellposeParams = field(default_factory=CellposeParams)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        d = dict(d or {})
        cp = CellposeParams(**{k: v for k, v in (d.pop("cellpose", None) or {}).items()
                               if k in CellposeParams.__dataclass_fields__})
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known, cellpose=cp)

    @classmethod
    def load(cls, folder: Path) -> "Settings":
        try:
            return cls.from_dict(json.loads((cache_dir(folder) / "settings.json").read_text("utf-8")))
        except (OSError, ValueError):
            return cls()

    def save(self, folder: Path) -> None:
        _write_json(cache_dir(folder) / "settings.json", asdict(self))


@dataclass
class Review:
    """What the user decided for one image."""
    discarded: list = field(default_factory=list)       # cell ids
    manual: list = field(default_factory=list)          # polygons [[x, y], ...]
    method: str | None = None                           # None = folder setting
    threshold: float | None = None
    roi: list | None = None                             # background [x0,y0,x1,y1]
    reviewed: bool = False
    i0_fields: dict = field(default_factory=dict)       # "cid" -> [[x, y], …] set by hand
    i0_seed: dict = field(default_factory=dict)         # "cid" -> n (re-drawn n times)
    display: dict | None = None                         # brightness/contrast/gamma, view only
    confirmed: list = field(default_factory=list)       # cell ids the reviewer confirmed (C)
    # Remarks for discussion, as in ErysReview: cell id (str) -> text, and one for the
    # image. Review file only, deliberately not exported (export files get shared).
    notes: dict = field(default_factory=dict)
    note: str = ""
    marks: list = field(default_factory=list)           # comment circles: {"x", "y", "r", "text"}, image px

    @staticmethod
    def path(folder: Path, image: Path) -> Path:
        rel = Path(image).relative_to(folder).with_suffix("")
        return cache_dir(folder) / f"{'__'.join(rel.parts)}_review.json"

    @classmethod
    def load(cls, folder: Path, image: Path) -> "Review":
        try:
            d = json.loads(cls.path(folder, image).read_text("utf-8"))
            return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, folder: Path, image: Path) -> None:
        _write_json(self.path(folder, image), asdict(self))


def list_images(folder: Path) -> list[Path]:
    folder = Path(folder).resolve()
    out = []
    for p in sorted(folder.rglob("*")):
        rel = p.relative_to(folder)
        if any(part.startswith(".") or part == "outputs" for part in rel.parts[:-1]):
            continue
        if p.is_file() and p.suffix.lower() in IMG_EXTS and not p.name.startswith("."):
            out.append(p)
    return out


def load_image(path: Path):
    import numpy as np
    if Path(path).suffix.lower() in (".tif", ".tiff"):
        try:
            import tifffile
            arr = tifffile.imread(str(path))
        except Exception:
            from PIL import Image
            arr = np.array(Image.open(str(path)))
    else:
        from PIL import Image
        arr = np.array(Image.open(str(path)))
    if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[-1] not in (3, 4):
        arr = np.moveaxis(arr, 0, -1)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = arr[..., :3]
    if arr.dtype != np.uint8:
        # 16-bit brightfield: float on the 0..255 scale, not rounded — OD is a
        # ratio (I/I₀), so the scale is free but the precision is kept.
        top = np.iinfo(arr.dtype).max if arr.dtype.kind in "ui" else (float(arr.max()) or 1.0)
        arr = (arr.astype(np.float32) * (255.0 / top)).clip(0, 255)
    return np.ascontiguousarray(arr)
