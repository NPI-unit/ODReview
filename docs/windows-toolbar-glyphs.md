# Toolbar-Symbole unter Windows — „?"-Kästen bei Exportieren und Cursor

Stand: 2026-08-24. Gemessen auf **Windows 11, 150 % Skalierung**
(`tk scaling` ≈ 2.0 mit der DPI-Awareness, die `app._enable_windows_dpi_awareness()`
beim Start setzt), Tk 8.6.13, miniforge3 / conda-forge.

Anders als die Änderungen in [`windows-performance-notes.md`](windows-performance-notes.md)
hat diese **einen Plattform-Zweig**: `sys.platform == "win32"`. Das ist Absicht.
macOS bleibt unverändert.

---

## 1. Symptom

Zwei Toolbar-Buttons zeigten unter Windows statt ihres Symbols einen Kasten mit
einem Fragezeichen darin:

- **Exportieren** — immer.
- **Cursor** — nur, solange das Werkzeug ausgewählt ist. Unausgewählt war das
  Symbol korrekt, was das Ganze zunächst zufällig aussehen ließ.

Alle übrigen Symbole (⟳ ◉ ⛶ ↺ ⓘ ❝) waren unauffällig.

---

## 2. Ursache

**Die Toolbar rendert nicht in der Schrift, die der Code annimmt.**

`theme.font()` baut den Tupel-Spec `("TkDefaultFont", size)`. Tk liest einen
Tupel aber als `{family size style}` — „TkDefaultFont" wird also als
*Familienname* interpretiert, nicht als der gleichnamige **benannte Font**. Eine
Familie dieses Namens gibt es nicht, also ersetzt Tk. Nachgemessen im laufenden
Fenster:

| Aufruf | `.actual("family")` |
|---|---|
| `tkfont.nametofont("TkDefaultFont")` | `Segoe UI` |
| `tkfont.Font(font=("TkDefaultFont", 11))` | **`Arial`** |

Arial enthält fast keines dieser Symbole selbst. Tk holt sie deshalb per
Font-Linking aus einer Symbolschrift — und dieser Link scheitert für die
**fette** Schnittform einiger Zeichen. Nur dann erscheint der „?"-Kasten.

Das erklärt, warum es genau zwei Buttons traf: es sind die einzigen, die fett
gesetzt werden.

| Button | ttk-Stil | fett? | Folge |
|---|---|---|---|
| Exportieren | `ErysPrimary.TButton` | immer | ⬇ U+2B07 immer als Kasten |
| Cursor | `ErysToolActive.TButton` | nur wenn aktiv | ↖ U+2196 nur im aktiven Zustand |
| Resegmentieren | `ErysAction.TButton` | nie | ⟳ U+27F3 bricht fett **auch**, fällt aber nicht auf |
| übrige Werkzeuge | `ErysTool` / `ErysToolActive` | teils | Symbole robust, siehe Tabelle unten |

Gemessen in fetten `ttk.Button`s bei der Schriftgröße der Toolbar:

| Zeichen | Block | normal | fett |
|---|---|---|---|
| ↖ U+2196 | Arrows | ok | ok in der Isolation, **Kasten in der App** |
| ⬇ U+2B07 | Misc Symbols and Arrows | ok | **Kasten** |
| ⟳ U+27F3 | Misc Mathematical Symbols-A | ok | **Kasten** |
| ⇓ U+21D3 | Arrows (WGL4) | ok | ok |
| ⇩ U+21E9 | Arrows | ok | ok |
| ↓ U+2193 | Arrows (WGL4) | ok | ok |
| ◤ U+25E4 | Geometric Shapes | ok | ok |
| ⛶ U+26F6 · ❝ U+275D · ⓘ U+24D8 · ◉ U+25C9 · ↺ U+21BA | — | ok | ok |

Randnotiz zu ↖: in der **App** war es reproduzierbar ein Kasten (aktives
Cursor-Werkzeug, Screenshot), in einem isolierten Testfenster mit demselben
Font-Spec und derselben DPI-Awareness dagegen nicht. Der Unterschied ließ sich
nicht abschließend einkreisen. Deshalb wurde der Ersatz nicht aus einer einzelnen
Messung gewählt, sondern aus der Klasse, die in **allen** Läufen stabil war.

---

## 3. Änderung

`erysreview/toolbar.py` — die bisherige Konstante `GLYPHS` ist in eine Basis-
und eine Windows-Tabelle aufgeteilt:

```python
_GLYPHS_BASE = { ... }          # unverändert, das ist der macOS-Satz

_GLYPHS_WINDOWS = {
    "cursor": "◤",              # U+25E4, ersetzt ↖ U+2196
    "export": "⇓",              # U+21D3, ersetzt ⬇ U+2B07
}

GLYPHS = dict(_GLYPHS_BASE)
if sys.platform == "win32":
    GLYPHS.update(_GLYPHS_WINDOWS)
```

Sonst nichts: `_build()`, die Kompakt-/Voll-Labels und die Tooltips lesen
weiterhin nur `GLYPHS[...]`.

**Warum diese beiden Ersatzzeichen:**

- **⇓ U+21D3** steht in **WGL4**, dem Zeichensatz, den Arial selbst mitbringt.
  Es gibt also gar keinen Font-Link, der scheitern könnte — das ist die
  belastbarste Zusicherung, die hier zu haben ist.
- **◤ U+25E4** liegt in *Geometric Shapes*. Alle gefüllten Zeichen aus diesem
  Block (◉, ◤) haben in jedem Lauf und in beiden Schnitten gerendert. Es liest
  sich als Zeiger nach links oben, also in derselben Richtung wie ↖.

**Warum ⟳ U+27F3 (Resegmentieren) trotzdem bleibt:** sein Button ist nie fett,
das Symbol ist korrekt. Ein Tausch wäre eine Änderung ohne sichtbaren Nutzen und
mit eigenem Risiko. Im Code steht als Warnung dabei, dass es fett bricht — wer
`ErysAction` fett macht, muss das Zeichen mittauschen.

---

## 4. Wirkung auf macOS

Keine. `sys.platform` ist dort nicht `"win32"`, `GLYPHS` ist byte-identisch mit
`_GLYPHS_BASE`, und das ist der alte Inhalt der Konstante.

---

## 5. Prüfung

- **Sichtprüfung.** Echte `Toolbar` unter Windows 11 gerendert, mit
  `set_active_tool("cursor")` (also im fetten Zustand) und eingeschalteter
  DPI-Awareness. Beide Kästen weg, alle übrigen Symbole unverändert.
- **`tests/test_toolbar_labels.py`** — 13/13 ok, ohne Anpassung am Test. Er
  liest `GLYPHS` nach dem Import, sieht also den plattform-aufgelösten Satz:

  ```
  every compact label is distinct (['1','2','3','❝','◤','⟳','◉','⛶','↺','ⓘ','⇓'])
  the cursor tool shows its glyph ('◤   Cursor')
  ```

---

## 6. Offen

- **Die eigentliche Ursache ist nicht behoben.** `theme.font()` liefert weiterhin
  einen Tupel, dessen Familienname ins Leere zeigt; die gesamte Windows-UI läuft
  damit in Arial statt in Segoe UI. Ein Fix wäre, die Familie einmal aus dem
  benannten Font zu holen:

  ```python
  tkinter.font.nametofont("TkDefaultFont").actual("family")
  ```

  Das würde die Symbol-Sonderbehandlung überflüssig machen — ändert aber
  sämtliche Textmetriken unter Windows und damit jede Breite, die daraus folgt
  (Toolbar-Schwelle, Sidebar, Dialoge). Bewusst nicht in dieser Änderung.
- Ob ⛶ U+26F6 und ❝ U+275D auf einem Windows **ohne** Segoe UI Symbol halten,
  ist nicht geprüft. Auf dieser Maschine rendern sie in beiden Schnitten.
