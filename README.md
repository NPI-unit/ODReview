# NKReview

Desktop-Tool zur **optischen Dichte (DAB) von NK-Zellen** in RGB-Hellfeld-Bildern.
Abgeleitet von ErysReview @ 3cacb26 (Qt-Oberfläche, Look, Installer) – die Mess-Schicht ist neu.

## Ablauf

1. **Ordner öffnen** – alle Bilder (auch in Unterordnern) erscheinen links.
2. **Cellpose läuft im Hintergrund**, das angezeigte Bild zuerst. Status: ○ wartet · ◐ läuft · ● segmentiert · ✔ geprüft · ✕ Fehler.
3. **Durchsehen** mit ← / →: Konturen prüfen, Zellen verwerfen oder von Hand zeichnen, ggf. Schwelle oder Hintergrund-ROI anpassen, mit Enter als geprüft markieren.
4. **Export** → `outputs/nk_messungen_<Zeit>.csv` + `.xlsx`, `nk_einstellungen_<Zeit>.json`, `overlays/*.png`.

## Messprinzip

| Schritt | Verfahren |
|---|---|
| Vorfilter | Cellpose läuft nur auf Ausschnitten um DAB-positive Bereiche (geglättete DAB-OD > 0,04, ≥ 150 px, +48 px Rand, mind. 256 px, auf Vielfache von 32 mit Hintergrundfarbe aufgefüllt). Farbloser Debris erreicht Cellpose gar nicht; ein dünn besiedeltes Bild dauert Sekunden statt einer Minute. |
| Kontur (Standard: Hybrid) | Cellpose (cpsam, Standard-Durchmesser) findet die Zelle; der Rand wird aus der Gesamt-OD neu gezogen: alle Pixel > Schwelle (0,05), die mit der Zelle zusammenhängen, höchstens 10 px außerhalb, Löcher gefüllt. Ist die Zelle zu blass dafür, bleibt die Cellpose-Kontur. Alternativen: nur Cellpose · nur OD-Schwelle (Backup ohne Cellpose). |
| Hintergrund I₀ | pro Bild und Kanal: Median aller Pixel ≥ 15 px von jeder Zelle entfernt; optional per Rechteck-ROI. |
| OD | Beer–Lambert, dekadisch: OD = −log₁₀(I / I₀) pro RGB-Kanal, nicht bei 0 abgeschnitten (Rauschen mittelt sich heraus). |
| DAB | Ruifrok–Johnston-Deconvolution mit dem Fiji-Preset „H DAB“, gerechnet auf der OD (ohne Fijis 8-bit-Rundung). |

Keine Step-Tablet-Kalibrierung: I₀ aus dem Bild macht den Wert bereits zu einer echten Transmissions-OD.

### CSV-Spalten (eine Zeile pro Zelle)

`bild, geprueft, zelle, verworfen, manuell, kontur, flaeche_px, x, y, randzelle,`
`dab_od_mean, dab_od_sd, dab_od_median, dab_od_p95, dab_od_max, dab_iod,`
`gesamt_od_mean, gesamt_iod, kanal_h_od_mean, kanal_rest_od_mean,`
`i0_r, i0_g, i0_b, i0_quelle, ring_i0_abweichung_pct, ring_warnung, schwelle_od`

- **IOD** = Summe der OD über alle Pixel der Zelle (OD · px).
- **kanal_h** ist der zweite Deconvolution-Kanal. Ohne Hämatoxylin-Gegenfärbung ist er Übersprechen bzw. Kontrolle, kein Hämatoxylin.
- **ring_i0_abweichung_pct**: Der lokale Hintergrund (Ring 15–60 px um die Zelle) weicht so weit vom Bild-I₀ ab. Über 5 % wird `ring_warnung` gesetzt (ungleichmäßige Ausleuchtung, Debris in der Nähe).
- Verworfene Zellen stehen mit `verworfen = True` in der Tabelle, damit nichts stillschweigend verschwindet. Für die Auswertung bitte filtern.

## Bedienung

| Taste | Aktion |
|---|---|
| ← / → | vorheriges / nächstes Bild |
| 1 / 2 / 3 | Auswählen (Ziehen = verschieben) · Zelle zeichnen (freihand) · Hintergrund-ROI ziehen |
| X / Entf | ausgewählte Zelle verwerfen / zurückholen |
| Enter | Bild als geprüft markieren |
| D | Ansicht: Original → DAB-Kanal → Gesamt-OD (feste Skala 0–0,6) |
| O | Konturen an/aus |
| F | ganzes Bild einpassen |
| ⌘Z | rückgängig |
| Mausrad | Zoom |

Methode und Schwelle gelten für den ganzen Ordner, außer „nur für dieses Bild“ ist angehakt.

## Daten

Alles, was die App sich merkt, liegt im Bildordner unter `.nk_cache/`: `settings.json` (Ordner-Einstellungen), `<bild>_cellpose.npz/.json` (Masken + Parameter) und `<bild>_review.json` (verworfen, gezeichnet, ROI, geprüft). Ändert sich ein Cellpose-Parameter, wird neu gerechnet.

## Installation / Start

macOS: `install.command` doppelklicken (legt `.venv` an, wie bei ErysReview), danach `run.command` oder NKReview auf dem Schreibtisch.
Windows: `install.bat` (eigene conda-Umgebung `nkreview`), dann `run.bat`. Unter Windows bisher ungetestet.

Tests: `./.venv/bin/python tests/test_core.py`

## Bekannte Grenzen

- Cellpose-SAM: Ein **kleinerer** Durchmesser verliert die Zelle (das Bild wird hochskaliert); `cellprob < 0` bläht die Maske nur auf. Am Testbild getestet, deshalb bleibt der Standard-Durchmesser.
- Cellpose-SAM liefert auf Ausschnitten, deren Seiten keine Vielfachen von 16 sind, keine Masken. Deshalb das Auffüllen (cellpose 4.1.1).
- Sich berührende, stark gefärbte Zellen: Der DAB-Vorfilter fasst sie zu einem Ausschnitt zusammen; Cellpose trennt sie dort wie gewohnt.
