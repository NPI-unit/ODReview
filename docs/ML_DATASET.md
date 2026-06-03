# From ErysReview export to an ML training dataset

This note explains how the structured export maps onto a machine-learning
dataset for **erythrocyte subtype recognition**, and what to watch out for so
the data you collect now stays usable later. No ML code ships with the app yet —
the export is deliberately kept in a form that converts losslessly into a
training set when you are ready.

## What the export already gives you

For every reviewed image, `outputs/<Zeitpunkt>/<Zeitpunkt>_<Proband>/` contains:

| File | ML role |
|---|---|
| `<stem>.tif` | the raw image (input), copied losslessly |
| `<stem>_masks.npy` | an **instance segmentation mask** — each cell is a distinct integer label |
| `<stem>_stats.csv` | one **row per cell**: `cell_id` (= mask label), `type`, `type_name`, `label_source`, `image_in_evaluation`, `is_active`, `is_border`, `is_duplicate`, `is_manual`, `area`, `centroid_x/y` |
| `<stem>_overlay.png` | human QA only |
| `<Zeitpunkt>_<Proband>_summary.csv` | aggregate counts (not needed for training) |
| `manifest.json` | Cellpose version + parameters + profile snapshot (provenance) |

Joining the mask label to the `cell_id` column gives you, for each cell:
its pixels (from the mask), its class (`type`), and quality flags. That is
exactly an instance-segmentation-plus-classification dataset.

## The six things to get right

1. **Canonical labels.** `type` is the profile's internal key (e.g. `stechapfel`).
   Different studies may use different keys/names for the same biology. Before
   training across studies, define one **canonical vocabulary** and map each
   profile's keys onto it. Keep a `class_map.json` next to your dataset.

2. **Train only on active, clean cells.** Filter rows to
   `is_active == True and is_border == False and is_duplicate == False`. Border
   and duplicate cells are deliberately excluded from counts and make noisy
   training examples. `label_source == "reviewer"` marks cells a human explicitly
   typed; `"auto"` means the default (`Normal`) fallback — useful if you want to
   weight or audit labels.

3. **Class imbalance.** `Normal` dominates; subtypes are rare. The 2000-cell cap
   (`image_in_evaluation`) is great for counting but can starve rare subtypes.
   For ML collection, consider reviewing extra images **beyond** the cap for
   subtype-rich subjects, or oversampling/weighting rare classes at train time.

4. **Manual cells are circles.** Cells added by hand (`is_manual == True`) are
   painted into the mask as discs, so their contours are approximate. Keep them
   for classification crops, but exclude them when training a **segmentation**
   model (where boundary fidelity matters).

5. **Consolidate.** Per-image CSVs are awkward to train on. Concatenate them into
   one table (one row per cell) with extra columns for `subject`, `timepoint`,
   `image`, and a stable `cell_uid` (e.g. `f"{image}#{cell_id}"`). Parquet is a
   good format. Optionally also emit per-cell bounding-box crops (PNG) for a
   pure classifier, or a COCO-style JSON for instance models.

6. **Provenance & reproducibility.** `manifest.json` records the Cellpose
   version and parameters that produced the masks, plus the reviewer name in
   share packages. Keep it with the dataset so results are reproducible and you
   can detect when masks came from a different segmentation regime.

## Minimal consolidation sketch

```python
import json, pandas as pd, numpy as np
from pathlib import Path

rows = []
for stats_csv in Path("outputs").rglob("*_stats.csv"):
    df = pd.read_csv(stats_csv)
    df = df[df.is_active & ~df.is_border & ~df.is_duplicate]
    stem = stats_csv.name.replace("_stats.csv", "")
    df["image"] = stem
    df["cell_uid"] = stem + "#" + df["cell_id"].astype(str)
    rows.append(df)

dataset = pd.concat(rows, ignore_index=True)
# map study-specific keys -> canonical labels
class_map = json.loads(Path("class_map.json").read_text())
dataset["label"] = dataset["type"].map(class_map).fillna(dataset["type"])
dataset.to_parquet("erys_cells.parquet")
```

To extract per-cell crops, load `<stem>.tif` and `<stem>_masks.npy`, then for
each `cell_id` take the bounding box of `masks == cell_id` (pad a few pixels) and
save the image patch under `crops/<label>/<cell_uid>.png`.

## Recommended next step in the app (when you want it)

Add an **"ML-Datensatz exportieren"** action that performs the consolidation
above automatically: one `erys_cells.parquet` + `class_map.json` + optional
`crops/` tree. The current per-cell columns were chosen so this can be added
without re-reviewing anything.
