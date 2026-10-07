"""Abgleich im Hauptfenster (mockup "Abgleich im Hauptfenster").

Study A is open; with "⇆ Abgleich" the image view splits: A (the study's own
rating, every tool works as usual) and B (a second rating, view only) side by
side or one above the other, zoom and pan coupled. The cells they rate
differently are framed. Per difference: "B → A" gives A the rating B has for
that cell — in study A itself, for the real evaluation — or "A behalten".
A is backed up when the comparison starts ("A vor Abgleich mit B"); the
statistics use that backup, and it can be loaded again at any time.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QProgressBar, QVBoxLayout,
)

from .. import interrater as ir
from ..overlay import compute_cell_infos


class SplitController:
    def __init__(self, win):
        self.win = win
        self.S, self.P = win.S, win.P
        self.active = False
        self.rater: str | None = None
        self.diffs: list[tuple[int, str, str, tuple]] = []      # (cid, A, B, bbox)
        self.pos = 0
        self.rb = None
        self.undo_stack: list[tuple] = []
        self._syncing = False
        self._build_bar()
        self._build_panel()

    # ── widgets ──
    def _build_bar(self) -> None:
        S = self.S
        self.bar = QFrame(); self.bar.setObjectName("cmpBar")
        h = QHBoxLayout(self.bar); h.setContentsMargins(12, 6, 12, 6); h.setSpacing(10)
        self.a_label = QLabel(""); self.a_label.setStyleSheet("font-weight: 600;")
        self.snap_label = QLabel(""); self.snap_label.setStyleSheet(f"color: {self.P['state_finished']}; font-size: 12px;")
        load = QPushButton(S["sp_load_backup"]); load.clicked.connect(lambda: self.win.open_backups())
        save = QPushButton(S["sp_save_now"]); save.clicked.connect(lambda: self.win.backup_now())
        h.addWidget(self.a_label); h.addWidget(self.snap_label); h.addWidget(load); h.addWidget(save)
        h.addWidget(QLabel("↔"))
        self.b_combo = QComboBox(); self.b_combo.setMinimumWidth(140)
        self.b_combo.activated.connect(lambda _i: self.set_rater(self.b_combo.currentText()))
        h.addWidget(QLabel(S["sp_b"])); h.addWidget(self.b_combo)
        # which number ICC / Bland-Altman compare per proband (stored per B)
        h.addWidget(QLabel(S["sp_metric"]))
        self.metric_combo = QComboBox(); self.metric_combo.setMinimumWidth(190)
        self.metric_combo.activated.connect(lambda _i: self.set_metric(self.metric_combo.currentData()))
        h.addWidget(self.metric_combo)
        h.addStretch(1)
        seg = QFrame(); seg.setObjectName("segment")
        sh = QHBoxLayout(seg); sh.setContentsMargins(0, 0, 0, 0); sh.setSpacing(0)
        self.layout_group = QButtonGroup(seg)
        for i, (key, lk) in enumerate((("side", "sp_side"), ("rows", "sp_rows"))):
            b = QPushButton(S[lk]); b.setCheckable(True); b.setChecked(key == "side")
            b.setProperty("first", i == 0); b.setProperty("last", i == 1); b.setMinimumHeight(30)
            b.clicked.connect(lambda _c=False, k=key: self.set_layout(k))
            self.layout_group.addButton(b); sh.addWidget(b)
        h.addWidget(seg)
        self.only_cb = QCheckBox(S["sp_only_diffs"]); self.only_cb.toggled.connect(lambda _on: self._apply_only())
        h.addWidget(self.only_cb)
        self.bar.hide()

    def _build_panel(self) -> None:
        S, P = self.S, self.P
        self.panel = QFrame(); self.panel.setObjectName("settingsNav"); self.panel.setFixedWidth(300)
        v = QVBoxLayout(self.panel); v.setContentsMargins(14, 14, 14, 14); v.setSpacing(10)
        cap = QLabel(S["sp_diff"].upper()); cap.setObjectName("caption"); v.addWidget(cap)
        self.card = QLabel(""); self.card.setObjectName("settingsCard"); self.card.setWordWrap(True)
        self.card.setTextFormat(Qt.RichText); self.card.setStyleSheet("padding: 10px;")
        v.addWidget(self.card)
        row = QHBoxLayout()
        self.btn_take = QPushButton(S["sp_take"]); self.btn_take.setProperty("class", "primary")
        self.btn_take.clicked.connect(lambda: self.take_b())
        self.btn_keep = QPushButton(S["sp_keep"]); self.btn_keep.clicked.connect(lambda: self.keep_a())
        row.addWidget(self.btn_take); row.addWidget(self.btn_keep)
        v.addLayout(row)
        nav = QHBoxLayout()
        prev = QPushButton("◀  " + S["sp_prev"]); prev.clicked.connect(lambda: self.step(-1))
        nxt = QPushButton(S["sp_next"] + "  ▶"); nxt.clicked.connect(lambda: self.step(+1))
        nav.addWidget(prev); nav.addWidget(nxt)
        v.addLayout(nav)
        self.btn_undo = QPushButton("↶  " + S["sp_undo"]); self.btn_undo.clicked.connect(lambda: self.undo())
        v.addWidget(self.btn_undo)
        cap = QLabel(S["sp_this_image"].upper()); cap.setObjectName("caption"); v.addWidget(cap)
        self.prog = QProgressBar(); self.prog.setTextVisible(False); v.addWidget(self.prog)
        self.prog_text = QLabel(""); self.prog_text.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        v.addWidget(self.prog_text)
        self.group_vals = QLabel(""); self.group_vals.setStyleSheet("font-size: 12px; font-weight: 600;")
        self.group_vals.setWordWrap(True)
        v.addWidget(self.group_vals)
        allb = QPushButton(S["sp_take_all"]); allb.clicked.connect(lambda: self.take_all())
        v.addWidget(allb)
        legend = QLabel(S["sp_legend"]); legend.setWordWrap(True)
        legend.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        v.addWidget(legend)
        v.addStretch(1)
        self.panel.hide()

    # ── on / off ──
    def available(self) -> bool:
        w = self.win
        return (w.ctl.folder is not None and w.ctl.has_images and not w.ctl.profile.rater
                and bool(ir.list_raters(w.ctl.folder)))

    def activate(self, rater: str | None = None) -> bool:
        w, S = self.win, self.S
        raters = ir.list_raters(w.ctl.folder) if w.ctl.folder else []
        if not raters:
            return False
        rater = rater or (self.rater if self.rater in raters else raters[0])
        locked, done, total = ir.blind_locked(w.ctl.folder, rater)
        if locked:
            QMessageBox.information(w, S["sp_title"], S["ir_blind_locked"].format(name=rater, done=done, total=total))
            return False
        self.b_combo.blockSignals(True); self.b_combo.clear(); self.b_combo.addItems(raters)
        self.b_combo.setCurrentText(rater); self.b_combo.blockSignals(False)
        self.active = True
        w.ensure_canvas_b()
        w.canvas.view_listener = lambda: self._sync(w.canvas, w.canvas_b)
        w.canvas_b.view_listener = lambda: self._sync(w.canvas_b, w.canvas)
        self.bar.show(); self.panel.show(); w.canvas_b.show()
        self._tags()
        self.set_rater(rater)
        return True

    def _tags(self) -> None:
        """'A · Finn — editable' / 'B · Anna — view only' in the views' corner."""
        w, S = self.win, self.S
        for attr, canvas in (("_tag_a", w.canvas), ("_tag_b", w.canvas_b)):
            if getattr(self, attr, None) is None:
                lab = QLabel(canvas)
                lab.setStyleSheet("color: white; background: rgba(0,0,0,150); border-radius: 6px;"
                                  " padding: 3px 9px; font-weight: 600;")
                setattr(self, attr, lab)
        me = w.settings.reviewer_name or S["ir_me"]
        self._tag_a.setText(f"A · {me} — {S['sp_tag_edit']}")
        self._tag_b.setText(f"B · {self.rater or ''} — {S['sp_tag_view']}")
        for lab in (self._tag_a, self._tag_b):
            lab.adjustSize(); lab.move(12, 14); lab.raise_(); lab.show()

    def deactivate(self) -> None:
        w = self.win
        self.active = False
        w.sidebar.clear_compare()
        if getattr(self, "_tag_a", None) is not None:
            self._tag_a.hide()
        self.bar.hide(); self.panel.hide()
        if getattr(w, "canvas_b", None) is not None:
            w.canvas_b.hide(); w.canvas_b.view_listener = None
        w.canvas.view_listener = None
        w.canvas.only_ids = None
        w.canvas.set_markers([])
        if w.ctl.has_images:
            w.canvas._refresh_overlay(); w.canvas._full_redraw()

    def set_rater(self, rater: str) -> None:
        w = self.win
        self.rater = rater
        ir.ensure_a_backup(w.ctl.folder, rater)
        if getattr(self, "_tag_b", None) is not None:
            self._tags()
        self._update_backup_label()
        self.undo_stack.clear()
        self._fill_metrics()
        self._to_sidebar()
        self.load_image()

    # ── list filter, ticks and metric for the agreement statistics ──
    def _to_sidebar(self) -> None:
        w = self.win
        folder, rater, prof = w.ctl.folder, self.rater, w.ctl.profile
        sel = ir.selection(folder, rater)
        w.sidebar.set_compare(ir.common_stems(folder, rater), sel["excluded"],
                              lambda info: ir.image_differs(folder, info, prof, rater),
                              self._on_tick)

    def _on_tick(self, stems: list[str], ticked: bool) -> None:
        ir.set_excluded(self.win.ctl.folder, self.rater, stems, not ticked)
        self._update_group_vals()

    def _fill_metrics(self) -> None:
        from ..stats import available_metrics
        cur = ir.selection(self.win.ctl.folder, self.rater)["metric"]
        self.metric_combo.blockSignals(True)
        self.metric_combo.clear()
        for key, label in available_metrics(self.win.ctl.profile):
            self.metric_combo.addItem(label, key)
        self.metric_combo.setCurrentIndex(max(0, self.metric_combo.findData(cur)))
        self.metric_combo.blockSignals(False)

    def set_metric(self, key: str) -> None:
        if not self.active or not key:
            return
        ir.set_metric(self.win.ctl.folder, self.rater, key)
        self.metric_combo.setCurrentIndex(max(0, self.metric_combo.findData(key)))
        self._update_group_vals()

    def _update_group_vals(self) -> None:
        """'Proband: A 12,4 % · B 13,1 %' — the chosen metric over the
        proband's ticked common images, A as it is now."""
        w = self.win
        infos = w.ctl.current_group() if self.active else None
        if not infos:
            self.group_vals.setText("")
            return
        sel = ir.selection(w.ctl.folder, self.rater)
        res = ir.group_values(w.ctl.folder, infos, w.ctl.profile, self.rater, sel["excluded"], sel["metric"])
        if res is None:
            self.group_vals.setText("")
            return
        pct = sel["metric"].startswith("pct")
        fmt = lambda v: (f"{v:.1f}".replace(".", self.S["qt_decimal_sep"]) + " %") if pct else f"{v:.0f}"
        self.group_vals.setText(self.S["sp_group_vals"].format(a=fmt(res[0]), b=fmt(res[1]))
                                + f"\n{self.metric_combo.currentText()} · n = {res[2]}")

    def _update_backup_label(self) -> None:
        from .. import backups
        w, S = self.win, self.S
        self.a_label.setText(f"A · {w.settings.reviewer_name or S['ir_me']}")
        bid = ir.rater_info(w.ctl.folder, self.rater).get("a_backup")
        b = backups.get(w.ctl.folder, bid) if bid else None
        self.snap_label.setText(S["sp_backup_info"].format(date=b.created.replace("T", " ")[:16]) if b else "")

    def set_layout(self, key: str) -> None:
        self.win.views.setOrientation(Qt.Horizontal if key == "side" else Qt.Vertical)

    # ── per image ──
    def load_image(self, keep_pos: bool = False) -> None:
        """B's rating of the image in view, and the differences to A (live)."""
        from ..cellpose_runner import load_cached_masks
        w = self.win
        if not self.active or not w.ctl.has_images:
            return
        path = w.ctl.current_path
        self.rb = ir.load_review(w.ctl.folder, self.rater, path.stem)
        masks = load_cached_masks(w.ctl.folder, path)
        cb = w.canvas_b
        if self.rb is None or masks is None or w.canvas.image is None:
            cb.clear()
            self.diffs = []
            self._show(self.S["sp_b_missing"].format(b=self.rater))
            return
        cb.set_image(w.canvas.image, masks, self.rb)
        cb.set_overlay_visible(w.settings.overlay_visible)
        cb.set_view(w.canvas.ax.get_xlim(), w.canvas.ax.get_ylim())
        if getattr(w.ctl.review, "display", None):
            cb.set_display(w.ctl.review.display)
        self._masks = masks
        self._infos = compute_cell_infos(masks, set())
        if not keep_pos:
            self.pos = 0
        self.refresh()
        self._update_group_vals()

    def refresh(self) -> None:
        """Recompute the differences after A changed (an edit, B → A)."""
        w = self.win
        if not self.active or self.rb is None or getattr(self, "_masks", None) is None:
            return
        ids, auto = ir.mask_cells(self._masks, w.ctl.profile)
        ca = ir.categories(ids, auto, w.ctl.review, w.ctl.profile)
        cb = ir.categories(ids, auto, self.rb, w.ctl.profile)
        dec = ir.decisions(w.ctl.folder, self.rater).get(w.ctl.current_path.stem, {})
        self.diffs = []
        for cid in ids:
            if ca[cid] != cb[cid] or str(cid) in dec:
                i = self._infos.get(cid)
                if i is not None:
                    self.diffs.append((cid, ca[cid], cb[cid], (i.bbox_x0, i.bbox_y0, i.bbox_x1, i.bbox_y1)))
        self.diffs.sort(key=lambda d: (d[3][1] // 80, d[3][0]))
        self._dec = dec
        self.pos = min(self.pos, max(0, len(self.diffs) - 1))
        self._paint()

    def _state(self, k: int) -> str:
        cid = self.diffs[k][0]
        if str(cid) in self._dec:
            return "done"
        return "current" if k == self.pos else "open"

    def _paint(self) -> None:
        w = self.win
        marks = [(*d[3], self._state(k)) for k, d in enumerate(self.diffs)]
        for c in (w.canvas, w.canvas_b):
            c.set_markers(marks)
        self._apply_only(redraw=False)
        n = len(self.diffs)
        done = sum(1 for d in self.diffs if str(d[0]) in self._dec)
        self.prog.setRange(0, max(1, n)); self.prog.setValue(done if n else 1)
        self.prog_text.setText(self.S["sp_progress"].format(done=done, n=n))
        if not n:
            self._show(self.S["sp_no_diffs"])
            return
        cid, a, b, _bb = self.diffs[self.pos]
        name = lambda k: self.S["ir_excluded"] if k == ir.EXCLUDED else (
            (w.ctl.profile.type_by_key(k).name or k) if w.ctl.profile.type_by_key(k) else k)
        state = self._dec.get(str(cid))
        extra = {"taken": self.S["sp_state_taken"], "kept": self.S["sp_state_kept"]}.get(state, self.S["sp_state_open"])
        self._show(f"<b>{self.S['sp_cell']} {cid}</b><br>A · {name(a)}<br>B · {name(b)}<br>"
                   f"<span style='font-size:12px'>{extra}</span>", enable=state is None)
        w._set_status(self.S["sp_status"].format(i=self.pos + 1, n=n, cid=cid, a=name(a), b=name(b)))

    def _show(self, html: str, enable: bool = False) -> None:
        self.card.setText(html)
        self.btn_take.setEnabled(enable); self.btn_keep.setEnabled(enable)
        self.btn_undo.setEnabled(bool(self.undo_stack))

    def _apply_only(self, redraw: bool = True) -> None:
        w = self.win
        ids = {d[0] for d in self.diffs} if self.only_cb.isChecked() else None
        for c in (w.canvas, w.canvas_b):
            c.only_ids = ids
            if c.image is not None:
                c._refresh_overlay(); c._full_redraw()

    def _sync(self, src, dst) -> None:
        if self._syncing or dst is None or not dst.isVisible():
            return
        self._syncing = True
        try:
            dst.set_view(src.ax.get_xlim(), src.ax.get_ylim())
        finally:
            self._syncing = False

    # ── navigation & decisions ──
    def step(self, d: int) -> None:
        if not self.active or not self.diffs:
            return
        self.pos = (self.pos + d) % len(self.diffs)
        self._paint()
        x0, y0, x1, y1 = self.diffs[self.pos][3]
        pad = max(60, (x1 - x0) * 3)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.win.canvas.set_view((cx - pad, cx + pad), (cy + pad, cy - pad))
        self.win.canvas._notify_view()

    def _first_take_hint(self) -> bool:
        w = self.win
        if w.settings.abgleich_hint_shown:
            return True
        from .. import backups
        bid = ir.rater_info(w.ctl.folder, self.rater).get("a_backup")
        b = backups.get(w.ctl.folder, bid) if bid else None
        box = QMessageBox(w); box.setIcon(QMessageBox.Information)
        box.setWindowTitle(self.S["sp_title"])
        box.setText(self.S["sp_hint"].format(date=b.created.replace("T", " ")[:16] if b else "–"))
        cb = QCheckBox(self.S["sp_hint_dont"]); box.setCheckBox(cb)
        ok = box.addButton(self.S["sp_hint_ok"], QMessageBox.AcceptRole)
        box.addButton(self.S["settings_cancel"], QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not ok:
            return False
        if cb.isChecked():
            w.settings.abgleich_hint_shown = True
            w.settings.save()
        return True

    def take_b(self, confirm: bool = True) -> None:
        w = self.win
        if not self.active or not self.diffs:
            return
        cid = self.diffs[self.pos][0]
        if str(cid) in self._dec or (confirm and not self._first_take_hint()):
            return
        before = ir.take_cell(w.ctl.review, self.rb, cid)
        stem = w.ctl.current_path.stem
        ir.set_decision(w.ctl.folder, self.rater, stem, cid, "taken")
        self.undo_stack.append(("taken", stem, cid, before))
        self._after_change()

    def keep_a(self) -> None:
        w = self.win
        if not self.active or not self.diffs:
            return
        cid = self.diffs[self.pos][0]
        if str(cid) in self._dec:
            return
        stem = w.ctl.current_path.stem
        ir.set_decision(w.ctl.folder, self.rater, stem, cid, "kept")
        self.undo_stack.append(("kept", stem, cid, None))
        self._after_change()

    def take_all(self) -> None:
        w, S = self.win, self.S
        open_ = [d for d in self.diffs if str(d[0]) not in self._dec]
        if not open_ or not self._first_take_hint():
            return
        if QMessageBox.question(w, S["sp_title"], S["sp_take_all_q"].format(n=len(open_))) != QMessageBox.Yes:
            return
        stem = w.ctl.current_path.stem
        for cid, _a, _b, _bb in open_:
            before = ir.take_cell(w.ctl.review, self.rb, cid)
            ir.set_decision(w.ctl.folder, self.rater, stem, cid, "taken")
            self.undo_stack.append(("taken", stem, cid, before))
        self._after_change()

    def undo(self) -> None:
        w = self.win
        if not self.undo_stack:
            return
        kind, stem, cid, before = self.undo_stack.pop()
        if stem != w.ctl.current_path.stem:          # decisions are undone per image
            self.undo_stack.append((kind, stem, cid, before))
            return
        if kind == "taken":
            ir.restore_cell(w.ctl.review, cid, before)
        ir.set_decision(w.ctl.folder, self.rater, stem, cid, None)
        self._after_change(advance=False)

    def _after_change(self, advance: bool = True) -> None:
        """A's review changed in memory: save it, redraw A, move on."""
        w = self.win
        w._save_review()
        w.canvas._refresh_overlay()
        w.canvas._refresh_numbers()
        w.canvas._full_redraw()
        old = self.pos
        self.refresh()
        self._update_group_vals()
        if advance and self.diffs:
            nxt = next((k for k in range(old + 1, len(self.diffs)) if str(self.diffs[k][0]) not in self._dec), None)
            if nxt is None:
                nxt = next((k for k in range(len(self.diffs)) if str(self.diffs[k][0]) not in self._dec), old)
            self.pos = nxt
            self._paint()
