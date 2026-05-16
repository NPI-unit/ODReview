"""
Nummerierung von Cellpose-GUI-Masken
=====================================
Workflow:
  1. In der Cellpose GUI segmentieren (CPSAM)
  2. File → Save masks  →  speichert als  images/<name>_seg.npy
  3. Dieses Skript starten:
       python3 04_number_gui_masks.py

Ergebnis:
  outputs/overlay_numbered.png   — Bild mit Zellnummern
  outputs/cell_stats_gui.csv     — Zellstatistik (ID, Fläche, Position)
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from cellpose import io
from skimage.measure import regionprops
from skimage.color import label2rgb

# ─────────────────────────────────────────────
# Einstellungen
# ─────────────────────────────────────────────
IMAGE_PATH  = Path("images/TRIBAL_195_Prä_19.11.25_005.tif")   # <-- anpassen
OUTPUT_DIR  = Path("outputs")

MIN_AREA    = 80      # Artefakte rausfiltern
MAX_AREA    = 5000    # Klumpen rausfiltern
SHOW_PLOT   = True
# ─────────────────────────────────────────────


def load_image(path):
    img = io.imread(str(path))
    if img.ndim == 3 and img.shape[-1] == 4:
        img = img[..., :3]
    return img


def load_gui_masks(image_path):
    """
    Cellpose GUI speichert Masken als  <bildname>_seg.npy  im selben Ordner.
    Die .npy-Datei enthält ein Dict mit dem Key 'masks'.
    """
    seg_path = image_path.parent / (image_path.stem + "_seg.npy")
    if not seg_path.exists():
        raise FileNotFoundError(
            f"Keine GUI-Masken gefunden: {seg_path}\n"
            "Bitte in der Cellpose GUI segmentieren und mit File → Save masks speichern."
        )
    data = np.load(seg_path, allow_pickle=True).item()
    masks = data["masks"]
    print(f"GUI-Masken geladen: {seg_path}  ({int(masks.max())} Zellen roh)")
    return masks.astype(np.int32)


def filter_masks(masks, min_area, max_area):
    filtered = np.zeros_like(masks, dtype=np.int32)
    rows = []
    new_id = 1
    for prop in regionprops(masks):
        area = int(prop.area)
        if min_area <= area <= max_area:
            filtered[masks == prop.label] = new_id
            cy, cx = prop.centroid
            rows.append({
                "cell_id":    new_id,
                "orig_label": int(prop.label),
                "area":       area,
                "centroid_y": float(cy),
                "centroid_x": float(cx),
            })
            new_id += 1
    return filtered, pd.DataFrame(rows)


def save_overlay(img, masks, stats, out_png):
    fig, ax = plt.subplots(figsize=(14, 9))

    if img.ndim == 2:
        img_norm = (img - img.min()) / (img.max() - img.min() + 1e-8)
        ax.imshow(img_norm, cmap="gray")
        overlay = label2rgb(masks, image=img_norm, bg_label=0, alpha=0.3)
    else:
        img_norm = img.astype(float) / (img.max() + 1e-8)
        ax.imshow(img_norm)
        overlay = label2rgb(masks, image=img_norm, bg_label=0, alpha=0.3)

    ax.imshow(overlay)

    for _, row in stats.iterrows():
        ax.text(
            row["centroid_x"], row["centroid_y"],
            str(int(row["cell_id"])),
            color="yellow", fontsize=7,
            ha="center", va="center", fontweight="bold",
        )

    ax.set_title(
        f"{IMAGE_PATH.name}  —  {len(stats)} Erys  (Cellpose GUI / CPSAM)",
        fontsize=12
    )
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")
    print(f"Overlay gespeichert: {out_png}")
    if SHOW_PLOT:
        plt.show()
    else:
        plt.close()


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Lade Bild: {IMAGE_PATH}")
    img = load_image(IMAGE_PATH)

    masks_raw = load_gui_masks(IMAGE_PATH)
    masks_filtered, stats = filter_masks(masks_raw, MIN_AREA, MAX_AREA)

    np.save(OUTPUT_DIR / "masks_filtered.npy", masks_filtered)
    stats.to_csv(OUTPUT_DIR / "cell_stats.csv", index=False)

    save_overlay(img, masks_filtered, stats, OUTPUT_DIR / "overlay_numbered.png")

    print(f"\nFERTIG — {len(stats)} Erys erkannt")
    print(f"  outputs/overlay_numbered.png")
    print(f"  outputs/cell_stats.csv")
    print()
    print("Tipp: Danach 03_interactive_review.py starten für manuelle Korrekturen.")


if __name__ == "__main__":
    main()
