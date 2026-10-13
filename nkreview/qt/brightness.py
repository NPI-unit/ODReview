"""Brightness & contrast (taken over from ErysReview): a large live preview of
the image — before | after split, wheel zooms, right button drags — with
brightness, contrast, gamma, an automatic stretch and a histogram; applied to
this image, every image of the subject, or every image of the study.

Display only (display_adjust.py): written as `display` into each image's
review file. The image file, Cellpose, the I₀ fields and every OD value are
untouched."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget,
)

from .. import display_adjust as da
from ..state import Review, load_image
from ..study import ImageInfo

PREVIEW_MAX = 1800          # longest side of the preview (speed; it is a preview)


def to_qimage(a: np.ndarray) -> QImage:
    """0…1 float (H,W) or (H,W,3) → QImage (a copy, owns its buffer)."""
    u = np.ascontiguousarray(np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8))
    if u.ndim == 2:
        h, w = u.shape
        return QImage(u.data, w, h, w, QImage.Format_Grayscale8).copy()
    u = np.ascontiguousarray(u[..., :3])
    h, w = u.shape[:2]
    return QImage(u.data, w, h, 3 * w, QImage.Format_RGB888).copy()


def normalize(img: np.ndarray) -> np.ndarray:
    """The image as 0…1 for display (8-bit scale, as the app shows it)."""
    return np.clip(img[..., :3].astype(np.float32) / 255.0, 0.0, 1.0)


class _View(QGraphicsView):
    """Wheel zooms around the mouse, right (or middle) button drags."""

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self._drag = None
        self.user_moved = False          # until the user zooms or drags, the image fits the view
        self.labels: list[QLabel] = []

    def wheelEvent(self, e):
        f = 1.18 if e.angleDelta().y() > 0 else 1 / 1.18
        self.user_moved = True
        self.scale(f, f)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if not self.user_moved:
            self.fit()
        self.place_labels()

    def showEvent(self, e):
        super().showEvent(e)
        if not self.user_moved:
            self.fit()
        self.place_labels()

    def place_labels(self):
        """BEFORE / AFTER sit at fixed screen positions — they must not grow with the zoom."""
        if len(self.labels) == 2:
            l, r = self.labels
            l.move(12, 12)
            r.move(self.viewport().width() - r.width() - 12, 12)

    def mousePressEvent(self, e):
        if e.button() in (Qt.RightButton, Qt.MiddleButton):
            self._drag = e.position()
            self.user_moved = True
            self.setCursor(Qt.ClosedHandCursor)
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            d = e.position() - self._drag
            self._drag = e.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(d.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(d.y()))
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self.unsetCursor()
            return
        super().mouseReleaseEvent(e)

    def contextMenuEvent(self, e):
        e.accept()               # right button is for dragging here

    def fit(self):
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)


class _Histogram(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(76)
        self.before = self.after = None

    def set_data(self, before: np.ndarray, after: np.ndarray):
        self.before, self.after = before, after
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        pal = self.palette()
        p.fillRect(self.rect(), pal.alternateBase().color())
        if self.before is None:
            return
        n = len(self.after)
        mx = max(1, int(max(self.after.max(), self.before.max())))
        w, h = self.width(), self.height() - 4
        bw = w / n
        p.setPen(Qt.NoPen); p.setBrush(pal.highlight().color())
        for i, v in enumerate(self.after):
            bh = v / mx * h
            p.drawRect(QRectF(i * bw + 0.5, self.height() - bh, max(1.0, bw - 1), bh))
        p.setPen(QPen(pal.text().color(), 1.5))
        pts = [QPointF(i * bw + bw / 2, self.height() - v / mx * h) for i, v in enumerate(self.before)]
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)


class BrightnessDialog(QDialog):
    """`current` is previewed; `subject` and `everything` are the other scopes."""

    def __init__(self, parent, current: ImageInfo, subject: list[ImageInfo], subject_label: str,
                 everything: list[ImageInfo]):
        super().__init__(parent)
        self.scopes = {"one": [current], "subject": subject, "all": everything}
        self.applied: dict[str, dict | None] = {}          # str(path) -> value
        self.setWindowTitle("Brightness & contrast")
        scr = parent.screen() if parent is not None else None
        geo = scr.availableGeometry() if scr is not None else None
        if geo is not None:
            self.resize(int(geo.width() * 0.85), int(geo.height() * 0.85))
        else:
            self.resize(1300, 860)
        rv = Review.load(current.folder, current.path)
        self.state = dict(da.clean(rv.display) or da.DEFAULT)
        img = load_image(current.path)
        step = max(1, int(np.ceil(max(img.shape[:2]) / PREVIEW_MAX)))
        self.norm = normalize(img[::step, ::step])
        self.split = True

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        head = QFrame(objectName="settingsFooter")
        hh = QHBoxLayout(head); hh.setContentsMargins(18, 12, 18, 12)
        t = QLabel("Brightness & contrast"); t.setStyleSheet("font-size: 17px; font-weight: 700;")
        chip = QLabel("display only", objectName="badgeStudy")
        name = QLabel(current.path.name, objectName="caption")
        hh.addWidget(t); hh.addWidget(chip); hh.addStretch(1); hh.addWidget(name)
        root.addWidget(head)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.scene = QGraphicsScene(self)
        self.pix = QGraphicsPixmapItem(); self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pix)
        self.view = _View(self.scene)
        for text in ("BEFORE", "AFTER"):
            lab = QLabel(text, self.view.viewport())
            lab.setStyleSheet("color: white; background: rgba(0,0,0,150); border-radius: 5px;"
                              " padding: 3px 8px; font-weight: 700; font-size: 11px; letter-spacing: 1px;")
            lab.adjustSize()
            self.view.labels.append(lab)
        body.addWidget(self.view, 1)

        side = QFrame(objectName="settingsNav"); side.setFixedWidth(330)
        sv = QVBoxLayout(side); sv.setContentsMargins(16, 16, 16, 16); sv.setSpacing(12)
        grid = QGridLayout(); grid.setVerticalSpacing(4)
        self.sliders = {}
        for r, (key, label, lo, hi, scale) in enumerate((("brightness", "Brightness", -100, 100, 1),
                                                         ("contrast", "Contrast", 50, 800, 1),
                                                         ("gamma", "Gamma", 40, 250, 100))):
            lab = QLabel(f"<b>{label}</b>")
            val = QLabel(""); val.setAlignment(Qt.AlignRight)
            val.setStyleSheet("font-family: Menlo, monospace;"); val.setObjectName("caption")
            sl = QSlider(Qt.Horizontal); sl.setRange(lo, hi)
            sl.setValue(int(round(self.state[key] * scale)))
            sl.valueChanged.connect(lambda v, k=key, sc=scale: self._set(k, v / sc if sc != 1 else v))
            grid.addWidget(lab, 2 * r, 0); grid.addWidget(val, 2 * r, 1)
            grid.addWidget(sl, 2 * r + 1, 0, 1, 2)
            self.sliders[key] = (sl, val, scale)
        sv.addLayout(grid)
        row = QHBoxLayout()
        auto = QPushButton("Auto"); auto.setToolTip("Stretch the 1st–99th percentile to the full range")
        auto.clicked.connect(self.auto)
        reset = QPushButton("Reset values"); reset.clicked.connect(self.reset)
        row.addWidget(auto); row.addWidget(reset)
        sv.addLayout(row)
        sv.addWidget(QLabel("HISTOGRAM  – line before, bars after", objectName="caption"))
        self.hist = _Histogram(); sv.addWidget(self.hist)
        sv.addWidget(QLabel("VIEW", objectName="caption"))
        seg = QFrame(objectName="segment")
        sh = QHBoxLayout(seg); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(0)
        vg = QButtonGroup(seg)
        for i, (mode, text) in enumerate((("split", "Before | after"), ("after", "After only"))):
            b = QPushButton(text); b.setCheckable(True); b.setChecked(mode == "split")
            b.setProperty("first", i == 0); b.setProperty("last", i == 1); b.setMinimumHeight(30)
            b.setMinimumWidth(b.sizeHint().width() + 16)
            b.clicked.connect(lambda _c=False, m=mode: self._set_split(m == "split"))
            vg.addButton(b); sh.addWidget(b)
        sv.addWidget(seg, 0, Qt.AlignLeft)
        sv.addWidget(QLabel("APPLY TO", objectName="caption"))
        self.scope = QButtonGroup(side)
        for key, text in (("one", "this image"),
                          ("subject", f"all images of {subject_label} ({len(subject)})"),
                          ("all", f"every image of the study ({len(everything)})")):
            rb = QRadioButton(text); rb.setProperty("scope", key); rb.setChecked(key == "one")
            self.scope.addButton(rb); sv.addWidget(rb)
        note = QLabel("Only how the image looks on screen changes. Cellpose, the I₀ fields and every "
                      "OD value use the original image.", objectName="checkLine")
        note.setWordWrap(True)
        sv.addWidget(note)
        sv.addStretch(1)
        body.addWidget(side)
        w = QWidget(); w.setLayout(body)
        root.addWidget(w, 1)

        foot = QFrame(objectName="settingsFooter")
        fh = QHBoxLayout(foot); fh.setContentsMargins(18, 12, 18, 12); fh.setSpacing(10)
        hint = QLabel("Mouse wheel zooms · right button drags", objectName="caption")
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.setProperty("class", "primary"); ok.clicked.connect(self.apply)
        fh.addWidget(hint, 1); fh.addWidget(cancel); fh.addWidget(ok)
        root.addWidget(foot)

        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.timeout.connect(self.render)
        self._labels()
        self.render()
        QTimer.singleShot(0, lambda: (self.view.fit(), self.view.place_labels()))

    # ── values ──
    def _set(self, key: str, v) -> None:
        self.state[key] = v
        self._labels()
        self._timer.start(25)

    def _labels(self) -> None:
        for key, (_sl, val, _scale) in self.sliders.items():
            v = self.state[key]
            val.setText(f"{int(v):+d}" if key == "brightness" else f"{int(v)} %" if key == "contrast"
                        else f"{v:.2f}")

    def _push(self) -> None:
        for key, (sl, _val, scale) in self.sliders.items():
            sl.blockSignals(True); sl.setValue(int(round(self.state[key] * scale))); sl.blockSignals(False)
        self._labels(); self.render()

    def auto(self) -> None:
        self.state = da.auto(self.norm)
        self._push()

    def reset(self) -> None:
        self.state = dict(da.DEFAULT)
        self._push()

    def _set_split(self, on: bool) -> None:
        self.split = on
        self.render()

    # ── preview ──
    def render(self) -> None:
        after = da.apply(self.norm, self.state)
        shown = after
        if self.split:
            shown = after.copy()
            w = shown.shape[1] // 2
            shown[:, :w] = self.norm[:, :w]
        qi = to_qimage(shown)
        if self.split:
            p = QPainter(qi)
            p.setPen(QPen(QColor(255, 255, 255, 230), max(1, qi.width() // 500)))
            p.drawLine(qi.width() // 2, 0, qi.width() // 2, qi.height())
            p.end()
        self.view.labels[0].setVisible(self.split)
        self.view.place_labels()
        self.pix.setPixmap(QPixmap.fromImage(qi))
        self.scene.setSceneRect(self.pix.boundingRect())
        g = lambda a: np.histogram((a if a.ndim == 2 else a.mean(-1))[::3, ::3], bins=64, range=(0, 1))[0]
        self.hist.set_data(g(self.norm), g(after))

    # ── apply ──
    def chosen_scope(self) -> str:
        b = self.scope.checkedButton()
        return b.property("scope") if b is not None else "one"

    def apply(self) -> None:
        value = da.clean(self.state)
        for info in self.scopes[self.chosen_scope()]:
            rv = Review.load(info.folder, info.path)
            rv.display = value
            rv.save(info.folder, info.path)
            self.applied[str(info.path)] = value
        self.accept()
