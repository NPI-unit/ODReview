"""Einstellungen › Allgemein: "Über ErysReview" (version, changes, GitHub,
contact) and "Deinstallieren" (with a warning and two confirmations)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextBrowser, QVBoxLayout,
)

from .. import __version__
from .. import uninstall as un

GITHUB = "https://github.com/finn-dreisigacker/ErysReview"
CONTACT_NAME = "Finn Dreisigacker"
CONTACT_MAIL = "dreisigacker.finn@web.de"


def git_info(root: Path) -> str | None:
    """'634ec7f · 2026-10-07' when the folder is a git checkout, else None."""
    if not (root / ".git").exists():
        return None
    try:
        out = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%h · %cs"],
                             capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def release_date(root: Path) -> str | None:
    """Date of the newest version in CHANGELOG.md ('## 18.0.0 — 2026-10-07')."""
    p = root / "CHANGELOG.md"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") and "—" in line:
                return line.split("—", 1)[1].strip()
    except OSError:
        pass
    return None


def about_card(parent, P: dict, S: dict) -> QFrame:
    root = un.app_root()
    f = QFrame(); f.setObjectName("settingsCard")
    h = QHBoxLayout(f); h.setContentsMargins(16, 14, 16, 14); h.setSpacing(16)
    icon = QLabel()
    pm = QPixmap(str(root / "assets" / "icon.png"))
    if not pm.isNull():
        icon.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    icon.setAlignment(Qt.AlignTop)
    h.addWidget(icon)
    v = QVBoxLayout(); v.setSpacing(4)
    title = QLabel(f"<span style='font-size:17px; font-weight:700'>ErysReview</span>"
                   f"&nbsp;&nbsp;<span style='color:{P['fg_secondary']}'>{S['ab_version']} {__version__}</span>")
    v.addWidget(title)
    meta = []
    d = release_date(root)
    if d:
        meta.append(S["ab_released"].format(date=d))
    g = git_info(root)
    if g:
        meta.append(S["ab_git"].format(rev=g))
    if meta:
        m = QLabel("  ·  ".join(meta)); m.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        v.addWidget(m)
    contact = QLabel(f"{S['ab_contact']}: {CONTACT_NAME} · <a href='mailto:{CONTACT_MAIL}'>{CONTACT_MAIL}</a>")
    contact.setOpenExternalLinks(True); contact.setTextFormat(Qt.RichText)
    v.addWidget(contact)
    row = QHBoxLayout(); row.setSpacing(8)
    changes = QPushButton(S["ab_changes"]); changes.clicked.connect(lambda: ChangelogDialog(parent, S).exec())
    gh = QPushButton("GitHub ↗"); gh.setToolTip(S["ab_github_tip"])
    gh.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB)))
    mail = QPushButton(S["ab_write"]); mail.clicked.connect(
        lambda: QDesktopServices.openUrl(QUrl(f"mailto:{CONTACT_MAIL}?subject=ErysReview%20{__version__}")))
    for b in (changes, gh, mail):
        row.addWidget(b)
    row.addStretch(1)
    v.addSpacing(4); v.addLayout(row)
    h.addLayout(v, 1)
    return f


class ChangelogDialog(QDialog):
    def __init__(self, parent, S: dict):
        super().__init__(parent)
        self.setWindowTitle(S["ab_changes"])
        self.resize(640, 560)
        v = QVBoxLayout(self)
        tb = QTextBrowser(); tb.setOpenExternalLinks(True)
        try:
            tb.setMarkdown((un.app_root() / "CHANGELOG.md").read_text(encoding="utf-8"))
        except OSError:
            tb.setPlainText("CHANGELOG.md fehlt.")
        v.addWidget(tb)
        ok = QPushButton("OK"); ok.setProperty("class", "primary"); ok.clicked.connect(self.accept)
        v.addWidget(ok, 0, Qt.AlignRight)


def uninstall_card(parent, P: dict, S: dict) -> QFrame:
    f = QFrame(); f.setObjectName("settingsCard")
    v = QVBoxLayout(f); v.setContentsMargins(16, 14, 16, 14); v.setSpacing(8)
    v.addWidget(QLabel(f"<b>{S['un_title']}</b>"))
    txt = QLabel(S["un_text"]); txt.setWordWrap(True); txt.setStyleSheet(f"color: {P['fg_secondary']};")
    v.addWidget(txt)
    parent.un_model = QCheckBox(S["un_opt_model"]); parent.un_settings = QCheckBox(S["un_opt_settings"])
    v.addWidget(parent.un_model); v.addWidget(parent.un_settings)
    b = QPushButton(S["un_button"]); b.setObjectName("dangerBtn")
    b.setStyleSheet(f"QPushButton#dangerBtn {{ color: {P['accent_error']}; border: 1px solid {P['accent_error']}; }}")
    b.clicked.connect(lambda: start_uninstall(parent, S, parent.un_model.isChecked(), parent.un_settings.isChecked()))
    v.addWidget(b, 0, Qt.AlignLeft)
    return f


def start_uninstall(parent, S: dict, remove_model: bool, remove_settings: bool,
                    ask=None, launch=None, quit_app=None) -> bool:
    """Warning with the list → confirm → second confirm → script → quit.
    `ask(title, text, yes_label) -> bool`, `launch(script)` and `quit_app()`
    can be replaced (tests). Returns True if the uninstall was started."""
    from ..persistence import app_support_dir
    root, home = un.app_root(), Path.home()
    items = un.plan(root, home, app_support_dir(), remove_model, remove_settings)
    dec = S["qt_decimal_sep"]
    listing = "\n".join(f"  • {S[i.key]} — {un.fmt_size(i.size, dec)}\n     {i.path}" for i in items)
    total = un.fmt_size(sum(i.size for i in items), dec)
    keep = [S["un_keeps_images"], S["un_keeps_folder"].format(folder=root)]
    if not remove_model:
        keep.append(S["un_keeps_model"])
    if not remove_settings:
        keep.append(S["un_keeps_settings"])

    def default_ask(title, text, yes):
        box = QMessageBox(parent); box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title); box.setText(text)
        y = box.addButton(yes, QMessageBox.DestructiveRole)
        box.addButton(S["settings_cancel"], QMessageBox.RejectRole)
        box.setDefaultButton(box.buttons()[-1])            # Abbrechen is the default
        box.exec()
        return box.clickedButton() is y
    ask = ask or default_ask
    first = S["un_warn1"].format(list=listing, total=total, keep="\n".join(f"  • {k}" for k in keep))
    if not ask(S["un_title"], first, S["un_continue"]):
        return False
    if not ask(S["un_title"], S["un_warn2"], S["un_do"]):
        return False
    script = un.write_script(items, os.getpid())
    (launch or (lambda p: QProcess.startDetached("/bin/bash", [str(p)])))(script)
    (quit_app or _quit_all)()
    return True


def _quit_all() -> None:
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            w.done(0)
    for w in QApplication.topLevelWidgets():
        if w.isVisible():
            w.close()
    QApplication.quit()
