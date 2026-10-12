"""The image view: pixmap + cell contours on a QGraphicsScene, with zoom/pan
and the three tools (select, draw a cell freehand, drag a background ROI)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView,
)

TOOL_SELECT, TOOL_DRAW, TOOL_ROI, TOOL_I0 = "select", "draw", "roi", "i0"


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


class ImageView(QGraphicsView):
    cellClicked = Signal(int)                 # 0 = background
    polygonDrawn = Signal(list)               # [[x, y], ...] image px
    roiDrawn = Signal(list)                   # [x0, y0, x1, y1]
    i0Clicked = Signal(float, float)          # image px, tool "I₀-Feld setzen"

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
        self._cell_items: dict[int, tuple[QGraphicsPathItem, QGraphicsSimpleTextItem]] = {}
        self._roi_item: QGraphicsRectItem | None = None
        self._field_items: list = []
        self.fields_visible = True
        self._tool = TOOL_SELECT
        self._drag_start: QPointF | None = None
        self._draw_pts: list[QPointF] = []
        self._draw_item: QGraphicsPathItem | None = None
        self._temp_rect: QGraphicsRectItem | None = None
        self._press_pos = None
        self.contours_visible = True
        self.colors = {"keep": "#1B7A4D", "gone": "#8A8F99", "sel": "#2161D9",
                       "warn": "#C77A12", "roi": "#2161D9"}

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
        for path_item, text in self._cell_items.values():
            self.scene().removeItem(path_item)
            self.scene().removeItem(text)
        self._cell_items.clear()
        self._labels = labels
        by_id = {r["zelle"]: r for r in rows}
        objs = ndi.find_objects(labels)
        f = QFont(); f.setPointSize(11); f.setBold(True)
        for cid, r in by_id.items():
            sl = objs[cid - 1] if cid - 1 < len(objs) else None
            if sl is None:
                continue
            p = QGraphicsPathItem(cell_path(labels[sl] == cid, sl[1].start, sl[0].start))
            p.setZValue(2)
            t = QGraphicsSimpleTextItem(self._caption(r))
            t.setFont(f)
            t.setFlag(QGraphicsSimpleTextItem.ItemIgnoresTransformations)
            t.setPos(sl[1].stop + 4, sl[0].start)
            t.setZValue(3)
            self.scene().addItem(p); self.scene().addItem(t)
            self._cell_items[cid] = (p, t)
        self.restyle(rows, selected)

    @staticmethod
    def _caption(r: dict) -> str:
        od = "–" if r["od_fiji"] is None else f'{r["od_fiji"]:.3f}'
        warn = " ⚠" if r["status"] == "ausgeschlossen" or r["debris_verdacht"] or r["randzelle"] else ""
        return f'#{r["zelle"]}  {od}{warn}'

    def restyle(self, rows: list[dict], selected: int) -> None:
        for r in rows:
            items = self._cell_items.get(r["zelle"])
            if not items:
                continue
            p, t = items
            if r["zelle"] == selected:
                col, width, style = self.colors["sel"], 2.5, Qt.SolidLine
            elif r["status"] == "verworfen":
                col, width, style = self.colors["gone"], 1.5, Qt.DashLine
            elif r["status"] == "ausgeschlossen":
                col, width, style = self.colors["warn"], 1.5, Qt.DashLine
            else:
                col, width, style = self.colors["keep"], 2.0, Qt.SolidLine
            pen = QPen(QColor(col), width, style)
            pen.setCosmetic(True)
            p.setPen(pen)
            t.setBrush(QBrush(QColor(col)))
            t.setText(self._caption(r))
            p.setVisible(self.contours_visible)
            t.setVisible(self.contours_visible)

    def set_roi(self, roi: list | None) -> None:
        if self._roi_item is not None:
            self.scene().removeItem(self._roi_item)
            self._roi_item = None
        if roi:
            x0, y0, x1, y1 = roi
            pen = QPen(QColor(self.colors["roi"]), 2, Qt.DashDotLine); pen.setCosmetic(True)
            self._roi_item = self.scene().addRect(QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized(), pen)
            self._roi_item.setZValue(4)

    def set_fields(self, fields: dict, size: int, selected: int = 0) -> None:
        """I₀ background fields: {cid: [[x, y], …]}; the selected cell's stronger."""
        for it in self._field_items:
            self.scene().removeItem(it)
        self._field_items = []
        half = size / 2
        for cid, pts in fields.items():
            sel = cid == selected
            pen = QPen(QColor(self.colors["warn"]), 2.0 if sel else 1.0)
            pen.setCosmetic(True)
            for x, y in pts:
                it = self.scene().addRect(QRectF(x - half, y - half, size, size), pen)
                it.setZValue(4 if sel else 3)
                it.setVisible(self.fields_visible)
                self._field_items.append(it)

    def set_fields_visible(self, on: bool) -> None:
        self.fields_visible = on
        for it in self._field_items:
            it.setVisible(on)

    def set_contours_visible(self, on: bool) -> None:
        self.contours_visible = on
        for p, t in self._cell_items.values():
            p.setVisible(on); t.setVisible(on)

    # ── tools ───────────────────────────────────────────────
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
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
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
