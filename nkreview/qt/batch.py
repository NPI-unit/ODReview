"""File › Segmentation …: choose the images, then run them – as ErysReview's
"process several images". Nothing is segmented without it."""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QStackedWidget, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator, QVBoxLayout, QWidget,
)

from ..segment import has_cellpose_cache, image_pixel_size
from ..state import Review
from ..study import ImageInfo, Study, is_counted, objective_rule

ALL = "__all__"


class BatchDialog(QDialog):
    def __init__(self, parent, study: Study, images: list[ImageInfo], worker):
        super().__init__(parent)
        self.setWindowTitle("Segmentation"); self.resize(720, 620)
        self.setAttribute(Qt.WA_DeleteOnClose)          # drops its worker connections with it
        self.study, self.images, self.worker = study, images, worker
        p = study.settings.cellpose
        self.seg = {str(i.path): has_cellpose_cache(i.folder, i.path, p) for i in images}
        self.counted = {str(i.path): is_counted(i, Review.load(i.folder, i.path), study.settings) for i in images}
        self.allowed = {str(i.path): objective_rule(study.settings, i.objective)["segment"] for i in images}
        self.pages = QStackedWidget()
        root = QVBoxLayout(self); root.addWidget(self.pages)
        self.pages.addWidget(self._choose_page()); self.pages.addWidget(self._run_page())
        self._fill()
        self._select("default")

    # ── choose ──────────────────────────────────────────────
    def _choose_page(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w)
        row = QHBoxLayout(); row.addWidget(QLabel("Select:"))
        for key, text in (("default", "Unsegmented, counted"), ("all", "All"), ("unseg", "Unsegmented"),
                          ("none", "None"), ("invert", "Invert")):
            b = QPushButton(text); b.clicked.connect(lambda _=False, k=key: self._select(k)); row.addWidget(b)
        row.addStretch(1); v.addLayout(row)
        row = QHBoxLayout()
        self.obj_combo, self.sub_combo = QComboBox(), QComboBox()
        for combo, label, values in ((self.obj_combo, "Magnification", sorted({i.objective or "unknown" for i in self.images})),
                                     (self.sub_combo, "Subject", sorted({i.proband for i in self.images}))):
            combo.addItem("All", ALL)
            for val in values:
                combo.addItem(val, val)
            combo.currentIndexChanged.connect(lambda _: self._fill(keep=True))
            row.addWidget(QLabel(label)); row.addWidget(combo)
        row.addStretch(1); v.addLayout(row)
        self.tree = QTreeWidget(); self.tree.setColumnCount(2); self.tree.setHeaderLabels(["Images", "Segmented"])
        self.tree.setColumnWidth(0, 440)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        v.addWidget(self.tree, 1)
        self.force_cb = QCheckBox("Re-segment images that are already segmented")
        self.force_cb.toggled.connect(lambda *_: self._update_count())
        v.addWidget(self.force_cb)
        foot = QHBoxLayout()
        self.count_label = QLabel(""); foot.addWidget(self.count_label); foot.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject); foot.addWidget(cancel)
        self.start_btn = QPushButton("Start"); self.start_btn.setProperty("class", "primary")
        self.start_btn.clicked.connect(lambda: self._start()); foot.addWidget(self.start_btn)   # clicked sends a bool
        v.addLayout(foot)
        return w

    def _fill(self, keep: bool = False) -> None:
        checked = self.chosen() if keep else set()
        obj, sub = self.obj_combo.currentData(), self.sub_combo.currentData()
        self.tree.blockSignals(True); self.tree.clear()
        groups: dict = {}
        for i in self.images:
            o = i.objective or "unknown"
            if (obj != ALL and o != obj) or (sub != ALL and i.proband != sub):
                continue
            groups.setdefault(i.proband, {}).setdefault(o, []).append(i)
        for s in sorted(groups, key=str.lower):
            si = QTreeWidgetItem([f"Subject {s}", ""])
            si.setFlags(si.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            self.tree.addTopLevelItem(si)
            for o in sorted(groups[s], key=mag_order):
                infos = groups[s][o]
                oi = QTreeWidgetItem([o, f"{sum(self.seg[str(i.path)] for i in infos)} / {len(infos)}"])
                oi.setFlags(oi.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                si.addChild(oi); oi.setExpanded(True)
                for i in sorted(infos, key=lambda k: k.path.name):
                    key = str(i.path)
                    it = QTreeWidgetItem([("" if self.counted[key] else "⊘ ") + i.path.name,
                                          "●" if self.seg[key] else "○"])
                    it.setData(0, Qt.UserRole, key)
                    if self.allowed[key]:
                        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                        it.setCheckState(0, Qt.Checked if key in checked else Qt.Unchecked)
                    else:
                        it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                        it.setText(1, "overview")
                    oi.addChild(it)
            si.setExpanded(True)
        self.tree.blockSignals(False)
        self._update_count()

    def _leaves(self):
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            if item.data(0, Qt.UserRole) and item.flags() & Qt.ItemIsUserCheckable:
                yield item
            it += 1

    def chosen(self) -> set[str]:
        return {i.data(0, Qt.UserRole) for i in self._leaves() if i.checkState(0) == Qt.Checked}

    def _select(self, mode: str) -> None:
        self.tree.blockSignals(True)
        for it in self._leaves():
            key = it.data(0, Qt.UserRole)
            on = {"default": not self.seg[key] and self.counted[key], "all": True,
                  "unseg": not self.seg[key], "none": False,
                  "invert": it.checkState(0) != Qt.Checked}[mode]
            it.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
        self.tree.blockSignals(False)
        self._update_count()

    def _todo(self) -> list[str]:
        force = self.force_cb.isChecked()
        return [k for k in self.chosen() if force or not self.seg[k]]

    def _update_count(self) -> None:
        n, todo = len(self.chosen()), len(self._todo())
        self.count_label.setText(f"{n} selected · {todo} to segment" + ("" if todo == n else f" · {n - todo} already done"))
        self.start_btn.setEnabled(todo > 0)

    # ── run ─────────────────────────────────────────────────
    def _run_page(self) -> QWidget:
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(12)
        v.addWidget(QLabel("Segmentation", objectName="tileTitle"))
        card = QFrame(objectName="settingsCard"); cl = QVBoxLayout(card); cl.setContentsMargins(18, 16, 18, 16)
        head = QHBoxLayout()
        self.run_big = QLabel("", objectName="runBig"); self.run_eta = QLabel("", objectName="tileSub")
        head.addWidget(self.run_big); head.addStretch(1); head.addWidget(self.run_eta)
        cl.addLayout(head)
        self.bar = QProgressBar(objectName="runBar"); self.bar.setTextVisible(False); self.bar.setFixedHeight(10)
        cl.addWidget(self.bar)
        self.now = QLabel("", objectName="tileSub"); cl.addWidget(self.now)
        v.addWidget(card)
        self.run_list = QListWidget(objectName="runList")
        self.run_list.setSelectionMode(QListWidget.NoSelection); self.run_list.setFocusPolicy(Qt.NoFocus)
        v.addWidget(self.run_list, 1)
        foot = QHBoxLayout()
        self.foot_note = QLabel("", objectName="tileSub"); foot.addWidget(self.foot_note); foot.addStretch(1)
        self.stop_btn = QPushButton("Stop after this image"); self.stop_btn.clicked.connect(self._stop)
        self.retry_btn = QPushButton(""); self.retry_btn.clicked.connect(self._retry); self.retry_btn.hide()
        self.close_btn = QPushButton("Keep running in background"); self.close_btn.setProperty("class", "primary")
        self.close_btn.clicked.connect(self.accept)
        for b in (self.stop_btn, self.retry_btn, self.close_btn):
            foot.addWidget(b)
        v.addLayout(foot)
        return w

    def _colors(self) -> dict:
        from ..theme import palette
        from .app import _recall
        P = palette(_recall("theme") or "system")
        return {"ok": P["state_finished"], "run": P["accent_primary"], "wait": P["fg_muted"], "fail": P["state_unsegmented"]}

    def _set_row(self, key: str, state: str, note: str) -> None:
        it = self._rows.get(key)
        if it is None:
            return
        icon = {"ok": "✓", "run": "◐", "wait": "○", "fail": "✕"}[state]
        it.setText(f"{icon}   {Path(key).name}    ·  {note}")
        it.setForeground(QColor(self._colors()[state]))
        if state == "run":
            self.run_list.scrollToItem(it)

    def _start(self, keys: list[str] | None = None, force: bool | None = None) -> None:
        by_key = {str(i.path): i for i in self.images}
        todo = sorted(keys if keys is not None else self._todo(), key=lambda k: by_key[k].path.name)
        force = self.force_cb.isChecked() if force is None else force
        self.total, self.n_done, self.failed, self.t0 = len(todo), 0, {}, time.monotonic()
        self.run_list.clear(); self._rows = {}
        for k in todo:
            it = QListWidgetItem(""); self.run_list.addItem(it); self._rows[k] = it
            self._set_row(k, "wait", "waiting")
        self.worker.set_queue([(by_key[k].folder, by_key[k].path) for k in todo],
                              self.study.settings.cellpose, force=force)
        if not getattr(self, "_connected", False):
            self.worker.started.connect(self._on_started)
            self.worker.finished.connect(self._on_finished)
            self.worker.failed.connect(self._on_failed)
            self._connected = True
        self.bar.setRange(0, max(self.total, 1)); self.bar.setValue(0)
        self.stop_btn.show(); self.stop_btn.setEnabled(True); self.retry_btn.hide()
        self.close_btn.setText("Keep running in background")
        self.foot_note.setText("You can close this window – segmentation goes on.")
        self._progress()
        self.pages.setCurrentIndex(1)

    def _on_started(self, path: str) -> None:
        if path in self._rows:
            px = image_pixel_size(Path(path))
            self.now.setText(f"Now: {Path(path).name}" + (f" · InstanSeg at {px:.3f} µm/px" if px else ""))
            self._set_row(path, "run", "segmenting …")

    def _on_finished(self, path: str) -> None:
        if path not in self._rows:
            return
        info = next((i for i in self.images if str(i.path) == path), None)
        n = _nuclei(self.study, info) if info else None
        self.n_done += 1
        self._set_row(path, "ok", f"{n} nuclei" if n is not None else "done")
        self._progress()

    def _on_failed(self, path: str, msg: str) -> None:
        if path not in self._rows:
            return
        self.n_done += 1; self.failed[path] = msg
        self._set_row(path, "fail", "could not be segmented – " + msg.split(":")[0])
        self._rows[path].setToolTip(msg)
        self._progress()

    def _progress(self) -> None:
        self.bar.setValue(min(self.n_done, self.total))
        left = self.total - self.n_done
        if left > 0:
            self.run_big.setText(f"{self.n_done} of {self.total}")
            per = (time.monotonic() - self.t0) / self.n_done if self.n_done else None
            self.run_eta.setText(f"about {_duration(per * left)} left" if per else "starting …")
            return
        ok = self.total - len(self.failed)
        self.run_big.setText(f"{ok} of {self.total} segmented")
        self.run_eta.setText(f"took {_duration(time.monotonic() - self.t0)}")
        self.now.setText(f"{len(self.failed)} image{'s' if len(self.failed) != 1 else ''} could not be segmented – see the list."
                         if self.failed else "All done.")
        self.foot_note.setText("The sidebar shows the new contours.")
        self.stop_btn.hide()
        self.retry_btn.setText(f"Retry {len(self.failed)} failed"); self.retry_btn.setVisible(bool(self.failed))
        self.close_btn.setText("Close")

    def _stop(self) -> None:
        self.worker.set_queue([], self.study.settings.cellpose)     # the image running now still finishes
        for k, it in self._rows.items():
            if it.text().startswith("○"):
                self._set_row(k, "wait", "not started")
        self.total = self.n_done + (1 if any(it.text().startswith("◐") for it in self._rows.values()) else 0)
        self.run_big.setText(f"Stopping · {self.n_done} done")
        self.stop_btn.setEnabled(False)
        self._progress()

    def _retry(self) -> None:
        self._start(list(self.failed), force=True)


def _nuclei(study: Study, info) -> int | None:
    """Nuclei kept in the new segmentation (inside the objective's size range)."""
    import numpy as np
    from ..analysis import area_limits
    from ..segment import cached_cellpose
    cp = cached_cellpose(info.folder, info.path, study.settings.cellpose)
    if cp is None:
        return None
    sizes = np.bincount(cp.ravel())[1:]
    lo, hi = area_limits(study.settings, info.path)
    return int(((sizes >= lo) & ((hi <= 0) | (sizes <= hi)) & (sizes > 0)).sum())


def _duration(sec: float) -> str:
    sec = max(1, round(sec))
    return f"{sec} s" if sec < 60 else f"{sec // 60} min {sec % 60} s"


def mag_order(label: str) -> float:
    """Highest magnification first: 20× before 10× before 2.5×; unknown last."""
    try:
        return -float(label.rstrip("×"))
    except ValueError:
        return 1.0
