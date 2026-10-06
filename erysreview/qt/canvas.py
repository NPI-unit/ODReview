"""Qt front end of the image view. All drawing lives in canvas_core.CanvasCore;
this only supplies the widget and matplotlib's Qt FigureCanvas."""

from __future__ import annotations

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ..canvas_core import CanvasCore
from ..persistence import TOOL_ADD, TOOL_REMOVE, TOOL_TYPE

_CURSORS = {TOOL_ADD: Qt.CrossCursor, TOOL_REMOVE: Qt.ForbiddenCursor,
            TOOL_TYPE: Qt.PointingHandCursor}


class QtEryCanvas(CanvasCore, QWidget):
    # CanvasCore first: PySide6's QWidget.__init__ chains cooperatively to the
    # next class in the MRO, which must then be `object`, not CanvasCore
    # (that would be called without its arguments).
    def __init__(self, settings, profile, save_callback, parent=None, **kwargs):
        QWidget.__init__(self, parent)
        CanvasCore.__init__(self, settings, profile, save_callback, **kwargs)
        fc = FigureCanvasQTAgg(self.fig)
        # Clicks on the image must not steal keyboard focus from the window,
        # or the tool hotkeys would stop reaching it.
        fc.setFocusPolicy(Qt.NoFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(fc)
        self._attach_canvas(fc)

    def _widget_size(self) -> tuple[int, int]:
        return self.canvas.width(), self.canvas.height()

    def set_tool_cursor(self, tool: str) -> None:
        self.canvas.setCursor(_CURSORS.get(tool, Qt.ArrowCursor))
