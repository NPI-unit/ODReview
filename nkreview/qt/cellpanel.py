"""The cell panel: metric cards, I₀ status, OD map, radial profile, histogram
of the per-pixel OD — the distribution inside one cell (mockup "Auswertung")."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

OD_SCALE = 0.4            # colour scale of map and histogram: 0 … 0.4 OD


def _magma(v: np.ndarray) -> np.ndarray:
    from matplotlib import colormaps
    return (colormaps["magma_r"](np.clip(v, 0, 1))[..., :3] * 255).astype(np.uint8)


def _fmt(v, n=3) -> str:
    return "–" if v is None else f"{v:.{n}f}".replace(".", ",")


class Card(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        lay = QVBoxLayout(self); lay.setContentsMargins(10, 8, 10, 8); lay.setSpacing(0)
        self.t = QLabel(title, objectName="caption")
        self.v = QLabel("–"); f = self.v.font(); f.setPointSizeF(f.pointSizeF() * 1.6); f.setBold(True); self.v.setFont(f)
        self.s = QLabel("", objectName="caption")
        for w in (self.t, self.v, self.s):
            lay.addWidget(w)

    def set(self, value: str, sub: str = "") -> None:
        self.v.setText(value); self.s.setText(sub)


class ODMap(QWidget):
    """Per-pixel OD inside the cell, cropped to the cell."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(150, 130)
        self._img: QImage | None = None

    def set_data(self, od: np.ndarray | None, mask: np.ndarray | None) -> None:
        if od is None or mask is None or not mask.any():
            self._img = None
        else:
            rgb = _magma(od / OD_SCALE)
            rgb[~mask] = (232, 228, 220)
            h, w, _ = rgb.shape
            self._img = QImage(np.ascontiguousarray(rgb).data, w, h, 3 * w, QImage.Format_RGB888).copy()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if self._img is None:
            p.setPen(self.palette().mid().color())
            p.drawText(self.rect(), Qt.AlignCenter, "keine Zelle gewählt")
            return
        bar = 14
        area = QRectF(0, 0, self.width(), self.height() - bar - 4)
        s = min(area.width() / self._img.width(), area.height() / self._img.height())
        w, h = self._img.width() * s, self._img.height() * s
        p.drawImage(QRectF((area.width() - w) / 2, (area.height() - h) / 2, w, h), self._img)
        # colour bar
        y = self.height() - bar
        ramp = _magma(np.linspace(0, 1, 100)[None, :])
        bimg = QImage(np.ascontiguousarray(ramp).data, 100, 1, 300, QImage.Format_RGB888).copy()
        p.drawImage(QRectF(22, y + 3, self.width() - 60, 6), bimg)
        p.setPen(self.palette().text().color())
        f = p.font(); f.setPointSizeF(f.pointSizeF() * 0.8); p.setFont(f)
        p.drawText(QRectF(0, y, 20, bar), Qt.AlignRight | Qt.AlignVCenter, "0")
        p.drawText(QRectF(self.width() - 36, y, 36, bar), Qt.AlignLeft | Qt.AlignVCenter, f" {_fmt(OD_SCALE, 1)}")


class Profile(QWidget):
    """Radial profile: mean pixel OD from the centre to the membrane."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(150, 130)
        self._v: list = []

    def set_data(self, values: list) -> None:
        self._v = [v for v in values]
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); mid = self.palette().mid().color()
        L, R, T, B = 8, 8, 14, 18
        W, H = self.width() - L - R, self.height() - T - B
        p.setPen(QPen(mid, 1)); p.drawLine(L, T + H, L + W, T + H)
        f = p.font(); f.setPointSizeF(f.pointSizeF() * 0.8); p.setFont(f)
        p.setPen(fg)
        p.drawText(QRectF(L, T + H + 2, 80, 14), Qt.AlignLeft, "Zentrum")
        p.drawText(QRectF(L + W - 80, T + H + 2, 80, 14), Qt.AlignRight, "Membran")
        vals = [v for v in self._v if v is not None]
        if not vals:
            return
        top = max(max(vals) * 1.15, 0.05)
        n = len(self._v)
        pts = [QPointF(L + i * W / max(n - 1, 1), T + H - (v / top) * H)
               for i, v in enumerate(self._v) if v is not None]
        accent = QColor("#2161D9")
        p.setPen(QPen(accent, 2)); p.drawPolyline(QPolygonF(pts))
        p.setBrush(accent)
        for q in pts:
            p.drawEllipse(q, 2.5, 2.5)
        p.setPen(fg)
        p.drawText(QPointF(pts[0].x() + 4, pts[0].y() - 4), _fmt(self._v[0], 2) if self._v[0] is not None else "")
        if self._v[-1] is not None:
            p.drawText(QRectF(pts[-1].x() - 40, pts[-1].y() - 16, 38, 14), Qt.AlignRight, _fmt(self._v[-1], 2))


class Histogram(QWidget):
    """Distribution of the per-pixel OD; marks the method value and p95."""
    BINS = 24
    LO, HI = -0.05, 0.55

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self._counts = None
        self._marks: list = []

    def set_data(self, values, marks: list) -> None:
        if values is None or len(values) == 0:
            self._counts = None
        else:
            self._counts, _ = np.histogram(np.clip(values, self.LO, self.HI - 1e-6), bins=self.BINS,
                                           range=(self.LO, self.HI))
        self._marks = marks
        self.update()

    def paintEvent(self, _):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); mid = self.palette().mid().color()
        L, R, T, B = 6, 6, 16, 16
        W, H = self.width() - L - R, self.height() - T - B
        x = lambda v: L + (v - self.LO) / (self.HI - self.LO) * W
        p.setPen(QPen(mid, 1)); p.drawLine(L, T + H, L + W, T + H)
        f = p.font(); f.setPointSizeF(f.pointSizeF() * 0.8); p.setFont(f)
        p.setPen(fg)
        for v in (0, 0.1, 0.2, 0.3, 0.4, 0.5):
            p.drawText(QRectF(x(v) - 15, T + H + 1, 30, 14), Qt.AlignCenter, _fmt(v, 1))
        if self._counts is None:
            return
        mx = max(self._counts.max(), 1)
        bw = W / self.BINS
        cols = _magma(((np.arange(self.BINS) + 0.5) * (self.HI - self.LO) / self.BINS + self.LO) / OD_SCALE)
        p.setPen(Qt.NoPen)
        for i, n in enumerate(self._counts):
            h = H * n / mx
            p.setBrush(QColor(*cols[i]))
            p.drawRect(QRectF(L + i * bw + 1, T + H - h, bw - 2, h))
        for k, (v, label, strong) in enumerate(self._marks):
            if v is None:
                continue
            p.setPen(QPen(fg, 2 if strong else 1, Qt.SolidLine if strong else Qt.DashLine))
            p.drawLine(QPointF(x(v), T - 2 + 12 * k), QPointF(x(v), T + H))
            p.drawText(QPointF(x(v) + 3, T + 8 + 12 * k), label)


class CellPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = QLabel(""); f = self.title.font(); f.setBold(True); f.setPointSizeF(f.pointSizeF() * 1.2); self.title.setFont(f)
        self.sub = QLabel("", objectName="caption")
        self.badge = QLabel("")
        head.addWidget(self.title); head.addWidget(self.sub); head.addStretch(1); head.addWidget(self.badge)
        lay.addLayout(head)
        grid = QGridLayout(); grid.setSpacing(6)
        self.c_od = Card("OD (Methode)"); self.c_pix = Card("Pixel-OD mean ± SD")
        self.c_i0 = Card("I₀ (Felder)"); self.c_dab = Card("DAB-OD (Zusatz)")
        for i, c in enumerate((self.c_od, self.c_pix, self.c_i0, self.c_dab)):
            grid.addWidget(c, i // 2, i % 2)
        lay.addLayout(grid)
        self.i0_state = QLabel(""); self.i0_state.setWordWrap(True); self.i0_state.setObjectName("i0state")
        lay.addWidget(self.i0_state)
        row = QHBoxLayout(); row.setSpacing(8)
        lm = QVBoxLayout(); lm.addWidget(QLabel("<b>OD-Karte</b>")); self.map = ODMap(); lm.addWidget(self.map, 1)
        lp = QVBoxLayout(); lp.addWidget(QLabel("<b>Radiales Profil</b>")); self.prof = Profile(); lp.addWidget(self.prof, 1)
        row.addLayout(lm, 1); row.addLayout(lp, 1)
        lay.addLayout(row)
        self.hist_label = QLabel("<b>Verteilung der Pixel-OD</b>")
        self.hist_sub = QLabel("", objectName="caption")
        hl = QHBoxLayout(); hl.addWidget(self.hist_label); hl.addWidget(self.hist_sub); hl.addStretch(1)
        lay.addLayout(hl)
        self.hist = Histogram(); lay.addWidget(self.hist)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def show_cell(self, row: dict | None, cm=None, gray=None, labels=None, s=None) -> None:
        if row is None:
            self.title.setText("Keine Zelle gewählt"); self.sub.setText("Klick auf eine Zelle")
            self.badge.setText("")
            for c in (self.c_od, self.c_pix, self.c_i0, self.c_dab):
                c.set("–")
            self.i0_state.setText("")
            self.map.set_data(None, None); self.prof.set_data([]); self.hist.set_data(None, []); self.hist_sub.setText("")
            return
        self.title.setText(f"Zelle #{row['zelle']}")
        self.sub.setText(f"{row['kontur']} · {row['flaeche_px']} px")
        colors = {"gewertet": ("#1B7A4D", "#E4F2EA"), "ausgeschlossen": ("#975E0C", "#F8EFDF"),
                  "verworfen": ("#5A6577", "#EEF0F4")}
        fg, bg = colors[row["status"]]
        self.badge.setText(row["status"])
        self.badge.setStyleSheet(f"color:{fg};background:{bg};border-radius:9px;padding:2px 10px;font-weight:600;")
        self.c_od.set(_fmt(row["od_fiji"]), "−log₁₀(Ī / I₀), grau")
        self.c_pix.set(_fmt(row["pixel_od_mean"]), f"± {_fmt(row['pixel_od_sd'])} · median {_fmt(row['pixel_od_median'])}")
        fields = " · ".join(_fmt(row.get(f"i0_feld{k + 1}"), 1) for k in range(s.i0_fields) if row.get(f"i0_feld{k + 1}") is not None)
        self.c_i0.set(_fmt(row["i0"], 1), fields or "kein Hintergrund gefunden")
        self.c_dab.set(_fmt(row["dab_od_mean"]), "Deconvolution H-DAB")
        if row["i0"] is None:
            msg, fg, bg = "Kein Hintergrund neben der Zelle – I₀-Felder von Hand setzen (Werkzeug 3)", "#975E0C", "#F8EFDF"
        elif row["i0_ok"]:
            msg, fg, bg = f"✓  I₀ {_fmt(row['i0'], 1)} liegt im Bereich {s.i0_min:g}–{s.i0_max:g}", "#1B7A4D", "#E4F2EA"
        else:
            msg, fg, bg = f"⚠  I₀ {_fmt(row['i0'], 1)} liegt außerhalb {s.i0_min:g}–{s.i0_max:g} → ausgeschlossen", "#975E0C", "#F8EFDF"
        if row.get("debris_verdacht"):
            msg += "  ·  Debris-Verdacht (H-Kanal hoch)"
        self.i0_state.setText(msg)
        self.i0_state.setStyleSheet(f"color:{fg};background:{bg};border-radius:8px;padding:6px 10px;")
        # map + histogram need the per-pixel OD
        from scipy import ndimage as ndi
        sl = ndi.find_objects((labels == row["zelle"]).astype(np.uint8))[0] if cm is not None and cm.i0 else None
        if sl is not None:
            pad = 4
            H, W = labels.shape
            y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, H)
            x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, W)
            od = -np.log10(np.clip(gray[y0:y1, x0:x1], 1, None) / cm.i0)
            self.map.set_data(od, labels[y0:y1, x0:x1] == row["zelle"])
        else:
            self.map.set_data(None, None)
        self.prof.set_data(cm.profile if cm is not None else [])
        self.hist.set_data(cm.hist if cm is not None and cm.i0 else None,
                           [(row["od_fiji"], f"Methode {_fmt(row['od_fiji'])}", True),
                            (row["pixel_od_p95"], "p95", False)])
        share = row.get("anteil_od_ueber")
        self.hist_sub.setText(f"p5 {_fmt(row['pixel_od_p5'])} · p95 {_fmt(row['pixel_od_p95'])}"
                              + (f" · > {_fmt(s.od_high, 1)}: {share * 100:.1f} %" if share is not None else ""))
