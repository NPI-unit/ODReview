# Changelog

## 0.1.0 — 2026-10-12

Erste Version, abgeleitet von ErysReview @ 3cacb26.

- Qt-Oberfläche: Bildliste, Bildansicht mit Konturen, Zelltabelle, Werkzeuge Auswählen / Zeichnen / Hintergrund-ROI, Undo.
- Cellpose im Hintergrund auf DAB-ROI-Ausschnitten (Vorfilter), Ergebnis-Cache je Bild.
- Hybrid-Kontur (Cellpose + OD-Rand), Backup: reine OD-Schwelle.
- DAB-OD per Ruifrok–Johnston (Fiji „H DAB“), I₀ automatisch pro Bild oder per ROI, Ring-I₀ als Kontrolle.
- Export CSV/XLSX + Einstellungen + Overlay-PNGs.
- Ery-spezifischer Code (Typen, Studien, Abgleich, erys.db) entfernt.
