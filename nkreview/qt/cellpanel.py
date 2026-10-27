"""The cell panel: metric cards, I₀ status, OD map, radial profile, histogram
of the per-pixel OD — the distribution inside one cell (mockup "Auswertung")."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

OD_SCALE = 0.4            # colour scale of map and histogram: 0 … 0.4 OD


def _magma(v: np.ndarray) -> np.ndarray:
    from matplotlib import colormaps
    return (colormaps["magma_r"](np.clip(v, 0, 1))[..., :3] * 255).astype(np.uint8)


def _fmt(v, n=3) -> str:
    return "–" if v is None else f"{v:.{n}f}"


STATUS_TEXT = {"kept": "counted · open", "confirmed": "confirmed", "warn": "I₀ warning · open",
               "excluded": "excluded",
               "discarded": "discarded"}
CONTOUR_TEXT = {"cellpose": "from the model", "hybrid": "hybrid (model + OD edge)",
                "threshold": "OD threshold", "manual": "drawn by hand"}
STATUS_COLORS = {"kept": ("#3D4450", "#EDEFF2"), "confirmed": ("#1B7A4D", "#E4F2EA"),
                 "warn": ("#975E0C", "#F8EFDF"), "excluded": ("#975E0C", "#F8EFDF"),
                 "discarded": ("#5A6577", "#EEF0F4")}


def chip(text: str, status: str) -> str:
    """Rich-text pill in the status colours (same as the contours)."""
    fg, bg = STATUS_COLORS[status]
    return f'<span style="color:{fg};background:{bg};">&nbsp;{text}&nbsp;</span>'


def state_of(row: dict) -> str:
    """'confirmed' (reviewer said yes), 'warn' (kept, I₀ check failed, still open), else the status."""
    if row.get("confirmed"):
        return "confirmed"
    return "warn" if row.get("i0_warning") else row["status"]


def edit_note(parent, title: str, text: str) -> str | None:
    """Remark editor as in ErysReview: returns the new text ('' deletes), None = cancelled.
    Keys typed here stay in the text field, so '1' or 'X' never trigger a shortcut."""
    from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QPushButton
    dlg = QDialog(parent); dlg.setWindowTitle(title); dlg.resize(440, 300)
    v = QVBoxLayout(dlg)
    edit = QPlainTextEdit(text); edit.setPlaceholderText("Remark for discussion – stays in the review, not in the export.")
    v.addWidget(edit, 1)
    box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
    result = {"text": None}
    if text:
        delete = QPushButton("Delete"); delete.setStyleSheet("color: #B83A3A;")
        box.addButton(delete, QDialogButtonBox.DestructiveRole)
        delete.clicked.connect(lambda: (result.update(text=""), dlg.accept()))
    box.accepted.connect(lambda: (result.update(text=edit.toPlainText().strip()), dlg.accept()))
    box.rejected.connect(dlg.reject)
    v.addWidget(box)
    edit.setFocus()
    dlg.exec()
    return result["text"]


class Fold(QWidget):
    """A section with a title that opens and closes its body."""
    def __init__(self, title: str, body: QWidget, opened: bool = False, on_toggle=None, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self.btn = QToolButton(text=title); self.btn.setCheckable(True)
        self.btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn.setStyleSheet("QToolButton { border: none; font-weight: 600; padding: 0; }")
        self.body = body
        lay.addWidget(self.btn); lay.addWidget(body)
        self.btn.setChecked(opened); self._show(opened)
        self.btn.toggled.connect(self._show)
        if on_toggle:
            self.btn.toggled.connect(on_toggle)

    def _show(self, on: bool) -> None:
        self.btn.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)


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
            p.drawText(self.rect(), Qt.AlignCenter, "no cell selected")
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
        p.drawText(QRectF(L, T + H + 2, 80, 14), Qt.AlignLeft, "centre")
        p.drawText(QRectF(L + W - 80, T + H + 2, 80, 14), Qt.AlignRight, "membrane")
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


def i0_band(row: dict, s) -> tuple[float, float] | None:
    """The I₀ range the check uses, so the axis turns red exactly where the check fails;
    None when there is nothing to check against (grey line)."""
    p = s.i0_rel_pct / 100
    if s.i0_check == "relativ":
        ref = row.get("i0_background")
        return None if ref is None else (ref * (1 - p), ref * (1 + p))
    if s.i0_check == "lokal":
        return None if row.get("i0_field_spread_pct") is None else (row["i0"] * (1 - p), row["i0"] * (1 + p))
    return (s.i0_min, s.i0_max)


class I0Axis(QWidget):
    """I₀ on a line: green where the check passes, red outside, the fields as dots,
    the mean as a bar. The range fits band and dots."""
    GREEN, RED, GREY = QColor("#9FD0B4"), QColor("#E3A29C"), QColor("#C9C9C4")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(70)
        self._fields: list = []; self._mean = None; self._band = None; self._ref = None; self._fmt_band = "g"

    def set_data(self, fields: list, mean: float, band, ref=None, fmt_band: str = "g") -> None:
        self._fields, self._mean, self._band, self._ref, self._fmt_band = fields, mean, band, ref, fmt_band
        self.update()

    def paintEvent(self, _):
        if self._mean is None:
            return
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing)
        fg = self.palette().text().color(); muted = self.palette().placeholderText().color()
        f = p.font(); f.setPointSizeF(f.pointSizeF() * 0.8); p.setFont(f)
        vals = [*self._fields, self._mean, *(self._band or ()), *([self._ref] if self._ref is not None else [])]
        lo, hi = min(vals), max(vals)
        pad = max(hi - lo, 1.0) * 0.1
        lo, hi = lo - pad, hi + pad
        L, R, y = 14, 14, 38
        x = lambda v: L + (v - lo) / (hi - lo) * (self.width() - L - R)
        x0, x1 = x(lo), x(hi)
        if self._band is None:
            p.setPen(QPen(self.GREY, 6, Qt.SolidLine, Qt.RoundCap)); p.drawLine(QPointF(x0, y), QPointF(x1, y))
        else:
            b0, b1 = x(self._band[0]), x(self._band[1])
            p.setPen(QPen(self.RED, 6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x0, y), QPointF(b0, y)); p.drawLine(QPointF(b1, y), QPointF(x1, y))
            p.setPen(QPen(self.GREEN, 6, Qt.SolidLine, Qt.FlatCap)); p.drawLine(QPointF(b0, y), QPointF(b1, y))
        xm = x(self._mean)
        below = [(xm, f"Ø {self._mean:.1f}", fg, True)]
        if self._band is not None:
            below += [(x(v), f"{v:{self._fmt_band}}", muted, False) for v in self._band]
        if self._ref is not None:
            xr = x(self._ref)
            p.setPen(Qt.NoPen); p.setBrush(muted)
            p.drawPolygon(QPolygonF([QPointF(xr, y + 6), QPointF(xr - 4, y + 12), QPointF(xr + 4, y + 12)]))
            below.append((xr, f"BG {self._ref:.1f}", muted, False))
        placed: list = []
        for bx, text, col, bold in below:        # mean first; skip labels that would overlap one already drawn
            hw = p.fontMetrics().horizontalAdvance(text) / 2 + 3
            if any(abs(bx - q) < hw + qw for q, qw in placed):
                continue
            placed.append((bx, hw))
            f.setBold(bold); p.setFont(f); p.setPen(col)
            p.drawText(QRectF(bx - 30, y + 12, 60, 16), Qt.AlignCenter, text)
        f.setBold(False); p.setFont(f)
        last = -1e9; row = 0
        for v in sorted(self._fields):           # dots, labels above; close labels alternate rows
            fx = x(v)
            row = 1 - row if fx - last < 40 else 0
            last = fx
            p.setPen(muted); p.drawText(QRectF(fx - 30, y - 22 - 12 * row, 60, 14), Qt.AlignCenter, f"{v:.1f}")
        p.setPen(QPen(self.palette().base().color(), 1.5)); p.setBrush(fg)
        for v in self._fields:
            p.drawEllipse(QPointF(x(v), y), 5, 5)
        p.setPen(Qt.NoPen); p.drawRoundedRect(QRectF(xm - 1.5, y - 11, 3, 22), 1.5, 1.5)


class CellPanel(QWidget):
    """The selected cell: result first (OD as exported), then why (I₀ check),
    then details on demand, then the distribution plots."""
    def __init__(self, parent=None, more_open: bool = False, on_more=None,
                 dist_open: bool = True, on_dist=None, i0_open: bool = True, on_i0=None):
        super().__init__(parent)
        self.noun = "Cell"
        # Two parts, so the window can put the cell's buttons between them:
        # `head` = result and why, `details` = more values and the distribution.
        self.head = QWidget(); lay = QVBoxLayout(self.head); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(8)
        top = QHBoxLayout()
        self.title = QLabel(""); f = self.title.font(); f.setBold(True); f.setPointSizeF(f.pointSizeF() * 1.2); self.title.setFont(f)
        self.badge = QLabel("")
        top.addWidget(self.title); top.addStretch(1); top.addWidget(self.badge)
        lay.addLayout(top)
        res = QHBoxLayout(); res.setSpacing(8)
        self.od = QLabel("–"); f = self.od.font(); f.setPointSizeF(f.pointSizeF() * 2.2); f.setBold(True); self.od.setFont(f)
        self.od_cap = QLabel("OD – the value in the export")
        res.addWidget(self.od); res.addWidget(self.od_cap, 0, Qt.AlignBottom); res.addStretch(1)
        lay.addLayout(res)
        self.sub = QLabel("", objectName="caption"); self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        i0box = QWidget(); il = QVBoxLayout(i0box); il.setContentsMargins(0, 0, 0, 0); il.setSpacing(2)
        self.i0_axis = I0Axis(); il.addWidget(self.i0_axis)
        self.i0_state = QLabel(""); self.i0_state.setWordWrap(True); il.addWidget(self.i0_state)   # only a ⚠ shows
        self.i0_fold = Fold("Background (I₀)", i0box, i0_open, on_i0)
        lay.addWidget(self.i0_fold)
        self.note = QLabel(""); self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.details = QWidget(); dl = QVBoxLayout(self.details); dl.setContentsMargins(0, 0, 0, 0); dl.setSpacing(8)
        more = QWidget(); grid = QGridLayout(more); grid.setContentsMargins(0, 0, 0, 0); grid.setSpacing(6)
        self.c_i0 = Card("Background I₀"); self.c_pix = Card("Pixel OD inside")
        self.c_dab = Card("DAB OD (extra)")
        for i, c in enumerate((self.c_i0, self.c_pix, self.c_dab)):
            grid.addWidget(c, 0, i)
        self.more = Fold("More values", more, more_open, on_more)
        dl.addWidget(self.more)
        dist = QWidget(); dv = QVBoxLayout(dist); dv.setContentsMargins(0, 0, 0, 0); dv.setSpacing(8)
        row = QHBoxLayout(); row.setSpacing(8)
        lm = QVBoxLayout(); lm.addWidget(QLabel("<b>OD map</b>")); self.map = ODMap(); lm.addWidget(self.map, 1)
        lp = QVBoxLayout(); lp.addWidget(QLabel("<b>Radial profile</b>")); self.prof = Profile(); lp.addWidget(self.prof, 1)
        row.addLayout(lm, 1); row.addLayout(lp, 1)
        dv.addLayout(row)
        self.hist_label = QLabel("<b>Distribution of pixel OD</b>")
        self.hist_sub = QLabel("", objectName="caption")
        hl = QHBoxLayout(); hl.addWidget(self.hist_label); hl.addWidget(self.hist_sub); hl.addStretch(1)
        dv.addLayout(hl)
        self.hist = Histogram(); dv.addWidget(self.hist)
        self.dist = Fold("Distribution", dist, dist_open, on_dist)
        dl.addWidget(self.dist)
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0); outer.setSpacing(8)
        outer.addWidget(self.head); outer.addWidget(self.details)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)

    def show_cell(self, row: dict | None, cm=None, gray=None, labels=None, s=None, note: str = "") -> None:
        if row is None:
            self.title.setText(f"No {self.noun.lower()} selected")
            self.sub.setText(f"Click a {self.noun.lower()} in the image or in the list.")
            self.badge.setText(""); self.badge.setStyleSheet(""); self.od.setText("–")
            for c in (self.c_pix, self.c_i0, self.c_dab):
                c.set("–")
            self.i0_state.setText(""); self.note.setText("")
            self.i0_fold.hide()
            self.map.set_data(None, None); self.prof.set_data([]); self.hist.set_data(None, []); self.hist_sub.setText("")
            return
        self.title.setText(f"{self.noun} #{row['cell']}")
        fg, bg = STATUS_COLORS[state_of(row)]
        self.badge.setText(STATUS_TEXT[state_of(row)])
        self.note.setText(f"❝  {note}" if note else "No note · N or tool 4 adds one")
        self.note.setStyleSheet("color:#6A4FC4;" if note else "color: gray;")
        self.badge.setStyleSheet(f"color:{fg};background:{bg};border-radius:9px;padding:2px 10px;font-weight:600;")
        self.od.setText(_fmt(row["od"]))
        contour = CONTOUR_TEXT.get(row["contour"], row["contour"])
        self.c_pix.set(_fmt(row["pixel_od_mean"]), f"± {_fmt(row['pixel_od_sd'])} · median {_fmt(row['pixel_od_median'])}")
        fields = " · ".join(f"{k + 1}: {_fmt(row.get(f'i0_field{k + 1}'), 1)}" for k in range(s.i0_fields)
                            if row.get(f"i0_field{k + 1}") is not None)
        self.c_i0.set(_fmt(row["i0"], 1), fields or "no background found")
        self.c_dab.set(_fmt(row["dab_od_mean"]), "deconvolution H-DAB")
        if row["i0"] is None:
            msg, fg, bg = "No background next to the cell – set the I₀ fields by hand (tool 3)", "#975E0C", "#F8EFDF"
        elif s.i0_check == "relativ":
            ref, dev = row.get("i0_background"), row.get("i0_dev_pct")
            if dev is None:
                msg, fg, bg = "⚠  No free background in the image – I₀ cannot be checked – check the fields, then confirm (C) if they look right", "#975E0C", "#F8EFDF"
            else:
                txt = (f"I₀ {_fmt(row['i0'], 1)} · free background {_fmt(ref, 1)} "
                       f"({dev:+.1f} %, allowed ± {s.i0_rel_pct:g} %)")
                ok = row["i0_ok"]
                msg = ("✓  " if ok else "⚠  ") + txt + ("" if ok else " – check the fields, then confirm (C) if they look right")
                fg, bg = ("#1B7A4D", "#E4F2EA") if ok else ("#975E0C", "#F8EFDF")
        elif s.i0_check == "lokal":
            spread = row.get("i0_field_spread_pct")
            agree = ("only one field – nothing to compare" if spread is None
                     else f"agree within {spread:.1f} % (allowed ± {s.i0_rel_pct:g} %)")
            if row["i0_ok"]:
                msg, fg, bg = f"✓  Background I₀ {_fmt(row['i0'], 1)} – fields {agree}", "#1B7A4D", "#E4F2EA"
            else:
                msg, fg, bg = (f"⚠  Background I₀ fields disagree by {spread:.1f} % (allowed ± "
                               f"{s.i0_rel_pct:g} %) – one may lie on debris. "
                               "Move it (tool 3), draw new ones (R), or confirm (C) if they look right."), "#975E0C", "#F8EFDF"
        elif row["i0_ok"]:
            whose = "this image's fields" if s.i0_scope == "bild" else "the fields"
            msg, fg, bg = (f"✓  Background I₀ {_fmt(row['i0'], 1)} (mean of {whose}) "
                           f"is within {s.i0_min:g}–{s.i0_max:g}"), "#1B7A4D", "#E4F2EA"
        else:
            msg, fg, bg = (f"⚠  Background I₀ {_fmt(row['i0'], 1)} is outside "
                           f"{s.i0_min:g}–{s.i0_max:g} – check the fields, then confirm (C) if they look right"), "#975E0C", "#F8EFDF"
        extra = [f"contour: {contour}"]
        if row.get("positive") is not None:
            extra.append(f"{'positive' if row['positive'] else 'negative'} (cut-off OD {_fmt(s.pos_threshold)})")
        if row.get("debris_suspect"):
            extra.append("debris suspected (high H channel)")
        self.sub.setText(("Discarded by you – not counted (X restores it) · " if row["status"] == "discarded" else "")
                         + f"−log₁₀(mean grey value / I₀) · {row['area_px']} px · " + " · ".join(extra))
        warn = fg == "#975E0C"
        self.i0_state.setText(msg if warn else "")
        self.i0_state.setStyleSheet(f"color:{fg};")
        self.i0_state.setVisible(warn)
        self.i0_fold.show()
        vals = [row[f"i0_field{k + 1}"] for k in range(s.i0_fields) if row.get(f"i0_field{k + 1}") is not None]
        self.i0_axis.setVisible(row["i0"] is not None and bool(vals))
        if row["i0"] is not None:
            self.i0_axis.set_data(vals, row["i0"], i0_band(row, s),
                                  row.get("i0_background") if s.i0_check == "relativ" else None,
                                  "g" if s.i0_check not in ("relativ", "lokal") else ".1f")
        # map + histogram need the per-pixel OD
        from scipy import ndimage as ndi
        sl = ndi.find_objects((labels == row["cell"]).astype(np.uint8))[0] if cm is not None and cm.i0 else None
        if sl is not None:
            pad = 4
            H, W = labels.shape
            y0, y1 = max(sl[0].start - pad, 0), min(sl[0].stop + pad, H)
            x0, x1 = max(sl[1].start - pad, 0), min(sl[1].stop + pad, W)
            od = -np.log10(np.clip(gray[y0:y1, x0:x1], 1, None) / cm.i0)
            self.map.set_data(od, labels[y0:y1, x0:x1] == row["cell"])
        else:
            self.map.set_data(None, None)
        self.prof.set_data(cm.profile if cm is not None else [])
        self.hist.set_data(cm.hist if cm is not None and cm.i0 else None,
                           [(row["od"], f"method {_fmt(row['od'])}", True),
                            (row["pixel_od_p95"], "p95", False)])
        share = row.get("share_od_above")
        self.hist_sub.setText(f"p5 {_fmt(row['pixel_od_p5'])} · p95 {_fmt(row['pixel_od_p95'])}"
                              + (f" · > {_fmt(s.od_high, 1)}: {share * 100:.1f} %" if share is not None else ""))
