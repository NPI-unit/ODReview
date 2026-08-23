# Feature-Block: Anmerkungen, Status, Export-Auswahl, Performance

**Angelegt:** 2026-08-21 · **Stand:** A, B, C, D, E, F erledigt — offen nur noch **A2** (Umzug aus iCloud)
**Basis:** `main` @ `e795dd5` (17.5.0)

Dieses Dokument ist die Arbeitsgrundlage für den Block. Es hält die
Entscheidungen samt Begründung fest, die **gemessenen** Befunde, und den
Fortschritt. Erledigtes wird abgehakt, nicht gelöscht.

---

## 0. Zwei Befunde, die vor allem anderen kommen

### 0.1 Der Arbeitsordner liegt in iCloud Drive — Daten sind bereits verloren

`~/Desktop/` ist über „Schreibtisch & Dokumente" mit iCloud synchronisiert.
macOS lagert Dateiinhalte aus („Speicher optimieren"): die Datei behält ihre
Größe, belegt aber **0 Blöcke** und trägt das Flag `dataless`.

Gemessen im Bilderordner:

| | |
|---|---|
| Masken geprüft | 35 |
| **nicht lesbar** | **3** (`EOFError: No data left in file`) |
| Beispiel | `TRIBAL_CH_T0_010_masks.npy` — 45 836 Bytes, 0 Blöcke, `dataless` |

Betroffen sind auch `outputs/T0/T0_CH/TRIBAL_CH_T0_006` und `…_011`.
Die Statistik **stürzt** daran ab, statt es zu melden.

**Nachtrag 2026-08-21 — der Befund ist größer als gedacht.** Beim Testen des
Export-Dialogs sind zwei weitere Ausprägungen aufgetaucht:

* **Auch Rohbilder und Quellcode sind betroffen.** 1 von 20 TIFs
  (`TRIBAL_CH_T0_004.tif`) war ausgelagert — und mit `erysreview/extras.py`
  sowie `erysreview/theme.py` **zwei Quelldateien der App selbst**.
* **Ein Lesevorgang liefert manchmal 0 Bytes, statt zu blockieren.** `head` und
  `awk` bekamen leeren Inhalt aus `extras.py`; `cat` auf dieselbe Datei lud sie
  korrekt herunter. Das ist derselbe Mechanismus wie der `EOFError` oben, nur
  ohne Fehlermeldung — der gefährlichere Fall.
* **`shutil.copy2` klont den Platzhalter.** Auf APFS geht `copy2` über
  `fcopyfile`/clonefile. Bei einer ausgelagerten Datei entsteht eine Kopie, die
  die volle logische Größe meldet, aber **keine Daten** enthält — und außerhalb
  des synchronisierten Ordners nie mehr gefüllt werden kann. Im
  Trainingsdaten-Export hätte das stumm leere TIFs neben gültige Masken
  geschrieben. Behoben durch `_copy_source()` (explizites read/write).

Der Code ist jetzt so hart, wie er sein kann: unlesbare Masken werden je Datei
einmal gemeldet statt zu crashen, Originale werden lesend kopiert, und das TIF
wird beim Export nur noch geladen, wenn wirklich ein Overlay entsteht. **Was er
nicht kann: Daten erfinden, die nicht auf der Platte liegen.** A2 bleibt die
eigentliche Abhilfe.

### 0.2 Derselbe Effekt erklärt die Trägheit

Auch `.venv` liegt in iCloud — 1 GB, regenerierbar, trotzdem synchronisiert.

| | |
|---|---|
| Torch-Dateien gesamt | 14 556 |
| davon `dataless` | **2 374** |
| `import torch` **kalt** | **44 415 ms** |
| `import torch` **warm** | **2 084 ms** |
| Profil-Editor bauen (Torch warm) | 171 ms |
| Einstellungen bauen | 18 ms |

Der Profil-Editor rief `detect_gpu_backend()` beim Bauen auf ([dialogs.py:538](../erysreview/dialogs.py)),
nur um ein Info-Feld zu füllen — und löste damit den Torch-Import aus.
**~95 % der Wartezeit war iCloud, nicht der Code.** Die Einstellungen selbst
sind mit 18 ms schnell; der Eindruck „Einstellungen laden langsam" stammt vom
Profil-Editor.

---

## 1. Entscheidungen

| # | Thema | Entscheidung | Warum |
|---|---|---|---|
| E1 | iCloud | Projekt **und** Daten aus iCloud herausziehen **und** App gegen unlesbare Masken härten | Umzug löst das Problem hier; die Härtung schützt Laborrechner, wo OneDrive dasselbe tut |
| E2 | Anmerkung auslösen | **Rechtsklick** auf die Zelle, aus jedem Werkzeug heraus | Linksklick ist von den drei Werkzeugen belegt; Anmerken ist gelegentlich, ein Moduswechsel wäre teurer als die Handlung |
| E3 | Anmerkung sichtbar | Kleines Symbol an der Zelle, kein Zoom-Gate | Ohne Markierung müsste man jede Zelle einzeln anklicken. Nutzt die Artist-Verwaltung der Zellnummern |
| E4 | Statusmodell | `is_finished` bleibt der einzige Zustand, wird pro Gruppe setzbar | „Das will ich besprechen" deckt bereits E2 ab; ein dritter Status wäre dieselbe Information zweimal |
| E5 | Export-Umfang | Auswahl **nur auf Gruppenebene**, plus Vorauswahl „Gruppen mit Bearbeitung" | Bildweise Filter ändern still die Prozentzahlen — siehe 2.1 |
| E6 | Export + Status | Häkchen „Exportierte als fertig markieren", **Vorgabe aus** | Der Status wird künftig von Hand gepflegt; ein Export darf ihn nicht nebenbei überschreiben |
| E7 | Performance | Ordner umziehen **und** GPU-Abfrage in den Hintergrund | Umzug nimmt ~42 s, die Hintergrund-Abfrage die restlichen 2 s |
| E8 | Anmerkungen im Export | **Nein**, nur in der App | Kein Datenvertrag berührt, keine DB-Migration, kein Freitext in weitergegebenen Dateien |

---

## 2. Randbedingungen, die beim Bauen gelten

### 2.1 Die 2000er-Kappung hängt an der übergebenen Bildliste

`effective_eval_stems(folder, images_in_group, profile)` füllt die Auswertung
über **genau die Liste** auf, die sie bekommt. Weniger Bilder einer Gruppe →
andere Auffüllung → **andere `%verändert_2000` und `RBC`**, ohne dass es
irgendwo auffällt.

* Ganze Gruppen weglassen: unbedenklich, jede exportierte Gruppe bleibt vollständig.
* Bilder **innerhalb** einer Gruppe abwählen: verändert die Zahlen. Deshalb E5.

Dieselbe Überlegung hatte schon die Juli-Sitzung zum Verwerfen von Chunking geführt.

### 2.2 Das Zahnrad ist vergeben

Das ⚙ neben jedem Probanden öffnet `eval_select_dialog` („welche Bilder zählen").
Der Status braucht deshalb eine andere Geste — Rechtsklick auf die Zeile.

### 2.3 Wiederverwendbar statt neu bauen

* Hierarchischer Picker Zeitpunkt→Proband→Bild: existiert in `package_create_dialog`.
* Artist-Verwaltung je Zelle (dict, Sichtbarkeit einzeln): `_number_artists` in `canvas.py`.
* Hintergrund-Arbeit mit lebendigem Fenster: `run_in_thread_pumping` (Hauptthread)
  bzw. Queue + `after()`-Poll (Worker) — **nicht mischen**, siehe Merge-Notiz zu `_compute_one`.
* DB-Migration bei Schemasprung: existiert seit `reviewed` (Schema 2).

---

## 3. Aufgaben

### A — Sofort (Datenintegrität)

- [x] **A1** `_read_mask` härten: unlesbare/ausgelagerte Maske → klare Meldung
      mit Bildname statt `EOFError` nach oben. Betrifft Statistik, Übersicht,
      Export, Sidebar.
- [ ] **A2** Umzug aus iCloud vorbereiten und durchführen (Anleitung in 5.).

### B — Anmerkungen (E2, E3, E8)

- [x] **B1** `ReviewState`: `notes: dict[str, str]` (Zell-ID → Text) und
      `note: str` (Bild). Alte JSON ohne die Felder lädt weiter.
- [x] **B2** Rechtsklick im Canvas → kleiner Eingabedialog. Leerer Text löscht.
- [x] **B3** Marker-Artists je annotierter Zelle, Muster wie `_number_artists`.
- [x] **B4** Detailpanel: Bild-Anmerkung bearbeitbar + Liste der Zell-Anmerkungen.
- [x] **B5** Hinweis auf die Rechtsklick-Geste in der Hilfe (Tastenkürzel-Dialog).

### C — Status (E4)

- [x] **C1** Rechtsklick auf eine Proband-Zeile → „Alle als fertig markieren" /
      „Markierung aufheben".
- [x] **C2** Sidebar nach der Änderung auffrischen (Zähler + Symbole).

### D — Export-Dialog (E5, E6)

- [x] **D1** Dialog vor dem Export: Gruppen-Checkliste, Vorauswahl-Knöpfe
      (alle / nur mit Bearbeitung), Häkchen „als fertig markieren" (aus).
- [x] **D2** `export_folder_structured` nimmt eine Gruppenauswahl entgegen.
- [x] **D3** Auto-Markierung aus [app.py:1028](../erysreview/app.py) an das Häkchen binden.

### E — Performance (E7)

- [x] **E1** GPU-Abfrage aus dem Dialogaufbau gelöst. Gemessen: Profil-Editor
      **4,86 s → 0,29 s** (kaltes `torch`), Label nach ~1 s gefüllt. Der Worker
      rührt Tk *gar nicht* an — auch `widget.after()` nicht, das registriert ein
      Kommando am Interpreter und wirft „main thread is not in main loop"; er
      schreibt in einen Halter, den der Main-Thread pollt.
- [x] **E2** Statistik/Übersicht gemessen: **0,13 s** bzw. **0,07 s** im kalten
      Prozess. Die früher beobachteten 17,6 s waren vollständig iCloud-
      Materialisierung, kein Code-Problem. Kein Eingriff nötig — die Abhilfe
      ist A2.

### F — Konsistenz zum Schluss

- [x] **F1** Installer: keine neuen Abhängigkeiten. Alles Neue ist stdlib
      (`threading`, `queue`, `sqlite3`); `install.bat:82` löst bereits jedes
      benutzte Paket in einem conda-forge-Solve auf.
- [x] **F2** README: Anmerkungen, Sammelstatus und Export-Dialog beschrieben;
      neuer Abschnitt „Do not keep the study folder in a cloud drive"; nebenbei
      fünf veraltete Stellen zur Duplikaterkennung korrigiert (das Profil hat
      seit PF-13 `size_filter`, kein `duplicate_detection` — die README
      dokumentierte einen Schlüssel, den es nicht mehr gibt) und `erys.db` im
      Ordner-Layout ergänzt.
- [x] **F3** Audit-Doc fortgeschrieben: UI-4 (Export-Hälfte) und PF-9 (Thread-
      Hälfte) aktualisiert, neuer Punkt **SR-4** für die Cloud-Auslagerung.
- [x] **F4** Sechs Gates grün, App startet (1,27 s), Anmerkungen/Status/Export
      funktional gegen echte Daten geprüft, Dialoge in DE und EN gebaut.

### Gates

| Datei | Umfang |
|---|---|
| `test_sizefilter.py` | 7 Checks — Größenfilter-Grenzen, Median-Bezug |
| `test_model_resolve.py` | 10 Checks — kein stiller Rückfall auf `cpsam` |
| `test_overlay.py` | 7 Mutationen — Patch == Vollaufbau, pixelgenau |
| `test_export_db.py` | 3 Gruppen — SQL-Views == `group_eval_summary` |
| `test_notes_and_scope.py` | **neu**, 15 Checks — Anmerkungen persistieren, Teilexport bleibt in seiner Auswahl |
| `test_i18n_keys.py` | **neu**, 300 Schlüssel — kein fehlender String kann einen Dialog erreichen |

---

## 4. Was bewusst NICHT gebaut wird

* **Dritter Status** („zu besprechen") — durch Anmerkungen abgedeckt (E4).
* **Bildweise Export-Auswahl** — würde die Prozentzahlen still verändern (2.1).
* **Anmerkungen im Export** — E8.
* **Masken-Bearbeitung** — unverändert außerhalb des Umfangs (siehe ML_DATASET.md).

---

## 5. Umzug aus iCloud — Anleitung

Vor dem Verschieben müssen ausgelagerte Dateien **heruntergeladen** werden,
sonst wandern leere Hüllen mit:

```bash
# 1. Alles materialisieren (kann dauern, braucht Netz)
find ~/Desktop/Apps/ErysReview ~/Desktop/Apps/Bilder -type f -flags +dataless \
  -exec brctl download {} \;

# 2. Prüfen, dass nichts mehr ausgelagert ist
find ~/Desktop/Apps/ErysReview ~/Desktop/Apps/Bilder -type f -flags +dataless | wc -l   # muss 0 sein

# 3. Verschieben (außerhalb von iCloud)
mkdir -p ~/Entwicklung ~/ErysDaten
mv ~/Desktop/Apps/ErysReview ~/Entwicklung/
mv ~/Desktop/Apps/Bilder     ~/ErysDaten/

# 4. venv neu bauen — es enthält absolute Pfade
cd ~/Entwicklung/ErysReview && rm -rf .venv && ./install.command
```

Die drei bereits unlesbaren Masken lassen sich nach Schritt 1 prüfen; sind sie
weiterhin defekt, müssen die betroffenen Bilder neu gerechnet werden
(006, 010, 011 — Reviews dieser Bilder gehen dabei verloren).

---

## 6. Fortschritt

| Datum | Was |
|---|---|
| 2026-08-21 | Grill abgeschlossen, E1–E8 entschieden, Befunde 0.1/0.2 gemessen |

---

## 7. Zweiter Block: Anmerkungs-Werkzeug + Flächen (2026-08-22)

**Entschieden** (Rückfragen im Verlauf):

* **G1** Anmerken wird das **4. Werkzeug** bei 1/2/3, nicht ein Knopf zwischen ↺ und ⓘ.
  Es ist ein Modus und gehört zu den Modi; Taste `4` fällt aus `TOOLS_ORDER` von selbst
  ab, die Aktiv-Hervorhebung gilt unverändert.
* **G2** Symbol **❝ (U+275D)** für den Werkzeugknopf (Wahl des Nutzers). Der Marker auf
  der Zelle bleibt **✎ (U+270E)** — als kleines Zeichen über einer Zelle ist der
  Bleistift lesbarer als ein Anführungszeichen. Beide sind in Segoe UI Symbol
  vorhanden (UI-15 hatte gezeigt, dass das zählt).
* **G3** Markierungsfarbe **blau `#5B9CFF`**, für Zelle *und* Bild dieselbe.
  Rot war nicht verfügbar: Zelltyp `stechapfel` ist `#FF6B6B`, die Fehlerfarbe
  `#F26B6B` — ein roter Ring wäre von einer Stechapfel-Zelle nicht zu unterscheiden.
  Blau ist zusätzlich die sicherste Signalfarbe unter Rot-Grün-Schwäche.
* **G4** **Kein Pfeil zur Zelle.** Bei ~271 Zellen je Bild kreuzen sich Pfeile schon bei
  einem Dutzend Anmerkungen und verdecken Nachbarzellen. Ein Ring nutzt die vorhandene
  Umrandungs-Maschinerie und skaliert beim Zoomen mit.
* **G5** **Ein einziges** verschiebbares und skalierbares Textfeld, das jeweils die
  gewählte Anmerkung zeigt — nicht eines je Anmerkung. Gebaut als Tk-Widget über dem
  Canvas (`place()`), nicht als matplotlib-Objekt: echter Text mit Cursor, und das
  Ziehen kollidiert nicht mit Schwenken/Zoomen, die auf denselben Maustasten liegen.
  Das Feld **ist** der Editor — kein modaler Dialog mehr im Arbeitsfluss.
* **G6** Flächen: **`is_active=1` UND `image_in_evaluation=1`**, Einheit **Pixel²**.
  Deckungsgleich mit der Stichprobe hinter RBC und %verändert_2000, damit nicht zwei
  Kennzahlen desselben Probanden verschiedene Nenner haben. Randzellen sind
  angeschnitten und müssen draußen bleiben, sonst wäre die Fläche systematisch zu klein.

**Befund vorab:** die Flächen stehen **bereits** je Zelle in `*_stats.csv`
([export.py:122](../erysreview/export.py)) und in `erys.db` (`cells.area`, REAL).
Es fehlt ausschließlich die Aggregation — `summary.py`, `overview.py` und
`evaluation.py` enthalten das Wort „area" kein einziges Mal.

### Aufgaben

- [x] **G-A1** `TOOL_NOTE` in `TOOLS_ORDER`; Toolbar-Knopf „4 Anmerken" mit ❝.
- [x] **G-A2** Linksklick mit dem Werkzeug: Zelle → deren Anmerkung, Leerfläche → Bild.
- [x] **G-A3** Blauer Ring statt/neben ✎ auf annotierten Zellen.
- [x] **G-A4** Schwebendes Textfeld: verschiebbar, skalierbar, Position in `AppSettings`.
- [x] **G-A5** Sidebar: blaue Markierung bei Bildern mit Anmerkung.
- [x] **G-A6** Sidebar-Filter „nur mit Anmerkung" als 5. Option.
- [x] **G-B1** Flächen-Aggregation je Bild und je Zeitpunkt/Proband (n, Mittel, Median, SD, min, max).
- [x] **G-B2** Menü `Auswertung → Zellflächen …`, nur auf Anfrage; Fenster + CSV-Export.

### Ergebnis

* **Werkzeug „4 ❝ Anmerken"** — Zelle anklicken öffnet deren Anmerkung, Klick auf
  Leerfläche die des Bildes. Rechtsklick bleibt als schnellere Geste und landet im
  selben Feld. Taste `4` fällt aus `TOOLS_ORDER` ab.
* **Schwebendes Textfeld** (`notebox.py`) — verschiebbar am Kopf, skalierbar an der
  Ecke, Geometrie in `AppSettings`. Kein Speichern-Knopf: 400 ms Debounce nach dem
  Tippen plus sofortiges Schreiben bei Zielwechsel und beim Schließen. Schließt beim
  Bildwechsel, sonst zeigte es den Text des verlassenen Bildes.
* **Blauer Ring + ✎** auf annotierten Zellen, beides an der Overlay-Schaltung.
* **Sidebar** — `❝` hinter dem Dateinamen; Filter „mit Anmerkung" als 5. Option.
  Bewusst *kein* blaues Zeilenblau: die Zeilenfarbe trägt bereits den Status, und den
  ausgerechnet für die gesuchten Zeilen zu verlieren wäre ein schlechter Tausch.
  Das Flag fällt als Nebenprodukt aus `_image_status()` ab — ein zweiter
  `ReviewState.load` je Zeile hätte die Sidebar-Auffrischung verdoppelt.
* **`Auswertung → Zellflächen …`** (`areas.py` rechnet, `extras.py` zeigt) — n,
  Mittel, Median, SD, Min, Max je Zeitpunkt/Proband, aufklappbar auf die Einzelbilder,
  als CSV speicherbar. Rechnung auf einem Worker-Thread hinter `BusyDialog`.
  Gemessen an 20 TRIBAL-Bildern: 2097 Zellen, Mittel 5024,6 px², Median 5019,0,
  SD 730,9 — Mittel ≈ Median bestätigt die symmetrische Verteilung, SD/Mittel = 14,5 %
  passt zur dokumentierten Streuung von 0,90–1,09× Median.
* **Hilfetext** um das 4. Werkzeug ergänzt (dieselbe Stelle, die vorher *zu viele*
  Werkzeuge auflistete).

**Gates:** `tests/test_areas.py` (15 Checks) neu; 9/9 grün.
