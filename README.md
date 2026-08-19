# ErysReview

A desktop reviewer for **automated erythrocyte (red-blood-cell) segmentation** of light-microscopy images, built on top of [Cellpose](https://github.com/MouseLand/cellpose). Loads a folder of TIFs, runs Cellpose lazily per image, lets you visually correct each segmentation (add / remove / re-classify cells), and exports a clean, lab-friendly summary CSV per subject and timepoint.

Designed for hematology / parasitology studies where you need to classify erythrocytes across many images.

---

## Easiest install (double-click)

If you don't want to touch a terminal:

1. One-time prerequisites:
   - **Windows**: nothing to install first — `install.bat` will offer to download and install **Miniforge** for you, then build the environment from conda-forge.
   - **macOS**: install Homebrew Python + Tk once — `brew install python@3.12 python-tk@3.12` (see <https://brew.sh>). `install.command` builds a local venv from it (deliberately *not* conda — that avoids a Tk crash on macOS 26).
2. **Download or clone** this repo to a folder of your choice (e.g. your Desktop).
3. Double-click the installer for your OS:
   - **macOS**: `install.command`
   - **Windows**: `install.bat`
4. Wait until you see `Done. ErysReview is installed.` (~5–10 min on first run).
5. Launch from the new **ErysReview** on your Desktop — a real `.app` bundle on macOS and a shortcut on Windows, both showing the proper icon and name in the Dock / taskbar. Or double-click `run.command` / `run.bat` inside the repo folder.

If anything in those steps fails, scroll down to **Manual install** for the explicit conda commands.

> **NVIDIA GPU users (Windows):** `install.bat` now detects an NVIDIA GPU and automatically installs the CUDA build of PyTorch (a one-time ~2.5 GB download), so Cellpose runs on the GPU — seconds per image instead of minutes on the CPU. No manual step needed.

> Note: this project is unaffiliated with the Cellpose authors. It is a UI wrapper that calls the Cellpose Python package locally on your machine. All segmentation credit goes to the Cellpose project.

---

## Why this exists

Cellpose 4 segments erythrocytes reliably, but a real lab workflow needs more:

- **Curate** every cell as active / not-counted / typed
- **Aggregate** counts across many images into one summary per subject-timepoint
- **Track progress** toward a fixed target (e.g. 2000 cells)
- **Persist** every click, so you can leave and come back days later
- **Configure** all of this per study without touching code

ErysReview gives you exactly that, in a single Tk window.

---

## Highlights

- **Profiles per study** — regex-based filename grouping into `Zeitpunkt → Proband → Bild`, configurable cell types, Cellpose parameters, target cell count, duplicate-detection rules.
- **Lazy Cellpose** with on-disk cache and parameter manifest. Open 200 images, only the ones you actually look at trigger compute.
- **Hierarchical sidebar** with collapsible subject groups and an always-visible counter `n/target Erys` per group.
- **Cockpit detail panel** that shows live per-image or per-subject statistics — % per cell type, % verändert, % toward target.
- **3-tool palette**: `Neue Zelle` (add manual cell), `Zählen ein/aus` (toggle active state, also re-include border/duplicate cells), `Verändert` (assign type from dropdown).
- **Automatic duplicate detection** (centroid distance) with configurable threshold + policy.
- **Margin / border filter** so partial cells at image edges don't pollute counts. Configurable per profile.
- **Auto-save** on every click into per-image JSON sidecars. No `Save` button.
- **Structured export** to `outputs/<Zeitpunkt>/<Zeitpunkt>_<Proband>/` with original TIFs copied alongside masks, overlay PNGs, per-image stats CSV, and a per-subject summary CSV in a 4-block layout matching the standard lab spreadsheet format.
- **Overview workbook** — on export, `outputs/Uebersicht.xlsx` aggregates every group: a tidy `Daten` sheet (one row per `Zeitpunkt × Condition × Proband` with counts, percentages, `% verändert`, `% Target`) plus pivot matrices (`% verändert` and one per cell type, `Proband × Zeitpunkt`) and an `RBC_nBilder` control sheet. Toggle per profile; a small preview pops up after export.
- **Batch with hierarchical filtering** — *Mehrere Bilder* mirrors the sidebar: a `Zeitpunkt → Proband → Bild` tree plus `Zeitpunkt`/`Proband` dropdown filters, so you can target a single subject or timepoint. Quick-selects (all / unsegmented / unfinished / invert) and *Erste N pro Proband* stack as intersections; *Bis Ziel rechnen* computes each group in image order until its active count reaches the profile target; *wachhalten* prevents sleep during long runs (`caffeinate` on macOS, `SetThreadExecutionState` on Windows).
- **Recursive image loading** — images in sub-folders are read too; the `outputs/` export folder and `.erys_cache/` are skipped and duplicate filenames are ignored.
- **Two themes** — Lab Neutral (light) and Soft Dark (dark) — sharing identical semantic color keys, both targeted at scientific work.
- **DE / EN** strings.
- **Share packages** — bundle a study (precomputed `.npy` masks + reviews, originals optional) into one ZIP via `Datei → Paket erstellen …`. Creation shows a `Zeitpunkt → Proband → Bild` checklist so you pick exactly which segmented images go in (group rows toggle all their children; All/None; default all). A teammate runs `Datei → Paket importieren …` and reviews **without ever running Cellpose** — ideal when their machine is too weak to segment. Imported images are always set to *bearbeitet* (never *fertig*); per-image conflicts with local reviews are resolved interactively (import / keep / all).
- **Editable 2000-cell cap** — each subject×timepoint group auto-selects images (in order) until the running active count just exceeds the target; press `e` to swap any single image in/out/auto. Skipped images are marked `⊘` in the sidebar and drop out of the summary CSV.
- **Friendly filename parser** — instead of a regex, tick delimiters (`_`, space, `-`, `.`) and assign each token a role (Proband / Zeitpunkt / Bildnummer / ignorieren) with a live preview over your real files. Regex stays available as an advanced fallback.
- **In-app statistics** — `Auswertung → Statistik …` shows box-plots (by timepoint / by subject) and a descriptive table for `% verändert`, `% verändert pro 2000`, per-subtype %, and absolute counts.
- **Progress dashboard & time tracking** — `Auswertung → Übersicht …` lists each group's `n/Ziel`, whether the target is reached, and the review time spent per subject and in total.
- **No auto-render & instant navigation** — opening a folder or pressing `←`/`→` never blocks: unsegmented images show the raw picture immediately; you segment on demand with the Cellpose button / `r` (bulk via *Mehrere Bilder*). Per-image counts are cached on disk (mtime-invalidated) so the sidebar no longer reloads masks. Editing the profile (regex/parser) re-groups live; `F5` re-scans the folder.
- **Responsive segmentation** — Cellpose runs on a worker thread, so the window keeps animating and the batch *Abbrechen* stays clickable even during slow CPU-only inference (e.g. Windows without a GPU); it never paints "Not Responding".

> Building a downstream subtype classifier? See [`docs/ML_DATASET.md`](docs/ML_DATASET.md) — the per-cell `*_stats.csv` already carries canonical type keys, a `label_source` (reviewer vs. auto) flag and an `image_in_evaluation` flag so the export converts losslessly into a training set.

---

## Screenshots

> _placeholder — drop your screenshots into `docs/screenshots/` and link them here_

---

## Manual install (if the double-click installer fails)

ErysReview is a Python application. If you have never used Python before, follow the section that matches your operating system.

The two platforms install **differently on purpose**: macOS uses a Homebrew-Python virtual environment (conda's Tk 8.6.13 crashes on macOS 26 "Tahoe" in modal dialogs, Homebrew's Tk 9 is safe), while Windows uses a **conda-forge** environment (a single OpenMP runtime — mixing conda's MKL numpy with a pip-installed torch deadlocks Cellpose). Either way Cellpose's PyTorch stays isolated from your system Python.

### macOS

macOS uses a Homebrew-Python **venv**, *not* conda — see the note above.

1. **Install Homebrew** (<https://brew.sh>) if you don't have it, then install Python 3.12 + its Tk:
   ```bash
   brew install python@3.12 python-tk@3.12
   ```

2. **Clone this repo**:
   ```bash
   git clone https://github.com/finn-dreisigacker/ErysReview.git
   cd ErysReview
   ```

3. **Create the venv** and install dependencies via `requirements.txt`:
   ```bash
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   On Apple Silicon, `pip install` pulls a PyTorch build with MPS (GPU) support automatically. Segmentation will run on the Mac GPU.

4. **Run**:
   ```bash
   python -m erysreview
   ```

5. (Optional) **Double-click launcher**: `install.command` builds an `ErysReview.app` on your Desktop (proper Dock icon + name). To make one by hand, create `run_erys.command` in the repo root with:
   ```bash
   #!/bin/bash
   cd "$(dirname "$0")"
   exec "$(pwd)/.venv/bin/python" -m erysreview
   ```
   `chmod +x run_erys.command` once in Terminal, then you can double-click it from Finder.

### Windows

> ⚠️ **Windows: do not `pip install` the dependencies.** Pip-installing torch on top of conda's MKL numpy loads `libiomp5md.dll` twice and **deadlocks Cellpose on the first run** (OMP Error #15 — the app appears frozen forever). Install the whole stack from **conda-forge** instead, as below. The easiest path is just to double-click `install.bat`, which does exactly this.

1. **Install Miniforge**: download `Miniforge3-Windows-x86_64.exe` from <https://github.com/conda-forge/miniforge#download> and run it. Accept defaults. (Or skip this — `install.bat` offers to do it for you.)

2. **Clone this repo** (you need [Git for Windows](https://git-scm.com/download/win) installed first):
   ```cmd
   git clone https://github.com/finn-dreisigacker/ErysReview.git
   cd ErysReview
   ```

3. From the Start menu, open **Miniforge Prompt**, then build the env entirely from conda-forge (one OpenMP runtime — no pip for the scientific stack):
   ```cmd
   conda create -n erys -c conda-forge python=3.12 "cellpose>=4.1.1,<5" pytorch numpy scipy scikit-image matplotlib-base pandas pillow tifffile openpyxl -y
   conda activate erys
   ```
   _(Upgrading from an older pip-based install? Remove the stale env first — `conda env remove -n erys -y` — so the duplicate-OpenMP problem doesn't carry over, then run the create line above.)_

   The default solve installs the **CPU** PyTorch build. For an NVIDIA GPU, request the CUDA build from conda-forge instead (e.g. add a `pytorch=*=*cuda*` spec or install `pytorch-gpu`) — **do not** `pip install` a CUDA torch into this env.

4. **Run**:
   ```cmd
   python -m erysreview
   ```

5. (Optional) Create a `run_erys.bat` next to the repo root:
   ```bat
   @echo off
   call "%USERPROFILE%\miniforge3\Scripts\activate.bat" erys
   python -m erysreview
   pause
   ```
   Double-click it to launch the app.

### Verifying the install

Inside the environment, run:
```bash
python -c "import cellpose, torch; print(cellpose.version, torch.__version__, torch.backends.mps.is_available() or torch.cuda.is_available())"
```
You should see versions printed. The last value is `True` when a GPU is available (Mac MPS or NVIDIA CUDA) and `False` on a CPU-only Windows box — that's expected; segmentation still works, just on CPU (and the window stays responsive while it runs).

---

## Quickstart

1. Launch the app (`python -m erysreview`).
2. **First start** opens the **Profile Manager**. Click **Default laden** — a TRIBAL-style profile is created with sensible defaults.
3. Menu → **Profil → Aktives Profil bearbeiten …** and set:
   - **Bilder-Ordner** to your folder of TIFs
   - **Filename-Regex** if your filenames differ from `STUDY_SUBJECT_TIMEPOINT_DATE_NUM.tif`
   - **Typen** (delete, add, reorder — `Normal` is protected)
   - **Ziel-Zellzahl** (default 2000)
   - **Cellpose-Parameter** (diameter, flow, cellprob, GPU on/off)
4. Click **Speichern**. The sidebar populates as `Zeitpunkt → Proband → Bild`.
5. Click any image — Cellpose runs (modal spinner shows progress on first load, instant from cache afterwards).
6. **Tools** in the toolbar (or press `1`, `2`, `3`):
   - `1 Neue Zelle` — click empty space to add a manual cell
   - `2 Zählen ein/aus` — click an active cell to remove it from the count, click a gray (border/duplicate) cell to manually include it
   - `3 Verändert` — pick a type from the dropdown and click cells to label them
7. Press `f` to mark the current image as **fertig** (green ●).
8. Click `↓ Exportieren` (or `Cmd+S`) — writes `outputs/<Zeitpunkt>/<Zeitpunkt>_<Proband>/…` with everything.

---

## Hotkeys

| Key | Action |
|---|---|
| `1` / `2` / `3` | switch tool |
| `Linksklick` | tool action (add / toggle count / assign type) |
| `Mausrad` | zoom on cursor |
| `Mittel-Drag` | pan |
| `Home` / `0` | reset zoom |
| `←` / `→` | previous / next image |
| `f` | mark image as finished |
| `e` | toggle current image in / out / auto for the 2000-cell evaluation |
| `o` | overlay on/off (also toggles margin overlay) |
| `r` | segment / re-run Cellpose on current image (no auto-render on load) |
| `F5` / `Cmd+R` | refresh — re-scan folder + re-group (also after editing the profile) |
| `z` | undo |
| `s` / `Cmd+S` / `Ctrl+S` | export |
| `q` | quit |

---

## Folder layout

```
<study_folder>/
  *.tif                       your microscope images (sub-folders are scanned too)
  .erys_cache/                created automatically
    <stem>_masks.npy          cached Cellpose mask
    <stem>_masks.json         which parameters produced this mask
    <stem>_review.json        per-image user edits (auto-saved on every click)
    folder_state.json         last opened image index
  outputs/                    created when you export
    <Zeitpunkt>/
      <Zeitpunkt>_<Proband>/
        *.tif                                copies of the originals
        *_stats.csv                          per-cell flags + types
        *_masks.npy                          final mask (with manual additions)
        *_overlay.png                        rendered overlay
        <Zeitpunkt>_<Proband>_summary.csv    4-block aggregated CSV
    Uebersicht.xlsx           cross-group overview (tidy sheet + pivot matrices)
    manifest.json             profile snapshot + app version + timestamp
```

App-wide preferences live in `~/Library/Application Support/Erys/settings.json` (macOS) or `%APPDATA%/Erys/settings.json` (Windows). Profiles live next to it in `profiles/<slug>.json`.

---

## CSV format

The per-subject `<Zeitpunkt>_<Proband>_summary.csv` is a 4-block layout designed for direct copy-paste into Excel:

```
Block 1 — counts per image
  Bildnummer; RBC; <Type1>; <Type2>; …; Normal

Block 2 — percent per image (immediately right of Block 1)
  %<Type1>; %<Type2>; …; %Normal

Block 3 — Summe row at bottom
  total counts | total percentages | %verändert | %verändert per 2000

Block 4 — % target reached
  total_RBC / target × 100
```

`Normal` is computed as the residual: `RBC − sum(other types)`. `%verändert per 2000` is the back-calculation: `%verändert × 2000 / total_RBC`.

CSV uses `;` as delimiter and `,` as decimal separator for direct Excel import on German locales.

---

## Profile system

A study profile is a small JSON file in `~/Library/Application Support/Erys/profiles/`:

```json
{
  "name": "TRIBAL 2026",
  "import_folder": "/path/to/images",
  "image_pattern": "^(?P<study>[A-Za-z]+)_(?P<subject>\\d+)_(?P<timepoint>[^_]+)_(?P<date>[^_]+)_(?P<image_num>\\d+)\\.tif$",
  "target_cells": 2000,
  "cell_types": [
    {"key": "normal",       "name": "Normal",               "color": "#FFD000"},
    {"key": "stechapfel",   "name": "Stechapfel",           "color": "#FF6B6B"},
    {"key": "auszug",       "name": "Auszüge Membran",      "color": "#4ECDC4"},
    {"key": "verschmolzen", "name": "Verschmolzen Membran", "color": "#9D4EDD"},
    {"key": "elongiert",    "name": "Elongierte RBC",       "color": "#F77F00"}
  ],
  "cellpose": {"diameter": 30, "flow_threshold": 0.4, "cellprob_threshold": 0.0, "model": "cpsam"},
  "use_gpu": true,
  "margin_n": 0,
  "show_margin_overlay": true,
  "duplicate_detection": {"enabled": true, "threshold_factor": 0.7, "policy": "remove_smaller"}
}
```

The **filename regex** must capture three named groups: `subject`, `timepoint`, `image_num`. Optional named groups (e.g. `study`, `date`) are stored for audit but not used for grouping. Files that don't match end up in an `_ungrouped` bucket.

You can keep multiple profiles side-by-side (one per study) and switch via menu → **Profil → Profile verwalten …**.

---

## Roadmap

Items the project will likely tackle next, in roughly the order they make sense:

- **v14** ✅ — Per-study aggregated Excel `.xlsx` export (`outputs/Uebersicht.xlsx`: tidy sheet + `Proband × Zeitpunkt` pivot matrices); hierarchical batch filtering with *Bis Ziel rechnen* + keep-awake; recursive image loading from sub-folders.
- **v15** — Auto-resegment workflow when profile parameters change (per-image user-confirm).
- **v16** — Pluggable Cellpose model paths (run your own custom-trained model from a profile).
- **v17** ✅ — Friendly filename parser (delimiter + roles, live preview), share packages (create/import), editable 2000-cell cap, in-app statistics box-plots, progress dashboard + time tracking, ML-ready per-cell export columns.
- **v18** — One-click ML dataset export (consolidated parquet + per-cell crops + COCO), see [`docs/ML_DATASET.md`](docs/ML_DATASET.md).
- **v19** — Built-in stats: A/B comparisons between timepoints, significance tests.
- **v20** — Multi-user sync (server-merged reviews).

---

## Acknowledgments

- **Cellpose** — the underlying segmentation engine. <https://github.com/MouseLand/cellpose>
  > Pachitariu, M., & Stringer, C. (2022). _Cellpose 2.0: how to train your own model._ Nature Methods.
  > Stringer, C., & Pachitariu, M. (2025). _Cellpose-SAM: superhuman generalization for cellular segmentation._ bioRxiv.
- **scikit-image** for the morphological operations + label measurements.
- **matplotlib** for the canvas.

If you publish work that used ErysReview, please cite the relevant Cellpose papers.

---

## License

MIT. See `LICENSE`.
