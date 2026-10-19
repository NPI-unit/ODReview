"""The installer window (mockup v7): start → running → done / error.

Started by "ODReview installieren.app" once Python 3.12 and PySide6 are
there (the app's launcher takes care of that) — and ONLY those: nothing here
may import numpy, cellpose or the app's Qt modules (test_installer_gui checks). All the work is done by
install.command --yes; this window only shows it: "[n/6] …" lines move the
step list, "CHECK …" lines fill the result list, "##DONE" / "##FAIL …" end
it. One script, two faces — terminal and window always do the same.

    ./.venv/bin/python tools/installer_gui.py
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LOCK = ROOT / "requirements-lock-macos.txt"
WHEELS = ROOT / "vendor" / "wheels"
MODELS = ROOT / "vendor" / "models"
HOME_MODEL = Path.home() / ".cellpose" / "models" / "cpsam"
WHEELS_MB = 330          # what vendor/wheels holds when complete (measured: 321 MB)

STEPS = ["Python 3.12 suchen", "Eigene Python-Umgebung (.venv)", "Pakete laden", "Pakete installieren",
         "Cellpose-Modell (1,2 GB)", "Selbsttest & ODReview.app"]
CHECK_NAMES = {"python": "Python", "qt": "Oberfläche (Qt)", "gpu": "Grafikkarte", "app": "ODReview",
               "model:cpsam": "Cellpose-Modell"}
SYM = {"ok": ("✓", "#1B7A4D"), "warn": ("!", "#975E0C"), "fail": ("✕", "#B83A3A"), "run": ("◌", "#2161D9"),
       "todo": ("·", "#9AA4B2")}


# ─────────────────────────────────────────────
# Pre-checks (fast, no side effects)
# ─────────────────────────────────────────────
def lock_pins() -> list[tuple[str, str]]:
    out = []
    for line in LOCK.read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            n, v = line.strip().split("==", 1)
            out.append((n, v))
    return out


def wheels_missing() -> int:
    """How many locked packages have no wheel in vendor/wheels yet."""
    have = {p.name.lower() for p in WHEELS.glob("*.whl")} if WHEELS.is_dir() else set()
    miss = 0
    for n, v in lock_pins():
        stem = re.sub(r"[-_.]+", "_", n).lower() + "-" + v.lower() + "-"
        if not any(h.startswith(stem) for h in have):
            miss += 1
    return miss


def model_present() -> bool:
    return any(p.is_file() and p.stat().st_size > 100_000_000 for p in (HOME_MODEL, MODELS / "cpsam"))


def online(timeout: float = 3.0) -> bool:
    try:
        socket.create_connection(("pypi.org", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


def dir_mb(p: Path) -> float:
    try:
        return sum(f.stat().st_size for f in p.glob("*") if f.is_file()) / 1e6
    except OSError:
        return 0.0


# ─────────────────────────────────────────────
# Widgets
# ─────────────────────────────────────────────
class Row(QWidget):
    def __init__(self, title: str, state: str = "todo", detail: str = ""):
        super().__init__()
        h = QHBoxLayout(self); h.setContentsMargins(0, 3, 0, 3); h.setSpacing(10)
        self.dot = QLabel(); self.dot.setFixedSize(22, 22); self.dot.setAlignment(Qt.AlignCenter)
        self.title = QLabel(title)
        self.detail = QLabel(detail); self.detail.setStyleSheet("color: #5A6577; font-size: 12px;")
        h.addWidget(self.dot); h.addWidget(self.title, 1); h.addWidget(self.detail)
        self.set(state, detail)

    def set(self, state: str, detail: str | None = None) -> None:
        self.state = state
        sym, col = SYM[state]
        self.dot.setText(sym)
        self.dot.setStyleSheet(f"border: 1.5px solid {col}; border-radius: 11px; color: {col}; font-weight: 700;")
        self.title.setStyleSheet(f"font-weight: {700 if state == 'run' else 500};"
                                 f" color: {'#9AA4B2' if state == 'todo' else 'palette(text)'};")
        if detail is not None:
            self.detail.setText(detail)


def card() -> tuple[QFrame, QVBoxLayout]:
    f = QFrame(); f.setObjectName("settingsCard")
    v = QVBoxLayout(f); v.setContentsMargins(16, 10, 16, 10); v.setSpacing(0)
    return f, v


class Installer(QWidget):
    def __init__(self, command: list[str] | None = None):
        super().__init__()
        self.setWindowTitle("ODReview installieren")
        self.setFixedSize(760, 600)
        icon = ROOT / "assets" / "icon.png"
        self._icon = QPixmap(str(icon)) if icon.is_file() else QPixmap()
        if not self._icon.isNull():
            self.setWindowIcon(QIcon(self._icon))
        self.command = command or [str(ROOT / "install.command"), "--yes"]
        self.proc: QProcess | None = None
        self.checks: list[tuple[str, str, str]] = []
        self.fail_reason = ""
        self.cancelled = False
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.addWidget(self.stack)
        self.stack.addWidget(self._page_start())
        self.stack.addWidget(self._page_run())
        self.stack.addWidget(self._page_done())
        self.stack.addWidget(self._page_error())
        self._poll = QTimer(self); self._poll.timeout.connect(self._poll_download)
        QTimer.singleShot(0, self.precheck)

    # ── frame helpers ──
    def _head(self, v: QVBoxLayout, title: str, sub: str) -> QLabel:
        h = QHBoxLayout(); h.setSpacing(16)
        ic = QLabel()
        if not self._icon.isNull():
            ic.setPixmap(self._icon.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        h.addWidget(ic)
        col = QVBoxLayout(); col.setSpacing(2)
        t = QLabel(title); f = QFont(t.font()); f.setPointSizeF(f.pointSizeF() * 1.45); f.setBold(True); t.setFont(f)
        s = QLabel(sub); s.setStyleSheet("color: #5A6577;"); s.setWordWrap(True)
        col.addWidget(t); col.addWidget(s)
        h.addLayout(col, 1)
        v.addLayout(h)
        return s

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget(); v = QVBoxLayout(w); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(14)
        return w, v

    # ── 1 · start ──
    def _page_start(self) -> QWidget:
        w, v = self._page()
        from nkreview import __version__
        self._head(v, "ODReview installieren", f"Version {__version__} · macOS · Ordner: {ROOT}")
        c, cv = card()
        cap = QLabel("<b>Schon geprüft</b>"); cap.setContentsMargins(0, 0, 0, 4); cv.addWidget(cap)
        self.pre = {k: Row(t, "run", "…") for k, t in (("python", "Python 3.12"), ("vendor", "Pakete im Ordner (vendor/)"),
                                                       ("net", "Internet"), ("disk", "Speicherplatz"))}
        for r in self.pre.values():
            cv.addWidget(r)
        v.addWidget(c)
        self.opt_app = QCheckBox("ODReview auf den Schreibtisch legen"); self.opt_app.setChecked(True)
        self.opt_keep = QCheckBox("Pakete im Ordner behalten — für weitere Macs ohne Internet (~1,5 GB)")
        self.opt_keep.setChecked(True)
        v.addWidget(self.opt_app); v.addWidget(self.opt_keep)
        gk = QLabel("ⓘ <b>Falls macOS beim ersten Öffnen warnt</b> („Entwickler nicht verifiziert“): im Finder "
                    "<b>Rechtsklick › Öffnen</b> und noch einmal <b>Öffnen</b>. Die Installation nimmt danach die Warnung "
                    "vom ganzen Ordner, auch von ODReview.app.")
        gk.setWordWrap(True); gk.setObjectName("checkLine"); gk.setTextFormat(Qt.RichText)
        v.addWidget(gk)
        v.addStretch(1)
        foot = QHBoxLayout()
        self.start_note = QLabel("Deine Studien und Bewertungen bleiben unverändert.")
        self.start_note.setStyleSheet("color: #5A6577; font-size: 12px;")
        cancel = QPushButton("Abbrechen"); cancel.clicked.connect(self.close)
        self.go = QPushButton("Installieren"); self.go.setProperty("class", "primary"); self.go.setDefault(True)
        self.go.setEnabled(False); self.go.clicked.connect(self.start)
        foot.addWidget(self.start_note, 1); foot.addWidget(cancel); foot.addWidget(self.go)
        v.addLayout(foot)
        return w

    def precheck(self) -> None:
        self.pre["python"].set("ok", f"{sys.version.split()[0]}")
        miss = wheels_missing()
        has_model = model_present()
        self.pre["vendor"].set("ok" if not miss and has_model else "warn",
                               "alles da — geht ohne Internet" if not miss and has_model else
                               f"{miss} Pakete fehlen" + ("" if has_model else " · Modell fehlt"))
        net = online()
        need_net = bool(miss) or not has_model
        self.pre["net"].set("ok" if net else ("fail" if miss else "warn" if need_net else "ok"),
                            "verbunden" if net else ("nötig, aber nicht verbunden" if need_net else "nicht nötig"))
        need_gb = (WHEELS_MB / 1000 if miss else 0) + (1.2 if not has_model else 0) + 0.9
        free_gb = shutil.disk_usage(ROOT).free / 1e9
        self.pre["disk"].set("ok" if free_gb > need_gb + 0.5 else "fail",
                             f"{need_gb:.1f} GB nötig · {free_gb:.0f} GB frei".replace(".", ","))
        for r in self.pre.values():
            r.detail.adjustSize()
        blocked = any(r.state == "fail" for r in self.pre.values())
        self.go.setEnabled(not blocked)
        if blocked:
            self.start_note.setText("Bitte erst das Rote beheben — oder den Ordner von einem fertigen Mac kopieren.")
        else:
            mins = 1 if not miss else 4
            self.start_note.setText(f"Ca. {mins} Minute{'n' if mins > 1 else ''} · Studien und Bewertungen bleiben unverändert.")

    # ── 2 · running ──
    def _page_run(self) -> QWidget:
        w, v = self._page()
        self._head(v, "Wird installiert …", "Bitte das Fenster offen lassen.")
        c, cv = card()
        self.rows = [Row(t) for t in STEPS]
        self.bar = QProgressBar(); self.bar.setRange(0, WHEELS_MB); self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6); self.bar.hide()
        for i, r in enumerate(self.rows):
            cv.addWidget(r)
            if i == 2:
                cv.addWidget(self.bar)
        v.addWidget(c)
        self.details_btn = QPushButton("▾ Details"); self.details_btn.setFlat(True)
        self.details_btn.setStyleSheet("color: #2161D9; text-align: left; border: none;")
        self.details_btn.clicked.connect(self._toggle_details)
        v.addWidget(self.details_btn, 0, Qt.AlignLeft)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)      # shown: people want to see it work
        self.log.setStyleSheet("background: #1E232B; color: #C9D1DB; font-family: Menlo, monospace; font-size: 11px;")
        v.addWidget(self.log, 1)
        v.addStretch(0)
        foot = QHBoxLayout()
        self.run_note = QLabel(""); self.run_note.setStyleSheet("color: #5A6577; font-size: 12px;")
        self.cancel_btn = QPushButton("Abbrechen"); self.cancel_btn.clicked.connect(self.cancel)
        foot.addWidget(self.run_note, 1); foot.addWidget(self.cancel_btn)
        v.addLayout(foot)
        return w

    def _toggle_details(self) -> None:
        on = self.log.isHidden()
        self.log.setVisible(on)
        self.details_btn.setText(("▾" if on else "▸") + " Details")

    def start(self) -> None:
        self.stack.setCurrentIndex(1)
        env = QProcessEnvironment.systemEnvironment()
        if not self.opt_app.isChecked():
            env.insert("NK_INSTALL_NO_APP", "1")
        if not self.opt_keep.isChecked():
            env.insert("NK_INSTALL_DROP_VENDOR", "1")
        self.proc = QProcess(self)
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(str(ROOT))
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self._buf = ""
        self.proc.start(self.command[0], self.command[1:])

    def _read(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            self.handle_line(line)

    def handle_line(self, line: str) -> None:
        self.log.appendPlainText(line)
        m = re.match(r"^\[(\d)/6\]\s*(.*)", line)
        if m:
            n = int(m.group(1)) - 1
            for i, r in enumerate(self.rows):
                if i < n and r.state in ("todo", "run"):
                    r.set("ok", "")
            self.rows[n].set("run", "")
            self.run_note.setText(f"Schritt {n + 1} von 6")
            self._poll.stop(); self.bar.hide()
            if n == 2:
                self._poll.start(700)
            return
        if line.startswith("      ") and any(r.state == "run" for r in self.rows):
            cur = next(r for r in self.rows if r.state == "run")
            if cur is not self.rows[2] or not self._poll.isActive():
                cur.detail.setText(line.strip()[:70])
        c = re.match(r"^CHECK (ok|warn|fail) (\S+)\s*(.*)", line)
        if c:
            self.checks.append((c.group(1), c.group(2), c.group(3)))
        if line.startswith("##FAIL"):
            self.fail_reason = line[len("##FAIL"):].strip()
        if line.startswith("##DONE"):
            for r in self.rows:
                if r.state in ("todo", "run"):
                    r.set("ok")

    def _poll_download(self) -> None:
        mb = dir_mb(WHEELS)
        self.bar.show(); self.bar.setValue(min(WHEELS_MB, int(mb)))
        self.rows[2].detail.setText(f"{mb:.0f} von ~{WHEELS_MB} MB")

    def cancel(self) -> None:
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            self.cancelled = True
            self.proc.kill()

    def _finished(self, code: int, _status) -> None:
        self._poll.stop()
        if self._buf:
            self.handle_line(self._buf); self._buf = ""
        if code == 0 and not self.cancelled:
            self.show_done()
        else:
            self.show_error("Abgebrochen." if self.cancelled else (self.fail_reason or f"Fehler (Code {code})."))

    # ── 3 · done ──
    def _page_done(self) -> QWidget:
        w, v = self._page()
        self.done_sub = self._head(v, "Fertig — ODReview ist installiert", "Selbsttest bestanden")
        c, self.done_list = card()
        v.addWidget(c)
        self.done_note = QLabel(""); self.done_note.setObjectName("checkLine"); self.done_note.setWordWrap(True)
        v.addWidget(self.done_note)
        v.addStretch(1)
        foot = QHBoxLayout()
        lg = QLabel("Protokoll: install.log"); lg.setStyleSheet("color: #5A6577; font-size: 12px;")
        folder = QPushButton("Ordner zeigen"); folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT))))
        self.open_btn = QPushButton("ODReview öffnen"); self.open_btn.setProperty("class", "primary")
        self.open_btn.clicked.connect(self.open_app)
        foot.addWidget(lg, 1); foot.addWidget(folder); foot.addWidget(self.open_btn)
        v.addLayout(foot)
        return w

    def show_done(self) -> None:
        names = CHECK_NAMES
        n_pkg = sum(1 for s, n, _d in self.checks if n.startswith("package:") and s == "ok")
        self.done_list.addWidget(Row("Pakete", "ok", f"{n_pkg} geprüft · getestete Versionen"))
        for s, n, d in self.checks:
            if n in names:
                self.done_list.addWidget(Row(names[n], s, d[:60]))
        if self.opt_app.isChecked():
            self.done_list.addWidget(Row("ODReview.app", "ok", "auf dem Schreibtisch"))
        warns = [d for s, n, d in self.checks if s == "warn"]
        self.done_sub.setText("Selbsttest bestanden" + (" — mit Hinweisen" if warns else ""))
        self.done_note.setText("✓ Der Ordner enthält jetzt alles für weitere Macs: mitsamt vendor/ kopieren und dort "
                               "„ODReview installieren“ öffnen — ohne Internet." if self.opt_keep.isChecked()
                               else "✓ Installiert. Die Pakete im Ordner wurden wie gewählt wieder entfernt.")
        self.stack.setCurrentIndex(2)

    def open_app(self) -> None:
        app = Path.home() / "Desktop" / "ODReview.app"
        if self.opt_app.isChecked() and app.exists():
            QProcess.startDetached("open", [str(app)])
        else:
            QProcess.startDetached(str(ROOT / ".venv" / "bin" / "python"), ["-m", "nkreview"], str(ROOT))
        QTimer.singleShot(300, self.close)

    # ── 4 · error ──
    def _page_error(self) -> QWidget:
        w, v = self._page()
        self.err_sub = self._head(v, "Installation nicht abgeschlossen", "")
        self.err_text = QLabel(""); self.err_text.setWordWrap(True); self.err_text.setObjectName("staleBox")
        v.addWidget(self.err_text)
        hint = QLabel("Nichts an deinen Studien wurde verändert. Bereits geladene Pakete bleiben im Ordner — "
                      "ein neuer Versuch macht dort weiter.")
        hint.setWordWrap(True); hint.setStyleSheet("color: #5A6577;")
        v.addWidget(hint)
        cap = QLabel("<b>Protokoll</b> (die letzten Zeilen — vollständig in install.log)")
        v.addWidget(cap)
        self.err_log = QPlainTextEdit(); self.err_log.setReadOnly(True)
        self.err_log.setStyleSheet("background: #1E232B; color: #C9D1DB; font-family: Menlo, monospace; font-size: 11px;")
        v.addWidget(self.err_log, 1)
        foot = QHBoxLayout()
        logb = QPushButton("Protokoll öffnen")
        logb.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ROOT / "install.log"))))
        py = QPushButton("python.org öffnen")
        py.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://www.python.org/downloads/macos/")))
        retry = QPushButton("Erneut versuchen"); retry.setProperty("class", "primary"); retry.clicked.connect(self.retry)
        foot.addStretch(1); foot.addWidget(logb); foot.addWidget(py); foot.addWidget(retry)
        v.addLayout(foot)
        return w

    def show_error(self, reason: str) -> None:
        self.err_text.setText(reason)
        lines = self.log.toPlainText().splitlines()
        self.err_log.setPlainText("\n".join(lines[-80:]))
        self.err_log.verticalScrollBar().setValue(self.err_log.verticalScrollBar().maximum())
        self.stack.setCurrentIndex(3)

    def retry(self) -> None:
        self.cancelled = False; self.fail_reason = ""; self.checks = []
        for r in self.rows:
            r.set("todo", "")
        self.log.clear()
        self.stack.setCurrentIndex(0)
        self.precheck()


def style_app(app) -> None:
    """The app's own look (light/dark like the system) — not the user's
    settings: the installer must not read or write them."""
    app.setStyle("Fusion")
    from nkreview.qt.style import apply_look
    apply_look(app, "system")


def unhide_qt_plugins() -> None:
    """As nkreview.qt.app does at start (copied, not imported: that module
    needs numpy & co., and at this point only PySide6 is installed). macOS
    flags files inside .venv hidden; Qt's plugin loader then finds no
    platform plugin."""
    if sys.platform != "darwin":
        return
    import stat
    import PySide6
    for f in (Path(PySide6.__file__).parent / "Qt" / "plugins").rglob("*"):
        try:
            st = os.lstat(f)
            if st.st_flags & stat.UF_HIDDEN:
                os.chflags(f, st.st_flags & ~stat.UF_HIDDEN, follow_symlinks=False)
        except OSError:
            pass


def main() -> int:
    unhide_qt_plugins()
    app = QApplication.instance() or QApplication(sys.argv)
    style_app(app)
    cmd = os.environ.get("NK_INSTALLER_CMD")
    w = Installer(cmd.split("|") if cmd else None)
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
