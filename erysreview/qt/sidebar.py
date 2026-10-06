"""Qt sidebar, as in mockup v3 (Q-Main): a filter row, the tree
timepoint › "Proband NN · finished/total" › image (status dot, ❝ marker), and a
detail panel for the current image's subject — counted cells against the
target, counted images, share changed, and the cell types as chips.

Rules come from sidebar_model (shared with Tk). Refreshing is group-local on
the click path: an edit can only change its own group's rows and numbers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..grouping import UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from ..sidebar_model import FILTER_STALE, FILTERS, group_details, image_status, is_stale, passes_filter
from ..theme import STATUS_ICONS_3, STATUS_TO_COLOR
from .style import _mix

ROLE_KIND, ROLE_KEY = Qt.UserRole, Qt.UserRole + 1


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")      # 2 104 — thin space, both languages


class QtSidebar(QWidget):
    def __init__(self, P: dict, S: dict, profile, on_select: Callable[[int], None], parent=None,
                 on_subject_eval: Callable[[str, str], None] | None = None,
                 on_subject_status: Callable[[str, str, bool], None] | None = None,
                 on_rerun: Callable[[list[Path]], None] | None = None):
        super().__init__(parent)
        self._on_rerun = on_rerun or (lambda paths: None)
        self._stale: dict[tuple[str, str], list[Path]] = {}
        self._on_subject_eval = on_subject_eval or (lambda tp, sub: None)
        self._on_subject_status = on_subject_status or (lambda tp, sub, done: None)
        self.P, self.S, self.profile = P, S, profile
        self._on_select = on_select
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.grouped = None
        self._item_by_idx: dict[int, QTreeWidgetItem] = {}
        self._idx_by_name: dict[str, int] = {}
        self._group_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._group_of_idx: dict[int, tuple[str, str]] = {}
        self._filter = "all"
        self._building = False
        self._current: int | None = None
        self.setObjectName("sidebar")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── filter row ──
        row = QFrame(); row.setObjectName("sidebarFilter")
        rh = QHBoxLayout(row); rh.setContentsMargins(12, 10, 12, 10); rh.setSpacing(8)
        lab = QLabel(S["filter_label"].rstrip(": ")); lab.setStyleSheet(f"color: {P['fg_secondary']};")
        self.filter_combo = QComboBox()
        self.filter_combo.setAccessibleName(S["filter_label"].rstrip(": "))
        for value, key in FILTERS + [FILTER_STALE]:
            self.filter_combo.addItem(S["qt_filter_all"] if value == "all" else S[key], value)
        self.filter_combo.activated.connect(
            lambda i: self._set_filter(self.filter_combo.itemData(i)))
        rh.addWidget(lab); rh.addWidget(self.filter_combo, 1)
        lay.addWidget(row)

        # ── tree ──
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setFocusPolicy(Qt.NoFocus)    # ←/→ belong to the image, not the tree
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._subject_menu)
        lay.addWidget(self.tree, 1)

        # ── detail panel: one line, counted cells against the target ──
        # (per-type numbers, counted images, % changed: Info, key I)
        self.details = QFrame(); self.details.setObjectName("sidebarDetails")
        dv = QVBoxLayout(self.details); dv.setContentsMargins(12, 10, 12, 10); dv.setSpacing(4)
        self.det_title = QLabel(""); self.det_title.setObjectName("caption")
        dv.addWidget(self.det_title)
        self.det_cells = QLabel("–"); self.det_cells.setObjectName("detCells")
        dv.addWidget(self.det_cells)
        lay.addWidget(self.details)
        self.details.hide()

    # ── public API ──
    def set_profile(self, profile) -> None:
        self.profile = profile

    def populate(self, folder: Path, images: list[Path], grouped) -> None:
        self.folder, self.images, self.grouped = folder, images, grouped
        self._idx_by_name = {p.name: i for i, p in enumerate(images)}
        self._rebuild()

    def refresh_all(self) -> None:
        self._rebuild()

    def refresh_group_of(self, idx: int) -> None:
        """Repaint one image's group (rows, counter, details) after an edit."""
        key = self._group_of_idx.get(idx)
        if key is not None and key in self._group_items:
            self._fill_group(self._group_items[key], *key)
        self.set_idx(idx)

    def set_idx(self, idx: int) -> None:
        self._current = idx
        item = self._item_by_idx.get(idx)
        self._building = True
        if item is None:
            self.tree.clearSelection()
        else:
            parent = item.parent()
            while parent is not None:          # the current image is always visible
                parent.setExpanded(True)
                parent = parent.parent()
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        self._building = False
        self._show_details(self._group_of_idx.get(idx))

    # ── internals ──
    def _set_filter(self, value: str) -> None:
        self._filter = value
        self._rebuild()
        if self._current is not None:
            self.set_idx(self._current)

    def _rebuild(self) -> None:
        expanded = {it.data(0, ROLE_KEY) for it in self._iter_items() if it.isExpanded()}
        first_build = not self._group_items
        self._building = True
        self.tree.clear()
        self._item_by_idx.clear()
        self._group_items.clear()
        self._group_of_idx.clear()
        self._stale.clear()
        if self.grouped is None:
            self._update_filter_count()
            self._building = False
            return
        bold = QFont(); bold.setBold(True)
        for tp in self.grouped.timepoints():
            tp_label = self.S["sb_ungrouped"] if tp == UNGROUPED_TIMEPOINT else tp
            tp_item = QTreeWidgetItem([tp_label])
            tp_item.setFont(0, bold)
            tp_item.setData(0, ROLE_KIND, "tp"); tp_item.setData(0, ROLE_KEY, f"tp::{tp}")
            self.tree.addTopLevelItem(tp_item)
            for sub in self.grouped.subjects_in(tp):
                sub_item = QTreeWidgetItem()
                sub_item.setFont(0, bold)
                sub_item.setData(0, ROLE_KIND, "sub"); sub_item.setData(0, ROLE_KEY, f"sub::{tp}::{sub}")
                tp_item.addChild(sub_item)
                self._group_items[(tp, sub)] = sub_item
                for info in self.grouped.tree[tp][sub]:
                    idx = self._idx_by_name.get(info.path.name)
                    if idx is not None:
                        self._group_of_idx[idx] = (tp, sub)
                self._fill_group(sub_item, tp, sub)
                sub_item.setExpanded(f"sub::{tp}::{sub}" in expanded)
            tp_item.setExpanded(first_build or f"tp::{tp}" in expanded)
        self._building = False
        self._update_filter_count()

    def _fill_group(self, sub_item: QTreeWidgetItem, tp: str, sub: str) -> None:
        infos = self.grouped.tree[tp][sub]
        for k in reversed(range(sub_item.childCount())):
            self._item_by_idx.pop(sub_item.child(k).data(0, ROLE_KEY), None)
            sub_item.removeChild(sub_item.child(k))
        n_finished = 0
        stale_paths: list[Path] = []
        # Which images count toward the target (one disk pass, shared with the
        # detail line). Not counted ones get ⊘ and move to the end of the
        # proband; old values show only as the ⟳ count on the proband row.
        counted = self._counted_stems(tp, sub)
        rows = []
        for info in infos:
            status, has_note = image_status(self.folder, info.path)
            stale = is_stale(self.folder, info.path, self.profile.cellpose)
            if stale:
                stale_paths.append(info.path)
            n_finished += status == "finished"
            if not passes_filter(self._filter, status, has_note, stale):
                continue
            idx = self._idx_by_name.get(info.path.name)
            if idx is None:
                continue
            rows.append((info, status, has_note, counted is None or info.path.stem in counted, idx))
        # stable: counted, then not yet segmented, then ⊘ (segmented, not counted) last
        rows.sort(key=lambda r: 0 if r[3] else (1 if r[1] == "unsegmented" else 2))
        for info, status, has_note, is_counted, idx in rows:
            off = not is_counted and status != "unsegmented"
            text = (f"{STATUS_ICONS_3[status]}  {info.path.name}" + ("  ❝" if has_note else "")
                    + ("   ⊘" if off else ""))
            it = QTreeWidgetItem([text])
            it.setData(0, ROLE_KIND, "img"); it.setData(0, ROLE_KEY, idx)
            col = QColor(self.P[STATUS_TO_COLOR[status]])
            if off:
                col = QColor(self.P["fg_muted"])
            it.setForeground(0, QBrush(col))
            it.setToolTip(0, info.path.name + ("\n" + self.S["qt_tip_not_counted"] if off else ""))
            sub_item.addChild(it)
            self._item_by_idx[idx] = it
        self._stale[(tp, sub)] = stale_paths
        name = (self.S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT
                else f"{self.S['pe_role_subject']} {sub}")
        text = f"{name}  ·  {n_finished}/{len(infos)}"
        state = ("state_finished" if infos and n_finished == len(infos)
                 else "state_in_progress" if n_finished else "state_unsegmented")
        sub_item.setForeground(0, QBrush(QColor(self.P[state])))
        sub_item.setToolTip(0, text)
        if stale_paths:
            # Text + an orange "⟳ n" badge: rich text needs a label in the row.
            sub_item.setText(0, "")
            warn, bg = self.P["accent_warn"], _mix(self.P["sidebar_bg"], self.P["accent_warn"], 0.16)
            lab = QLabel(f'<b style="color:{self.P[state]};">{text}</b>&nbsp;&nbsp;'
                         f'<span style="color:{warn}; background-color:{bg};">&nbsp;⟳ {len(stale_paths)}&nbsp;</span>')
            lab.setStyleSheet("background: transparent;")
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            self.tree.setItemWidget(sub_item, 0, lab)
        else:
            self.tree.removeItemWidget(sub_item, 0)
            sub_item.setText(0, text)
        self._update_filter_count()
        if self._current is not None and self._group_of_idx.get(self._current) == (tp, sub):
            self._show_details((tp, sub))

    def _counted_stems(self, tp: str, sub: str) -> set[str] | None:
        """Stems of the group's images that count toward the target; None if
        that cannot be read (then nothing is marked)."""
        if tp == UNGROUPED_TIMEPOINT:
            return None
        from ..evaluation import group_eval
        try:
            return group_eval(self.folder, self.grouped.tree[tp][sub], self.profile)[1]
        except Exception:  # noqa: BLE001
            return None

    def _show_details(self, key: tuple[str, str] | None) -> None:
        if key is None or self.grouped is None:
            self.details.hide()
            return
        tp, sub = key
        d = group_details(self.folder, self.grouped.tree[tp][sub], self.profile)
        tp_disp = self.S["sb_ungrouped"] if tp == UNGROUPED_TIMEPOINT else tp
        sub_disp = (self.S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT
                    else f"{self.S['pe_role_subject']} {sub}")
        self.det_title.setText(f"{tp_disp} · {sub_disp}".upper())
        reached = d["n_active"] >= d["target"]
        self.det_cells.setText(self.S["qt_det_cells_line"].format(
            n=_num(d["n_active"]), target=_num(d["target"])) + (" ✓" if reached else ""))
        self.det_cells.setToolTip(self.S["qt_det_cells_tip"])
        self.det_cells.setProperty("reached", reached)
        self.det_cells.style().unpolish(self.det_cells); self.det_cells.style().polish(self.det_cells)
        self.details.show()

    def _subject_menu(self, pos) -> None:
        """Right-click on a subject row: pick counted images, or mark all done/open."""
        item = self.tree.itemAt(pos)
        if item is None or item.data(0, ROLE_KIND) != "sub":
            return
        _, tp, sub = item.data(0, ROLE_KEY).split("::", 2)
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(self.S["qt_sub_eval"], lambda: self._on_subject_eval(tp, sub))
        m.addSeparator()
        m.addAction(self.S["status_mark_all_done"], lambda: self._on_subject_status(tp, sub, True))
        m.addAction(self.S["status_mark_all_open"], lambda: self._on_subject_status(tp, sub, False))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def _update_filter_count(self) -> None:
        n = sum(len(v) for v in self._stale.values())
        i = self.filter_combo.findData("stale")
        if i >= 0:
            self.filter_combo.setItemText(i, f"⟳ {self.S['qt_filter_stale']} ({n})")

    def order(self) -> list[int]:
        """Image indices in the order the list shows them (proband by
        proband, ⊘ last, current filter applied) — what ←/→ step through."""
        out: list[int] = []
        for i in range(self.tree.topLevelItemCount()):
            tp = self.tree.topLevelItem(i)
            for j in range(tp.childCount()):
                sub = tp.child(j)
                for k in range(sub.childCount()):
                    idx = sub.child(k).data(0, ROLE_KEY)
                    if isinstance(idx, int):
                        out.append(idx)
        return out

    def stale_paths(self) -> list[Path]:
        return [p for v in self._stale.values() for p in v]

    def _iter_items(self):
        stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            it = stack.pop()
            yield it
            stack.extend(it.child(i) for i in range(it.childCount()))

    def _on_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._building:
            return
        if item.data(0, ROLE_KIND) == "img":
            self._on_select(int(item.data(0, ROLE_KEY)))
        else:
            item.setExpanded(not item.isExpanded())
