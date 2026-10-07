# Änderungen

## 18.0.0 — 2026-10-07

Neue Oberfläche (Qt) — die alte Tk-Oberfläche ist entfernt.

- **Neue Oberfläche** auf Qt/PySide6, gleiches Aussehen auf macOS und (später) Windows; Hell, Dunkel oder **Wie System**, Sprache und Schriftgröße wirken sofort.
- **Studie öffnen** mit Fortschritt je Proband; **Neue Studie** als Assistent in drei Schritten (Dateinamen werden erkannt, Probelauf misst den Zelldurchmesser).
- **Einstellungen** in einem Fenster: Studie (reist im Paket mit) und dieser Rechner; Speichern zeigt vorher die Folgen.
- **Hinweis auf alte Werte**: Masken, die mit anderen Cellpose-Werten gerechnet wurden, sind am Knopf, im Baum und im Filter markiert.
- **Seitenleiste**: nicht gezählte Bilder mit ⊘ am Ende des Probanden, ←/→ folgen der Liste; unten nur noch „Zellen / Ziel“ (rot unter, grün ab Ziel).
- **Info (I)**: Proband × Zeitpunkt und Bild nebeneinander.
- **Helligkeit & Kontrast**: Rechtsklick auf Bilder (Mehrfachauswahl) oder aufs Bild — großes Fenster mit Live-Vorschau, Automatisch, für ausgewählte Bilder, ganzen Probanden oder alle; nur Anzeige, reist im Paket mit.
- **Eigene Tastenkürzel** pro Rechner, auch Maus-Seitentasten; **Hilfsraster** (G); rechte Maustaste ziehen verschiebt das Bild.
- **Interrater / Intra-Rater**: Zweitbewertung aus einem Paket oder selbst (auch blind), Abgleich-Ansicht, Konsens, Cohens κ, Fleiss-κ, ICC(2,1), Bland-Altman — nur auf identischen Masken.
- **Export** zeigt „Fertig“ und „Exportiert“; **Paket erstellen** schätzt die Größe.
- **Installation (macOS)**: „ErysReview installieren“ mit Fenster, getestete Paketversionen, offline aus dem Ordner (vendor/), Selbsttest; Deinstallieren unter Einstellungen › Allgemein.
- **Nach Updates suchen** (Hilfe-Menü).
- Behoben: Beim Öffnen eines Ordners konnte die Bewertung des ersten Bildes überschrieben werden.

## 17.5.0 — 2026-08-24

Letzte Version mit Tk-Oberfläche.

- Anmerkungen zu Bildern und Zellen, Status-Reiter, Zellflächen.
- Robuster auf Wechseldatenträgern (USB-Sticks), schnellere Klicks bei vielen Bildern.
- Barrierefreiheit (Schriftgröße, Konturen je Zelltyp), Größenfilter, Modellauswahl, Trainingsdaten-Export.
- Windows: conda-forge-Installation, GPU (CUDA) für Cellpose, keine eingefrorene Oberfläche.
- Auswertung: Statistik mit Boxplot, Übersicht als Excel, Reporting-Datenbank (erys.db).
