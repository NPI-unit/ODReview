from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.ndimage import gaussian_filter

from cellpose import io, models
from skimage.measure import regionprops
from skimage.color import label2rgb


# =========================
# Einstellungen
# =========================
IMAGE_PATH  = Path("images/TRIBAL_195_Prä_19.11.25_020.tif")   # <-- anpassen
OUTPUT_DIR  = Path("outputs")

USE_GPU     = True    # True für GPU (auch Apple M1/M2/M3)
DIAMETER    = 35      # grobe Zellgröße in Pixeln; ggf. 25-45 testen
MIN_AREA    = 80      # kleine Artefakte rausfiltern
MAX_AREA    = 5000    # zu große Klumpen rausfiltern
CELLPROB    = -2.0    # niedriger = mehr Zellen; höher = weniger Artefakte
FLOW_THRESH = 0.4     # Standard; bei zu vielen Fehlern auf 0.6 erhöhen
BLUR_SIGMA  = 3       # Gaussian Blur zum Füllen der Donut-Mitte (0 = aus)
SHOW_PLOT   = True


def load_image(path: Path):
    """Lädt das Originalbild (unverändert, für Overlay-Anzeige)."""
    img = io.imread(str(path))
    if img.ndim == 3 and img.shape[-1] == 4:
        img = img[..., :3]
    return img


def preprocess_for_cellpose(img):
    """
    Bereitet das Bild für Cellpose vor:
    1. Invertieren — Erys (bikonkav, dunkle Mitte) werden zu hellen Kreisen
    2. Gaussian Blur  — füllt die Donut-Mitte auf, damit Cellpose ganze Zellen sieht
    """
    inv = img.max() - img

    if BLUR_SIGMA > 0:
        if inv.ndim == 2:
            inv = gaussian_filter(inv.astype(float), sigma=BLUR_SIGMA)
        else:
            inv = np.stack(
                [gaussian_filter(inv[..., c].astype(float), sigma=BLUR_SIGMA)
                 for c in range(inv.shape[-1])], axis=-1
            )
        inv = inv.astype(img.dtype)

    return inv


def run_cellpose(img_processed, use_gpu=False, diameter=None):
    channel_axis = -1 if img_processed.ndim == 3 else None

    model = models.CellposeModel(gpu=use_gpu, model_type="cpsam")
    result = model.eval(
        img_processed,
        diameter=diameter,
        channel_axis=channel_axis,
        cellprob_threshold=CELLPROB,
        flow_threshold=FLOW_THRESH,
    )

    if not isinstance(result, tuple) or len(result) < 3:
        raise RuntimeError("Unerwartetes Rückgabeformat von Cellpose.")

    masks, flows, styles = result[:3]
    return masks, flows


def filter_masks_by_area(masks, min_area, max_area):
    """Filtert Masken nach Fläche und vergibt neue fortlaufende IDs."""
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

    stats = pd.DataFrame(rows)
    return filtered, stats


def save_overlay(img_original, masks, stats, out_png):
    """Overlay wird auf dem ORIGINAL-Bild (nicht invertiert) gezeichnet."""
    fig, ax = plt.subplots(figsize=(14, 9))

    if img_original.ndim == 2:
        img_norm = (img_original - img_original.min()) / (img_original.max() - img_original.min() + 1e-8)
        ax.imshow(img_norm, cmap="gray")
        overlay = label2rgb(masks, image=img_norm, bg_label=0, alpha=0.3)
    else:
        img_norm = img_original.astype(float)
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
    ax.set_title(f"{IMAGE_PATH.name}  —  {n} Erys erkannt", fontsize=13)
    ax.axis("off")
    plt.tight_layout()
    plt.savefig(out_png, dpi=200, bbox_inches="tight")

    if SHOW_PLOT:
        plt.show()
    else:
        plt.close()


def main():
    if not IMAGE_PATH.exists():
        raise FileNotFoundError(f"Bild nicht gefunden: {IMAGE_PATH}")

    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"Lese Bild ein: {IMAGE_PATH}")
    img_original  = load_image(IMAGE_PATH)
    img_processed = preprocess_for_cellpose(img_original)

    print(f"Preprocessing: invertiert + Gaussian Blur (sigma={BLUR_SIGMA})")
    print("Starte Cellpose ...")
    masks_raw, flows = run_cellpose(img_processed, use_gpu=USE_GPU, diameter=DIAMETER)

    raw_count = int(masks_raw.max())
    np.save(OUTPUT_DIR / "masks_raw.npy", masks_raw)
    print(f"Roh-Zellzahl (vor Filter): {raw_count}")

    masks_filtered, stats = filter_masks_by_area(
        masks_raw,
        min_area=MIN_AREA,
        max_area=MAX_AREA,
    )

    filtered_count = int(masks_filtered.max())
    np.save(OUTPUT_DIR / "masks_filtered.npy", masks_filtered)
    stats.to_csv(OUTPUT_DIR / "cell_stats.csv", index=False)

    # Overlay auf Originalbild (nicht invertiert)
    save_overlay(img_original, masks_filtered, stats, OUTPUT_DIR / "overlay_ids.png")

    print("\nFERTIG")
    print(f"Gefilterte Zellzahl: {filtered_count}")
    print(f"Gespeichert: {OUTPUT_DIR / 'overlay_ids.png'}")
    print(f"Gespeichert: {OUTPUT_DIR / 'cell_stats.csv'}")

    if filtered_count == 0:
        print("\nHinweis: Keine Zellen nach Filter übrig.")
        print("Dann bitte MIN_AREA / MAX_AREA anpassen oder DIAMETER / BLUR_SIGMA ändern.")


if __name__ == "__main__":
    main()
