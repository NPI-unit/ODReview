"""The installer window (mockup v7), off-screen, with stand-in scripts
instead of the real install.command: the same output lines, no installing.

A good run moves the step list and ends on 'Fertig' with the self-test rows;
the options reach the script as environment variables; a failing run ends
on the error page with the script's reason."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

CHECKS = 0
OUT = Path(__file__).resolve().parent / "_out"

GOOD = """#!/bin/bash
echo "[1/6] Python 3.12 suchen"; echo "      /opt/homebrew/bin/python3.12"
echo "[2/6] Eigene Python-Umgebung (.venv)"
echo "[3/6] Pakete im Ordner bereitstellen (vendor/wheels)"
echo "[4/6] Pakete installieren (getestete Versionen)"
echo "[5/6] Cellpose-Modell (cpsam)"
echo "[6/6] Selbsttest und ErysReview.app"
echo "CHECK ok python 3.12.13"; echo "CHECK ok package:numpy 2.4.6"; echo "CHECK ok qt PySide6 6.11.2"
echo "CHECK warn gpu none — CPU"; echo "CHECK ok app ErysReview 17.5.0"; echo "CHECK ok model:cpsam 1.2 GB"
echo "RESULT warn"
echo "env NO_APP=${ERYS_INSTALL_NO_APP:-0} DROP=${ERYS_INSTALL_DROP_VENDOR:-0}"
echo "##DONE"
"""
BAD = """#!/bin/bash
echo "[1/6] Python 3.12 suchen"
echo "[3/6] Pakete im Ordner bereitstellen (vendor/wheels)"
echo "##FAIL Kein Internet und vendor/wheels unvollständig."
exit 1
"""


def check(cond: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise SystemExit(f"FAIL: {label}")
    print(f"  ok  {label}")


def script(tmp: Path, name: str, body: str) -> str:
    p = tmp / name
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def run(w, app) -> None:
    from PySide6.QtCore import QProcess
    w.start()
    w.proc.waitForFinished(10000)
    for _ in range(20):
        app.processEvents()


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    import installer_gui as ig
    ig.online = lambda timeout=3.0: True
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["test"])
    ig.style_app(app)
    OUT.mkdir(exist_ok=True)

    w = ig.Installer([script(tmp, "good.sh", GOOD)])
    w.show()
    for _ in range(5):
        app.processEvents()
    check(w.stack.currentIndex() == 0 and w.pre["python"].state == "ok" and w.pre["disk"].state in ("ok", "fail"),
          "the start page shows the pre-checks")
    check("Rechtsklick › Öffnen" in w.findChildren(type(w.start_note))[-1].text()
          or any("Rechtsklick" in l.text() for l in w.findChildren(type(w.start_note))),
          "it explains the Gatekeeper warning (right-click › Öffnen)")
    w.grab().save(str(OUT / "installer_start.png"))
    w.opt_app.setChecked(False); w.opt_keep.setChecked(False)
    run(w, app)
    check(w.stack.currentIndex() == 2 and all(r.state == "ok" for r in w.rows), "a good run ends on 'Fertig', all steps ✓")
    rows = [(r.title.text(), r.state) for r in w.findChildren(ig.Row) if r.parent() is w.done_list.parentWidget()]
    check(("Grafikkarte", "warn") in rows and ("Cellpose-Modell", "ok") in rows,
          f"the self-test rows are shown, warnings as such ({rows})")
    check("NO_APP=1 DROP=1" in w.log.toPlainText(), "the options reach the script")
    w.grab().save(str(OUT / "installer_done.png"))
    w.close()

    w2 = ig.Installer([script(tmp, "bad.sh", BAD)])
    w2.show(); run(w2, app)
    check(w2.stack.currentIndex() == 3 and "Kein Internet" in w2.err_text.text(),
          "a failing run ends on the error page with the script's reason")
    check(w2.rows[0].state == "ok" and w2.rows[2].state == "run", "the step list shows where it stopped")
    check("##FAIL" in w2.err_log.toPlainText() and w2.err_log.isVisibleTo(w2),
          "the error page shows the log")
    w2.grab().save(str(OUT / "installer_error.png"))
    w2.retry()
    check(w2.stack.currentIndex() == 0 and all(r.state == "todo" for r in w2.rows), "'Erneut versuchen' starts over")
    w2.close()
    import subprocess
    probe = ("import os, sys; os.environ['QT_QPA_PLATFORM']='offscreen'; sys.path.insert(0, 'tools'); "
             "import installer_gui as ig; from PySide6.QtWidgets import QApplication; "
             "app = QApplication([]); ig.style_app(app); w = ig.Installer(['true']); "
             "print(','.join(m for m in ('numpy', 'cellpose', 'torch', 'erysreview.qt.app') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         cwd=str(Path(__file__).resolve().parent.parent))
    check(out.returncode == 0 and out.stdout.strip() == "",
          f"the window needs nothing but PySide6 (bootstrap) ({out.stdout.strip() or out.stderr.strip()[-120:]})")
    print(f"OK — {CHECKS} checks: the installer window follows install.command's output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
