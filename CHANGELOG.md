# Changelog

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
