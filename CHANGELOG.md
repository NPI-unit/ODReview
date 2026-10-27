# Changelog

## 0.10.0 — 2026-10-20

- **Review side panel in three tabs** (from the design canvas): a fixed head with the image, “16 / 32 confirmed”, a progress bar and the counts; tabs **Cell/Nucleus · List · Notes**; a fixed foot with “Confirm all open”, ⋯ (rerun segmentation, contour method and OD threshold) and “Image reviewed”. Cell tab: result, why, the cell's buttons, then “More values” and “Distribution” (folds remembered). List tab: filter chips (all / open / I₀ warning / confirmed / discarded) and a table sortable by every column; a click selects and zooms. Notes tab: the image's note, the comment circles and the cells' notes – a click jumps there.
- **I₀ on an axis** in a foldable “Background (I₀)” section instead of the coloured I₀ banner (a ⚠ line only when the check fails; positive / debris / discarded moved to the grey line under the OD): the three fields as dots, the mean as a bar, the line green where the check passes and red outside – 210–220 (fixed range), background ± % (relative) or mean ± % (local); grey when there is nothing to check against. The axis fits the band and all fields.
- **File names as in ErysReview:** one chip per part with a role menu (one part per role), a short sample (unread files first, ✓ / ⚠), a line “812 of 812 files read · 24 subjects · …” and a one-click “Add delimiter” fix. Next needs a part as Subject.
- **One filter button in the sidebar:** marker and status in one menu (one tick each) instead of the marker chips and the Show list.
- **Settings in plain words:** Segmentation and Background I₀ open with a one-line summary, each field has an explanation, rarely used fields sit under “Advanced”; ⚠ in the navigation marks pages whose changes alter the results.

## 0.9.0 — 2026-10-19

- **New name: ODReview** – the app measures the optical density of NK cells, Tregs and muscle cells alike. Window title, logo (“OD”), installer, ODReview.app, help and README. Unchanged on purpose: the support folder (`~/Library/Application Support/NKReview`, studies and templates), the `.nk_cache` folders next to the images and the Python package `nkreview` – existing studies open as before. An old NKReview.app on the Desktop can be deleted after installing.
- **Status filter in the image list** as in ErysReview: All / Not segmented / In progress / Finished / With notes, each with its count.
- **Nuclei with InstanSeg** (`brightfield_nuclei`, Apache-2.0) instead of Cellpose for the target “nucleus”: finds pale and dark nuclei in one run, ~1–7 s per image, contour used as is. On the pilot images Cellpose-SAM outlined the whole cell whatever input or threshold it got. The 16 MB model downloads on first use into the app's support folder.
- **Magnification presets** for nuclei: high (0.25 µm/px, nuclei 200–4000 px) and low (0.45 µm/px, 100–1500 px). The pixel size is estimated from the nucleus size until the microscope calibration is known.
- **Max. cell area** next to the min. area: larger objects are dropped (whole cells, clumps).
- **Settings › Segmentation:** model (Cellpose-SAM for cells / InstanSeg for nuclei), magnification preset, pixel size and max. area; editing a value by hand shows the preset as “custom”. Cellpose-only fields are greyed out for InstanSeg. Status bar, legend and “Rerun segmentation” no longer say Cellpose; help and README describe the nucleus path.
- **Clearer review view:** a legend under the image explains every contour (kept – counted, excluded – I₀ invalid, discarded by you, selected, I₀ fields). No numbers or OD next to the contours any more; kept cells are filled green (Settings › Display: on/off and opacity, for every study). I₀ fields are drawn only for the selected cell, purple and numbered 1–3 as in the panel.
- **Side panel in reading order:** count of the image (kept / excluded / discarded), the list folded away, then the selected cell – its OD as exported in large, how it is computed, and why it is kept or excluded (I₀ and its fields). Pixel OD, I₀ and DAB OD under “More values”. Buttons grouped by “This cell” and “This image”; contour method and OD threshold under “Contour method (advanced)”.
- **I₀ per image** (Settings › Background I₀ › I₀ from): instead of 3 fields next to every cell, one set of 3 fields per image in its free background (≥ 40 px from every cell) gives the I₀ of all its cells. Seeded like before; R draws a new set, tool 3 moves a field. Shown for the whole image, numbered 1–3. “Per cell” stays the default; existing studies are unchanged.
- **I₀ check is a warning, not an exclusion:** a cell whose I₀ fails the rule stays (status kept, column `i0_warning`), is drawn orange and labelled “I₀ warning”; the reviewer looks at the fields and can still confirm it (C). “Confirm all open” skips warned cells. Only a cell without any background (no OD) is still excluded.
- **Comment circles:** tool 4 next to the cells – click or drag – puts a numbered dashed circle anywhere in the image with a comment; a click on it reads, edits or deletes it (empty text deletes). Listed under “Comments” in the side panel (click zooms to it); review file only, not exported.
- **Local I₀ check** (Settings › Background I₀ › I₀ check: “fields agree with each other”): a cell is valid when each of its I₀ fields lies within ± x % of their mean – independent of how bright that part of the image is, so vignetting no longer flags cells, while a field on debris still does. New column `i0_field_spread_pct`. On the pilot images: 23 of 147 nuclei flagged at ± 3 % instead of 98 with the fixed 210–220. New nucleus studies use it (± 3 %) by default; existing studies and whole-cell studies keep their rule.
- “I₀ per image” shows a warning: only for evenly lit or flat-field corrected images (with vignetting the OD is off by 10–25 % on the pilot).
- **Confirm each cell:** a counted cell is white until you confirm it (C, or “✔ Confirm”) – then green. C jumps to the next open cell; “Confirm all open” does the rest of an image. Enter asks while cells are still open. **summary.csv counts confirmed cells only**; cells.csv keeps every kept cell with a `confirmed` column. Discarding a cell drops its confirmation.
- **Notes as in ErysReview:** tool 4 or N – click a cell for its note, next to the cells for the image's. Cells with a note carry a purple dot, images a ❝ in the list. Notes stay in the review file and are not exported.
- **Excluded contours** are solid and thicker (orange, 3 px) instead of thin dashed lines.
- **Fix: crash after closing the settings** while segmentation started (SIGBUS): Python's garbage collector ran in the segmentation thread and destroyed the closed dialog there. Collection now runs only on the GUI thread.
- Existing studies and templates keep their Cellpose settings and cached results; switch a study to InstanSeg under Settings › Segmentation (re-runs the segmentation).

## 0.8.0 — 2026-10-13

- **Brightness & contrast** as in ErysReview (☀︎ in the toolbar, key B, or right click on the image): large before | after preview, brightness, contrast, gamma, Auto stretch, histogram; apply to this image, all images of the subject, or the whole study. Display only – Cellpose, the I₀ fields and every OD value use the original image. Adjusted images carry ☀︎ in the image list; right click › Reset brightness.

## 0.7.0 — 2026-10-13

- **Studies view like ErysReview:** one card per study (last opened first) with target and markers, folders and image count, subjects done as a big number, a bar of reviewed images, a chip per subject (done / in progress / open) and the measurement in one line. Progress is counted after the window shows. Actions for the selected study: Open, Settings, Save as template, Duplicate, Rename, Delete (removes only the study entry – images, Cellpose results, reviews and exports stay).

## 0.6.0 — 2026-10-13

- **Settings window like ErysReview:** navigation on the left (study switch, Images & names, Segmentation, Background I₀, Analysis, Templates), one page per topic with cards, footer with the number of unsaved changes, Discard, Save, Close. Warns before Cellpose would re-run. Opens from ⚙ in the toolbar (⌘,) or “Settings” in the study list; the “New study” assistant uses the same fields.
- **Study button** shows only the study name (details in the tooltip).
- **Side panels** can be dragged shut and come back with the arrow on their divider; the image view cannot be closed.

## 0.5.0 — 2026-10-13

- **Help window** (“?” in the toolbar, F1): pipeline from import to export with the steps for whole cells and nuclei, formula sheet with “Copy LaTeX”, worked example with an OD calculator. Formulas drawn offline with matplotlib; “Open as web page” opens the interactive version (`nkreview/help/pipeline.html`).
- **Own logo** (`tools/make_logo.py`): a DAB-brown cell with its contour and the three I₀ fields. App icon, `assets/icon.*`, and NKReview.app on the Desktop.

## 0.4.0 — 2026-10-13

- **English** throughout: interface, status values (kept / discarded / excluded), export files (`cells.csv`, `summary.csv`, `settings.json`) and column names (`subject`, `group`, `od`, …). Number fields use a decimal point. Stored study and template files keep their keys, so existing studies open unchanged.
- **Smaller study assistant** (900 × 640, never more than 90 % of the screen); the measurement page has two columns and scrolls.
- **Folder tree** when adding image folders: sub-folders with image counts, the images, other files; `.nk_cache` and `outputs` shown as skipped.

## 0.3.0 — 2026-10-13

- **Vorlagen:** „Als Vorlage speichern“ in der Studienübersicht; im Assistenten „Neue Studie“ ganz oben wählbar. Übernommen werden Ziel, Dateinamen-Schema und alle Messparameter – nicht Name, Ordner, Gruppen.
- **Datum im Dateinamen** bleibt ein Teil (2026-08-27, 27.08.2026, 10.02.26), neue Rolle „Datum“, Export als JJJJ-MM-TT. Vergrößerungen wie „20x“ werden automatisch ignoriert.
- **I₀-Prüfung relativ** zum freien Hintergrund des eigenen Bildes (für Serien mit unterschiedlicher Belichtung), Spalten `i0_hintergrund`, `i0_abw_pct`.
- **Mindestfläche** für Cellpose-Objekte, **Positiv-Grenze** mit Spalte `positiv` und `n_positiv`/`anteil_positiv` in der Zusammenfassung.

## 0.2.0 — 2026-10-13

- **Studien** wie in ErysReview: Studien-Datei, mehrere Ordner, Assistent „Neue Studie“ mit erweiterten Namensrollen (Proband, Gruppe, Zeitpunkt, Zelltyp, Marker, Bildnummer) und Live-Vorschau, Gruppen je Proband, Fortschritt Proband × Zeitpunkt × Marker.
- **Fiji-konforme Messung** als Hauptwert: Grauwert, I₀ je Zelle aus 3 zufälligen 81-px-Feldern neben der Zelle (versetzbar, neu losbar), OD des Mittelwerts, Ausschluss bei I₀ außerhalb 210–220.
- **Zellpanel** mit OD-Karte, radialem Profil und Histogramm der Pixel-OD.
- **Zellkern-Studien**: Cellpose auf der DAB-Karte, Kontur „Nur Cellpose“, Debris-Verdacht.
- Dichte Bilder: Cellpose läuft aufs ganze Bild statt auf einer riesigen zusammengelegten Box.
- **Export pro Studie**: zellen.csv, zusammenfassung.csv, einstellungen.json, overlays/, nk_export.xlsx.

## 0.1.0 — 2026-10-12

Erste Version, abgeleitet von ErysReview @ 3cacb26.

- Qt-Oberfläche: Bildliste, Bildansicht mit Konturen, Zelltabelle, Werkzeuge Auswählen / Zeichnen / Hintergrund-ROI, Undo.
- Cellpose im Hintergrund auf DAB-ROI-Ausschnitten (Vorfilter), Ergebnis-Cache je Bild.
- Hybrid-Kontur (Cellpose + OD-Rand), Backup: reine OD-Schwelle.
- DAB-OD per Ruifrok–Johnston (Fiji „H DAB“), I₀ automatisch pro Bild oder per ROI, Ring-I₀ als Kontrolle.
- Export CSV/XLSX + Einstellungen + Overlay-PNGs.
- Ery-spezifischer Code (Typen, Studien, Abgleich, erys.db) entfernt.
