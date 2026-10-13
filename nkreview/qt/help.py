"""Help window: how NKReview measures the OD.

Three tabs — the pipeline (click a step, switch whole cell / nucleus), the
formula sheet (Copy LaTeX), and the worked example with a small calculator.
Formulas are drawn offline with matplotlib mathtext; the interactive web
version (nkreview/help/pipeline.html) opens in the browser.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QPixmap, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..help_content import FORMULAS, SHEET, STEPS, TARGETS, step

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
WEB_PAGE = Path(__file__).resolve().parent.parent / "help" / "pipeline.html"
RENDER_DPI = 220            # rendered at 2× and shown at half size → sharp on Retina


def _formula_image(mt: str, color: str) -> QImage:
    from matplotlib import mathtext
    buf = io.BytesIO()
    mathtext.math_to_image(mt, buf, dpi=RENDER_DPI, format="png", color=color)
    img = QImage(); img.loadFromData(buf.getvalue(), "PNG")
    return img


class FormulaBrowser(QTextBrowser):
    """Rich text with formula images (img src="formula:key") and copy links (copy:key)."""

    def __init__(self, status: QLabel, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._link)
        self._status = status
        self._cache: dict[str, QImage] = {}

    def _color(self) -> str:
        return self.palette().text().color().name()

    def formula_html(self, key: str) -> str:
        f = FORMULAS[key]
        name = f"formula:{key}:{self._color()}"
        if name not in self._cache:
            self._cache[name] = _formula_image(f["mt"], self._color())
        img = self._cache[name]
        self.document().addResource(QTextDocument.ImageResource, QUrl(name), img)
        w = max(1, img.width() // 2)
        return (f'<table width="100%" cellspacing="0" cellpadding="8" style="margin:6px 0;">'
                f'<tr><td style="border-left:3px solid #8A5323;">'
                f'<img src="{name}" width="{w}"><br>'
                f'<span style="color:#5B6476;">{f["cap"]}</span><br>'
                f'<a href="copy:{key}">Copy LaTeX</a></td></tr></table>')

    def _link(self, url: QUrl) -> None:
        s = url.toString()
        if s.startswith("copy:"):
            key = s.split(":", 1)[1]
            QGuiApplication.clipboard().setText(FORMULAS[key]["tex"])
            self._status.setText(f"LaTeX copied: {key}")
        else:
            QDesktopServices.openUrl(url)


class HelpWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("NKReview – how the OD is measured")
        self.setModal(False)
        from .studies import fit_to_screen
        fit_to_screen(self, 1000, 720)
        self.target = "cell"
        self.current = "od"
        lay = QVBoxLayout(self); lay.setContentsMargins(18, 14, 18, 12); lay.setSpacing(10)

        head = QHBoxLayout(); head.setSpacing(14)
        logo = QLabel()
        pm = QPixmap(str(ASSETS / "icon_256.png"))
        if not pm.isNull():
            logo.setPixmap(pm.scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        title = QLabel("<b>From a brightfield image to one optical density per cell</b>")
        f = title.font(); f.setPointSizeF(f.pointSizeF() * 1.35); title.setFont(f); title.setWordWrap(True)
        sub = QLabel("NKReview finds cells or nuclei, measures each against the background right next to it, "
                     "and reports the OD as the Fiji method does – plus the OD distribution inside every cell.",
                     objectName="caption")
        sub.setWordWrap(True)
        tv = QVBoxLayout(); tv.addWidget(title); tv.addWidget(sub)
        b_web = QPushButton("Open as web page"); b_web.clicked.connect(self.open_web)
        head.addWidget(logo); head.addLayout(tv, 1); head.addWidget(b_web, 0, Qt.AlignTop)
        lay.addLayout(head)

        self.status = QLabel("", objectName="caption")
        tabs = QTabWidget()
        tabs.addTab(self._pipeline_tab(), "Pipeline")
        tabs.addTab(self._sheet_tab(), "Formula sheet")
        tabs.addTab(self._example_tab(), "Worked example")
        lay.addWidget(tabs, 1)
        bottom = QHBoxLayout(); bottom.addWidget(self.status, 1)
        b_close = QPushButton("Close"); b_close.clicked.connect(self.close)
        bottom.addWidget(b_close)
        lay.addLayout(bottom)
        self._render_detail()

    # ── pipeline ─────────────────────────────────────────
    def _pipeline_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(8, 10, 8, 8); l.setSpacing(10)
        row = QHBoxLayout()
        row.addWidget(QLabel("Click a step. Target:"))
        seg = QFrame(objectName="segment"); sl = QHBoxLayout(seg); sl.setContentsMargins(0, 0, 0, 0); sl.setSpacing(0)
        self.t_group = QButtonGroup(self)
        for i, (key, t) in enumerate(TARGETS.items()):
            b = QPushButton(t["label"]); b.setCheckable(True); b.setChecked(key == self.target)
            b.setProperty("first", i == 0); b.setProperty("last", i == len(TARGETS) - 1)
            b.clicked.connect(lambda _=False, k=key: self._set_target(k))
            self.t_group.addButton(b); sl.addWidget(b)
        row.addWidget(seg); row.addStretch(1)
        l.addLayout(row)

        flow = QWidget(); fl = QGridLayout(flow); fl.setContentsMargins(0, 0, 0, 0); fl.setHorizontalSpacing(4); fl.setVerticalSpacing(2)
        self.step_buttons: dict[str, QPushButton] = {}
        self.step_group = QButtonGroup(self)
        col = 0
        for i, s in enumerate(STEPS):
            if i == 0 or STEPS[i - 1]["phase"] != s["phase"]:
                ph = QLabel(s["phase"].upper(), objectName="caption")
                fl.addWidget(ph, 0, col)
            b = QPushButton(); b.setCheckable(True); b.setProperty("class", "tool")
            b.setMinimumWidth(176); b.setMinimumHeight(58)
            b.clicked.connect(lambda _=False, k=s["id"]: self._select(k))
            self.step_group.addButton(b)
            self.step_buttons[s["id"]] = b
            fl.addWidget(b, 1, col)
            col += 1
            if i < len(STEPS) - 1:
                arrow = QLabel("→"); arrow.setAlignment(Qt.AlignCenter)
                fl.addWidget(arrow, 1, col); col += 1
        sc = QScrollArea(); sc.setWidget(flow); sc.setWidgetResizable(True); sc.setFrameShape(QFrame.NoFrame)
        sc.setFixedHeight(112); sc.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        l.addWidget(sc)
        self.detail = FormulaBrowser(self.status)
        l.addWidget(self.detail, 1)
        self._label_steps()
        return w

    def _label_steps(self) -> None:
        for i, s0 in enumerate(STEPS):
            s = step(s0["id"], self.target)
            b = self.step_buttons[s0["id"]]
            b.setText(f"{i + 1:02d}  {s['t']}\n{s['s']}")
            b.setChecked(s0["id"] == self.current)

    def _set_target(self, k: str) -> None:
        self.target = k
        for b, key in zip(self.t_group.buttons(), TARGETS):
            b.setChecked(key == k)
        self._label_steps()
        self._render_detail()

    def _select(self, k: str) -> None:
        self.current = k
        self._label_steps()
        self._render_detail()

    def _render_detail(self) -> None:
        s = step(self.current, self.target)
        i = next(n for n, x in enumerate(STEPS) if x["id"] == self.current)
        extra = (" – " + TARGETS[self.target]["label"]) if self.current in ("seg", "i0", "od") else ""
        html = [f'<p style="color:#8A5323; font-family:Menlo,monospace; font-size:11px;">STEP {i + 1} · {s["phase"].upper()}</p>',
                f"<h2>{s['t']}{extra}</h2><p>{s['body']}</p>"]
        if s["params"]:
            html.append('<table cellspacing="0" cellpadding="3">')
            for k, v in s["params"]:
                html.append(f'<tr><td style="color:#5B6476; padding-right:16px;">{k}</td>'
                            f'<td style="font-family:Menlo,monospace;">{v}</td></tr>')
            html.append("</table>")
        if s["f"]:
            html.append("<h3>Formulas</h3>")
            html += [self.detail.formula_html(k) for k in s["f"]]
        else:
            html.append('<p style="color:#5B6476;">No formula in this step – it organises the images so every '
                        'cell carries subject, group, timepoint and marker into the export.</p>')
        self.detail.setHtml("".join(html))

    # ── formula sheet ────────────────────────────────────
    def _sheet_tab(self) -> QWidget:
        w = QWidget(); l = QVBoxLayout(w); l.setContentsMargins(8, 10, 8, 8)
        l.addWidget(QLabel("Everything the app computes. “Copy LaTeX” puts the exact LaTeX on the clipboard."))
        sheet = FormulaBrowser(self.status)
        sheet.setHtml("".join(sheet.formula_html(k) for k in SHEET) + self._symbols_html())
        l.addWidget(sheet, 1)
        return w

    @staticmethod
    def _symbols_html() -> str:
        rows = [("G(p)", "grey value of pixel p (ImageJ RGB → 8-bit)", "(R+G+B)/3"),
                ("C", "pixels inside the cell or nucleus contour", "hybrid / Cellpose"),
                ("F_k", "background field k, a square of s × s px", "k = 1…3, s = 9"),
                ("d(p)", "distance of pixel p from the contour", "14–40 px (nuclei 40–90)"),
                ("I0_bg", "median grey value of the image's free background", "≥ 40 px from all cells"),
                ("τ", "positive cut-off on the OD", "0.01 (C2C12)")]
        body = "".join(f'<tr><td style="font-family:Menlo,monospace;">{a}</td><td>{b}</td>'
                       f'<td style="font-family:Menlo,monospace; color:#5B6476;">{c}</td></tr>' for a, b, c in rows)
        return ('<h3>Symbols</h3><table cellspacing="0" cellpadding="4">'
                '<tr><th align="left">Symbol</th><th align="left">Meaning</th><th align="left">Default</th></tr>'
                f"{body}</table>")

    # ── worked example ───────────────────────────────────
    def _example_tab(self) -> QWidget:
        w = QWidget(); l = QHBoxLayout(w); l.setContentsMargins(8, 10, 8, 8); l.setSpacing(16)
        text = FormulaBrowser(self.status)
        steps = [
            ("1 · Background I₀ from three fields", r"$I_0 = \frac{1}{3}(217.4 + 217.0 + 217.5) = 217.3$"),
            ("2 · Mean grey value in the contour", r"$\bar{I} = \dfrac{1}{5515}\sum_{p \in C} G(p) = 149.0$"),
            ("3 · Optical density", r"$\mathrm{OD} = -\log_{10}\left(\dfrac{149.0}{217.3}\right) = 0.164$"),
        ]
        html = ['<p>Real values from <code>TEST A NK 5hmc 10.02.2026_002.tif</code>, cell #1 '
                '(hybrid contour, 5515 px).</p>']
        for i, (h, mt) in enumerate(steps):
            name = f"example:{i}:{text._color()}"
            img = _formula_image(mt, text._color())
            text.document().addResource(QTextDocument.ImageResource, QUrl(name), img)
            html.append(f"<h3>{h}</h3><p><img src=\"{name}\" width=\"{img.width() // 2}\"></p>")
        html.append('<p style="color:#5B6476;">The mean of the pixel ODs is 0.168: since −log is convex, it is '
                    'always ≥ the OD of the mean. The method uses the OD of the mean; the app reports both.</p>')
        text.setHtml("".join(html))
        l.addWidget(text, 3)

        calc = QFrame(objectName="metricCard"); cl = QVBoxLayout(calc); cl.setContentsMargins(16, 14, 16, 14)
        cl.addWidget(QLabel("<b>Try it</b>"))
        form = QFormLayout()
        self.c_i = QDoubleSpinBox(); self.c_i.setRange(1, 255); self.c_i.setDecimals(1); self.c_i.setValue(149.0)
        self.c_0 = QDoubleSpinBox(); self.c_0.setRange(1, 255); self.c_0.setDecimals(1); self.c_0.setValue(217.3)
        self.c_bg = QDoubleSpinBox(); self.c_bg.setRange(1, 255); self.c_bg.setDecimals(1); self.c_bg.setValue(217.0)
        form.addRow("Ī (cell):", self.c_i); form.addRow("I₀ (fields):", self.c_0); form.addRow("free background:", self.c_bg)
        cl.addLayout(form)
        self.c_od = QLabel(""); f = self.c_od.font(); f.setPointSizeF(f.pointSizeF() * 2.4); f.setBold(True); self.c_od.setFont(f)
        self.c_rules = QLabel(""); self.c_rules.setWordWrap(True)
        cl.addWidget(self.c_od); cl.addWidget(self.c_rules); cl.addStretch(1)
        for sb in (self.c_i, self.c_0, self.c_bg):
            sb.valueChanged.connect(self._calc)
        l.addWidget(calc, 2)
        self._calc()
        return w

    def _calc(self) -> None:
        i, i0, bg = self.c_i.value(), self.c_0.value(), self.c_bg.value()
        od = -math.log10(i / i0)
        dev = (i0 - bg) / bg * 100
        ok_abs, ok_rel = 210 <= i0 <= 220, abs(dev) <= 3
        self.c_od.setText(f"OD = {od:.3f}")
        self.c_rules.setText(f"{'✓' if ok_abs else '✕'} fixed rule 210–220\n"
                             f"{'✓' if ok_rel else '✕'} relative rule ±3 % ({dev:+.1f} %)")

    def open_web(self) -> None:
        if WEB_PAGE.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(WEB_PAGE)))
        else:
            self.status.setText("Web page not found: " + str(WEB_PAGE))
