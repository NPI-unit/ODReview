"""Phase 4 windows (mockup v3): Paket erstellen / importieren (with the
conflict question), Statistik (boxplot), Übersicht, Zellflächen. Rules and
numbers come from package.py, stats.py, areas.py and study.py."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressDialog,
    QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .. import package as pkg
from .. import stats as statsmod
from ..areas import write_csv
from ..cellpose_runner import has_cache
from ..persistence import ReviewState
from ..study import study_progress
from ..tasks import Task
from .processing import ROLE, _disp, _footer
from .style import _mix
from .tasks import run_task_modal


def _num(v: float, S: dict, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace(".", S["qt_decimal_sep"])


# ─────────────────────────────────────────────
# Paket erstellen
# ─────────────────────────────────────────────
class PackageCreateDialog(QDialog):
    def __init__(self, parent, P, S, folder: Path, grouped, profile, reviewer: str):
        super().__init__(parent)
        self.S, self.folder, self.grouped, self.profile = S, folder, grouped, profile
        self.reviewer_used: str | None = None
        self.setWindowTitle(S["pkg_create_title"])
        self.resize(820, 640)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        body = QWidget(); bv = QVBoxLayout(body); bv.setContentsMargins(24, 18, 24, 12); bv.setSpacing(10)
        card = QFrame(); card.setObjectName("settingsCard")
        g = QGridLayout(card); g.setContentsMargins(14, 12, 14, 12); g.setHorizontalSpacing(12)
        g.addWidget(QLabel(S["qt_nav_study"]), 0, 0)
        g.addWidget(QLabel(f"<b>{profile.name}</b>  <span style='color:{P['fg_secondary']}'>· "
                           f"{S['qt_badge_travels']}</span>"), 0, 1)
        g.addWidget(QLabel(S["pkg_reviewer"]), 1, 0)
        self.reviewer = QLineEdit(reviewer); self.reviewer.setMaximumWidth(260)
        g.addWidget(self.reviewer, 1, 1)
        bv.addWidget(card)
        self.include = QCheckBox(S["pkg_include_images"]); self.include.setChecked(True)
        self.include.toggled.connect(lambda *_: self._update())
        bv.addWidget(self.include)
        self._sizes: dict[str, tuple[int, int, int]] = {}
        row = QHBoxLayout()
        lab = QLabel(S["pkg_pick_images"]); lab.setStyleSheet("font-weight: 600;")
        row.addWidget(lab, 1)
        for text, val in ((S["pkg_select_all"], True), (S["pkg_select_none"], False)):
            b = QPushButton(text); b.clicked.connect(lambda _c=False, v=val: self._set_all(v)); row.addWidget(b)
        bv.addLayout(row)
        self.tree = QTreeWidget(); self.tree.setHeaderHidden(True)
        self.tree.itemChanged.connect(lambda *_: self._update())
        bv.addWidget(self.tree, 1)
        v.addWidget(body, 1)
        f = _footer(v)
        self.count = QLabel("")
        cancel = QPushButton(S["settings_cancel"]); cancel.clicked.connect(self.reject)
        self.go = QPushButton(S["pkg_create_btn"]); self.go.setProperty("class", "primary")
        self.go.clicked.connect(self._create)
        f.addWidget(self.count, 1); f.addWidget(cancel); f.addWidget(self.go)
        self._fill()

    def _fill(self) -> None:
        S = self.S
        self.tree.blockSignals(True)
        for tp in self.grouped.timepoints():
            tp_item = None
            for sub in self.grouped.subjects_in(tp):
                infos = [i for i in self.grouped.tree[tp][sub] if has_cache(self.folder, i.path)]
                if not infos:
                    continue
                if tp_item is None:
                    tp_item = QTreeWidgetItem([_disp(S, tp=tp)])
                    tp_item.setFlags(tp_item.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                    self.tree.addTopLevelItem(tp_item)
                si = QTreeWidgetItem([_disp(S, sub=sub)])
                si.setFlags(si.flags() | Qt.ItemIsAutoTristate | Qt.ItemIsUserCheckable)
                tp_item.addChild(si)
                for info in infos:
                    it = QTreeWidgetItem([info.path.name])
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(0, Qt.Checked)
                    it.setData(0, ROLE, info.path.stem)
                    self._sizes[info.path.stem] = pkg.entry_sizes(self.folder, info.path)
                    si.addChild(it)
            if tp_item is not None:
                tp_item.setExpanded(True)
        self.tree.blockSignals(False)
        self._update()

    def _leaves(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            if it.data(0, ROLE):
                yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def selected(self) -> set[str]:
        return {it.data(0, ROLE) for it in self._leaves() if it.checkState(0) == Qt.Checked}

    def _set_all(self, on: bool) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self._update()

    def estimate(self) -> tuple[int, int, int]:
        """(images, masks, reviews) bytes of the current selection, uncompressed."""
        sel = self.selected()
        img = sum(self._sizes.get(s, (0, 0, 0))[0] for s in sel) if self.include.isChecked() else 0
        masks = sum(self._sizes.get(s, (0, 0, 0))[1] for s in sel)
        rev = sum(self._sizes.get(s, (0, 0, 0))[2] for s in sel)
        return img, masks, rev

    def _update(self) -> None:
        S = self.S
        n = len(self.selected())
        img, masks, rev = self.estimate()
        dec = S["qt_decimal_sep"]
        size = S["pkg_size"].format(total=pkg.fmt_bytes(img + masks + rev, dec))
        parts = S["pkg_size_parts"].format(images=pkg.fmt_bytes(img, dec), masks=pkg.fmt_bytes(masks, dec),
                                           reviews=pkg.fmt_bytes(rev, dec))
        self.count.setText(f"{S['pkg_n_images'].format(n=n)}  <b>{size}</b><br>"
                           f"<span style='font-size:11px'>{parts}</span>")
        self.go.setEnabled(n > 0)

    def _create(self, dest: str | None = None) -> None:
        S = self.S
        sel = self.selected()
        if dest is None:
            default = f"{self.profile.name}_{self.folder.name}.eryspkg.zip".replace(" ", "_")
            dest, _ = QFileDialog.getSaveFileName(self, S["pkg_save_as"], default, "ZIP (*.zip)")
        if not dest:
            return
        import shutil
        need = sum(self.estimate())
        try:
            free = shutil.disk_usage(Path(dest).parent).free
        except OSError:
            free = None
        if free is not None and free < need:
            dec = S["qt_decimal_sep"]
            QMessageBox.warning(self, S["pkg_create_title"], S["pkg_no_space"].format(
                need=pkg.fmt_bytes(need, dec), free=pkg.fmt_bytes(free, dec)))
            return
        reviewer = self.reviewer.text().strip()
        work = lambda progress, cancelled: pkg.create_package(
            self.folder, self.grouped, self.profile, Path(dest),
            include_images=self.include.isChecked(), reviewer=reviewer, selected_stems=sel,
            progress_cb=lambda i, n, name: progress(i, f"{i}/{n}  {name}"))
        try:
            res = run_task_modal(self, Task(work=work), S["pkg_create_title"], S["pkg_creating"],
                                 maximum=len(sel))
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, S["pkg_create_title"], str(e))
            return
        self.reviewer_used = reviewer
        QMessageBox.information(self, S["pkg_create_title"],
                                S["pkg_created_msg"].format(n=res["n_images"], path=res["path"]))
        self.accept()


# ─────────────────────────────────────────────
# Paket importieren
# ─────────────────────────────────────────────
class ConflictDialog(QDialog):
    """One image already has a local review: keep it or take the package's.
    Esc keeps the local one — the safe choice."""

    def __init__(self, parent, P, S, folder: Path, stem: str):
        super().__init__(parent)
        self.choice = pkg.KEEP
        self.setWindowTitle(S["pkg_conflict_title"])
        self.setMinimumWidth(500)
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 20); v.setSpacing(12)
        t = QLabel(S["pkg_conflict_title"]); t.setStyleSheet("font-size: 16px; font-weight: 700;")
        msg = QLabel(S["pkg_conflict_msg"].format(name=stem)); msg.setWordWrap(True)
        v.addWidget(t); v.addWidget(msg)
        rv = ReviewState.load(folder, stem)
        local = QLabel(f"<span style='color:{P['fg_muted']};font-size:11px'>LOKAL</span><br>"
                       f"{S['status_finished'] if rv.is_finished else '–'} · "
                       f"{len(rv.force_excluded)} {S['status_excluded']} · "
                       f"{len(rv.manual_cells)} {S['details_extras_manual']}")
        local.setObjectName("settingsCard"); local.setStyleSheet("padding: 10px;")
        v.addWidget(local)
        row = QHBoxLayout(); row.addStretch(1)
        for text, val, primary in ((S["pkg_conflict_keep_all"], pkg.KEEP_ALL, False),
                                   (S["pkg_conflict_import_all"], pkg.IMPORT_ALL, False),
                                   (S["pkg_conflict_keep"], pkg.KEEP, False),
                                   (S["pkg_conflict_import"], pkg.IMPORT, True)):
            b = QPushButton(text)
            if primary:
                b.setProperty("class", "primary")
            b.clicked.connect(lambda _c=False, x=val: self._pick(x))
            row.addWidget(b)
        v.addLayout(row)

    def _pick(self, val: str) -> None:
        self.choice = val
        self.accept()


def import_package_flow(parent, P, S, active_profile, ask_zip=None, ask_target=None) -> dict | None:
    """Pick a package, confirm, choose the target, merge. Runs on the GUI thread
    like the Tk version: the merge asks per conflicting image (K4)."""
    zip_str = ask_zip() if ask_zip else QFileDialog.getOpenFileName(
        parent, S["pkg_import_pick"], "", "Erys package / ZIP (*.zip);;All files (*)")[0]
    if not zip_str:
        return None
    zip_path = Path(zip_str)
    manifest = pkg.inspect_package(zip_path)
    if manifest is None:
        QMessageBox.information(parent, S["pkg_import_title"], S["pkg_not_a_package"])
        return None
    info = S["pkg_import_info"].format(
        reviewer=manifest.get("reviewer") or "?", n=manifest.get("n_images", 0),
        images=S["confirm_yes"] if manifest.get("includes_images") else S["confirm_no"],
        profile=manifest.get("profile", {}).get("name", "?"))
    if QMessageBox.question(parent, S["pkg_import_title"], info) != QMessageBox.Yes:
        return None
    target_str = ask_target() if ask_target else QFileDialog.getExistingDirectory(parent, S["pkg_pick_target"])
    if not target_str:
        return None
    target = Path(target_str)
    if manifest.get("includes_images") and target.name != manifest.get("source_folder_name"):
        sub = target / (manifest.get("source_folder_name") or "imported")
        if QMessageBox.question(parent, S["pkg_import_title"],
                                S["pkg_import_subfolder"].format(name=sub.name)) == QMessageBox.Yes:
            target = sub
    dlg = QProgressDialog(S["pkg_importing"], None, 0, max(1, manifest.get("n_images", 1)), parent)
    dlg.setWindowTitle(S["pkg_import_title"]); dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0); dlg.setCancelButton(None)

    def prog(i, n, stem):
        dlg.setValue(i); dlg.setLabelText(f"{S['pkg_importing']}\n{i}/{n}  {stem}")
        QApplication.processEvents()

    def resolver(stem: str) -> str:
        c = ConflictDialog(parent, P, S, target, stem)
        c.exec()
        return c.choice
    try:
        result = pkg.import_package(zip_path, target, active_profile, resolver, prog)
    except Exception as e:  # noqa: BLE001
        dlg.close()
        QMessageBox.warning(parent, S["pkg_import_title"], str(e))
        return None
    dlg.close()
    msg = S["pkg_import_done"].format(imported=len(result["imported"]), skipped=len(result["skipped"]),
                                      target=result["target"])
    if result["warnings"]:
        msg += "\n\n⚠ " + "\n".join(result["warnings"])
    QMessageBox.information(parent, S["pkg_import_title"], msg)
    return result


# ─────────────────────────────────────────────
# Statistik
# ─────────────────────────────────────────────
class StatsWindow(QDialog):
    def __init__(self, parent, P, S, folder: Path, profile, grouped):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure
        self.P, self.S, self.folder, self.profile, self.grouped = P, S, folder, profile, grouped
        self.setWindowTitle(f"{S['stats_title']} · {profile.name}")
        self.resize(1100, 720)
        self.records = statsmod.collect_group_records(folder, profile, grouped)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        top = QFrame(); top.setObjectName("toolbar")
        h = QHBoxLayout(top); h.setContentsMargins(24, 12, 24, 12); h.setSpacing(10)
        h.addWidget(QLabel(S["stats_metric"].rstrip(":")))
        self.metric_combo = QComboBox(); self.metric_combo.setMinimumWidth(240)
        for key, lab in statsmod.available_metrics(profile):
            self.metric_combo.addItem(lab, key)
        self.metric_combo.currentIndexChanged.connect(lambda *_: self.redraw())
        h.addWidget(self.metric_combo)
        h.addSpacing(16); h.addWidget(QLabel(S["stats_view"].rstrip(":")))
        self.by = QButtonGroup(self)
        for i, (val, key) in enumerate((("timepoint", "stats_by_timepoint"), ("subject", "stats_by_subject"))):
            rb = QRadioButton(S[key]); rb.setChecked(i == 0); rb.setProperty("val", val)
            rb.toggled.connect(lambda on: on and self.redraw())
            self.by.addButton(rb); h.addWidget(rb)
        h.addStretch(1)
        refresh = QPushButton(S["btn_refresh"]); refresh.clicked.connect(self._reload)
        h.addWidget(refresh)
        v.addWidget(top)
        body = QWidget(); bh = QHBoxLayout(body); bh.setContentsMargins(24, 18, 24, 18); bh.setSpacing(18)
        self.fig = Figure(figsize=(5, 4), facecolor=P["panel_bg"])
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        chart = QFrame(); chart.setObjectName("settingsCard")
        cl = QVBoxLayout(chart); cl.setContentsMargins(10, 10, 10, 10); cl.addWidget(self.canvas)
        bh.addWidget(chart, 5)
        self.table = QTreeWidget(); self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels([S["stats_col_group"], "n", S["stats_mean"], S["stats_median"],
                                    S["stats_sd"], S["stats_min"], S["stats_max"], "Q1", "Q3"])
        from PySide6.QtWidgets import QHeaderView
        self.table.header().setSectionResizeMode(QHeaderView.Stretch)   # all 9 columns fit
        bh.addWidget(self.table, 6)
        v.addWidget(body, 1)
        self.empty = QLabel(S["stats_no_data"]); self.empty.setAlignment(Qt.AlignCenter)
        v.addWidget(self.empty)
        self.redraw()

    def _reload(self) -> None:
        self.records = statsmod.collect_group_records(self.folder, self.profile, self.grouped)
        self.redraw()

    def redraw(self) -> None:
        P, S = self.P, self.S
        by = next(b.property("val") for b in self.by.buttons() if b.isChecked())
        groups = statsmod.grouped_values(self.records, by, self.metric_combo.currentData())
        self.empty.setVisible(not groups)
        ax = self.ax
        ax.clear()
        ax.set_facecolor(P["panel_bg"])
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(P["border"])
        ax.tick_params(colors=P["fg_secondary"])
        if groups:
            labels = list(groups)
            bp = ax.boxplot([groups[k] for k in labels], tick_labels=labels, showmeans=True,
                            patch_artist=True, widths=0.5,
                            medianprops={"color": P["accent_warn"], "linewidth": 2.2},
                            meanprops={"marker": "^", "markerfacecolor": P["state_finished"],
                                       "markeredgecolor": P["state_finished"]},
                            whiskerprops={"color": P["fg_secondary"]},
                            capprops={"color": P["fg_secondary"]},
                            flierprops={"markeredgecolor": P["fg_muted"]})
            for box in bp["boxes"]:
                box.set_facecolor(_mix(P["panel_bg"], P["accent_primary"], 0.18))
                box.set_edgecolor(P["accent_primary"]); box.set_linewidth(1.8)
            ax.set_ylabel(self.metric_combo.currentText(), color=P["fg_secondary"])
            ax.grid(axis="y", color=P["border"], alpha=0.8)
            self.fig.tight_layout()
        self.canvas.draw_idle()
        self.table.clear()
        for label, vals in groups.items():
            d = statsmod.describe(vals)
            self.table.addTopLevelItem(QTreeWidgetItem(
                [label, str(d["n"])] + [_num(d[k], S, 2) for k in ("mean", "median", "sd", "min", "max", "q1", "q3")]))


# ─────────────────────────────────────────────
# Übersicht
# ─────────────────────────────────────────────
class DashboardWindow(QDialog):
    def __init__(self, parent, P, S, folder: Path, profile, grouped):
        super().__init__(parent)
        self.P, self.S, self.folder, self.profile, self.grouped = P, S, folder, profile, grouped
        self.setWindowTitle(f"{S['dash_title']} · {profile.name}")
        self.resize(1100, 720)
        v = QVBoxLayout(self); v.setContentsMargins(24, 18, 24, 18); v.setSpacing(14)
        self.tiles = QHBoxLayout(); self.tiles.setSpacing(12)
        v.addLayout(self.tiles)
        self.table = QTreeWidget(); self.table.setRootIsDecorated(False)
        self.table.setHeaderLabels([S["dash_col_tp"], S["dash_col_sub"], S["dash_col_images"],
                                    S["dash_col_progress"], S["dash_col_reached"], S["dash_col_time"]])
        from PySide6.QtWidgets import QHeaderView
        self.table.header().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self.subj = QLabel(""); self.subj.setWordWrap(True); self.subj.setStyleSheet(f"color: {P['fg_secondary']};")
        v.addWidget(self.subj)
        row = QHBoxLayout(); row.addStretch(1)
        r = QPushButton(S["btn_refresh"]); r.clicked.connect(self.populate)
        c = QPushButton(S["btn_close"]); c.setProperty("class", "primary"); c.clicked.connect(self.accept)
        row.addWidget(r); row.addWidget(c)
        v.addLayout(row)
        self.populate()

    def _tile(self, label: str, value: str, color: str | None = None) -> QFrame:
        f = QFrame(); f.setObjectName("settingsCard")
        l = QVBoxLayout(f); l.setContentsMargins(14, 12, 14, 12); l.setSpacing(2)
        a = QLabel(label); a.setStyleSheet(f"color: {self.P['fg_secondary']};")
        b = QLabel(value); b.setStyleSheet(f"font-size: 22px; font-weight: 700; color: {color or self.P['fg']};")
        l.addWidget(a); l.addWidget(b)
        return f

    def populate(self) -> None:
        S, P = self.S, self.P
        while self.tiles.count():
            w = self.tiles.takeAt(0).widget()
            if w:
                w.deleteLater()
        records = statsmod.collect_group_records(self.folder, self.profile, self.grouped)
        prog = study_progress(self.folder, self.grouped)
        total, per = statsmod.time_totals(records)
        target = max(1, self.profile.target_cells)
        n_groups = sum(len(self.grouped.subjects_in(tp)) for tp in self.grouped.timepoints())
        reached = sum(1 for r in records if r["reached"])
        avg = total / len(per) if per else 0.0
        self.tiles.addWidget(self._tile(S["study_menu_progress"].format(done="", total="").strip(" /"),
                                        f"{prog.done} / {prog.total}", P["state_finished"]))
        self.tiles.addWidget(self._tile(S["dash_col_reached"], f"{reached} / {n_groups}"))
        self.tiles.addWidget(self._tile(S["dash_total_time"].format(time="").rstrip(": "),
                                        statsmod.fmt_duration(total)))
        self.tiles.addWidget(self._tile("Ø " + S["dash_subject_totals"].rstrip(":"),
                                        statsmod.fmt_duration(avg)))
        self.table.clear()
        for r in records:
            it = QTreeWidgetItem([r["timepoint"], r["subject"], str(r["n_images"]), f"{r['rbc']} / {target}",
                                  S["dash_yes"] if r["reached"] else S["dash_no"], statsmod.fmt_duration(r["time_s"])])
            if r["reached"]:
                it.setForeground(4, QApplication.palette().link())
            self.table.addTopLevelItem(it)
        self.subj.setText(S["dash_subject_totals"] + "  " + ("   ".join(
            f"{s}: {statsmod.fmt_duration(t)}" for s, t in per.items()) or "—"))


# ─────────────────────────────────────────────
# Zellflächen
# ─────────────────────────────────────────────
class AreasWindow(QDialog):
    def __init__(self, parent, P, S, groups):
        super().__init__(parent)
        self.S, self.groups = S, groups
        self.setWindowTitle(S["areas_title"])
        self.resize(1000, 600)
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(0)
        body = QWidget(); bv = QVBoxLayout(body); bv.setContentsMargins(24, 18, 24, 12); bv.setSpacing(10)
        hint = QLabel(S["areas_hint"]); hint.setWordWrap(True); hint.setStyleSheet(f"color: {P['fg_secondary']};")
        bv.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([S["areas_col_group"], S["areas_col_n"], S["areas_col_mean"],
                                   S["areas_col_median"], S["areas_col_sd"], S["areas_col_min"], S["areas_col_max"]])
        self.tree.setColumnWidth(0, 300)
        vals = lambda st: [str(st.n), _num(st.mean, S), _num(st.median, S), _num(st.sd, S),
                           f"{st.minimum:.0f}", f"{st.maximum:.0f}"]
        if not groups:
            self.tree.addTopLevelItem(QTreeWidgetItem([S["stats_no_data"]]))
        for g in groups:
            gi = QTreeWidgetItem([f"{g.timepoint} / {g.subject}"] + vals(g.stats))
            for stem, st in g.per_image:
                gi.addChild(QTreeWidgetItem([stem] + vals(st)))
            self.tree.addTopLevelItem(gi)
        bv.addWidget(self.tree, 1)
        v.addWidget(body, 1)
        f = _footer(v)
        f.addStretch(1)
        save = QPushButton(S["areas_save_csv"]); save.clicked.connect(self._save)
        close = QPushButton(S["btn_close"]); close.setProperty("class", "primary"); close.clicked.connect(self.accept)
        f.addWidget(save); f.addWidget(close)

    def _save(self, dest: str | None = None) -> None:
        if dest is None:
            dest, _ = QFileDialog.getSaveFileName(self, self.S["areas_save_csv"], "Zellflaechen.csv", "CSV (*.csv)")
        if not dest:
            return
        write_csv(self.groups, dest)
        QMessageBox.information(self, self.S["areas_title"], self.S["areas_saved"].format(path=dest))
