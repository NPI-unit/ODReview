# Umbauplan: ErysReview von Tk auf Qt (PySide6) — macOS

Stand: 2026-10-06. Ziel-Look: Mockup v3 („Qt / macOS – alle Fenster“),
https://claude.ai/artifact/KMd5GkuruPik6y77cHzbd6. Look-Test: `~/Desktop/Apps/ErysQt-Test`.
Windows ist bewusst außen vor; die Logik bleibt aber plattformneutral, damit Windows später nur ein Test- und Installer-Thema ist.

## Ausgangslage

Die Trennung ist schon gut: **22 Module sind reine Logik ohne Tk** (Cellpose, Export, Pakete, Gruppierung, Persistenz, Statistik, Größenfilter, Datenbank …). Sie bleiben unverändert.

Tk steckt in 9 Modulen: `app.py`, `canvas.py`, `sidebar.py`, `detail_panel.py`, `toolbar.py`, `dialogs.py`, `extras.py`, `notebox.py`, `tkfixes.py`. Diese werden ersetzt.

## Konflikte mit der Logik

Stellen, an denen Logik und Tk verflochten sind. Sie müssen *vor* oder *beim* Umbau gelöst werden.

| # | Konflikt | Wo | Lösung |
|---|---|---|---|
| K1 | **Logik-Module importieren aus `canvas.py`**, das Tk und `FigureCanvasTkAgg` lädt: `export.py`, `image_state.py` und `sizefilter.py` ziehen dadurch Tk mit. | `canvas.py:49–262` (`CellInfo`, `compute_cell_infos`, `is_border_cell`, `OverlayColors`, `build_overlay_rgba`, `patch_overlay_cell`, `cell_contours`, `build_margin_rgba`, `normalize_image_for_display`) | In ein neues Modul `overlay.py` ohne UI verschieben. Reine Verschiebung, Tests (`test_overlay`, `test_blit_equivalence`) sichern sie ab. |
| K2 | **Bearbeitungslogik lebt im Bild-Widget**: Zelle hinzufügen/entfernen, Typ setzen, Rückgängig ändern `review` direkt in `EryCanvas`. | `canvas.py:511–625` (`_act_add`, `_act_remove_toggle`, `_act_type`, `undo`) | Eigene Klasse `ReviewEditor` (Modell + Rückgängig-Stapel), die das Bild-Widget nur aufruft. Danach ist das Widget austauschbar. |
| K3 | **Hintergrundarbeit über „Event-Pumpe“**: `run_in_thread_pumping` lässt Cellpose im Thread laufen und ruft dabei `widget.update()` in einer Schleife auf. Das ist ein Tk-Muster, in Qt fehleranfällig. | `dialogs.py:156`, Aufrufer in `app.py` (Batch, Export, Training, Cellpose) | Ein kleines `Task`-Interface (Start, Fortschritt, Abbruch, Ergebnis). In Qt: `QThread` + Signale. Die Arbeitsfunktionen selbst bleiben gleich. |
| K4 | **Rückfrage aus laufender Arbeit**: Der Paket-Import ruft mitten im Import `conflict_resolver(stem)` auf und erwartet eine Antwort (Konflikt-Dialog). | `package.py:200`, `extras.py:438` | Läuft heute im Hauptthread, also unproblematisch. Kommt der Import in einen Thread (K3), muss die Rückfrage blockierend in den Hauptthread wechseln (`BlockingQueuedConnection`). Alternativ: Konflikte vorab sammeln und einmal fragen (auch die bessere Bedienung). |
| K5 | **Zustand und Abläufe stecken in `EryApp`** (Ordner, Bilderliste, Index, aktueller Review, Gruppierung, Zeiterfassung, Autosave), gemischt mit Fensteraufbau. 1 586 Zeilen. | `app.py` | Vor dem Umbau einen UI-freien `AppController` herausziehen: Zustand + Aktionen (Bild wechseln, fertig markieren, exportieren …). Die Qt-Oberfläche ruft nur noch den Controller auf. |
| K6 | **Zeitgeber über Tk** (`after()`): Zeiterfassung mit Leerlauf-Pause, verzögertes Profil-Speichern, Tooltips, Spinner. 18 Stellen. | `app.py`, `dialogs.py`, `toolbar.py` | `QTimer`. Die Zeiterfassung gehört in den Controller (K5), der nur einen Takt von außen bekommt. |
| K7 | **Logik in Dialogen**: Auto-Vorschlag der zählenden Bilder (`_eval_compute`), Auswahlregeln und Zeitschätzung im Batch-Dialog, Zustand „bearbeitet/unbearbeitet“ im Export-Dialog, Dateinamen-Vorschau und Werte-Prüfung (`_clampf`) im Profil-Editor. | `extras.py:28`, `dialogs.py` (Batch, Export, Profil-Editor) | Jeweils in reine Funktionen auslagern (z. B. `batchplan.py`, `exportplan.py`, Parser-Vorschau zu `nameparser.py`) und testen. Dann bauen beide Oberflächen darauf auf. |
| K8 | **Einstellungen mit Tk-Bedeutung**: `window_w/h`, `sidebar_width`, `notebox_x/y/w/h`, `_safe_geometry`, und `theme.font()` liefert Tk-Schrift-Tupel (`"TkDefaultFont"`). | `persistence.py`, `theme.py`, `app.py` | Felder behalten (gleiche JSON-Datei, Tk- und Qt-Version laufen in der Übergangszeit parallel). Qt speichert Fenster zusätzlich per `saveGeometry`. Für Qt `theme.qss()` aus derselben Palette erzeugen. |
| K9 | **Tastenkürzel ohne Modifier** (1–5, F, O, R, Z, E, ←/→) sind an das ganze Fenster gebunden. In Qt würden sie auch beim Tippen in die Anmerkung oder ein Eingabefeld auslösen. Dazu teilt sich die Seitenleiste heute Pfeiltasten mit dem Hauptfenster. | `app.py:412–436`, `sidebar.py:166` | `QShortcut` mit `WidgetWithChildrenShortcut` auf die Bildansicht, Texteingaben schlucken Tasten. Ein eigener Test „Tippen in Anmerkung löst kein Werkzeug aus“. |
| K10 | **Leistung der Bildansicht**: Ein Klick zeichnet nur die betroffene Zelle neu (Blit-Cache, Agg-Region). Das wurde für große Bilder und USB-Sticks eigens optimiert. | `canvas.py:685ff`, `test_blit_equivalence` | Phase 1: matplotlib mit `QtAgg`-Backend, Blit-Logik bleibt, nur der Tk-Teil wird getauscht. Erst wenn das läuft, optional auf `QGraphicsView` mit `QImage`-Overlay wechseln. Messen gegen den heutigen Stand. |
| K11 | **Seitenleiste mit Teil-Aktualisierung** (nur die geänderte Gruppe neu, nicht der ganze Baum). | `sidebar.py`, `test_sidebar_refresh` | `QTreeView` + eigenes Modell. Die Regeln (Status, Filter, ❝-Marker) sind schon UI-frei formulierbar und kommen in ein Modul `sidebar_model.py`. |
| K12 | **Texte werden beim Aufbau festgeschrieben**: Jedes Fenster bekommt das Wörterbuch `S` einmal. Das Mockup verspricht Sprach- und Theme-Wechsel ohne Neustart. | alle UI-Module | Für den Start: Neustart-Hinweis wie heute beibehalten. Live-Wechsel später durch Neuaufbau der offenen Fenster. |
| K13 | **Neue Funktionen aus dem Mockup brauchen neue Logik**, die es noch nicht gibt. | — | Siehe „Neue Logik“ unten. |

## Stand Phase 0 (abgeschlossen 2026-10-06, Branch `ui-studien-v2`, nicht committet)

| Konflikt | Ergebnis |
|---|---|
| K1 | `overlay.py`: Zellgeometrie, Randregel, Farben, Overlay. `canvas.py` re-exportiert die Namen. |
| K2 | `editor.py` (`ReviewEditor`): hinzufügen, an/aus, Typ, Rückgängig. `canvas.py` zeichnet nur. |
| K3 | `tasks.py` (`Task`, `CancelFlag`, `run_sync`, `run_in_thread`). Cellpose, Export, Training, Flächen, Übersicht laufen als Task; Tk führt sie über `run_task_pumping` aus. |
| K5 | `controller.py` (`AppController`): Ordner, Bilder, aktuelles Bild, Review, Zeiterfassung, zuletzt geöffnet, Studienwechsel/-bearbeitung, Neuberechnung, Export-Auswahl, Statuszahlen (`status_counts`). `EryApp` leitet seinen Zustand an den Controller weiter. |
| K7 | `batch.py` (Auswahl, Zeitschätzung, beide Läufe, Wachhalten), `evaluation.inclusion_with_overrides`, `export.export_scope_rows`/`scope_preset`, `profile.LIMITS`/`clamp_field`, `nameparser.sample_filenames`. |

Absicherung: `test_no_tk_in_logic` (24 Logik-Module laden ohne Tk), `test_editor`, `test_controller`, `test_batch_plan`, `test_profile_rules`, `test_app_smoke` (echte Tk-App Ende zu Ende in einem Wegwerf-Einstellungsordner). Alle 21 Testdateien grün.

Bewusst noch in Tk: Zeitgeber (K6), Tastenkürzel (K9), Seitenleisten-Regeln (K11), Fenstergrößen (K8) — sie werden mit dem jeweiligen Qt-Fenster in Phase 1 ersetzt.

## Stand Phase 1 (Grundgerüst, Branch `qt-phase1`)

Start: `./run-qt.command` bzw. `python -m erysreview.qt`. Tk bleibt `run.command`.

- `canvas_core.py`: komplette Bildansicht ohne Tk; `canvas.py` (Tk) und `qt/canvas.py` sind dünne Hüllen.
- `qt/`: Hauptfenster, Werkzeugleiste A/C, Seitenleiste (Gliederung wie Tk, Regeln aus `sidebar_model.py`), Anmerkungs-Box, Studien-Karte, Stylesheet aus `theme.palette`, Task-Runner über `QThread`.
- Funktioniert: Studie laden, Ordner öffnen/neu einlesen/zuletzt verwendet, Bilder wechseln, alle 5 Werkzeuge, Typwahl (Liste oder Chips), Rückgängig, fertig, Auswertung umschalten, Anmerkungen, Overlay, Zoom, Cellpose für ein Bild, Zeiterfassung, Autosave.
- Noch nicht (Hinweis „folgt“): Studie bearbeiten/wechseln, Einstellungen (Phase 2); Stapel, Export, Info-Panel (Phase 3); Pakete, Statistik, Übersicht, Zellflächen (Phase 4).
- **macOS-Falle:** macOS markiert Dateien in `.venv` als versteckt; Qt lädt dann kein Plattform-Plugin. `qt/app.py: unhide_qt_plugins()` räumt das beim Start auf.
- Test: `test_qt_shell.py` (20 Checks, ohne Bildschirm, inkl. K9 und Bildschirmfotos nach `tests/_out/`).

## Stand Phase 2 (Studien & Einstellungen, Branch `qt-phase1`)

- `profile_diff.py`: ordnet jede Studien-Änderung ihrer Folge zu (Neuberechnung, Gruppierung, Zählung, Studie, Export, Darstellung) und rechnet Auswirkungen auf dem echten Ordner aus (Bilder mit Masken, Gruppenwechsel, nicht zugeordnet vorher → nachher).
- `qt/study_picker.py`: „Studie öffnen“ mit Karten (Ordner, Fortschritt pro ID, Modellstatus), Neu, Duplizieren, Umbenennen, Löschen, Standard. Erscheint beim Start, wenn noch keine Studie gewählt ist.
- `qt/settings.py`: ein Einstellungsfenster. Studien-Seiten (Bilder & Gruppierung mit anklickbaren Dateinamen-Teilen und Live-Prüfung, Segmentierung mit Modellstatus, Zelltypen, Größenfilter & Bildrand, Darstellung, Export & Statistik) wirken erst über „Speichern …“ mit Folgen-Sheet. Rechner-Seiten (Allgemein, Bearbeiten, Modelldateien) speichern sofort.
- Hauptfenster: Datei › Studie öffnen (⌘⇧O), Einstellungen (⌘,), Studien-Karte mit „Studie bearbeiten“/„Studie wechseln“.
- Noch offen: „Neuberechnung jetzt öffnen“ (braucht das Stapel-Fenster aus Phase 3), „Paket importieren“ (Phase 4), Sprach-/Theme-Wechsel ohne Neustart.
- Tests: `test_profile_diff.py` (7), `test_qt_settings.py` (19, mit Bildschirmfotos).

## Stand Phase 3 (Verarbeitung & Export) + Korrekturen

- `qt/processing.py`: Mehrere Bilder verarbeiten (Auswahl, Filter, erste N, bis Ziel, Mac wachhalten, Lauf-Ansicht mit Abbruch), Export (Gruppen, Voreinstellungen, „als fertig markieren“), Zählende Bilder wählen (Auswertung + Status), Bildinfo (ⓘ).
- Seitenleiste: Rechtsklick auf Proband → Zählende Bilder / alle fertig / alle offen. Datei › Mehrere Bilder (⌘B), Exportieren (⌘S, Taste S).
- Speichern-Sheet: „Neuberechnung jetzt öffnen“ öffnet das Stapel-Fenster (Standard folgt Rechner › Bearbeiten).
- `evaluation.load/save_group_selection`, `controller.status_counts` mit Anzahl je Typ.
- Theme: vollständige QPalette aus der App-Palette — Listen, Menüs, Rückfragen folgen dem gewählten Theme, auch wenn macOS dunkel ist. Zahlenfelder mit Pfeilen in Theme-Farbe. „Jetzt neu starten“ für Sprache/Theme/Schrift.
- Anmerkungs-Box 420 × 320, vergrößerbar, Größe gespeichert.
- Fehler behoben: Absturz/Hänger nach einem Stapel-Lauf (Aufräumen des Arbeits-Threads), Dialog-Feld `done` überschrieb `QDialog.done`.
- Test: `test_qt_processing.py` (15, simuliert dunkles macOS).

## Neue Logik, die das Mockup voraussetzt

- **Speichern mit Folgen-Vorschau**: Unterschied altes/neues Profil berechnen und je Änderung die Folge bestimmen (Neuberechnung / Gruppierung / nur Darstellung) inklusive Anzahl betroffener Bilder. Neues Modul `profile_diff.py`, gut testbar.
- **Fortschritt pro Proband-ID**: vorhanden (`study.py` auf Branch `ui-studien-v2`).
- **Spalte „Exportiert“** im Export-Dialog: aus derselben Regel wie `study.py` (Summary-CSV vorhanden).
- **„Genutzt von“** bei Modelldateien: alle Profile durchsuchen.
- **Paketgröße vorab schätzen**: Summe der Dateigrößen.
- **Dateinamen-Prüfung „412 von 412 erkannt“**: `group_images` auf den echten Ordner anwenden (vorhanden), Ergebnis zählen.
- **„Später entscheiden“ als Standard**: Einstellung vorhanden (`resegment_default`, Branch `ui-studien-v2`).

## Reihenfolge

Jede Phase endet mit einer lauffähigen App. Die Tk-Version bleibt bis Phase 5 der Standard.

**Phase 0 — Entflechten, noch in Tk (keine sichtbare Änderung).** K1, K2, K5, K7 lösen, K3 als `Task`-Interface vorbereiten. Alle bestehenden Tests müssen grün bleiben, neue Tests für `ReviewEditor`, `AppController` und die ausgelagerten Dialog-Regeln. *Das ist der wichtigste Schritt: Danach ist die Oberfläche austauschbar, ohne die Logik anzufassen.*

**Phase 1 — Qt-Grundgerüst.** Eigener Einstieg `python -m erysreview.qt`. Hauptfenster mit Toolbar A/C, Bildansicht (matplotlib `QtAgg`), Seitenleiste, Statuszeile, Anmerkung. Ordner öffnen, Bilder wechseln, Zellen bearbeiten, Rückgängig, Autosave. Gemeinsames Stylesheet aus `theme.py`.

**Phase 2 — Studien und Einstellungen.** „Studie öffnen“, das Einstellungsfenster mit Studien- und Rechner-Seiten, Speichern-Sheet mit Folgen-Vorschau (`profile_diff.py`).

**Phase 3 — Verarbeitung und Export.** Cellpose für ein Bild und im Stapel über `QThread` (K3), Fortschrittsfenster mit Abbruch, Export-Dialog, Zählende Bilder.

**Phase 4 — Pakete und Auswertung.** Paket erstellen/importieren inkl. Konflikt-Rückfrage (K4), Statistik mit Boxplot (matplotlib `QtAgg`, Code aus `extras.py:600ff` übernehmen), Übersicht, Zellflächen, Tastenkürzel.

**Phase 5 — Umstellen.** `run.command` startet die Qt-Version, Tk-Module und `tkfixes.py` entfallen, Tk-Tests werden durch Qt-Tests ersetzt (offscreen, gleicher Skript-Stil wie heute). Danach erst Windows.

## Risiken

- **Abhängigkeiten**: PySide6 (~250 MB) zusätzlich zu Cellpose/PyTorch im selben `.venv`. Vorab einmal gemeinsam installieren und Cellpose + Qt-Fenster zusammen starten.
- **Leistung der Bildansicht** (K10): vor Phase 1 Abschluss gegen die heutige Version messen, besonders auf USB-Stick.
- **Doppelbetrieb**: Tk- und Qt-Version teilen sich `settings.json`, Profile und Reviews. Neue Felder dürfen die Tk-Version nicht stören (unbekannte Felder werden dort ignoriert; das ist schon so).

## To-Do (offen, Stand 2026-10-06)

- [x] **Hinweis auf alte Werte** (Mockup v4, Vorschlag 1 + 3; umgesetzt, `test_qt_stale.py`; Neu rechnen setzt die Bewertung nach Rückfrage zurück): Cellpose-Knopf zeigt den Zustand des Bildes (ruhig / orange „alte Werte“ / blau „Rechnen“), Baum mit ⟳ am Bild und Zähler am Probanden, Filter „Alte Werte (n)“, Studien-Karte und Detail-Feld mit „n Bilder mit alten Werten“ → Stapel mit genau diesen Bildern. Der Text „Andere Parameter – neu rechnen?“ in der Werkzeugleiste entfällt.
- [x] **Eigene Tastenkürzel** (umgesetzt: Rechner › Tastenkürzel, `shortcuts.py`, `test_qt_shortcuts.py`): in Rechner › Bearbeiten jede Aktion neu belegen; Belegen per Klick in ein Feld und dann Taste drücken *oder* Maustaste klicken — auch die Seitentasten „Vor“/„Zurück“ der Maus (z. B. für nächstes/vorheriges Bild). Gespeichert pro Rechner.
- [x] **Kürzel eindeutig** (beim Belegen abgelehnt mit Name des Besitzers; beim Start aufgelöst): jede Taste und Maustaste darf nur einer Aktion zugeordnet sein; Doppelbelegung beim Belegen sofort melden (mit Angabe, wer sie schon hat) und beim Start prüfen. Test dafür (Erweiterung von `test_qt_shell`: „kein Menü-Kürzel überdeckt ein Werkzeug-Kürzel“).
- [x] **Phase 4:** Paket erstellen/importieren (mit Konflikt-Rückfrage; auch aus „Studie öffnen“), Statistik mit Boxplot, Übersicht mit Kennzahl-Kacheln, Zellflächen mit CSV, Übersicht als Excel, Trainingsdaten (`qt/analysis.py`, `test_qt_analysis.py`). Werkzeugleiste A ohne Gruppen-Überschriften, alles auf einer Ebene.
- [x] **Sprache/Theme/Schrift ohne Neustart** (`MainWindow.restyle`: Hauptfenster wird um dieselbe Sitzung neu aufgebaut, Einstellungen öffnen wieder auf derselben Seite mit ungespeicherten Änderungen; `test_qt_look.py`). Theme **„Wie System“** folgt dem macOS-Wechsel live; das gewählte Hell/Dunkel geht auch an macOS (`styleHints().setColorScheme`), damit Menüleisten-Menüs, Dateidialoge und Farbwähler mitziehen. Bildlaufleisten, Tabellenköpfe und Optionsknöpfe jetzt aus dem Theme.
- [x] **Assistent „Neue Studie“** (Mockup v5, `qt/new_study.py`, Logik `study_setup.py`, `test_qt_new_study.py`): Ordner & Name (Bilder zählen, Größe, Stick), Vorlage-Studie oder Standardwerte, Dateinamen mit erkannten Trennzeichen/Rollen + Probe an echten Dateien + Ein-Klick-Korrektur, Zelltypen & Cellpose mit Probelauf (misst den Durchmesser), Zusammenfassung, „Jetzt im Hintergrund“ startet den Stapel neben dem Hauptfenster.
- [x] **„Studie öffnen“ schnell auch auf langsamem Stick**: Fortschritt im Hintergrund gezählt und in `study_progress.json` zwischengespeichert; Fenster öffnet sofort.
- [x] **Export: Spalten „Fertig“ und „Exportiert“** (Datum), Vorauswahl „fertig, nicht exportiert“.
- [x] **Paket erstellen: Größe vorab** (Bilder/Masken/Prüfstand, „ohne Bilder“ live) und Warnung bei zu wenig Platz am Ziel.
- [ ] Offen: Standard-Studie heißt noch „TRIBAL (Default)“ (Dateiname = Schlüssel; Umbenennen braucht Migration) — klären.
- [x] **Phase 5 (2026-10-07):** `run.command` und `python -m erysreview` starten Qt; Tk-Oberfläche (app, canvas, dialogs, extras, sidebar, toolbar, notebox, detail_panel, tkfixes) und ihre Tests entfernt; Blit- und Chip-Test auf Qt umgestellt; `test_no_tk_in_logic` prüft jetzt das ganze Paket. Installation macOS (2026-10-07): PySide6-Essentials statt PySide6 (−885 MB), `requirements-lock-macos.txt` (41 Pakete, `tools/make_lock.py`), `install.command` mit vendor/wheels + vendor/models (offline-fähig, Neuinstallation aus dem Ordner 33 s), Selbsttest `tools/check_install.py`, keine Tk-/Homebrew-Tk-Pflicht mehr. Offen: Update-Prüfung in Qt verdrahten (Updater noch inaktiv), ungenutzte Tk-Texte in i18n aufräumen, Windows (`run.bat`/`install.bat`) testen.


## Stand 2026-10-06 (spät)

- [x] **Seitenleiste:** nicht gezählte Bilder (segmentiert, aber nicht im Ziel) tragen ⊘, sind ausgegraut und rutschen innerhalb des Probanden ans Ende (Reihenfolge: gezählt → noch nicht segmentiert → ⊘). ⟳ für alte Werte nur noch am Probanden. Hinweis: Die Pfeiltasten folgen weiter der Ordner-Reihenfolge, nicht der Baum-Reihenfolge — beobachten, ob das stört.
- [x] **Hilfsraster (explorativ):** Ansicht › Hilfsraster Aus / 2×2 … 5×5, Felder A1…E5, Taste G schaltet weiter, pro Rechner gespeichert. Offen: Wofür genau (systematisches Absuchen? Zählen je Feld? Feld als „erledigt“ abhaken?) — nach dem Ausprobieren entscheiden.

## Interrater (Mockup v6) — Stand: erster Teil umgesetzt

- [x] Import mit Wahl „abgleichen / übernehmen“, Masken-Prüfung je Bild (SHA-256), Speicherung `.erys_cache/raters/<Name>/` (`interrater.py`, `qt/interrater.py`, `test_interrater.py`).
- [x] Fenster Übereinstimmung (Auswertung-Menü): Kennzahlen, Heatmap, A gegen B mit 45°-Linie, Bland-Altman, ICC-Text, Export CSV + PNG. ICC gegen Shrout & Fleiss (1979) geprüft.
- [ ] Abgleich-Ansicht mit zwei gekoppelten Bildflächen (Board 3) und „nächster Unterschied“.
- [ ] „Neue Studie › Abgleich“ / blind (Board 2) — bisher genügt der Import in den offenen Ordner, keine eigene Studie nötig.
- [ ] Konsens-Bewertung, mehr als 2 Rater (Fleiss-κ), Literaturangaben im Fenster.

### Ursprüngliche Notizen

Ziel: Annas Paket mit meiner Bewertung abgleichen und die Übereinstimmung beziffern, ohne meine Bewertung anzufassen.

- **Einstieg:** „Paket importieren“ fragt neu: *mit meiner abgleichen (Interrater)* oder *in meine Bewertung übernehmen* (heute). Alternativ „Neue Studie › Abgleich“ — auch für Intra-Rater (ich selbst nach Abstand, blind).
- **Speicherort:** Annas Bewertungen getrennt in `.erys_cache/raters/<Name>/`; Abgleich-Studie = eigene Studie, verweist auf denselben Bilderordner und beide Bewertungen.
- **Regel: nur mit identischen Masken.** Einmal rechnen, dann per Paket verteilen; Anna bewertet ohne neu zu rechnen. Prüfung je Bild per Prüfsumme der Maske (+ Cellpose-Parameter). Weichen Masken ab, ist der Abgleich gesperrt bzw. die Bilder werden genannt und ausgeschlossen — keine Zuordnung über Überlappung. Zellnummern damit 1:1; manuell hinzugefügte Zellen zählen als „nur bei A“ / „nur bei B“.
- **Ansicht:** ein Fenster mit zwei gekoppelten Bildflächen (Zoom/Ausschnitt synchron), Unterschiede markiert, „nächster Unterschied“ per Taste; Umschalter A/B per Leertaste für kleine Unterschiede; Filter „nur Unterschiede“. Echte zwei Fenster später für zwei Bildschirme.
- **Darstellung Ergebnis:** Heatmap Zelltyp A×B in Blau (gleich, Diagonale) / Rot (abweichend, Anteil der Zeile); Streudiagramm Rater A gegen Rater B mit 45°-Linie (y = x); Bland-Altman mit Achsen, Gitter, Mittelwert und ±1,96 SD. ICC-Erklärtext im Fenster (Intra-/Inter-Rater-Reliabilität), Literatur/Referenzen folgen.
- **Maße:** Zelltyp Cohens κ (2 Rater) bzw. Fleiss-κ (≥ 3) mit Konfusionsmatrix; gezählt ja/nein als % Übereinstimmung; Gruppenwerte (% verändert, Zellzahl je Proband × Zeitpunkt) ICC(2,1) absolute Übereinstimmung + Bland-Altman. Export CSV je Zelle + Zusammenfassung.
- **Konsens (optional):** dritte Bewertung, Unterschiede durchgehen, je Zelle A oder B übernehmen.
- **Offene Fragen:** Anzahl Rater (2 oder mehr)? Gelten Annas oder meine „zählenden Bilder“? Muss Anna dieselbe App-Version nutzen? Rater-Namen im Export anonymisieren? Blind-Modus nötig?
