"""The image view: pixmap + cell contours on a QGraphicsScene, with zoom/pan
and the three tools (select, draw a cell freehand, drag a background ROI)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsEllipseItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView,
)

TOOL_SELECT, TOOL_DRAW, TOOL_ROI, TOOL_I0, TOOL_NOTE = "select", "draw", "roi", "i0", "note"
# One meaning per colour; the legend under the image uses the same table.
CONTOUR_COLORS = {"keep": "#1B7A4D", "open": "#FFFFFF", "halo": "#2B2F36", "gone": "#8A8F99",
                  "sel": "#2161D9", "warn": "#C77A12", "roi": "#2161D9", "field": "#6A4FC4",
                  "note": "#6A4FC4"}
# Pen per state: (colour key, width, line style). Orange is solid and thick: the
# dashed thin line was hard to see.
CONTOUR_PENS = {"keep": ("keep", 2.5, Qt.SolidLine), "open": ("open", 2.5, Qt.SolidLine),
                "warn": ("warn", 3.0, Qt.SolidLine), "gone": ("gone", 1.5, Qt.DashLine),
                "sel": ("sel", 3.0, Qt.SolidLine)}


def contour_state(r: dict) -> str:
    if r["status"] == "discarded":
        return "gone"
    if r["status"] == "excluded" or (r.get("i0_warning") and not r.get("confirmed")):
        return "warn"
    return "keep" if r.get("confirmed") else "open"


def to_qimage(rgb: np.ndarray) -> QImage:
    a = np.ascontiguousarray(rgb[..., :3].astype(np.uint8))
    h, w, _ = a.shape
    return QImage(a.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def cell_path(mask: np.ndarray, x0: int, y0: int) -> QPainterPath:
    """Outline of a boolean mask (bbox crop at x0/y0) as a painter path."""
    from skimage.measure import find_contours
    path = QPainterPath()
    padded = np.pad(mask, 1).astype(np.uint8)
    for c in find_contours(padded, 0.5):
        pts = [QPointF(x + x0 - 1 + 0.5, y + y0 - 1 + 0.5) for y, x in c]
        if len(pts) > 2:
            path.moveTo(pts[0])
            for p in pts[1:]:
                path.lineTo(p)
            path.closeSubpath()
    return path


LEGEND = [("open", "counted, not confirmed yet"), ("keep", "confirmed by you"),
          ("warn", "I₀ warning – check, then confirm"), ("gone", "discarded by you"), ("sel", "selected"),
          ("note", "has a note"), ("field", "I₀ fields 1–3 of the selected"), ("mark", "comment (tool 4)")]


def legend_icon(kind: str, fill_alpha: float = 0.0) -> QPixmap:
    """Swatch drawn exactly like the contour it explains."""
    pm = QPixmap(24, 14); pm.fill(Qt.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
    c = CONTOUR_COLORS
    if kind == "field":
        p.setPen(QPen(QColor(c["field"]), 2)); p.drawRect(QRectF(8, 3, 8, 8))
    elif kind == "mark":
        for col, w in ((c["halo"], 3.5), (c["open"], 1.5)):
            p.setPen(QPen(QColor(col), w, Qt.DashLine)); p.drawEllipse(QRectF(6, 1, 12, 12))
    elif kind == "note":
        p.setPen(Qt.NoPen); p.setBrush(QColor(c["note"])); p.drawEllipse(QRectF(6, 1, 12, 12))
    else:
        key, width, style = CONTOUR_PENS[kind]
        if kind == "open":                                       # white needs its dark rim
            p.setPen(QPen(QColor(c["halo"]), width + 2)); p.drawRoundedRect(QRectF(2, 2, 20, 10), 4, 4)
        p.setPen(QPen(QColor(c[key]), width, style))
        if fill_alpha:
            f = QColor(c[kind]); f.setAlphaF(fill_alpha); p.setBrush(f)
        p.drawRoundedRect(QRectF(2, 2, 20, 10), 4, 4)
    p.end()
    return pm


class ImageView(QGraphicsView):
    cellClicked = Signal(int)                 # 0 = background
    polygonDrawn = Signal(list)               # [[x, y], ...] image px
    roiDrawn = Signal(list)                   # [x0, y0, x1, y1]
    i0Clicked = Signal(float, float)          # image px, tool "I₀-Feld setzen"
    menuRequested = Signal()                  # right click on the image
    noteClicked = Signal(int)                 # tool "Note": the cell clicked
    markDrawn = Signal(float, float, float)   # tool "Note" next to cells: a new comment circle x, y, r
    markClicked = Signal(int)                 # tool "Note" on an existing comment circle (index)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setFocusPolicy(Qt.NoFocus)        # hotkeys stay with the window
        self._pix = QGraphicsPixmapItem()
        self._pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene().addItem(self._pix)
        self._labels: np.ndarray | None = None
        self._cell_items: dict[int, list] = {}            # cid -> [halo, contour, note dot?]
        self._rows: list[dict] = []
        self._selected = 0
        self._roi_item: QGraphicsRectItem | None = None
        self._field_items: list = []
        self._mark_items: list = []
        self._marks: list = []
        self._mark_start: QPointF | None = None
        self._mark_temp = None
        self.fields_visible = True
        self._tool = TOOL_SELECT
        self._drag_start: QPointF | None = None
        self._draw_pts: list[QPointF] = []
        self._draw_item: QGraphicsPathItem | None = None
        self._temp_rect: QGraphicsRectItem | None = None
        self._press_pos = None
        self.contours_visible = True
        self.colors = CONTOUR_COLORS
        self.fill_kept, self.fill_alpha = True, 0.35   # display preference (app.json)

    # ── content ─────────────────────────────────────────────
    def set_pixmap(self, rgb: np.ndarray, fit: bool = False) -> None:
        self._pix.setPixmap(QPixmap.fromImage(to_qimage(rgb)))
        self.scene().setSceneRect(QRectF(self._pix.pixmap().rect()))
        if fit:
            self.fit()

    def fit(self) -> None:
        self.fitInView(self._pix, Qt.KeepAspectRatio)

    def zoom_to(self, x: float, y: float, size: float = 220) -> None:
        self.fitInView(QRectF(x - size / 2, y - size / 2, size, size), Qt.KeepAspectRatio)

    def set_cells(self, labels: np.ndarray, rows: list[dict], selected: int = 0) -> None:
        from scipy import ndimage as ndi
        for items in self._cell_items.values():
            for it in items:
                self.scene().removeItem(it)
        self._cell_items.clear()
        self._labels = labels
        objs = ndi.find_objects(labels)
        for r in rows:
            cid = r["cell"]
            sl = objs[cid - 1] if cid - 1 < len(objs) else None
            if sl is None:
                continue
            path = cell_path(labels[sl] == cid, sl[1].start, sl[0].start)
            halo = QGraphicsPathItem(path); halo.setZValue(1.9)       # dark rim under a white contour
            p = QGraphicsPathItem(path); p.setZValue(2)
            items = [halo, p]
            if r.get("has_note"):
                dot = QGraphicsEllipseItem(-7, -7, 14, 14)
                dot.setBrush(QBrush(QColor(self.colors["note"]))); dot.setPen(QPen(Qt.white, 1))
                dot.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations)
                dot.setPos(sl[1].stop, sl[0].start); dot.setZValue(5)
                items.append(dot)
            for it in items:
                self.scene().addItem(it)
            self._cell_items[cid] = items
        self.restyle(rows, selected)

    def set_fill(self, on: bool, alpha: float) -> None:
        self.fill_kept, self.fill_alpha = on, alpha
        self.restyle(self._rows, self._selected)

    def restyle(self, rows: list[dict], selected: int) -> None:
        """Contours carry no text: number and values are in the side panel."""
        self._rows, self._selected = rows, selected
        for r in rows:
            items = self._cell_items.get(r["cell"])
            if not items:
                continue
            halo, p = items[0], items[1]
            state = contour_state(r)
            key, width, style = CONTOUR_PENS["sel" if r["cell"] == selected else state]
            pen = QPen(QColor(self.colors[key]), width, style); pen.setCosmetic(True)
            p.setPen(pen)
            rim = QPen(QColor(self.colors["halo"]), width + 2); rim.setCosmetic(True)
            halo.setPen(rim)
            halo.setVisible(self.contours_visible and key == "open")
            if self.fill_kept and state == "keep":
                fill = QColor(self.colors["keep"]); fill.setAlphaF(self.fill_alpha)
                p.setBrush(QBrush(fill))
            else:
                p.setBrush(Qt.NoBrush)
            for it in items[1:]:
                it.setVisible(self.contours_visible)

    def set_roi(self, roi: list | None) -> None:
        if self._roi_item is not None:
            self.scene().removeItem(self._roi_item)
            self._roi_item = None
        if roi:
            x0, y0, x1, y1 = roi
            pen = QPen(QColor(self.colors["roi"]), 2, Qt.DashDotLine); pen.setCosmetic(True)
            self._roi_item = self.scene().addRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized(), pen)
            self._roi_item.setZValue(4)

    def set_fields(self, pts: list, size: int) -> None:
        """The selected cell's I₀ background fields, numbered 1, 2, 3 as in the side panel."""
        for it in self._field_items:
            self.scene().removeItem(it)
        self._field_items = []
        half = size / 2
        col = QColor(self.colors["field"])
        pen = QPen(col, 2.0); pen.setCosmetic(True)
        f = QFont(); f.setPointSize(11); f.setBold(True)
        for k, (x, y) in enumerate(pts, start=1):
            box = self.scene().addRect(QRectF(x - half, y - half, size, size), pen)
            num = QGraphicsSimpleTextItem(str(k)); num.setFont(f); num.setBrush(QBrush(col))
            num.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            num.setPos(x + half + 1, y - half - 2)
            self.scene().addItem(num)
            for it in (box, num):
                it.setZValue(4); it.setVisible(self.fields_visible)
                self._field_items.append(it)

    def set_fields_visible(self, on: bool) -> None:
        self.fields_visible = on
        for it in self._field_items:
            it.setVisible(on)

    def set_contours_visible(self, on: bool) -> None:
        self.contours_visible = on
        for it in self._mark_items:
            it.setVisible(on)
        self.restyle(self._rows, self._selected)

    # ── tools ───────────────────────────────────────────────
    def contextMenuEvent(self, e):
        self.menuRequested.emit()
        e.accept()

    def _mark_pens(self):
        rim = QPen(QColor(self.colors["halo"]), 4.5, Qt.DashLine); rim.setCosmetic(True)
        line = QPen(QColor(self.colors["open"]), 2.5, Qt.DashLine); line.setCosmetic(True)
        return rim, line

    def set_marks(self, marks: list) -> None:
        """Comment circles anywhere in the image: dashed white with a dark rim, numbered 1, 2, …"""
        for it in self._mark_items:
            self.scene().removeItem(it)
        self._mark_items, self._marks = [], list(marks)
        rim, line = self._mark_pens()
        f = QFont(); f.setPointSize(11); f.setBold(True)
        for k, m in enumerate(marks, start=1):
            x, y, r = float(m["x"]), float(m["y"]), float(m["r"])
            box = QRectF(x - r, y - r, 2 * r, 2 * r)
            under = self.scene().addEllipse(box, rim); over = self.scene().addEllipse(box, line)
            badge = QGraphicsEllipseItem(-9, -9, 18, 18)
            badge.setBrush(QBrush(QColor("#FFFFFF"))); badge.setPen(QPen(QColor(self.colors["halo"]), 2))
            badge.setFlag(QGraphicsEllipseItem.ItemIgnoresTransformations)
            badge.setPos(x + r * 0.71, y - r * 0.71)
            num = QGraphicsSimpleTextItem(str(k), badge); num.setFont(f)
            num.setBrush(QBrush(QColor(self.colors["halo"])))
            num.setPos(-num.boundingRect().width() / 2, -num.boundingRect().height() / 2)
            self.scene().addItem(badge)
            for it in (under, over, badge):
                it.setZValue(6); it.setVisible(self.contours_visible)
                self._mark_items.append(it)

    def mark_at(self, x: float, y: float) -> int:
        """Index of the comment circle at x, y (the smallest one if they overlap), else -1."""
        hits = [(float(m["r"]), k) for k, m in enumerate(self._marks)
                if (x - float(m["x"])) ** 2 + (y - float(m["y"])) ** 2 <= float(m["r"]) ** 2]
        return min(hits)[1] if hits else -1

    def cell_at(self, x: float, y: float) -> int:
        if self._labels is None:
            return 0
        h, w = self._labels.shape
        x, y = int(x), int(y)
        return int(self._labels[y, x]) if 0 <= x < w and 0 <= y < h else 0

    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self.setDragMode(QGraphicsView.ScrollHandDrag if tool == TOOL_SELECT else QGraphicsView.NoDrag)
        self.viewport().setCursor(Qt.OpenHandCursor if tool == TOOL_SELECT else Qt.CrossCursor)

    def wheelEvent(self, e):
        k = 1.0015 ** e.angleDelta().y()
        self.scale(k, k)

    def mousePressEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        if e.button() == Qt.LeftButton and self._tool == TOOL_DRAW:
            self._draw_pts = [pos]
            pen = QPen(QColor(self.colors["warn"]), 2); pen.setCosmetic(True)
            self._draw_item = self.scene().addPath(QPainterPath(pos), pen)
            self._draw_item.setZValue(5)
            return
        if e.button() == Qt.LeftButton and self._tool == TOOL_I0:
            self.i0Clicked.emit(pos.x(), pos.y())
            return
        if e.button() == Qt.LeftButton and self._tool == TOOL_NOTE:
            cid, k = self.cell_at(pos.x(), pos.y()), self.mark_at(pos.x(), pos.y())
            if cid:                                 # a cell wins, even inside a comment circle
                self.noteClicked.emit(cid)
            elif k >= 0:
                self.markClicked.emit(k)
            else:                                   # next to the cells: drag a comment circle
                self._mark_start = pos
                self._mark_temp = self.scene().addEllipse(QRectF(pos, pos), self._mark_pens()[1])
                self._mark_temp.setZValue(6)
            return
        if e.button() == Qt.LeftButton and self._tool == TOOL_ROI:
            self._drag_start = pos
            pen = QPen(QColor(self.colors["roi"]), 2, Qt.DashLine); pen.setCosmetic(True)
            self._temp_rect = self.scene().addRect(QRectF(pos, pos), pen)
            self._temp_rect.setZValue(5)
            return
        self._press_pos = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        pos = self.mapToScene(e.position().toPoint())
        if self._draw_item is not None:
            self._draw_pts.append(pos)
            path = QPainterPath(self._draw_pts[0])
            for p in self._draw_pts[1:]:
                path.lineTo(p)
            self._draw_item.setPath(path)
            return
        if self._temp_rect is not None:
            self._temp_rect.setRect(QRectF(self._drag_start, pos).normalized())
            return
        if self._mark_temp is not None:
            r = (pos - self._mark_start).manhattanLength()
            c = self._mark_start
            self._mark_temp.setRect(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._mark_temp is not None:
            c = self._mark_start
            r = self._mark_temp.rect().width() / 2
            self.scene().removeItem(self._mark_temp)
            self._mark_temp, self._mark_start = None, None
            self.markDrawn.emit(c.x(), c.y(), r if r >= 8 else 40.0)   # a plain click: default circle
            return
        if self._draw_item is not None:
            self.scene().removeItem(self._draw_item)
            self._draw_item = None
            pts = [[p.x(), p.y()] for p in self._draw_pts]
            self._draw_pts = []
            if len(pts) >= 5:
                self.polygonDrawn.emit(pts)
            return
        if self._temp_rect is not None:
            r = self._temp_rect.rect()
            self.scene().removeItem(self._temp_rect)
            self._temp_rect = None
            if r.width() >= 5 and r.height() >= 5:
                self.roiDrawn.emit([r.left(), r.top(), r.right(), r.bottom()])
            return
        super().mouseReleaseEvent(e)
        if self._tool == TOOL_SELECT and e.button() == Qt.LeftButton and self._press_pos is not None:
            moved = (e.position() - self._press_pos).manhattanLength()
            self._press_pos = None
            if moved < 4 and self._labels is not None:
                p = self.mapToScene(e.position().toPoint())
                x, y = int(p.x()), int(p.y())
                h, w = self._labels.shape
                self.cellClicked.emit(int(self._labels[y, x]) if 0 <= x < w and 0 <= y < h else 0)
        self.viewport().setCursor(Qt.OpenHandCursor if self._tool == TOOL_SELECT else Qt.CrossCursor)
