"""Floating remark editor (Qt) — same behaviour as the Tk NoteBox:

one box over the canvas for whichever remark is selected; explicit saving
(an existing remark opens read-only, *Bearbeiten* unlocks it); closing with
unsaved text asks first; and keys typed into it stay inside — Qt gives a
focused text field the key before any window shortcut, so a `1` typed here
cannot switch tools (K9; asserted in tests/test_qt_shell.py).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QMessageBox,
    QPlainTextEdit, QPushButton, QSizeGrip, QVBoxLayout,
)
from PySide6.QtGui import QColor


class QtNoteBox(QFrame):
    MIN_W, MIN_H = 360, 260

    def __init__(self, parent, S: dict, on_save: Callable[[str | None, str], None],
                 on_closed: Callable[[], None] | None = None, settings=None):
        super().__init__(parent)
        self.S = S
        self.settings = settings
        self._on_save = on_save
        self._on_closed = on_closed or (lambda: None)
        self._target: str | None = None
        self._saved_text = ""
        self.setObjectName("notebox")
        # Remembered size (AppSettings.notebox_w/h, shared with the Tk box), but
        # never smaller than a comfortable writing area.
        w = max(self.MIN_W, int(getattr(settings, "notebox_w", 0) or 0), 420)
        h = max(self.MIN_H, int(getattr(settings, "notebox_h", 0) or 0), 320)
        self.setMinimumSize(self.MIN_W, self.MIN_H)
        self.resize(w, h)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28); shadow.setOffset(0, 10); shadow.setColor(QColor(26, 34, 51, 70))
        self.setGraphicsEffect(shadow)

        v = QVBoxLayout(self); v.setContentsMargins(12, 10, 12, 12); v.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel(""); self.title.setStyleSheet("font-weight: 600;")
        close = QPushButton("✕"); close.setFixedWidth(30); close.setAccessibleName("Schließen")
        close.clicked.connect(self.request_close)
        head.addWidget(self.title, 1); head.addWidget(close)
        v.addLayout(head)
        self.text = QPlainTextEdit(); self.text.setMinimumHeight(140)
        self.text.setPlaceholderText(S["note_hint"])
        v.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.btn_delete = QPushButton(S["note_delete"])
        self.btn_delete.setStyleSheet("border: none; color: #B83A3A;")
        self.btn_delete.clicked.connect(self._delete)
        self.btn_primary = QPushButton(S["note_save"]); self.btn_primary.setProperty("class", "primary")
        self.btn_primary.clicked.connect(self._on_primary)
        grip = QSizeGrip(self)                 # drag the corner to resize
        row.addWidget(self.btn_delete); row.addStretch(1); row.addWidget(self.btn_primary)
        row.addWidget(grip, 0, Qt.AlignBottom | Qt.AlignRight)
        v.addLayout(row)
        self.hide()
        self._drag: QPoint | None = None

    # ── public API (same as the Tk NoteBox) ──
    @property
    def visible(self) -> bool:
        return self.isVisible()

    def show_for_cell(self, cid: int, text: str) -> None:
        self._switch(str(cid), self.S["note_title_cell"].format(cid=cid), text)

    def show_for_image(self, text: str) -> None:
        self._switch(None, self.S["note_title_image"], text)

    def request_close(self) -> bool:
        """Close unless there is unsaved text the user wants to keep."""
        if not self.isVisible():
            return True
        if self._dirty() and not self._ask_discard():
            return False
        self.hide()
        self._on_closed()
        return True

    # ── internals ──
    def _switch(self, target: str | None, title: str, text: str) -> None:
        if self.isVisible() and self._dirty() and not self._ask_discard():
            return
        self._target, self._saved_text = target, text
        self.title.setText(f"❝ {title}")
        self.text.setPlainText(text)
        self._set_editing(not text)       # an existing remark opens read-only
        self.btn_delete.setVisible(bool(text))
        if not self.isVisible():
            p = self.parentWidget()
            self.move(max(8, p.width() - self.width() - 24), 24)
        self.show(); self.raise_()
        if not text:
            self.text.setFocus()

    def _dirty(self) -> bool:
        return self.text.toPlainText() != self._saved_text

    def _ask_discard(self) -> bool:
        return QMessageBox.question(
            self, self.S["note_discard_title"], self.S["note_discard_msg"],
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes

    def _set_editing(self, editing: bool) -> None:
        self.text.setReadOnly(not editing)
        self.btn_primary.setText(self.S["note_save"] if editing else self.S["note_edit"])

    def _on_primary(self) -> None:
        if self.text.isReadOnly():
            self._set_editing(True)
            self.text.setFocus()
            return
        text = self.text.toPlainText().strip()
        self._saved_text = text
        self.text.setPlainText(text)
        self._on_save(self._target, text)
        self.btn_delete.setVisible(bool(text))
        self._set_editing(False)

    def _delete(self) -> None:
        self._saved_text = ""
        self.text.setPlainText("")
        self._on_save(self._target, "")
        self.hide()
        self._on_closed()

    # drag by the header area
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 34:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            p = self.parentWidget()
            pos = e.globalPosition().toPoint() - self._drag
            self.move(max(0, min(pos.x(), p.width() - self.width())),
                      max(0, min(pos.y(), p.height() - self.height())))

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.settings is not None and self.isVisible():
            self.settings.notebox_w, self.settings.notebox_h = self.width(), self.height()
