# NKReview

Desktop-Tool zur **optischen Dichte (DAB) von NK-Zellen** in RGB-Hellfeld-Bildern.
Abgeleitet von ErysReview @ 3cacb26 (Qt-Oberfläche, Look, Installer) – die Mess-Schicht ist neu.

## Ablauf

1. **Studie wählen oder anlegen.** Der Assistent „Neue Studie“ hat vier Schritte:
   - **Ordner und Ziel:** Name, ein oder mehrere Bildordner, Ziel *ganze Zelle* (NK) oder *Zellkern* (z. B. C2C12 Ki67).
   - **Dateinamen:** Trennzeichen und eine Rolle pro Namensteil (Proband, Gruppe, Zeitpunkt, Zelltyp, Marker, Bildnummer, ignorieren), mit Live-Vorschau; Dateien mit abweichender Teilezahl stehen oben in Rot.
   - **Messung:** I₀-Regel, Grauwert, Kontur und Gruppen je Proband.
   - **Fertig.**
2. **Cellpose läuft im Hintergrund**, das angezeigte Bild zuerst.
3. **Durchsehen:** Bildbaum nach Zeitpunkt · Proband, Filter nach Marker. Pro Zelle siehst du OD, I₀-Felder, OD-Karte, radiales Profil und die Verteilung der Pixel-OD. Du kannst Zellen verwerfen, Konturen zeichnen und I₀-Felder versetzen oder neu losen; Enter markiert ein Bild als geprüft.
4. **Export:** `outputs/nkreview_<Zeit>/` im ersten Ordner der Studie.

## Messprinzip (wie im Methodenteil)

| Schritt | Verfahren |
|---|---|
| Grauwert | ImageJ-Standard (R+G+B)/3, gerundet (alternativ gewichtet 0,299/0,587/0,114) |
| I₀ je Zelle | Mittel von 3 Feldern à 9×9 = 81 px, zufällig 14–40 px neben der Kontur, nicht auf Zellen oder gefärbtem Debris. Reproduzierbar geseedet; per Werkzeug 3 versetzbar, mit R neu losbar. |
| Gültigkeit | I₀ außerhalb 210–220 (pro Studie einstellbar) → Zelle **ausgeschlossen**, nicht gelöscht |
| OD | −log₁₀(Ī / I₀), Ī = mittlerer Grauwert in der Kontur (OD des Mittelwerts, wie Fiji) |
| Verteilung | Pixel-OD mean/SD/median/p5/p95, Anteil > 0,3, radiales Profil in 5 Ringen vom Zentrum zur Membran |
| Kontur | Ganze Zelle: Hybrid (Cellpose auf RGB, Rand aus Gesamt-OD > 0,05). Zellkern: Cellpose auf der DAB-Karte. Backup: reine OD-Schwelle; manuell zeichnen jederzeit |
| Vorfilter | Cellpose nur auf Ausschnitten um DAB-positive Bereiche (bei dichten Bildern automatisch ganzes Bild) |
| Zusatz | DAB-OD (Ruifrok–Johnston „H DAB“), Gesamt-OD aus RGB, Ring-I₀-Kontrolle, Debris-Verdacht (H-Kanal ≥ 0,04, nur Kern-Studien) |

Offen: das Makro aus Appendix E. Bis dahin gelten ImageJs Standard-Graukonversion und „80 pixels“ als Fläche 9×9.

### Export

| Datei | Inhalt |
|---|---|
| `zellen.csv` | 1 Zeile pro Zelle (Long-Format für R): Rollen aus dem Dateinamen, `status` (gewertet / verworfen / ausgeschlossen) + `grund`, `od_fiji`, `i0`, `i0_feld1…3`, `i0_ok`, Pixel-OD-Statistik, `profil_r1…r5`, `dab_od_mean`, `gesamt_od_mean`, `debris_verdacht`, `kontur` |
| `zusammenfassung.csv` | Proband × Gruppe × Zeitpunkt × Zelltyp × Marker: n Bilder, n Zellen, `od_fiji` mean/SD/median (nur gewertete Zellen) |
| `einstellungen.json` | die Studie mit allen Parametern, App-Version, Export-Optionen |
| `overlays/` | PNG je Bild: Konturen nach Status, I₀-Felder, OD |
| `nk_export.xlsx` | beide Tabellen als Blätter |

## Bedienung

| Taste | Aktion |
|---|---|
| ← / → | vorheriges / nächstes Bild |
| 1 / 2 / 3 | Auswählen · Kontur zeichnen (freihand) · I₀-Feld setzen (verschiebt das nächste Feld der gewählten Zelle) |
| X / Entf | Zelle verwerfen / zurückholen |
| R | I₀-Felder der Zelle neu losen |
| Enter | Bild als geprüft markieren |
| D | Ansicht: Original → Grau-OD → DAB-Kanal |
| O / I | Konturen / I₀-Felder an/aus |
| F | einpassen · ⌘Z rückgängig · Mausrad Zoom |

## Daten

Studien liegen als JSON unter `~/Library/Application Support/NKReview/studien/`. Pro Bildordner speichert `.nk_cache/` die Cellpose-Masken (mit den Parametern, die sie erzeugt haben; geänderte Parameter → neu rechnen) und `<bild>_review.json` (verworfen, gezeichnet, I₀-Felder, geprüft).

## Installation / Start

macOS: `install.command` doppelklicken (legt `.venv` an, wie bei ErysReview), danach `run.command` oder NKReview auf dem Schreibtisch.
Windows: `install.bat` (eigene conda-Umgebung `nkreview`), dann `run.bat`. Unter Windows bisher ungetestet.

Tests: `./.venv/bin/python tests/test_core.py`

## Bekannte Grenzen

- Cellpose-SAM: Ein **kleinerer** Durchmesser verliert die Zelle (das Bild wird hochskaliert); `cellprob < 0` bläht die Maske nur auf. Am Testbild getestet, deshalb bleibt der Standard-Durchmesser.
- Cellpose-SAM liefert auf Ausschnitten, deren Seiten keine Vielfachen von 16 sind, keine Masken. Deshalb das Auffüllen (cellpose 4.1.1).
- Sich berührende, stark gefärbte Zellen: Der DAB-Vorfilter fasst sie zu einem Ausschnitt zusammen; Cellpose trennt sie dort wie gewohnt.
