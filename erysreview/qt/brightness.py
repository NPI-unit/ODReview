"""Helligkeit & Kontrast (mockup): a large live preview of one selected
image — before | after split, wheel zooms, right button drags — with
brightness, contrast, gamma, an automatic stretch and a histogram; applied to
the selected images, the whole proband or every image of the study.

Display only (display_adjust.py): written as `display` into each image's
review file; the image file, Cellpose, counts and export are untouched."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView, QGridLayout,
    QHBoxLayout, QLabel, QPushButton, QRadioButton, QSlider, QVBoxLayout, QWidget,
)

from .. import display_adjust as da
from ..overlay import normalize_image_for_display
from ..persistence import ReviewState

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
    def __init__(self, P):
        super().__init__()
        self.P = P
        self.setFixedHeight(76)
        self.before = self.after = None

    def set_data(self, before: np.ndarray, after: np.ndarray):
        self.before, self.after = before, after
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(self.P["sidebar_bg"]))
        if self.before is None:
            return
        n = len(self.after)
        mx = max(1, int(max(self.after.max(), self.before.max())))
        w, h = self.width(), self.height() - 4
        bw = w / n
        p.setPen(Qt.NoPen); p.setBrush(QColor(self.P["accent_primary"]))
        for i, v in enumerate(self.after):
            bh = v / mx * h
            p.drawRect(QRectF(i * bw + 0.5, self.height() - bh, max(1.0, bw - 1), bh))
        p.setPen(QPen(QColor(self.P["fg_secondary"]), 1.5))
        pts = [QPointF(i * bw + bw / 2, self.height() - v / mx * h) for i, v in enumerate(self.before)]
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)


class BrightnessDialog(QDialog):
    """`selected`, `group`, `everything`: image paths per scope; the first of
    `selected` is previewed."""

    def __init__(self, parent, P, S, folder: Path, selected: list[Path], group: list[Path],
                 group_label: str, everything: list[Path]):
        super().__init__(parent)
        self.P, self.S, self.folder = P, S, folder
        self.scopes = {"sel": selected, "group": group, "all": everything}
        self.applied: dict[Path, dict | None] = {}
        self.setWindowTitle(S["br_title"])
        scr = (parent.screen() if parent is not None else None)
        geo = scr.availableGeometry() if scr is not None else None
        if geo is not None:
            self.resize(int(geo.width() * 0.9), int(geo.height() * 0.88))
        else:
            self.resize(1400, 900)
        first = selected[0]
        rv = ReviewState.load(folder, first.stem)
        self.state = dict(da.clean(rv.display) or da.DEFAULT)
        from ..cellpose_runner import load_image
        img = load_image(first)
        step = max(1, int(np.ceil(max(img.shape[:2]) / PREVIEW_MAX)))
        self.norm = normalize_image_for_display(img[::step, ::step])
        self.split = True

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        head = QFrame(); head.setObjectName("settingsFooter")
        hh = QHBoxLayout(head); hh.setContentsMargins(18, 12, 18, 12)
        t = QLabel(S["br_title"]); t.setStyleSheet("font-size: 17px; font-weight: 700;")
        chip = QLabel(S["br_n_images"].format(n=len(selected))); chip.setObjectName("badgeStudy")
        name = QLabel(first.name); name.setStyleSheet(f"color: {P['fg_secondary']};")
        hh.addWidget(t); hh.addWidget(chip); hh.addStretch(1); hh.addWidget(name)
        root.addWidget(head)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        self.scene = QGraphicsScene(self)
        self.pix = QGraphicsPixmapItem(); self.pix.setTransformationMode(Qt.SmoothTransformation)
        self.scene.addItem(self.pix)
        self.view = _View(self.scene)
        self.view.setStyleSheet(f"background: {P['canvas_bg']};")
        for text in (S["br_before"], S["br_after_lbl"]):
            lab = QLabel(text, self.view.viewport())
            lab.setStyleSheet("color: white; background: rgba(0,0,0,150); border-radius: 5px;"
                              " padding: 3px 8px; font-weight: 700; font-size: 11px; letter-spacing: 1px;")
            lab.adjustSize()
            self.view.labels.append(lab)
        body.addWidget(self.view, 1)

        side = QFrame(); side.setObjectName("settingsNav"); side.setFixedWidth(330)
        sv = QVBoxLayout(side); sv.setContentsMargins(16, 16, 16, 16); sv.setSpacing(12)
        grid = QGridLayout(); grid.setVerticalSpacing(4)
        self.sliders = {}
        for r, (key, lk, lo, hi, scale) in enumerate((("brightness", "br_brightness", -100, 100, 1),
                                                      ("contrast", "br_contrast", 50, 800, 1),
                                                      ("gamma", "br_gamma", 40, 250, 100))):
            lab = QLabel(f"<b>{S[lk]}</b>")
            val = QLabel(""); val.setAlignment(Qt.AlignRight)
            val.setStyleSheet(f"color: {P['fg_secondary']}; font-family: Menlo, monospace;")
            sl = QSlider(Qt.Horizontal); sl.setRange(lo, hi)
            sl.setValue(int(round(self.state[key] * scale)))
            sl.valueChanged.connect(lambda v, k=key, sc=scale: self._set(k, v / sc if sc != 1 else v))
            grid.addWidget(lab, 2 * r, 0); grid.addWidget(val, 2 * r, 1)
            grid.addWidget(sl, 2 * r + 1, 0, 1, 2)
            self.sliders[key] = (sl, val, scale)
        sv.addLayout(grid)
        row = QHBoxLayout()
        auto = QPushButton(S["br_auto"]); auto.clicked.connect(self.auto)
        reset = QPushButton(S["br_reset_values"]); reset.clicked.connect(self.reset)
        row.addWidget(auto); row.addWidget(reset)
        sv.addLayout(row)
        cap = QLabel(S["br_hist"].upper()); cap.setObjectName("caption"); sv.addWidget(cap)
        self.hist = _Histogram(P); sv.addWidget(self.hist)
        cap = QLabel(S["br_view"].upper()); cap.setObjectName("caption"); sv.addWidget(cap)
        seg = QFrame(); seg.setObjectName("segment")
        sh = QHBoxLayout(seg); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(0)
        vg = QButtonGroup(seg)
        for i, (mode, lk) in enumerate((("split", "br_split"), ("after", "br_after"))):
            b = QPushButton(S[lk]); b.setCheckable(True); b.setChecked(mode == "split")
            b.setProperty("first", i == 0); b.setProperty("last", i == 1); b.setMinimumHeight(30)
            b.setMinimumWidth(b.sizeHint().width() + 16)
            b.clicked.connect(lambda _c=False, m=mode: self._set_split(m == "split"))
            vg.addButton(b); sh.addWidget(b)
        sv.addWidget(seg, 0, Qt.AlignLeft)
        cap = QLabel(S["br_scope"].upper()); cap.setObjectName("caption"); sv.addWidget(cap)
        self.scope = QButtonGroup(side)
        for key, text in (("sel", S["br_scope_sel"].format(n=len(selected)) if len(selected) > 1 else S["br_scope_one"]),
                          ("group", S["br_scope_group"].format(group=group_label, n=len(group))),
                          ("all", S["br_scope_all"].format(n=len(everything)))):
            rb = QRadioButton(text); rb.setProperty("scope", key); rb.setChecked(key == "sel")
            self.scope.addButton(rb); sv.addWidget(rb)
        note = QLabel(S["br_note"]); note.setObjectName("checkLine"); note.setWordWrap(True)
        sv.addWidget(note)
        sv.addStretch(1)
        body.addWidget(side)
        w = QWidget(); w.setLayout(body)
        root.addWidget(w, 1)

        foot = QFrame(); foot.setObjectName("settingsFooter")
        fh = QHBoxLayout(foot); fh.setContentsMargins(18, 12, 18, 12); fh.setSpacing(10)
        hint = QLabel(S["br_hint"]); hint.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        cancel = QPushButton(S["settings_cancel"]); cancel.clicked.connect(self.reject)
        ok = QPushButton(S["br_apply"]); ok.setProperty("class", "primary"); ok.clicked.connect(self.apply)
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
        dec = self.S["qt_decimal_sep"]
        for key, (sl, val, scale) in self.sliders.items():
            v = self.state[key]
            val.setText(f"{v:+d}" if key == "brightness" else f"{int(v)} %" if key == "contrast"
                        else f"{v:.2f}".replace(".", dec))

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
        self.view.labels[1].setText(self.S["br_after_lbl"]); self.view.labels[1].adjustSize()
        self.view.place_labels()
        self.pix.setPixmap(QPixmap.fromImage(qi))
        self.scene.setSceneRect(self.pix.boundingRect())
        g = lambda a: np.histogram((a if a.ndim == 2 else a.mean(-1))[::3, ::3], bins=64, range=(0, 1))[0]
        self.hist.set_data(g(self.norm), g(after))

    # ── apply ──
    def chosen_scope(self) -> str:
        b = self.scope.checkedButton()
        return b.property("scope") if b is not None else "sel"

    def apply(self) -> None:
        value = da.clean(self.state)
        for path in self.scopes[self.chosen_scope()]:
            rv = ReviewState.load(self.folder, path.stem)
            rv.display = value
            rv.save(self.folder, path.stem)
            self.applied[path] = value
        self.accept()
