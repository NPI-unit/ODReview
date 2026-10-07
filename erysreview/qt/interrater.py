"""Übereinstimmung (mockup v6, board 4): my reviews against a second rater's.

Key figures (agreement, Cohen's κ, ICC(2,1), masks), the cell-type matrix as a
heatmap (blue = same, red = different, by share of the row), rater A against
rater B per proband × timepoint with the 45° line, and Bland-Altman with
grid, mean difference and ±1.96 SD. The computation is interrater.py."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .. import interrater as ir
from .style import _mix


REFERENCES = [
    "Cohen J. A coefficient of agreement for nominal scales. Educ Psychol Meas. 1960;20(1):37–46.",
    "Fleiss JL. Measuring nominal scale agreement among many raters. Psychol Bull. 1971;76(5):378–382.",
    "Landis JR, Koch GG. The measurement of observer agreement for categorical data. Biometrics. "
    "1977;33(1):159–174.",
    "Shrout PE, Fleiss JL. Intraclass correlations: uses in assessing rater reliability. Psychol Bull. "
    "1979;86(2):420–428.",
    "Bland JM, Altman DG. Statistical methods for assessing agreement between two methods of clinical "
    "measurement. Lancet. 1986;1(8476):307–310.",
    "McGraw KO, Wong SP. Forming inferences about some intraclass correlation coefficients. Psychol Methods. "
    "1996;1(1):30–46.",
    "Koo TK, Li MY. A guideline of selecting and reporting intraclass correlation coefficients for reliability "
    "research. J Chiropr Med. 2016;15(2):155–163.",
]


def _fmt(S, v: float, nd: int = 2) -> str:
    if v != v:          # NaN
        return "–"
    if round(v, nd) == 0:
        v = 0.0         # no "−0,00"
    return f"{v:.{nd}f}".replace(".", S["qt_decimal_sep"]).replace("-", "−")


def _tile(title: str, value: str, sub: str, color: str, P: dict) -> QFrame:
    f = QFrame(); f.setObjectName("settingsCard")
    v = QVBoxLayout(f); v.setContentsMargins(14, 10, 14, 10); v.setSpacing(2)
    t = QLabel(title); t.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
    val = QLabel(value); val.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {color};")
    s = QLabel(sub); s.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;"); s.setWordWrap(True)
    v.addWidget(t); v.addWidget(val); v.addWidget(s)
    return f


class InterraterWindow(QDialog):
    def __init__(self, parent, P, S, folder: Path, grouped, profile, me: str = "", rater: str | None = None,
                 base_profile=None):
        """`profile` decides types and filters; `base_profile` is the study
        rated first (A) — a consensus study is made from it."""
        super().__init__(parent)
        self.base_profile = base_profile or profile
        self.P, self.S, self.folder, self.grouped, self.profile = P, S, folder, grouped, profile
        self.me = me or S["ir_me"]
        self.setWindowTitle(S["ir_title"])
        self.resize(1280, 900)
        v = QVBoxLayout(self); v.setContentsMargins(24, 18, 24, 16); v.setSpacing(12)
        head = QHBoxLayout()
        self.title = QLabel(""); self.title.setStyleSheet("font-size: 20px; font-weight: 700;")
        head.addWidget(self.title, 1)
        head.addWidget(QLabel(S["ir_compare_with"]))
        self.rater_combo = QComboBox(); self.rater_combo.addItems(ir.list_raters(folder))
        if rater:
            self.rater_combo.setCurrentText(rater)
        self.rater_combo.currentTextChanged.connect(lambda _t: self.refresh())
        head.addWidget(self.rater_combo)
        head.addWidget(QLabel(S["ir_groups"]))
        self.tp_combo = QComboBox(); self.tp_combo.addItem(S["batch_filter_all"], None)
        for tp in grouped.timepoints():
            if not tp.startswith("_"):
                self.tp_combo.addItem(tp, tp)
        self.tp_combo.currentIndexChanged.connect(lambda _i: self._draw_plots())
        head.addWidget(self.tp_combo)
        self.all_cb = QCheckBox(S["ir_all"]); self.all_cb.setToolTip(S["ir_all_tip"])
        self.all_cb.toggled.connect(lambda _on: self.refresh())
        self.all_cb.setEnabled(len(ir.list_raters(folder)) >= 2)
        head.addWidget(self.all_cb)
        # A as it was when the comparison started: corrections taken over from
        # B afterwards would otherwise flatter the agreement.
        self.snap_cb = QCheckBox(S["ir_use_backup"]); self.snap_cb.setChecked(True)
        self.snap_cb.setToolTip(S["ir_use_backup_tip"])
        self.snap_cb.toggled.connect(lambda _on: self.refresh())
        head.addWidget(self.snap_cb)
        v.addLayout(head)
        self.lock = QLabel(""); self.lock.setObjectName("staleBox"); self.lock.setWordWrap(True); self.lock.hide()
        v.addWidget(self.lock)

        self.tiles = QHBoxLayout(); self.tiles.setSpacing(12); v.addLayout(self.tiles)

        mid = QHBoxLayout(); mid.setSpacing(12)
        card = QFrame(); card.setObjectName("settingsCard")
        cv = QVBoxLayout(card); cv.setContentsMargins(14, 12, 14, 12)
        self.matrix_title = QLabel(""); self.matrix_title.setStyleSheet("font-weight: 600;")
        cv.addWidget(self.matrix_title)
        self.table = QTableWidget(); self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        cv.addWidget(self.table, 1)
        leg = QLabel(S["ir_legend"]); leg.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 11px;")
        leg.setWordWrap(True)
        cv.addWidget(leg)
        mid.addWidget(card, 2)

        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        pcard = QFrame(); pcard.setObjectName("settingsCard")
        pv = QVBoxLayout(pcard); pv.setContentsMargins(8, 8, 8, 8)
        self.fig = Figure(figsize=(8.5, 4.0), facecolor=P["panel_bg"])
        self.plot = FigureCanvasQTAgg(self.fig)
        pv.addWidget(self.plot)
        mid.addWidget(pcard, 3)
        v.addLayout(mid, 1)

        icc = QLabel(S["ir_icc_text"] + "<br><br>" + S["ir_refs_title"] + "<br>" + "<br>".join(
            f"<span style='color:{P['fg_secondary']}; font-size:11px'>{r}</span>" for r in REFERENCES))
        icc.setWordWrap(True); icc.setTextFormat(Qt.RichText)
        icc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        icc.setObjectName("settingsCard"); icc.setStyleSheet("padding: 10px 14px;")
        v.addWidget(icc)

        foot = QHBoxLayout()
        self.foot = QLabel(""); self.foot.setStyleSheet(f"color: {P['fg_secondary']};"); self.foot.setWordWrap(True)
        exp = QPushButton(S["ir_export"]); exp.clicked.connect(lambda: self._export())
        close = QPushButton(S["qt_close"]); close.setProperty("class", "primary"); close.clicked.connect(self.accept)
        foot.addWidget(self.foot, 1); foot.addWidget(exp); foot.addWidget(close)
        v.addLayout(foot)
        self.ag: ir.Agreement | None = None
        self.refresh()

    # ── data ──
    def refresh(self) -> None:
        S, P = self.S, self.P
        rater = self.rater_combo.currentText()
        if not rater:
            self.title.setText(S["ir_title"])
            self.foot.setText(S["ir_none"])
            return
        locked, done, total = ir.blind_locked(self.folder, rater)
        self.lock.setVisible(locked)
        if locked:
            self.lock.setText(S["ir_blind_locked"].format(name=rater, done=done, total=total))
            self.title.setText(S["ir_head"].format(a=self.me, b=rater))
            self.ag = None
            while self.tiles.count():
                w = self.tiles.takeAt(0).widget()
                if w:
                    w.deleteLater()
            self.table.clear(); self.table.setRowCount(0); self.table.setColumnCount(0)
            self.fig.clear(); self.plot.draw_idle()
            return
        a_dir = ir.a_backup_dir(self.folder, rater) if self.snap_cb.isChecked() else None
        sel = ir.selection(self.folder, rater)          # ticks in the list, metric in the bar
        self.ag = ag = ir.compare(self.folder, self.grouped, self.profile, rater, self.me, a_dir=a_dir,
                                  excluded=sel["excluded"], metric=sel["metric"])
        self.title.setText(S["ir_head"].format(a=self.me, b=rater))
        while self.tiles.count():
            w = self.tiles.takeAt(0).widget()
            if w:
                w.deleteLater()
        k, klo, khi = ag.kappa
        i, ilo, ihi = ag.icc
        good = lambda x, thr: P["state_finished"] if x == x and x >= thr else P["accent_warn"]
        self.tiles.addWidget(_tile(S["ir_same"], _fmt(S, ag.pct_same, 1) + " %",
                                   S["ir_same_sub"].format(n=f"{ag.n_same:,}".replace(",", " "),
                                                           total=f"{ag.n_cells:,}".replace(",", " ")),
                                   good(ag.pct_same, 90), P))
        self.tiles.addWidget(_tile(S["ir_kappa"], _fmt(S, k), S["ir_ci"].format(lo=_fmt(S, klo), hi=_fmt(S, khi))
                                   + " · " + S[self._kappa_word(k)], good(k, 0.8), P))
        self.tiles.addWidget(_tile(S["ir_icc"].format(m=self._metric_name()), _fmt(S, i), S["ir_ci"].format(lo=_fmt(S, ilo), hi=_fmt(S, ihi))
                                   + f" · n = {len(ag.groups)}", good(i, 0.75), P))
        if self.all_cb.isChecked():
            names = [r for r in ir.list_raters(self.folder) if not ir.blind_locked(self.folder, r)[0]]
            many = ir.compare_many(self.folder, self.grouped, self.profile, [None, *names], [self.me, *names],
                                   a_dir=a_dir, excluded=sel["excluded"], metric=sel["metric"])
            fi, flo, fhi = many.icc
            self.tiles.addWidget(_tile(S["ir_fleiss"].format(k=len(many.raters)), _fmt(S, many.fleiss),
                                       S["ir_fleiss_sub"].format(n=many.n_cells, icc=_fmt(S, fi),
                                                                 lo=_fmt(S, flo), hi=_fmt(S, fhi)),
                                       good(many.fleiss, 0.8), P))
        else:
            sub = S["ir_masks_sub"].format(n=ag.n_images)
            if ag.n_masks_changed:
                sub += " · " + S["ir_masks_changed"].format(n=ag.n_masks_changed)
            self.tiles.addWidget(_tile(S["ir_masks"], S["ir_masks_ok"], sub, P["state_finished"], P))
        self._fill_matrix()
        self._draw_plots()
        self.foot.setText(S["ir_foot"].format(n=ag.n_images, a=ag.manual_only_a, b=ag.manual_only_b,
                                              me=self.me, rater=rater)
                          + (" " + S["ir_left_out"].format(n=ag.n_left_out) if ag.n_left_out else ""))

    def _metric_name(self) -> str:
        from ..stats import available_metrics
        key = self.ag.metric if self.ag else ir.DEFAULT_METRIC
        return dict(available_metrics(self.profile)).get(key, key)

    def _kappa_word(self, k: float) -> str:
        # Landis & Koch (1977)
        for thr, key in ((0.81, "ir_k_almost"), (0.61, "ir_k_substantial"), (0.41, "ir_k_moderate"),
                         (0.21, "ir_k_fair"), (0.0, "ir_k_slight")):
            if k == k and k >= thr:
                return key
        return "ir_k_poor"

    def _type_name(self, key: str) -> str:
        if key == ir.EXCLUDED:
            return self.S["ir_excluded"]
        t = self.profile.type_by_key(key)
        return (t.name or t.key) if t else key

    def _fill_matrix(self) -> None:
        S, P, ag = self.S, self.P, self.ag
        self.matrix_title.setText(S["ir_matrix"].format(a=ag.rater_a, b=ag.rater_b))
        cats = ag.categories
        self.table.clear()
        self.table.setRowCount(len(cats)); self.table.setColumnCount(len(cats))
        names = [self._type_name(c) for c in cats]
        self.table.setHorizontalHeaderLabels(names); self.table.setVerticalHeaderLabels(names)
        blue, red, base = QColor("#2161D9"), QColor("#C82D2D"), QColor(P["panel_bg"])
        for r in range(len(cats)):
            row_sum = int(ag.matrix[r].sum())
            for c in range(len(cats)):
                n = int(ag.matrix[r, c])
                share = n / row_sum if row_sum else 0.0
                if r == c:
                    t = 0.15 + 0.75 * share
                    col = _mix(base.name(), blue.name(), t)
                else:
                    t = min(0.9, share * 6) if n else 0.0
                    col = _mix(base.name(), red.name(), t)
                it = QTableWidgetItem(f"{n:,}".replace(",", " "))
                it.setTextAlignment(Qt.AlignCenter)
                it.setBackground(QColor(col))
                it.setForeground(QColor("#FFFFFF") if t > 0.5 else QColor(P["fg"]))
                it.setToolTip(S["ir_cell_tip"].format(a=names[r], b=names[c], n=n,
                                                     pct=_fmt(S, 100 * share, 1)))
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()

    def _draw_plots(self) -> None:
        if self.ag is None:
            return
        S, P = self.S, self.P
        tp = self.tp_combo.currentData()
        groups = [g for g in self.ag.groups if tp is None or g.tp == tp]
        x = [g.val_a for g in groups]
        y = [g.val_b for g in groups]
        self.fig.clear()
        ax1, ax2 = self.fig.subplots(1, 2)
        for ax in (ax1, ax2):
            ax.set_facecolor(P["panel_bg"])
            ax.grid(True, color=_mix(P["panel_bg"], P["fg"], 0.12), linewidth=0.8)
            ax.set_axisbelow(True)
            ax.tick_params(colors=P["fg_secondary"], labelsize=8)
            for s in ax.spines.values():
                s.set_color(_mix(P["panel_bg"], P["fg"], 0.35))
        acc, warn, fg = P["accent_primary"], P["accent_warn"], P["fg"]
        # A against B with the 45° line
        hi = max([*x, *y, 1.0]) * 1.08
        ax1.plot([0, hi], [0, hi], linestyle="--", color=P["state_finished"], linewidth=1.3, label="y = x (45°)")
        ax1.scatter(x, y, s=26, color=acc, alpha=0.8, edgecolors="white", linewidths=0.6, zorder=3)
        ax1.set_xlim(0, hi); ax1.set_ylim(0, hi)          # same scale on both axes
        ax1.set_xlabel(S["ir_axis_a"].format(a=self.ag.rater_a, m=self._metric_name()), color=fg, fontsize=9)
        ax1.set_ylabel(S["ir_axis_b"].format(b=self.ag.rater_b, m=self._metric_name()), color=fg, fontsize=9)
        ax1.set_title(S["ir_scatter"], color=fg, fontsize=10, loc="left")
        ax1.legend(loc="upper left", fontsize=8, frameon=False, labelcolor=P["state_finished"])
        # Bland-Altman
        md, lo, up = ir.bland_altman(x, y)
        means = [(a + b) / 2 for a, b in zip(x, y)]
        diffs = [b - a for a, b in zip(x, y)]
        ax2.axhline(0, color=_mix(P["panel_bg"], P["fg"], 0.3), linewidth=0.8)
        if md == md:
            ax2.axhline(md, color=fg, linewidth=1.3, label=S["ir_ba_mean"].format(v=_fmt(S, md)))
            ax2.axhline(up, color=warn, linestyle="--", linewidth=1.2, label=f"+1,96 SD: {_fmt(S, up)}")
            ax2.axhline(lo, color=warn, linestyle="--", linewidth=1.2, label=f"−1,96 SD: {_fmt(S, lo)}")
            ax2.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=3, fontsize=7.5,
                       frameon=False, labelcolor=fg)
        ax2.scatter(means, diffs, s=26, color=acc, alpha=0.8, edgecolors="white", linewidths=0.6, zorder=3)
        ax2.set_xlabel(S["ir_ba_x"].format(m=self._metric_name()), color=fg, fontsize=9)
        ax2.set_ylabel(S["ir_ba_y"].format(a=self.ag.rater_a, b=self.ag.rater_b), color=fg, fontsize=9)
        ax2.set_title(S["ir_ba"].format(m=self._metric_name()), color=fg, fontsize=10, loc="left")
        if diffs:
            span = max(abs(v) for v in [*diffs, up, lo] if v == v) * 1.25 or 1.0
            ax2.set_ylim(-span, span)
        self.fig.tight_layout()
        self.plot.draw_idle()

    def _export(self, dest: str | None = None) -> None:
        if self.ag is None:
            return
        S = self.S
        if dest is None:
            default = f"Uebereinstimmung_{self.ag.rater_a}_{self.ag.rater_b}.csv".replace(" ", "_")
            dest, _ = QFileDialog.getSaveFileName(self, S["ir_export"], default, "CSV (*.csv)")
        if not dest:
            return
        names = {t.key: (t.name or t.key) for t in self.profile.cell_types}
        ir.write_csv(self.ag, Path(dest), names)
        png = Path(dest).with_suffix(".png")
        self.fig.savefig(png, dpi=200, facecolor="white")
        QMessageBox.information(self, S["ir_export"], S["ir_exported"].format(path=dest, png=png.name))


def compare_package_flow(parent, P, S, folder: Path, zip_path: Path) -> str | None:
    """Store a package as second rater (identical masks only); returns the
    rater's name or None. Tells the user which images were left out and why."""
    try:
        res = ir.import_rater(zip_path, folder)
    except ValueError:
        QMessageBox.warning(parent, S["ir_title"], S["ir_no_identical"])
        return None
    msg = S["ir_imported"].format(n=len(res["imported"]), name=res["name"])
    if res["rejected"]:
        reasons = {"different": S["ir_rej_different"], "no_local": S["ir_rej_no_local"],
                   "no_mask": S["ir_rej_no_mask"], "no_review": S["ir_rej_no_review"]}
        lines = [f"– {stem}: {reasons.get(why, why)}" for stem, why in list(res["rejected"].items())[:15]]
        more = len(res["rejected"]) - 15
        msg += "\n\n" + S["ir_rejected"].format(n=len(res["rejected"])) + "\n" + "\n".join(lines)
        if more > 0:
            msg += f"\n… +{more}"
    QMessageBox.information(parent, S["ir_title"], msg)
    return res["name"]
