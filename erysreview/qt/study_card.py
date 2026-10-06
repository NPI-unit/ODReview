"""The card under the study button (mockup v3): study name, progress per
proband ID (finished & exported), folder and model status, and the ways to
edit or switch the study. A Qt popup — rounded and with a shadow, which the
Tk version could only approximate with a native menu."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QGraphicsDropShadowEffect, QGridLayout, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)

from ..study import StudyProgress


class StudyCard(QWidget):
    def __init__(self, parent, P: dict, S: dict, name: str, folder: str | None,
                 progress: StudyProgress | None, model_ok: bool | None, model_name: str,
                 actions: list[tuple[str, Callable[[], None] | None]]):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_DeleteOnClose)
        outer = QVBoxLayout(self); outer.setContentsMargins(16, 8, 16, 22)
        card = QFrame(); card.setObjectName("card"); card.setFixedWidth(380)
        sh = QGraphicsDropShadowEffect(card)
        sh.setBlurRadius(32); sh.setOffset(0, 12); sh.setColor(QColor(26, 34, 51, 60))
        card.setGraphicsEffect(sh)
        outer.addWidget(card)

        v = QVBoxLayout(card); v.setContentsMargins(18, 16, 18, 14); v.setSpacing(6)
        t = QLabel(name); t.setObjectName("cardTitle"); v.addWidget(t)
        if progress is None:
            v.addWidget(QLabel(S["study_menu_no_folder"]))
        else:
            v.addWidget(QLabel(S["study_menu_progress"].format(done=progress.done, total=progress.total)))
            bar = QProgressBar(); bar.setRange(0, max(1, progress.total))
            bar.setValue(progress.done); bar.setTextVisible(False)
            v.addWidget(bar)
            legend = QLabel(S["study_menu_rest"].format(wip=progress.wip, open=progress.open))
            legend.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
            v.addWidget(legend)
        v.addWidget(self._sep())
        grid = QGridLayout(); grid.setHorizontalSpacing(12); grid.setVerticalSpacing(4)
        rows = [(S["qt_card_folder"], folder or "–", P["fg"])]
        if model_ok is not None:
            rows.append((S["qt_card_model"],
                         f"{model_name} {'✓' if model_ok else '⚠'}",
                         P["state_finished"] if model_ok else P["accent_warn"]))
        for r, (k, val, col) in enumerate(rows):
            kl = QLabel(k); kl.setStyleSheet(f"color: {P['fg_muted']};")
            vl = QLabel(val); vl.setStyleSheet(f"color: {col};"); vl.setWordWrap(True)
            grid.addWidget(kl, r, 0); grid.addWidget(vl, r, 1)
        v.addLayout(grid)
        v.addWidget(self._sep())
        h = QHBoxLayout(); h.setSpacing(8)
        for i, (label, fn) in enumerate(actions):
            b = QPushButton(label)
            if i == 0:
                b.setProperty("class", "primary")
            if fn is None:
                b.setEnabled(False); b.setToolTip(S["qt_later"])
            else:
                b.clicked.connect(lambda _c=False, f=fn: (self.close(), f()))
            h.addWidget(b)
        v.addLayout(h)

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame(); s.setObjectName("sep"); s.setFixedHeight(1)
        return s
