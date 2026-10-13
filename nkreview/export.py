"""Study export into outputs/<time>/ next to the study's first folder:

  zellen.csv            one row per cell, long format for R
  zusammenfassung.csv   one row per proband × zeitpunkt × marker (kept cells)
  einstellungen.json    the study incl. every parameter + app version
  overlays/*.png        contours, I₀ fields and the OD per image
  nk_export.xlsx        the two tables as sheets
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
from .study import META_ROLES, ROLE_IMAGE_NUM, ImageInfo, Study


def overlay_png(img: np.ndarray, res: ImageResult, path: Path, field_size: int) -> None:
    from PIL import Image, ImageDraw
    from skimage.segmentation import find_boundaries
    rgb = img[..., :3].astype(np.uint8).copy()
    colors = {"gewertet": (33, 97, 217), "ausgeschlossen": (151, 94, 12), "verworfen": (150, 150, 150)}
    for r in res.rows:
        m = res.labels == r["zelle"]
        rgb[find_boundaries(m, mode="outer")] = colors[r["status"]]
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    half = field_size // 2
    for r in res.rows:
        cm = res.cells.get(r["zelle"])
        if cm and r["status"] != "verworfen":
            for x, y in cm.fields:
                d.rectangle([x - half, y - half, x + half, y + half], outline=(151, 94, 12), width=1)
        od = "–" if r["od_fiji"] is None else f'{r["od_fiji"]:.3f}'
        d.text((r["x"] + 30, r["y"] - 30), f'#{r["zelle"]} {od} {r["status"]}', fill=colors[r["status"]])
    im.save(path)


def summarize(cells: list[dict]) -> list[dict]:
    """Per proband × zeitpunkt × marker over the kept cells."""
    groups: dict = {}
    for c in cells:
        if c["status"] != STATUS_KEPT or c["od_fiji"] is None:
            continue
        k = (c["proband"], c["gruppe"], c["zeitpunkt"], c["zelltyp"], c["marker"])
        groups.setdefault(k, []).append(c)
    out = []
    for (prob, grp, tp, ct, mk), cs in sorted(groups.items()):
        od = np.array([c["od_fiji"] for c in cs], dtype=float)
        pos = [c["positiv"] for c in cs if c.get("positiv") is not None]
        out.append({
            "proband": prob, "gruppe": grp, "zeitpunkt": tp, "zelltyp": ct, "marker": mk,
            "n_bilder": len({c["bild"] for c in cs}), "n_zellen": len(cs),
            "od_fiji_mean": round(float(od.mean()), 4),
            "od_fiji_sd": round(float(od.std(ddof=1)), 4) if len(od) > 1 else None,
            "od_fiji_median": round(float(np.median(od)), 4),
            "n_positiv": sum(pos) if pos else None,
            "anteil_positiv": round(sum(pos) / len(pos), 4) if pos else None,
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
        if only_reviewed and not rv.reviewed:
            skipped += 1
            continue
        img = load_image(info.path)
        res = analyze(info.folder, info.path, img, study.settings, rv)
        if res.note:
            notes.append(f"{info.path.name}: {res.note}")
        rel = info.path.relative_to(info.folder).as_posix()
        meta = {r: info.meta.get(r, "") for r in META_ROLES if r != ROLE_IMAGE_NUM}
        for r in res.rows:
            if r["status"] != STATUS_KEPT and not with_excluded:
                continue
            cells.append({**meta, "bild_nr": info.meta.get(ROLE_IMAGE_NUM, ""), "bild": rel,
                          "ordner": str(info.folder), "geprueft": rv.reviewed, **r})
        stem = "__".join(info.path.relative_to(info.folder).with_suffix("").parts)
        overlay_png(img, res, out / "overlays" / f"{info.folder.name}__{stem}.png",
                    study.settings.i0_field_size)
    if progress:
        progress(len(images), len(images), "")
    df = pd.DataFrame(cells)
    summary = pd.DataFrame(summarize(cells))
    df.to_csv(out / "zellen.csv", index=False)
    summary.to_csv(out / "zusammenfassung.csv", index=False)
    try:
        with pd.ExcelWriter(out / "nk_export.xlsx") as xw:
            df.to_excel(xw, sheet_name="zellen", index=False)
            summary.to_excel(xw, sheet_name="zusammenfassung", index=False)
    except Exception as e:      # openpyxl missing: the CSVs are the record anyway
        notes.append(f"XLSX nicht geschrieben: {e}")
    info = study.to_dict()
    info.update({"export_zeit": _dt.datetime.now().isoformat(timespec="seconds"),
                 "app_version": __version__, "nur_gepruefte": only_reviewed,
                 "mit_ausgeschlossenen": with_excluded})
    (out / "einstellungen.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), "utf-8")
    kept = sum(c["status"] == STATUS_KEPT for c in cells)
    return {"folder": out, "n_cells": kept, "n_rows": len(cells), "n_images": len(images) - skipped,
            "skipped": skipped, "notes": notes}
