"""Abgleich-Ansicht (mockup v6, board 3): rating A and rating B of the same
image side by side — two image views that zoom and pan together — with the
cells they rated differently framed. N / Shift+N jump to the next / previous
difference; in "Umschalten" mode one view shows A or B, Leertaste flips.

Konsens: for the difference in view, "A übernehmen" / "B übernehmen" writes
that cell into the consensus rating (interrater.CONSENSUS), which starts as a
copy of A. Nothing here changes rating A or B. Computation: interrater.py."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPushButton, QVBoxLayout, QWidget,
)

from .. import interrater as ir
from ..overlay import OverlayColors, build_overlay_rgba, compute_cell_infos, normalize_image_for_display

ROLE = Qt.UserRole + 7


class CompareWindow(QDialog):
    def __init__(self, parent, P, S, folder: Path, grouped, profile, rater_b: str,
                 label_a: str, base: str | None = None, on_consensus=None):
        super().__init__(parent)
        self.P, self.S, self.folder, self.grouped, self.profile = P, S, folder, grouped, profile
        self.rater_b, self.label_a, self.base = rater_b, label_a, base
        self._on_consensus = on_consensus or (lambda: None)
        self.setWindowTitle(S["cv_title"].format(a=label_a, b=rater_b))
        self.resize(1400, 860)
        self.mode = "side"           # "side" | "flip"
        self.flip_b = False
        self.only_diffs = False
        self.diffs: list[ir.CellDiff] = []
        self.pos = -1
        self.decided: dict[tuple[str, int], str] = {}

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        bar = QFrame(); bar.setObjectName("toolbar")
        bh = QHBoxLayout(bar); bh.setContentsMargins(14, 8, 14, 8); bh.setSpacing(10)
        seg = QFrame(); seg.setObjectName("segment")
        sh = QHBoxLayout(seg); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(0)
        grp = QButtonGroup(seg)
        for i, (key, text) in enumerate((("side", S["cv_side"]), ("flip", S["cv_flip"]))):
            b = QPushButton(text); b.setCheckable(True); b.setChecked(key == "side")
            b.setProperty("first", i == 0); b.setProperty("last", i == 1); b.setMinimumHeight(30)
            b.clicked.connect(lambda _c=False, k=key: self.set_mode(k))
            grp.addButton(b); sh.addWidget(b)
        bh.addWidget(seg)
        self.only_cb = QCheckBox(S["cv_only_diffs"]); self.only_cb.toggled.connect(self._toggle_only)
        bh.addWidget(self.only_cb)
        bh.addStretch(1)
        prev = QPushButton("◀  " + S["cv_prev"]); prev.clicked.connect(lambda: self.goto_diff(-1))
        nxt = QPushButton(S["cv_next"] + "  ▶"); nxt.setProperty("class", "primary")
        nxt.clicked.connect(lambda: self.goto_diff(+1))
        bh.addWidget(prev); bh.addWidget(nxt)
        root.addWidget(bar)

        body = QHBoxLayout(); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(0)
        side = QFrame(); side.setObjectName("sidebar"); side.setFixedWidth(270)
        sv = QVBoxLayout(side); sv.setContentsMargins(8, 8, 8, 8)
        self.list = QListWidget(); self.list.currentItemChanged.connect(lambda cur, _p: self._open_item(cur))
        sv.addWidget(self.list, 1)
        self.info = QLabel(""); self.info.setWordWrap(True); self.info.setObjectName("settingsCard")
        self.info.setStyleSheet("padding: 10px;")
        sv.addWidget(self.info)
        cons = QFrame(); cons.setObjectName("settingsCard")
        cv = QVBoxLayout(cons); cv.setContentsMargins(10, 8, 10, 8); cv.setSpacing(6)
        cv.addWidget(QLabel(f"<b>{S['cv_consensus']}</b>"))
        row = QHBoxLayout()
        self.take_a = QPushButton(S["cv_take"].format(x="A")); self.take_a.clicked.connect(lambda: self.take("a"))
        self.take_b = QPushButton(S["cv_take"].format(x="B")); self.take_b.clicked.connect(lambda: self.take("b"))
        row.addWidget(self.take_a); row.addWidget(self.take_b)
        cv.addLayout(row)
        hint = QLabel(S["cv_consensus_hint"]); hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 11px;")
        cv.addWidget(hint)
        sv.addWidget(cons)
        body.addWidget(side)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.fig = Figure(facecolor=P["canvas_bg"])
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.mpl_connect("button_press_event", self._on_press)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("button_release_event", lambda _e: setattr(self, "_drag", None))
        self._drag = None
        body.addWidget(self.canvas, 1)
        w = QWidget(); w.setLayout(body)
        root.addWidget(w, 1)
        self.status = QLabel(""); self.status.setObjectName("caption")
        self.status.setStyleSheet(f"padding: 6px 14px; color: {P['fg_secondary']}; font-size: 12px;")
        root.addWidget(self.status)

        QShortcut(QKeySequence("N"), self, activated=lambda: self.goto_diff(+1))
        QShortcut(QKeySequence("Shift+N"), self, activated=lambda: self.goto_diff(-1))
        QShortcut(QKeySequence("Space"), self, activated=self.flip)
        QShortcut(QKeySequence("A"), self, activated=lambda: self.take("a"))
        QShortcut(QKeySequence("B"), self, activated=lambda: self.take("b"))
        self._fill_list()

    # ── images ──
    def _fill_list(self) -> None:
        ag = ir.compare(self.folder, self.grouped, self.profile, self.rater_b, self.label_a, base=self.base)
        self._infos = {i.path.name: i for i in self.grouped.flat}
        for name, n, same in ag.per_image:
            pct = 100.0 * same / n if n else 100.0
            it = QListWidgetItem(f"{name}\n{same} / {n}  ·  {pct:.0f} %")
            it.setData(ROLE, name)
            it.setForeground(QColor(self.P["state_finished"] if pct >= 95 else self.P["accent_warn"]))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.status.setText(self.S["cv_nothing"])

    def _open_item(self, item) -> None:
        if item is None:
            return
        info = self._infos[item.data(ROLE)]
        res = ir.image_diffs(self.folder, info, self.profile, self.rater_b, self.base)
        if res is None:
            return
        self.info_path = info.path
        self.masks, self.ra, self.rb, self.diffs = res
        from ..cellpose_runner import load_image
        self.img = normalize_image_for_display(load_image(info.path))
        self.cell_infos = compute_cell_infos(self.masks, set())
        _ids, auto = ir.mask_cells(self.masks, self.profile)
        self._auto = auto
        self.pos = -1
        self._render(reset_view=True)
        self.goto_diff(+1)

    def _overlay(self, review) -> np.ndarray:
        rgba = build_overlay_rgba(self.masks, self.cell_infos, review, self.profile, self._auto, set())
        if self.only_diffs:
            keep = np.isin(self.masks, [d.cid for d in self.diffs])
            rgba = rgba.copy(); rgba[~keep, 3] = 0
        return rgba

    def _render(self, reset_view: bool = False) -> None:
        P, S = self.P, self.S
        lims = None
        if not reset_view and self.fig.axes:
            ax0 = self.fig.axes[0]
            lims = (ax0.get_xlim(), ax0.get_ylim())
        self.fig.clear()
        if self.mode == "side":
            ax1 = self.fig.add_subplot(1, 2, 1)
            ax2 = self.fig.add_subplot(1, 2, 2, sharex=ax1, sharey=ax1)
            views = [(ax1, self.ra, f"A · {self.label_a}"), (ax2, self.rb, f"B · {self.rater_b}")]
        else:
            ax = self.fig.add_subplot(1, 1, 1)
            views = [(ax, self.rb if self.flip_b else self.ra,
                      (f"B · {self.rater_b}" if self.flip_b else f"A · {self.label_a}") + f"   ({S['cv_space']})")]
        for ax, rv, title in views:
            ax.imshow(self.img, interpolation="nearest", cmap="gray")
            ax.imshow(self._overlay(rv), interpolation="nearest")    # RGBA, alpha from the study
            ax.set_title(title, color=P["fg"], fontsize=11, loc="left")
            ax.axis("off")
            from matplotlib.patches import Rectangle
            for k, d in enumerate(self.diffs):
                x0, y0, x1, y1 = d.bbox
                cur = k == self.pos
                done = (self.info_path.name, d.cid) in self.decided
                ax.add_patch(Rectangle((x0 - 3, y0 - 3), x1 - x0 + 6, y1 - y0 + 6, fill=False,
                                       edgecolor=P["state_finished"] if done else P["accent_warn"],
                                       linewidth=2.6 if cur else 1.2, linestyle="-" if cur else "--"))
        self.fig.subplots_adjust(left=0.01, right=0.99, top=0.95, bottom=0.01, wspace=0.02)
        if lims is not None:
            self.fig.axes[0].set_xlim(*lims[0]); self.fig.axes[0].set_ylim(*lims[1])
        self.canvas.draw_idle()
        self._update_info()

    def _update_info(self) -> None:
        S = self.S
        n = len(self.diffs)
        name = lambda k: S["ir_excluded"] if k == ir.EXCLUDED else (
            (self.profile.type_by_key(k).name or k) if self.profile.type_by_key(k) else k)
        counts = {}
        for d in self.diffs:
            kind = "excl" if ir.EXCLUDED in (d.a, d.b) else "type"
            counts[kind] = counts.get(kind, 0) + 1
        self.info.setText(S["cv_info"].format(n_cells=len(self.cell_infos), n=n, type=counts.get("type", 0),
                                              excl=counts.get("excl", 0)))
        if 0 <= self.pos < n:
            d = self.diffs[self.pos]
            dec = self.decided.get((self.info_path.name, d.cid))
            self.status.setText(S["cv_status"].format(i=self.pos + 1, n=n, cid=d.cid, a=name(d.a), b=name(d.b))
                                + (f"  ·  {S['cv_decided'].format(x=dec.upper())}" if dec else ""))
        else:
            self.status.setText(S["cv_no_diffs"] if not n else "")
        for b in (self.take_a, self.take_b):
            b.setEnabled(0 <= self.pos < n)

    # ── navigation ──
    def goto_diff(self, step: int) -> None:
        if not self.diffs:
            self._update_info()
            return
        self.pos = (self.pos + step) % len(self.diffs)
        x0, y0, x1, y1 = self.diffs[self.pos].bbox
        pad = max(60, (x1 - x0) * 3)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self._render()
        ax = self.fig.axes[0]
        ax.set_xlim(cx - pad, cx + pad); ax.set_ylim(cy + pad, cy - pad)
        self.canvas.draw_idle()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if hasattr(self, "masks"):
            self._render()

    def flip(self) -> None:
        if self.mode != "flip":
            self.set_mode("flip")
            return
        self.flip_b = not self.flip_b
        self._render()

    def _toggle_only(self, on: bool) -> None:
        self.only_diffs = on
        if hasattr(self, "masks"):
            self._render()

    # ── consensus ──
    def take(self, which: str) -> None:
        if not (0 <= self.pos < len(self.diffs)):
            return
        d = self.diffs[self.pos]
        src = self.ra if which == "a" else self.rb
        ir.set_consensus_cell(self.folder, self.info_path.stem, d.cid, src, self.ra)
        self.decided[(self.info_path.name, d.cid)] = which
        self._on_consensus()
        if self.pos < len(self.diffs) - 1:
            self.goto_diff(+1)
        else:
            self._render()

    # ── zoom & pan (both views follow: shared axes) ──
    def _on_scroll(self, e) -> None:
        if e.inaxes is None:
            return
        ax = e.inaxes
        f = 0.8 if e.button == "up" else 1.25
        x0, x1 = ax.get_xlim(); y0, y1 = ax.get_ylim()
        ax.set_xlim(e.xdata - (e.xdata - x0) * f, e.xdata + (x1 - e.xdata) * f)
        ax.set_ylim(e.ydata - (e.ydata - y0) * f, e.ydata + (y1 - e.ydata) * f)
        self.canvas.draw_idle()

    def _on_press(self, e) -> None:
        if e.inaxes is not None and e.button == 1:
            self._drag = (e.inaxes, e.x, e.y, e.inaxes.get_xlim(), e.inaxes.get_ylim())

    def _on_motion(self, e) -> None:
        if not self._drag:
            return
        ax, x, y, xl, yl = self._drag
        inv = ax.transData.inverted()
        (dx0, dy0), (dx1, dy1) = inv.transform([(x, y), (e.x, e.y)])
        ax.set_xlim(xl[0] - (dx1 - dx0), xl[1] - (dx1 - dx0))
        ax.set_ylim(yl[0] - (dy1 - dy0), yl[1] - (dy1 - dy0))
        self.canvas.draw_idle()
