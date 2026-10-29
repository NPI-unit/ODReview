"""Study export into outputs/nkreview_<time>/ next to the study's first folder:

  cells.csv         one row per cell, long format for R
  summary.csv       one row per subject × group × timepoint × cell type × marker (kept cells
                    the reviewer confirmed; cells.csv lists unconfirmed ones with confirmed = False)
  settings.json     the study incl. every parameter + app version
  overlays/*.png    contours, I₀ fields and the OD per image
  nk_export.xlsx    the two tables as sheets
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Callable

import numpy as np

from . import __version__
from .analysis import STATUS_KEPT, ImageResult, analyze
from .state import Review, load_image
from .study import META_ROLES, ROLE_COLUMNS, ROLE_IMAGE_NUM, ImageInfo, Study, is_counted


def overlay_png(img: np.ndarray, res: ImageResult, path: Path, field_size: int) -> None:
    from PIL import Image, ImageDraw
    from skimage.segmentation import find_boundaries
    rgb = img[..., :3].astype(np.uint8).copy()
    colors = {"confirmed": (27, 122, 77), "kept": (255, 255, 255), "excluded": (199, 122, 18),
              "discarded": (150, 150, 150)}
    def col(r):
        return colors["confirmed" if r.get("confirmed") else r["status"]]
    for r in res.rows:
        m = res.labels == r["cell"]
        rgb[find_boundaries(m, mode="outer")] = col(r)
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    half = field_size // 2
    for r in res.rows:
        cm = res.cells.get(r["cell"])
        if cm and r["status"] != "discarded":
            for x, y in cm.fields:
                d.rectangle([x - half, y - half, x + half, y + half], outline=(151, 94, 12), width=1)
        od = "–" if r["od"] is None else f'{r["od"]:.3f}'
        state = "confirmed" if r.get("confirmed") else r["status"]
        d.text((r["x"] + 30, r["y"] - 30), f'#{r["cell"]} {od} {state}', fill=col(r))
    im.save(path)


def summarize(cells: list[dict]) -> list[dict]:
    """Per subject × group × timepoint × cell type × marker over the kept cells the
    reviewer confirmed – an unchecked cell never enters a mean."""
    groups: dict = {}
    for c in cells:
        if c["status"] != STATUS_KEPT or not c.get("confirmed") or c["od"] is None:
            continue
        k = (c["subject"], c["group"], c["timepoint"], c["cell_type"], c["marker"])
        groups.setdefault(k, []).append(c)
    out = []
    for (prob, grp, tp, ct, mk), cs in sorted(groups.items()):
        od = np.array([c["od"] for c in cs], dtype=float)
        pos = [c["positive"] for c in cs if c.get("positive") is not None]
        out.append({
            "subject": prob, "group": grp, "timepoint": tp, "cell_type": ct, "marker": mk,
            "n_images": len({c["image"] for c in cs}), "n_cells": len(cs),
            "od_mean": round(float(od.mean()), 4),
            "od_sd": round(float(od.std(ddof=1)), 4) if len(od) > 1 else None,
            "od_median": round(float(np.median(od)), 4),
            "n_positive": sum(pos) if pos else None,
            "share_positive": round(sum(pos) / len(pos), 4) if pos else None,
        })
    return out


def export_study(study: Study, only_reviewed: bool = True, with_excluded: bool = False,
                 progress: Callable[[int, int, str], None] | None = None,
                 images: list[ImageInfo] | None = None) -> dict:
    import pandas as pd
    images = images if images is not None else study.images()
    base = Path(study.folders[0]) if study.folders else Path.home()
    out = base / "outputs" / f"nkreview_{_dt.datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    cells, notes = [], []
    skipped = 0
    for i, info in enumerate(images):
        if progress:
            progress(i, len(images), info.path.name)
        rv = Review.load(info.folder, info.path)
        if (only_reviewed and not rv.reviewed) or not is_counted(info, rv, study.settings):
            skipped += 1                                    # ⊘ (E / objective default) never exported
            continue
        img = load_image(info.path)
        res = analyze(info.folder, info.path, img, study.settings, rv)
        if res.note:
            notes.append(f"{info.path.name}: {res.note}")
        rel = info.path.relative_to(info.folder).as_posix()
        meta = {ROLE_COLUMNS[r]: info.meta.get(r, "") for r in META_ROLES if r != ROLE_IMAGE_NUM}
        for r in res.rows:
            if r["status"] != STATUS_KEPT and not with_excluded:
                continue
            cells.append({**meta, "image_no": info.meta.get(ROLE_IMAGE_NUM, ""), "image": rel,
                          "folder": str(info.folder), "reviewed": rv.reviewed, **r})
        stem = "__".join(info.path.relative_to(info.folder).with_suffix("").parts)
        overlay_png(img, res, out / "overlays" / f"{info.folder.name}__{stem}.png",
                    study.settings.i0_field_size)
    if progress:
        progress(len(images), len(images), "")
    df = pd.DataFrame(cells)
    summary = pd.DataFrame(summarize(cells))
    df.to_csv(out / "cells.csv", index=False)
    summary.to_csv(out / "summary.csv", index=False)
    try:
        with pd.ExcelWriter(out / "nk_export.xlsx") as xw:
            df.to_excel(xw, sheet_name="cells", index=False)
            summary.to_excel(xw, sheet_name="summary", index=False)
    except Exception as e:      # openpyxl missing: the CSVs are the record anyway
        notes.append(f"XLSX not written: {e}")
    info = study.to_dict()
    info.update({"exported_at": _dt.datetime.now().isoformat(timespec="seconds"),
                 "app_version": __version__, "only_reviewed": only_reviewed,
                 "with_excluded": with_excluded})
    (out / "settings.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), "utf-8")
    kept = sum(c["status"] == STATUS_KEPT for c in cells)
    confirmed = sum(bool(c.get("confirmed")) for c in cells)
    return {"folder": out, "n_cells": kept, "n_confirmed": confirmed, "n_rows": len(cells),
            "n_images": len(images) - skipped,
            "skipped": skipped, "notes": notes}
