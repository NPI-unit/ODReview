"""
Interaktive Ery-Überprüfung
============================
Starten:
    cd ~/Desktop/Erys
    python3 03_interactive_review.py

Steuerung:
  Linksklick auf Zelle   → Zelle deaktivieren (rot markiert)
  Linksklick nochmal     → Zelle wieder aktivieren
  Rechtsklick auf Stelle → Neue Zelle manuell hinzufügen (Kreis)
  Z                      → Letzten Schritt rückgängig
  S                      → Speichern (CSV + PNG)
  Q                      → Beenden (ohne Speichern)
"""

import matplotlib
matplotlib.use("MacOSX")          # für macOS; bei Fehler: "TkAgg"

from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import to_rgba
from cellpose import io
from skimage.color import label2rgb
from skimage.draw import disk

# ─────────────────────────────────────────────
# Einstellungen
# ─────────────────────────────────────────────
IMAGE_PATH   = Path("images/TRIBAL_195_Prä_19.11.25_020.tif")
MASKS_PATH   = Path("outputs/masks_filtered.npy")
STATS_PATH   = Path("outputs/cell_stats.csv")
OUTPUT_DIR   = Path("outputs")
NEW_CELL_RADIUS = 17      # Radius (px) beim manuellen Hinzufügen per Rechtsklick


# ─────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────
def load_image(path):
    img = io.imread(str(path))
    if img.ndim == 3 and img.shape[-1] == 4:
        img = img[..., :3]
    return img


def build_overlay(img, masks, active_ids):
    """Baut das Overlay-Array. Aktive Zellen = bunt, inaktive = halbtransparent grau."""
    display_masks = np.zeros_like(masks)
    for cid in active_ids:
        display_masks[masks == cid] = cid

    if img.ndim == 2:
        img_norm = (img - img.min()) / (img.max() - img.min() + 1e-8)
        overlay = label2rgb(display_masks, image=img_norm, bg_label=0, alpha=0.35)
    else:
        img_norm = img.astype(float)
        if img_norm.max() > 1:
            img_norm /= 255.0
        overlay = label2rgb(display_masks, image=img_norm, bg_label=0, alpha=0.35)

    return overlay, img_norm


def find_cell_at(x, y, masks):
    """Gibt cell_id an Position (x,y) zurück, oder None."""
    yi, xi = int(round(y)), int(round(x))
    if 0 <= yi < masks.shape[0] and 0 <= xi < masks.shape[1]:
        label = int(masks[yi, xi])
        return label if label > 0 else None
    return None


def add_circle_to_masks(masks, cx, cy, radius, new_id):
    """Zeichnet einen ausgefüllten Kreis in die Masken-Matrix."""
    rr, cc = disk((int(cy), int(cx)), radius, shape=masks.shape)
    masks[rr, cc] = new_id
    return masks


# ─────────────────────────────────────────────
# Haupt-App
# ─────────────────────────────────────────────
class EryReviewer:
    def __init__(self):
        # Daten laden
        self.img        = load_image(IMAGE_PATH)
        self.masks      = np.load(MASKS_PATH).astype(np.int32)
        self.stats      = pd.read_csv(STATS_PATH)

        # Zustand
        self.all_ids    = set(self.stats["cell_id"].tolist())
        self.active_ids = set(self.all_ids)   # alle starten als aktiv
        self.next_id    = int(self.masks.max()) + 1
        self.history    = []                  # für Undo

        # Figure aufbauen
        self.fig, self.ax = plt.subplots(figsize=(15, 10))
        self.fig.canvas.manager.set_window_title("Ery-Reviewer")
        plt.subplots_adjust(bottom=0.08)

        # Instruktionen unten
        self.fig.text(
            0.5, 0.01,
            "Linksklick: Zelle an/aus   |   Rechtsklick: Neue Zelle hinzufügen   |   Z: Rückgängig   |   S: Speichern   |   Q: Beenden",
            ha="center", fontsize=9, color="gray"
        )

        self.im_obj = None
        self._draw()

        # Events
        self.fig.canvas.mpl_connect("button_press_event", self._on_click)
        self.fig.canvas.mpl_connect("key_press_event",    self._on_key)

        plt.show()

    # ── Zeichnen ──────────────────────────────
    def _draw(self):
        self.ax.cla()
        overlay, img_norm = build_overlay(self.img, self.masks, self.active_ids)
        self.ax.imshow(overlay)

        # Inaktive Zellen grau schraffieren
        inactive = self.all_ids - self.active_ids
        for cid in inactive:
            row = self.stats[self.stats["cell_id"] == cid]
            if row.empty:
                continue
            cx, cy = float(row["centroid_x"]), float(row["centroid_y"])
            circle = mpatches.Circle(
                (cx, cy), radius=NEW_CELL_RADIUS,
                linewidth=1.5, edgecolor="red", facecolor="none", linestyle="--"
            )
            self.ax.add_patch(circle)

        # Nummern auf aktive Zellen
        for _, row in self.stats.iterrows():
            cid = int(row["cell_id"])
            col = "yellow" if cid in self.active_ids else "red"
            self.ax.text(
                row["centroid_x"], row["centroid_y"],
                str(cid), color=col, fontsize=7,
                ha="center", va="center", fontweight="bold"
            )

        n_active = len(self.active_ids)
        n_total  = len(self.all_ids)
        self.ax.set_title(
            f"{IMAGE_PATH.name}   —   ✓ {n_active} Erys aktiv   |   ✗ {n_total - n_active} deaktiviert   |   Gesamt erkannt: {n_total}",
            fontsize=11
        )
        self.ax.axis("off")
        self.fig.canvas.draw_idle()

    # ── Klick-Handler ─────────────────────────
    def _on_click(self, event):
        if event.inaxes != self.ax:
            return
        x, y = event.xdata, event.ydata

        if event.button == 1:
            # Linksklick: Toggle bestehende Zelle
            cid = find_cell_at(x, y, self.masks)
            if cid is not None and cid in self.all_ids:
                self.history.append(("toggle", cid, cid in self.active_ids))
                if cid in self.active_ids:
                    self.active_ids.discard(cid)
                    print(f"  Zelle {cid} deaktiviert  → aktiv: {len(self.active_ids)}")
                else:
                    self.active_ids.add(cid)
                    print(f"  Zelle {cid} aktiviert    → aktiv: {len(self.active_ids)}")
                self._draw()

        elif event.button == 3:
            # Rechtsklick: Neue Zelle hinzufügen
            new_id = self.next_id
            self.next_id += 1
            self.masks = add_circle_to_masks(self.masks, x, y, NEW_CELL_RADIUS, new_id)
            new_row = pd.DataFrame([{
                "cell_id":    new_id,
                "orig_label": -1,
                "area":       int(np.pi * NEW_CELL_RADIUS**2),
                "centroid_y": float(y),
                "centroid_x": float(x),
            }])
            self.stats = pd.concat([self.stats, new_row], ignore_index=True)
            self.all_ids.add(new_id)
            self.active_ids.add(new_id)
            self.history.append(("add", new_id))
            print(f"  Neue Zelle {new_id} hinzugefügt bei ({x:.0f}, {y:.0f})  → aktiv: {len(self.active_ids)}")
            self._draw()

    # ── Tastatur-Handler ──────────────────────
    def _on_key(self, event):
        if event.key == "z":
            self._undo()
        elif event.key == "s":
            self._save()
        elif event.key == "q":
            print("Beendet ohne Speichern.")
            plt.close(self.fig)

    def _undo(self):
        if not self.history:
            print("  Nichts zum Rückgängigmachen.")
            return
        action = self.history.pop()
        if action[0] == "toggle":
            _, cid, was_active = action
            if was_active:
                self.active_ids.add(cid)
            else:
                self.active_ids.discard(cid)
            print(f"  Rückgängig: Zelle {cid} → {'aktiv' if was_active else 'inaktiv'}")
        elif action[0] == "add":
            _, cid = action
            self.masks[self.masks == cid] = 0
            self.all_ids.discard(cid)
            self.active_ids.discard(cid)
            self.stats = self.stats[self.stats["cell_id"] != cid]
            self.next_id -= 1
            print(f"  Rückgängig: Zelle {cid} entfernt")
        self._draw()

    def _save(self):
        OUTPUT_DIR.mkdir(exist_ok=True)

        # Finale Stats (nur aktive)
        final_stats = self.stats[self.stats["cell_id"].isin(self.active_ids)].copy()
        final_stats.to_csv(OUTPUT_DIR / "cell_stats_reviewed.csv", index=False)

        # Finale Masken
        final_masks = self.masks.copy()
        inactive = self.all_ids - self.active_ids
        for cid in inactive:
            final_masks[final_masks == cid] = 0
        np.save(OUTPUT_DIR / "masks_reviewed.npy", final_masks)

        # PNG speichern
        overlay, img_norm = build_overlay(self.img, self.masks, self.active_ids)
        fig2, ax2 = plt.subplots(figsize=(14, 9))
        ax2.imshow(overlay)
        for _, row in final_stats.iterrows():
            ax2.text(
                row["centroid_x"], row["centroid_y"],
                str(int(row["cell_id"])), color="yellow",
                fontsize=7, ha="center", va="center", fontweight="bold"
            )
        ax2.set_title(f"{IMAGE_PATH.name}  —  {len(self.active_ids)} Erys (reviewed)", fontsize=12)
        ax2.axis("off")
        plt.tight_layout()
        png_path = OUTPUT_DIR / "overlay_reviewed.png"
        fig2.savefig(png_path, dpi=200, bbox_inches="tight")
        plt.close(fig2)

        print(f"\n  GESPEICHERT:")
        print(f"    {OUTPUT_DIR / 'cell_stats_reviewed.csv'}")
        print(f"    {OUTPUT_DIR / 'masks_reviewed.npy'}")
        print(f"    {png_path}")
        print(f"    Finale Zellzahl: {len(self.active_ids)}")


# ─────────────────────────────────────────────
if __name__ == "__main__":
    for p in [IMAGE_PATH, MASKS_PATH, STATS_PATH]:
        if not p.exists():
            raise FileNotFoundError(f"Datei nicht gefunden: {p}\nBitte zuerst 01_segment_count.py ausführen.")

    print("Ery-Reviewer startet ...")
    print(f"  Bild:   {IMAGE_PATH}")
    print(f"  Masken: {MASKS_PATH}")
    print()
    EryReviewer()
