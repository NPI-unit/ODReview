# Windows-Kompatibilität und Klick-Performance — Änderungen und Messungen

Stand: 2026-08-24. Alle Zahlen gemessen auf **Windows 11, 1920×1080 bei 150 %
Skalierung** (`tk scaling` ≈ 2.0), miniforge3 / conda-forge, Python 3.12,
matplotlib 3.11.1, torch 2.13.0, cellpose 4.1.1.
Studiendaten: `D:/01_UNI/Bedrest/Vollblut`, 238 Bilder à **1200 × 1920 px**,
180–390 Zellen pro Bild, Canvas 1920 × 944 px.

Keine dieser Änderungen hat einen Plattform-Zweig. macOS läuft durch denselben
Code; wo sich das Verhalten dort überhaupt merklich ändert, steht es unten
ausdrücklich dabei.

---

## 1. Toolbar: „Rückgängig" war abgeschnitten

**Ursache.** `Toolbar.COMPACT_WIDTH = 1870` war eine auf macOS abgelesene
Pixelkonstante. Unter Windows bei 150 % Skalierung brauchen dieselben Labels
**2446 px**. Bei maximiertem Fenster (~1900 px Toolbar-Breite) lag die Zeile
knapp über der Schwelle, schaltete auf volle Labels — und schnitt sie ab. Die
kumulierten Breiten zeigen, warum es genau „Rückgängig" traf:

| Button | kumulierte Breite |
|---|---|
| ⛶ Zoom zurücksetzen | 1828 px (passt) |
| ↺ **Rückgängig** | 2006 px ← Kante bei ~1900 px liegt mitten drin |
| ⓘ Info | 2119 px (fällt raus) |

**Änderung.** `erysreview/toolbar.py` — Konstante entfernt. `_natural_full_width()`
summiert `winfo_reqwidth()` + Padding über die Kinder der Zeile, einmalig beim
ersten `<Configure>`, solange die Labels noch voll sind. Parameter-Banner und
Spacer bleiben ausgenommen: das Banner darf umbrechen statt zu schrumpfen und
soll die Schwelle nicht hochziehen.

**Wirkung auf macOS.** Die Messung ersetzt eine Schätzung derselben Größe. Die
Änderung geht nur in eine Richtung: volle Labels können in etwas schmaleren
Fenstern erscheinen als vorher, wenn sie dort tatsächlich passen. Abschneiden
kann nichts mehr — das ist die Invariante, die `tests/test_toolbar_labels.py`
jetzt prüft.

---

## 2. Klick auf eine Zelle: 575 ms → ~85 ms

Aufteilung eines Klicks *vorher*, auf den echten Daten:

| Anteil | vorher |
|---|---|
| Zelle finden + Overlay-Pixel patchen | 13 ms |
| Speichern + Sidebar | 65–590 ms (je nach Ordnergröße) |
| **matplotlib zeichnet die Figur neu** | **483–510 ms** |

### 2a. Dateisystem: O(Bilder im Ordner) pro Klick

**Ursache.** `app._save_review()` rief `refresh_statuses()` — eine Review-JSON
pro Bild **im ganzen Ordner** — und `refresh_current_group_counter()`, das die
aktuelle Gruppe ein zweites Mal von Platte las (`group_eval_summary()` und
danach `effective_eval_stems()` über dieselben Dateien).

Warum das unter Windows auffällt und unter macOS nicht: `tests/bench_stick.py`
kalibriert APFS mit **4,2–4,5 µs pro Dateisystem-Operation**. Auf NTFS/SSD
gemessen: **130–200 µs**, also 30–45×. Derselbe Code, dieselbe Anzahl
Operationen.

**Änderungen.**

- `erysreview/sidebar.py` — `refresh_current_group()` neu: fasst nur die Zeilen
  der bearbeiteten Gruppe an. Eine Bearbeitung kann außerhalb ihrer Gruppe keine
  Zeile ändern. Geschwister werden zusätzlich übersprungen, solange sich die
  Cap-Grenze nicht bewegt hat — nur dann ändert sich an ihnen das ⊘.
  `refresh_statuses()` bleibt für Batch, Ordner öffnen und Gruppenstatus.
- `erysreview/evaluation.py` — `group_eval()` liefert Summary **und** Stem-Set
  aus einem Durchlauf. `group_eval_summary()` delegiert dorthin, der Vertrag für
  Export, Overview, Stats und db ist unverändert.
- `erysreview/app.py` — `_save_review()` nutzt den gruppenlokalen Refresh.

| Bilder im Ordner | vorher | nachher |
|---|---|---|
| 90 | 114 ms | 52 ms |
| 300 | 197 ms | 67 ms |
| 780 | 617 ms | 88 ms |

Wichtiger als der Faktor: die Kosten hängen nicht mehr an der Ordnergröße.

### 2b. Redraw: ganze Figur für eine Zelle

**Ursache.** `_patch_cell()` schreibt korrekt nur die Pixel einer Zelle
(0,1 ms). Danach zeichnete `canvas.draw_idle()` die **ganze** Figur neu, und
matplotlib resampelt dabei alle drei bildgroßen `imshow`-Layer (Bild, Overlay,
Rand). Gemessen pro Layer:

| | ms |
|---|---|
| leere Achse (Overhead) | 5 |
| nur Bild-Layer | ~160 |
| nur Overlay-Layer | ~160 |
| nur Rand-Layer | ~160 |
| Zellnummern (207 Text-Artists) | ~0 |

Der Boden liegt bei **~110 ms pro Bild-Artist und ist von der Array-Größe fast
unabhängig** — auch ein 300×480-Array kostet 111 ms. Es ist die Ausgabefläche,
nicht die Quelle. Deshalb bringt es nichts, das Overlay kleiner zu machen.

Verworfene Varianten, jeweils gemessen:

| Variante | Redraw |
|---|---|
| heute | 510 ms |
| `resample=False` / `interpolation='none'` | 237 / 218 ms — nur der Overlay-Layer, ändert nichts am Boden |
| Overlay-Artist auf die Zell-Bbox clippen | 314 ms — **schlechter**, matplotlib resampelt trotzdem alles |
| Standard-Blitting (Hintergrund cachen, Overlay komplett neu) | 298 ms |
| **nur das Rechteck der Zelle neu aufbauen** | **11 ms** |

**Änderung.** `erysreview/canvas.py`:

- `_full_redraw()` — verwirft den Blit-Cache und zeichnet voll. **Alle** 14
  bisherigen `draw_idle()`-Stellen laufen jetzt hierüber: neues Bild, Zoom, Pan,
  Resize, Theme, Rand, Overlay ein/aus, Undo, Notizmarker.
- `_blit_cell(cid)` — der schnelle Pfad. Stellt die zuletzt gerenderte Fläche
  wieder her, baut **nur das Rechteck um die Zelle** neu auf und kopiert dieses
  Rechteck auf den Bildschirm.
- `_draw_after_cell_edit(cid)` — nutzt den schnellen Pfad, fällt bei jedem
  Zweifel auf `_full_redraw()` zurück.

Verwendet von: `_act_remove_toggle` (ein/aus), `_act_type` (Typ zuweisen),
`_act_add` (neue Zelle). **Nicht** von Undo — das kann Zellen *entfernen*, deren
Bbox dann nicht mehr existiert; siehe „Offen" unten.

Wie das Rechteck aufgebaut wird, und warum es stimmt:

- Drei kleine `animated`-Sub-Artists zeigen Ausschnitte von Bild, Overlay und
  Rand. `animated` ist zwingend, sonst malte ein normaler Redraw diese
  Briefmarken über die Figur. Der Bild-Sub-Artist teilt sich Colormap **und**
  Norm-Objekt mit dem echten Layer, damit die Farben identisch sind.
- Das Rechteck wird um `BLIT_PAD_PX = 48` Displaypixel erweitert, damit eine
  Nummer oder Kontur, die über ihre eigene Bbox hinausragt, drin liegt.
- Nachbarzellen, deren Bbox ins Rechteck ragt, bekommen Nummer, Kontur und
  Notizmarker neu gezeichnet — der Neuaufbau hätte sie sonst gelöscht.
- Der Cache wird **nach** jedem Blit neu genommen. Sonst würden sich zwei
  Bearbeitungen mit überlappenden Rechtecken gegenseitig zurücksetzen.
- Der Cache ist an `(Breite, Höhe, xlim, ylim)` gebunden. Passt das nicht mehr,
  zeichnet der erste Klick einmal voll und nimmt den Cache neu auf.

**Messung nachher**, echte Daten:

| | vorher | nachher |
|---|---|---|
| Klick gesamt (Canvas) | 499 ms | **33 ms** |
| davon `_patch_cell` | 13 ms | 12 ms |

Die 12 ms sind fast vollständig `set_data()` auf dem 9,2-MB-Overlay-Array. Das
bleibt drin, weil der große Artist für den nächsten vollen Redraw korrekt sein
muss; siehe „Offen".

**Zusammen mit 2a:** ein Klick kostet jetzt ~85–120 ms statt ~575 ms.

---

## 3. Tests

- `tests/test_blit_equivalence.py` (neu) — vergleicht den **Pixelpuffer** nach
  dem schnellen Pfad mit dem nach einem vollen `canvas.draw()`. Exakt gleich,
  nicht „ähnlich": der Fehlerfall hier ist ein plausibel aussehendes veraltetes
  Rechteck. Abgedeckt: einfacher Toggle, zweiter Toggle mit **überlappendem**
  Rechteck, dicht benachbarte Zellen, am Bildrand abgeschnittene Zellen, nach
  einem Zoom, bei ausgeblendetem Overlay, und das Hinzufügen einer Zelle.
- `tests/test_sidebar_refresh.py` (neu) — der gruppenlokale Refresh muss
  denselben Baum hinterlassen wie der ordnerweite Sweep, inklusive des Falls,
  in dem sich die Cap-Grenze bewegt und das ⊘ eines *Geschwisters* mitwandern
  muss.
- `tests/test_toolbar_labels.py` — drei Checks dazu: volle Labels erscheinen nie
  unterhalb der Breite, die sie brauchen (geprüft u. a. bei 1870 und 1920 px).

**Windows-Falle beim Testen:** Tests, die rendern, müssen aus einer
**aktivierten** conda-Umgebung laufen — so wie `run.bat` die App startet. Ein
direkter Aufruf von `envs\erys\python.exe` lässt `%CONDA_PREFIX%\Library\bin`
vom PATH weg; matplotlibs `_tkagg` killt den Prozess dann beim ersten echten
Draw (Exit `0xC06D007F`, kein Traceback). Das ist keine Eigenschaft des
getesteten Codes.

---

## 4. Offen

- **Undo** (`_act_undo`) zeichnet weiter voll (~630 ms). Beim Rückgängigmachen
  eines „add" verschwindet eine Zelle samt Artists, das Rechteck lässt sich aus
  der verschwundenen Bbox nicht mehr bilden. Machbar, wenn man die alte Bbox
  vorher in die History legt.
- **`set_data()` in `_patch_cell`** kostet ~12 ms pro Klick, nur damit der große
  Overlay-Artist für den nächsten vollen Redraw stimmt. Aufschieben ginge, ist
  aber gefährlich: ein Tk-Resize löst einen Redraw aus, der nicht durch
  `_full_redraw()` läuft, und würde dann ein veraltetes Overlay zeigen.
- **Bildwechsel** (`set_image`) kostet ~950 ms, davon der Großteil ein voller
  Redraw. Nicht gemessen aufgeschlüsselt.
- **Die verbleibenden ~65 ms** eines Klicks liegen jetzt wieder im
  Speichern/Sidebar-Teil, dominiert von `group_eval()` über die aktuelle Gruppe.
  Eine mtime-geschlüsselte In-Memory-Cache für `_group_rows` würde das auf
  Stats reduzieren — nicht gemacht, weil `evaluation.py` auch die Exporte
  speist und eine falsche Invalidierung dort Zahlen verfälschen würde.

---

## 5. Nachtrag macOS-Prüfung (2026-08-24)

Der schnelle Pfad ist jetzt **`EryCanvas.BLIT_ENABLED = sys.platform == "win32"`**
— die einzige Ausnahme von der Regel oben, dass keine dieser Änderungen einen
Plattform-Zweig hat.

**Warum.** `tests/test_blit_equivalence.py` schlägt auf macOS fehl. Gemessen im
Agg-Puffer, echte Studienbilder, Fenster 1400×900: **5712 Pixel weichen ab, bis
zu 103 von 255 je Kanal** — die bearbeitete Zelle wird nach dem Klick sichtbar
dunkler dargestellt und bleibt es bis zum nächsten vollen Redraw. Kein
Datenfehler; Zählungen, Statistik und Export sind unberührt.

**Was ausgeschlossen wurde**, jeweils nachgemessen: kein Resampling-Phasenproblem
(tritt auch bei ganzzahligem Maßstab 1,0 und 2,0 auf, dort 793 bzw. 3172 Pixel);
`alpha`, `zorder`, `interpolation` und `cmap` sind zwischen den großen Artists und
ihren Briefmarken identisch; `norm` ist sogar dasselbe Objekt; `image_norm` und
`_im_handle.get_array()` sind elementweise gleich. Die Ursache wurde **nicht**
gefunden.

**Warum abschalten statt suchen.** Der Pfad löst ein Windows-Problem: dort kostete
ein Klick 575 ms, hier ist er auch ohne ihn erträglich: **171 ms** für Klick
plus vollen Redraw auf 1400×900, gegen 0 abweichende Pixel danach. Auf macOS abgeschaltet kostet er also nichts, was auffiele. Unter
Windows bleibt er aktiv und weiter durch das Gate abgesichert.

**Der Test** überspringt auf abgeschalteten Plattformen nicht, sondern prüft die
dort geltende Eigenschaft: die Bearbeitung greift, und `_blit_bg` bleibt `None`,
der volle Pfad zeichnet also. Sonst würde der Plattform-Zweig unbemerkt verrotten.

**Ebenfalls angepasst:** `tests/test_toolbar_labels.py` legte sein `Tk()` ohne
feste Geometrie an. Das Fenster schrumpft dann auf seinen Inhalt, die Toolbarbreite
hängt an ihrem eigenen Kompaktzustand, und das echte `<Configure>` aus
`update_idletasks()` kippte die synthetische Umschaltung sofort zurück. In der App
kann das nicht passieren (`fill="x"` in einem Fenster, das der Nutzer bemisst) —
nachgemessen: kompakt unter 2053 px, volle Labels darüber, sauber in beide
Richtungen. Eine Zeile `root.geometry(...)` im Test genügt.

**Zur Schwelle selbst:** auf diesem Mac misst `_natural_full_width()` **2053 px**,
die alte Konstante war 1870. Die Erwartung oben („volle Labels können in etwas
schmaleren Fenstern erscheinen als vorher") trifft hier also nicht zu — sie
erscheinen erst später. Auf einem 1512-px-Laptop ändert sich nichts, beide Male
kompakt.
