# NKReview

Desktop tool for the **optical density of cells and nuclei** in RGB brightfield images (DAB immunohistochemistry: NK cells 5mC/5hmC, C2C12 Ki67).
Derived from ErysReview @ 3cacb26 (Qt interface, look, installer) – the measuring layer is new.

## Workflow

1. **Choose or create a study.** The “New study” assistant has four steps:
   - **Folders and target:** optional template, name, one or more image folders (shown as a tree with sub-folders and images), target *whole cell* (NK) or *nucleus* (e.g. C2C12 Ki67).
   - **File names:** delimiters and one role per name part (subject, group, timepoint, cell type, marker, date, image no., ignore) with a live preview; files with a different number of parts are listed first in red. Dates (2026-08-27, 27.08.2026, 10.02.26) always stay one part.
   - **Measurement:** grey value, I₀ rule, contour, cut-offs, groups per subject.
   - **Done.**
2. **Cellpose runs in the background**, the image on screen first.
3. **Review:** image tree by timepoint · subject, filter by marker. Per cell: OD, I₀ fields, OD map, radial profile and the distribution of pixel OD. Discard cells, draw contours, move or redraw I₀ fields; Enter marks an image as reviewed.
4. **Export:** `outputs/nkreview_<time>/` in the study's first folder.

## Templates

A study can be saved as a template (Studies › “Save as template”). In “New study” a template fills in target, file-name scheme and every measurement setting; name, folders and groups belong to each study. Templates live in `~/Library/Application Support/NKReview/vorlagen/`.

**C2C12 nucleus 20×** (derived from the pilot NewPilot_27082026_Zheng): nuclei found on the DAB map, contour “Cellpose only”, min. area 500 px, I₀ fields 40–90 px from the nucleus, I₀ relative ±3 %, positive above grey OD 0.01 (noise floor; 99.9 % of pseudo-nuclei on background < 0.006). Ki67-negative nuclei cannot be found automatically without a counterstain → draw them by hand (tool 2).

## Measurement (as in the method section)

| Step | Method |
|---|---|
| Grey value | ImageJ default (R+G+B)/3, rounded (or weighted 0.299/0.587/0.114) |
| I₀ per cell | Mean of 3 fields of 9×9 = 81 px, at random 14–40 px from the contour (nuclei: 40–90 px), not on cells or stained debris. Seeded, so reproducible; move with tool 3, redraw with R. |
| Validity | fixed: I₀ outside 210–220 → **excluded** (not deleted) · or relative: I₀ deviates more than ±x % from the image's free background (series with different exposure) |
| OD | −log₁₀(Ī / I₀), Ī = mean grey value inside the contour (OD of the mean, as Fiji) |
| Positive | optional: grey OD > cut-off → `positive` (summary `n_positive`, `share_positive`) |
| Distribution | pixel OD mean/SD/median/p5/p95, share above 0.3, radial profile in 5 rings from centre to membrane |
| Contour | Whole cell: hybrid (Cellpose on RGB, edge from total OD > 0.05). Nucleus: Cellpose on the DAB map. Backup: OD threshold only; drawing by hand always possible |
| Pre-filter | Cellpose only on crops around DAB-positive regions (dense images: whole image) |
| Extra | DAB OD (Ruifrok–Johnston “H DAB”), total RGB OD, ring-I₀ control, debris flag (H channel ≥ 0.04, nucleus studies) |

Open: the macro from Appendix E. Until then ImageJ's default grey conversion is used and “80 pixels” is read as a 9×9 area.

### Export

| File | Content |
|---|---|
| `cells.csv` | one row per cell (long format for R): `subject`, `group`, `timepoint`, `cell_type`, `marker`, `date`, `image_no`, `image`, `status` (kept / discarded / excluded) + `reason`, `od`, `i0`, `i0_field1…3`, `i0_ok`, `i0_background`, `i0_dev_pct`, `positive`, pixel OD statistics, `profile_r1…r5`, `dab_od_mean`, `total_od_mean`, `debris_suspect`, `contour` |
| `summary.csv` | subject × group × timepoint × cell type × marker: `n_images`, `n_cells`, `od_mean`/`od_sd`/`od_median`, `n_positive`, `share_positive` (kept cells only) |
| `settings.json` | the study with every parameter, app version, export options |
| `overlays/` | PNG per image: contours by status, I₀ fields, OD |
| `nk_export.xlsx` | both tables as sheets |

## Keys

| Key | Action |
|---|---|
| ← / → | previous / next image |
| 1 / 2 / 3 | select · draw contour (freehand) · set I₀ field (moves the selected cell's nearest field) |
| X / Del | discard / restore cell |
| R | redraw the cell's I₀ fields |
| Enter | mark image as reviewed |
| D | view: original → grey OD → DAB channel |
| O / I | contours / I₀ fields on/off |
| F | fit · ⌘Z undo · mouse wheel zoom |

## Data

Studies are JSON files in `~/Library/Application Support/NKReview/studien/`. Each image folder keeps `.nk_cache/` with the Cellpose masks (with the parameters that made them; changed parameters → recomputed) and `<image>_review.json` (discarded, drawn, I₀ fields, reviewed).

## Install / start

macOS: double-click `install.command` (creates `.venv`, as in ErysReview), then `run.command` or NKReview on the Desktop.
Windows: `install.bat` (own conda env `nkreview`), then `run.bat`. Not tested on Windows yet.

Tests: `./.venv/bin/python tests/test_core.py`

## Known limits

- Cellpose-SAM: a **smaller** diameter loses the cell (the image is upscaled); `cellprob < 0` only inflates the mask. Tested on the NK test image, so the default diameter stays.
- Cellpose-SAM returns no masks on crops whose sides are not multiples of 16 – hence the padding (cellpose 4.1.1).
- Unstained nuclei are not visible in brightfield without a counterstain; on the C2C12 pilot no Cellpose input found them reliably.
