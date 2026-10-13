"""A splitter whose side panes can be dragged shut and brought back with an
arrow on the handle — so a pane is never lost.

Handle 1 (between the first and second widget) controls the first widget,
the last handle controls the last widget. Each handle draws its divider line
and a small tab with an arrow: click it to fold the pane away or bring it back
at its last width. Dragging works as usual.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSplitter, QSplitterHandle

TAB_W, TAB_H = 14, 54


class _ArrowHandle(QSplitterHandle):
    def __init__(self, orientation, parent: "PaneSplitter", side: str):
        super().__init__(orientation, parent)
        self.side = side                       # "first" or "last"
        self.setMouseTracking(True)
        self._hover = False
        self.setToolTip("Click the arrow to hide or show this panel")

    def _tab(self) -> QRectF:
        return QRectF((self.width() - TAB_W) / 2, (self.height() - TAB_H) / 2, TAB_W, TAB_H)

    def _collapsed(self) -> bool:
        return self.splitter().pane_collapsed(self.side)

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        line = pal.mid().color()
        p.setPen(QPen(line, 1))
        x = self.width() / 2
        p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        tab = self._tab()
        path = QPainterPath(); path.addRoundedRect(tab, 5, 5)
        fill = pal.highlight().color() if self._hover else pal.window().color()
        p.fillPath(path, fill)
        p.setPen(QPen(line, 1)); p.drawPath(path)
        # arrow points where a click moves the divider
        pointing_left = (self.side == "first") != self._collapsed()
        cx, cy, a = tab.center().x(), tab.center().y(), 3.5
        pts = ([QPointF(cx + a / 2, cy - a), QPointF(cx - a / 2, cy), QPointF(cx + a / 2, cy + a)] if pointing_left else
               [QPointF(cx - a / 2, cy - a), QPointF(cx + a / 2, cy), QPointF(cx - a / 2, cy + a)])
        col = pal.highlightedText().color() if self._hover else pal.text().color()
        p.setPen(QPen(col, 1.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF(pts))

    def mouseMoveEvent(self, e):
        hov = self._tab().adjusted(-4, -4, 4, 4).contains(e.position())
        if hov != self._hover:
            self._hover = hov
            self.setCursor(Qt.PointingHandCursor if hov else Qt.SplitHCursor)
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._tab().adjusted(-4, -4, 4, 4).contains(e.position()):
            self.splitter().toggle_pane(self.side)
            return
        super().mousePressEvent(e)


class PaneSplitter(QSplitter):
    """Horizontal splitter: first and last widget foldable, the middle one not."""
    paneToggled = Signal(str, bool)            # side, now collapsed

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.setHandleWidth(TAB_W + 2)
        self._last: dict[str, int] = {}
        self.splitterMoved.connect(lambda *_: self._refresh())

    def createHandle(self):
        # count() is the number of widgets before the one being added
        return _ArrowHandle(self.orientation(), self, "first" if self.count() <= 1 else "last")

    def addWidget(self, w):
        super().addWidget(w)
        # the handle in front of the last widget controls it; earlier ones the first
        for i in range(1, self.count()):
            h = self.handle(i)
            if isinstance(h, _ArrowHandle):
                h.side = "first" if i == 1 else "last"
        if self.count() >= 2:
            for i in range(1, self.count() - 1):
                self.setCollapsible(i, False)

    def _index(self, side: str) -> int:
        return 0 if side == "first" else self.count() - 1

    def pane_collapsed(self, side: str) -> bool:
        sizes = self.sizes()
        return bool(sizes) and sizes[self._index(side)] == 0

    def toggle_pane(self, side: str) -> None:
        i = self._index(side)
        sizes = self.sizes()
        mid = 1 if self.count() > 2 else (1 - i)
        if sizes[i] == 0:
            want = self._last.get(side) or max(self.widget(i).minimumSizeHint().width(), 260)
            give = min(want, max(sizes[mid] - 200, 120))
            sizes[i], sizes[mid] = give, sizes[mid] - give
        else:
            self._last[side] = sizes[i]
            sizes[mid] += sizes[i]; sizes[i] = 0
        self.setSizes(sizes)
        self._refresh()
        self.paneToggled.emit(side, sizes[i] == 0)

    def _refresh(self) -> None:
        for i in range(1, self.count()):
            self.handle(i).update()
