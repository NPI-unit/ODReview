"""outputs/: one CSV/XLSX row per cell, the settings used, and overlay PNGs."""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

import numpy as np

from .analysis import ImageResult, analyze
from .state import Review, Settings, list_images, load_image


def overlay_png(img: np.ndarray, res: ImageResult, path: Path) -> None:
    from PIL import Image, ImageDraw
    from skimage.segmentation import find_boundaries
    rgb = img[..., :3].astype(np.uint8).copy()
    discarded = {r["zelle"] for r in res.rows if r["verworfen"]}
    keep = res.labels.copy()
    keep[np.isin(keep, list(discarded))] = 0
    rgb[find_boundaries(keep, mode="outer")] = (0, 200, 90)
    gone = np.where(np.isin(res.labels, list(discarded)), res.labels, 0)
    rgb[find_boundaries(gone, mode="outer")] = (150, 150, 150)
    im = Image.fromarray(rgb)
    d = ImageDraw.Draw(im)
    for r in res.rows:
        if not r["verworfen"]:
            d.text((r["x"] + 30, r["y"] - 30), f'#{r["zelle"]}  DAB {r["dab_od_mean"]:.3f}', fill=(0, 90, 40))
    im.save(path)


def export_folder(folder: Path, progress: Callable[[int, int, str], None] | None = None) -> dict:
    import pandas as pd
    folder = Path(folder)
    s = Settings.load(folder)
    out = folder / "outputs"
    (out / "overlays").mkdir(parents=True, exist_ok=True)
    rows, notes = [], []
    images = list_images(folder)
    for i, p in enumerate(images):
        if progress:
            progress(i, len(images), p.name)
        rv = Review.load(folder, p)
        img = load_image(p)
        res = analyze(folder, p, img, s, rv)
        if res.note:
            notes.append(f"{p.name}: {res.note}")
        rel = p.relative_to(folder).as_posix()
        for r in res.rows:
            rows.append({"bild": rel, "geprueft": rv.reviewed, **r})
        overlay_png(img, res, out / "overlays" / f"{'__'.join(p.relative_to(folder).with_suffix('').parts)}.png")
    df = pd.DataFrame(rows)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    csv = out / f"nk_messungen_{stamp}.csv"
    df.to_csv(csv, index=False)
    try:
        df.to_excel(csv.with_suffix(".xlsx"), index=False)
    except Exception as e:      # openpyxl missing: the CSV is the record anyway
        notes.append(f"XLSX nicht geschrieben: {e}")
    (out / f"nk_einstellungen_{stamp}.json").write_text(
        json.dumps(asdict(s), indent=2, ensure_ascii=False), "utf-8")
    kept = df[~df["verworfen"]] if len(df) else df
    return {"csv": csv, "n_cells": int(len(kept)), "n_images": len(images), "notes": notes}
