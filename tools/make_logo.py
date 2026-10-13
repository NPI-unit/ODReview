"""Draws the NKReview logo with Qt and writes every icon size the app needs.

    ./.venv/bin/python tools/make_logo.py        (writes assets/)

The mark: a DAB-brown cell with its segmentation contour, the three I₀
background fields next to it — the measurement in one picture — and "NK".
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient,
)

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def draw(size: int = 1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / 1024, size / 1024)

    # macOS icon grid: 824 px body, centred, continuous-corner-ish radius
    body = QRectF(100, 100, 824, 824)
    path = QPainterPath(); path.addRoundedRect(body, 186, 186)
    bg = QLinearGradient(body.topLeft(), body.bottomRight())
    bg.setColorAt(0, QColor("#24467A")); bg.setColorAt(1, QColor("#101D35"))
    p.fillPath(path, bg)
    p.setClipPath(path)

    # faint brightfield vignette
    vig = QRadialGradient(QPointF(470, 470), 520)
    vig.setColorAt(0, QColor(255, 255, 255, 34)); vig.setColorAt(1, QColor(255, 255, 255, 0))
    p.fillPath(path, vig)

    # the cell
    c, r = QPointF(468, 500), 248
    cell = QRadialGradient(QPointF(c.x() - 70, c.y() - 80), r * 1.25)
    cell.setColorAt(0.0, QColor("#E2A867"))
    cell.setColorAt(0.55, QColor("#B5713A"))
    cell.setColorAt(1.0, QColor("#6E3A14"))
    p.setPen(Qt.NoPen); p.setBrush(QBrush(cell))
    blob = QPainterPath()
    for k in range(73):                         # slightly irregular, like a real cell
        a = 2 * math.pi * k / 72
        rr = r * (1 + 0.035 * math.sin(3 * a + 0.6) + 0.025 * math.sin(5 * a + 1.9))
        q = QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))
        blob.moveTo(q) if k == 0 else blob.lineTo(q)
    blob.closeSubpath()
    p.drawPath(blob)
    # chromatin granules
    p.setBrush(QColor(80, 38, 10, 70))
    for x, y, s in ((-120, -40, 26), (-60, 110, 20), (90, 120, 24), (140, -20, 18), (30, -150, 16), (-150, 90, 14)):
        p.drawEllipse(QPointF(c.x() + x, c.y() + y), s, s)

    # segmentation contour
    pen = QPen(QColor("#9FD8FF"), 14); pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen); p.setBrush(Qt.NoBrush)
    contour = QPainterPath()
    for k in range(73):
        a = 2 * math.pi * k / 72
        rr = (r + 26) * (1 + 0.035 * math.sin(3 * a + 0.6) + 0.025 * math.sin(5 * a + 1.9))
        q = QPointF(c.x() + rr * math.cos(a), c.y() + rr * math.sin(a))
        contour.moveTo(q) if k == 0 else contour.lineTo(q)
    contour.closeSubpath()
    p.drawPath(contour)

    # three I₀ background fields
    p.setPen(QPen(QColor("#F4C772"), 13))
    for x, y in ((800, 300), (810, 560), (640, 820)):
        p.drawRoundedRect(QRectF(x - 44, y - 44, 88, 88), 14, 14)

    # NK
    f = QFont("Helvetica Neue", 1); f.setPixelSize(250); f.setWeight(QFont.Black)
    f.setLetterSpacing(QFont.PercentageSpacing, 94)
    p.setFont(f)
    rect = QRectF(c.x() - r, c.y() - r + 6, 2 * r, 2 * r)
    p.setPen(QColor(50, 20, 4, 110))
    p.drawText(rect.translated(0, 8), Qt.AlignCenter, "NK")
    p.setPen(QColor("#FFFFFF"))
    p.drawText(rect, Qt.AlignCenter, "NK")
    p.end()
    return img


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from nkreview.qt.app import unhide_qt_plugins
    unhide_qt_plugins()                     # macOS hides plugins inside .venv
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    ASSETS.mkdir(exist_ok=True)
    big = draw(1024)
    big.save(str(ASSETS / "icon.png"))
    for s in (256, 64):
        draw(s).save(str(ASSETS / f"icon_{s}.png"))
    # macOS .icns via iconutil
    iconset = ASSETS / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        draw(s).save(str(iconset / f"icon_{s}x{s}.png"))
        draw(2 * s).save(str(iconset / f"icon_{s}x{s}@2x.png"))
    if sys.platform == "darwin":
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")], check=True)
    # Windows .ico
    from PIL import Image
    Image.open(ASSETS / "icon.png").save(ASSETS / "icon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    for f in iconset.iterdir():
        f.unlink()
    iconset.rmdir()
    print("written:", sorted(p.name for p in ASSETS.iterdir()))
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
