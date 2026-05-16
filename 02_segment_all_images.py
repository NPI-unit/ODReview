"""
Erythrozyten-Zählung mit Cellpose — alle Bilder im images/-Ordner
==================================================================
Ausführen:
    cd /Users/finndreisigacker/Desktop/Erys
    python3 02_segment_all_images.py

Ergebnisse landen im Ordner outputs/
"""

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")          # kein GUI nötig
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.colors import ListedColormap
import random

from cellpose import io, models
from skimage.measure import regionprops
from skimage.color import label2rgb

# ============================================================
# Einstellungen — bei Bedarf anpassen
# ============================================================
IMAGE_DIR   = Path("images")
OUTPUT_DIR  = Path("outputs")
IMAGE_GLOB  = "*.tif"          # auch "*.tiff", "*.png" möglich

USE_GPU     = False            # True wenn CUDA-GPU vorhanden
DIAMETER    = 30               # grobe Zellgröße in Pixeln (25–45 testen)
MIN_AREA    = 80               # kleinste erlaubte Zelle (px²)
MAX_AREA    = 5000             # größte erlaubte Zelle (px²)
FLOW_THRESH = 0.4              # Cellpose flow-threshold (0.4 ist Default)
CELLPROB    = 0.0              # Cellpose cellprob-threshold
# ============================================================


def load_image(path: Path):
    img = io.imread(str(path))
    if img.ndim == 3 and img.shape[-1] == 4:
        img = img[..., :3]    # Alpha-Kanal entfernen
    return img


def run_cellpose(img, model, diameter):
    channel_axis = -1 if img.ndim == 3 else None
    result = model.eval(
        img,
        diameter=diameter,
        channel_axis=channel_axis,
        flow_threshold=FLOW_THRESH,
        cellprob_threshold=CELLPROB,
    )
    if not isinstance(result, tuple) or len(result) < 3:
        raise RuntimeError("Unerwartetes Rückgabeformat von Cellpose.")
    masks, flows, styles = result[:3]
    return masks


def filter_masks(masks):
    filtered = np.zeros_like(masks, dtype=np.int32)
    rows = []
    new_id = 1
    for prop in regionprops(masks):
        area = int(prop.area)
        if MIN_AREA <= area <= MAX_AREA:
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
    stats = pd.DataFrame(rows)
    return filtered, stats


def save_overlay(img, masks, stats, out_png, title=""):
    fig, ax = plt.subplots(figsize=(10, 10))

    if img.ndim == 2:
        img_norm = (img - img.min()) / (img.max() - img.min() + 1e-8)
        ax.imshow(img_norm, cmap="gray")
        overlay = label2rgb(masks, image=img_norm, bg_label=0, alpha=0.3)
    else:
        img_norm = img.astype(float)
        if img_norm.max() > 1:
            img_norm /= 255.0
        ax.imshow(img_norm)
        overlay = label2rgb(masks, image=img_norm, bg_label=0, alpha=0.3)

    ax.imshow(overlay)

    for _, row in stats.iterrows():
        ax.text(
            row["centroid_x"], row["centroid_y"],
            str(int(row["cell_id"])),
            color="yellow", fontsize=7,
            ha="center", va="center",
            fontweight="bold",
        )

    n = int(masks.max())
    ax.set_title(f"{title}  —  {n} Erys", fontsize=14, pad=8)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(out_png, dpi=180, bbox_inches="tight")
    plt.close()


def build_summary_grid(image_files, counts, summary_png):
    """Übersichtsgrafik: alle Overlay-PNGs in einem Raster + Balkendiagramm."""
    n = len(image_files)
    ncols = 4
    nrows = (n + ncols - 1) // ncols

    fig = plt.figure(figsize=(ncols * 5, nrows * 5 + 4))
    gs  = gridspec.GridSpec(nrows + 1, ncols, figure=fig,
                            height_ratios=[1]*nrows + [1.2])

    for idx, img_path in enumerate(image_files):
        row, col = divmod(idx, ncols)
        ax = fig.add_subplot(gs[row, col])
        overlay_file = OUTPUT_DIR / f"overlay_{img_path.stem}.png"
        if overlay_file.exists():
            thumb = plt.imread(str(overlay_file))
            ax.imshow(thumb)
        ax.set_title(f"{img_path.stem}\n{counts[idx]} Erys",
                     fontsize=9, pad=4)
        ax.axis("off")

    # Balkendiagramm
    ax_bar = fig.add_subplot(gs[nrows, :])
    labels = [p.stem for p in image_files]
    colors = plt.cm.tab20.colors
    bars = ax_bar.bar(labels, counts,
                      color=[colors[i % 20] for i in range(n)],
                      edgecolor="black", linewidth=0.5)
    ax_bar.bar_label(bars, fmt="%d", padding=3, fontsize=9)
    ax_bar.set_ylabel("Anzahl Erythrozyten")
    ax_bar.set_title("Erythrozyten-Anzahl pro Bild", fontsize=12)
    ax_bar.tick_params(axis="x", rotation=45)
    ax_bar.set_ylim(0, max(counts) * 1.15 if counts else 10)

    plt.suptitle("Erythrozyten-Auswertung — Gesamtübersicht", fontsize=14,
                 fontweight="bold", y=1.01)
    plt.tight_layout()
    plt.savefig(summary_png, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"Übersichtsgrafik gespeichert: {summary_png}")


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    image_files = sorted(IMAGE_DIR.glob(IMAGE_GLOB))
    if not image_files:
        raise FileNotFoundError(f"Keine Bilder in {IMAGE_DIR} mit Muster '{IMAGE_GLOB}'")

    print(f"Gefundene Bilder: {len(image_files)}")
    print("Lade Cellpose-Modell …")
    model = models.CellposeModel(gpu=USE_GPU)

    all_stats = []
    counts    = []

    for i, img_path in enumerate(image_files, 1):
        print(f"\n[{i}/{len(image_files)}] {img_path.name}")

        img       = load_image(img_path)
        masks_raw = run_cellpose(img, model, DIAMETER)

        raw_count = int(masks_raw.max())
        print(f"  Roh-Zellen (vor Filter): {raw_count}")

        masks_filt, stats = filter_masks(masks_raw)
        filt_count = int(masks_filt.max())
        print(f"  Gefilterte Zellen:       {filt_count}")

        # Masken speichern
        np.save(OUTPUT_DIR / f"masks_{img_path.stem}.npy", masks_filt)

        # Statistik mit Bildname ergänzen
        stats.insert(0, "image", img_path.name)
        all_stats.append(stats)

        # Overlay-PNG
        save_overlay(
            img, masks_filt, stats,
            OUTPUT_DIR / f"overlay_{img_path.stem}.png",
            title=img_path.stem,
        )
        counts.append(filt_count)

    # Gesamt-CSV
    all_df = pd.concat(all_stats, ignore_index=True) if all_stats else pd.DataFrame()
    csv_path = OUTPUT_DIR / "cell_stats_all.csv"
    all_df.to_csv(csv_path, index=False)

    # Zusammenfassungs-CSV
    summary_df = pd.DataFrame({
        "image":      [p.name for p in image_files],
        "ery_count":  counts,
    })
    summary_df.to_csv(OUTPUT_DIR / "summary_counts.csv", index=False)

    # Übersichtsgrafik
    build_summary_grid(image_files, counts, OUTPUT_DIR / "summary_overview.png")

    print("\n" + "="*50)
    print("FERTIG")
    print("="*50)
    for p, c in zip(image_files, counts):
        print(f"  {p.name:<35} → {c:>4} Erys")
    print(f"\n  Gesamt: {sum(counts)} Erys in {len(image_files)} Bildern")
    print(f"\nDateien in {OUTPUT_DIR}:")
    print("  overlay_<bild>.png     — Einzelbild mit nummerierten Zellen")
    print("  summary_overview.png   — Rasterübersicht aller Bilder")
    print("  cell_stats_all.csv     — Alle Zellen mit Fläche & Koordinaten")
    print("  summary_counts.csv     — Zählübersicht pro Bild")


if __name__ == "__main__":
    main()
