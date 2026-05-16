# EryCountUI

Single-threaded Tk + matplotlib + Cellpose application for reviewing erythrocyte segmentations on light-microscopy images.

## Layout

```
.
├── erys_v10/                  current app (v11.1 → v12 UI overhaul)
│   ├── __main__.py            entry point: python -m erys_v10
│   ├── app.py                 main window, sidebar, toolbar, status bar
│   ├── canvas.py              matplotlib canvas + click/scroll/drag
│   ├── cellpose_runner.py     lazy per-image Cellpose with cache
│   ├── dialogs.py             profile manager, profile editor, settings, batch
│   ├── duplicates.py          centroid-distance duplicate detection
│   ├── export.py              structured outputs/<Zeitpunkt>/<Zeitpunkt>_<Proband>/
│   ├── grouping.py            regex-based filename grouping
│   ├── i18n.py                DE/EN strings
│   ├── persistence.py         AppSettings + FolderState + ReviewState
│   ├── profile.py             Study profiles (TRIBAL default)
│   ├── summary.py             per-Proband-Zeitpunkt summary CSV
│   └── theme.py               dark/light palettes
└── 03_interactive_review.py   legacy single-image reviewer (kept for reference)
```

Old `ery_app*.py` and `erys/` are pre-v10 sketches retained for history.

## Run

```bash
.venv312/bin/python -m erys_v10
```

Requirements (versioned via repo `requirements.txt`):
- Python 3.12
- cellpose 4.1.1
- torch 2.11 (MPS or CUDA for GPU)
- matplotlib 3.10
- scikit-image, pandas, numpy

## Profile system

Each study lives as a JSON profile in `~/Library/Application Support/Erys/profiles/`.
A profile defines: filename regex (Zeitpunkt + Proband + Bildnummer), cell types,
Cellpose parameters, target cell count, duplicate detection rules, and margin width.

## Cache layout

Per-folder, next to the images:

```
<study_folder>/
  .erys_cache/
    <stem>_masks.npy        cached Cellpose mask
    <stem>_masks.json       parameters that produced this mask
    <stem>_review.json      per-image user edits (auto-saved on every click)
    folder_state.json       last opened image index
  outputs/                  structured export (created on Export)
    <Zeitpunkt>/
      <Zeitpunkt>_<Proband>/
        *.tif                   original images copied
        *_stats.csv             per-image cell flags
        *_masks.npy             final masks (with manual additions)
        *_overlay.png           rendered overlay
        <Zeitpunkt>_<Proband>_summary.csv
    all_stats.csv (when applicable)
    summary.csv
    manifest.json
```
