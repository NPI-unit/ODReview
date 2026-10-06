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
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..grouping import UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from ..sidebar_model import FILTERS, group_details, image_status, passes_filter
from ..theme import STATUS_ICONS_3, STATUS_TO_COLOR
from .style import _mix

ROLE_KIND, ROLE_KEY = Qt.UserRole, Qt.UserRole + 1


def _num(n: int) -> str:
    return f"{n:,}".replace(",", " ")      # 2 104 — thin space, both languages


class QtSidebar(QWidget):
    def __init__(self, P: dict, S: dict, profile, on_select: Callable[[int], None], parent=None):
        super().__init__(parent)
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
        for value, key in FILTERS:
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
        lay.addWidget(self.tree, 1)

        # ── detail panel ──
        self.details = QFrame(); self.details.setObjectName("sidebarDetails")
        dv = QVBoxLayout(self.details); dv.setContentsMargins(12, 12, 12, 12); dv.setSpacing(6)
        self.det_title = QLabel(""); self.det_title.setObjectName("caption")
        dv.addWidget(self.det_title)
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(4)
        self.det_values: dict[str, QLabel] = {}
        for r, key in enumerate(("qt_det_cells", "qt_det_counted", "qt_det_changed")):
            k = QLabel(S[key]); k.setStyleSheet(f"color: {P['fg_secondary']};")
            vlab = QLabel("–")
            grid.addWidget(k, r, 0); grid.addWidget(vlab, r, 1)
            self.det_values[key] = vlab
        dv.addLayout(grid)
        self.det_chips = QLabel(""); self.det_chips.setWordWrap(True)
        self.det_chips.setTextFormat(Qt.RichText)
        dv.addWidget(self.det_chips)
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
        if self.grouped is None:
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

    def _fill_group(self, sub_item: QTreeWidgetItem, tp: str, sub: str) -> None:
        infos = self.grouped.tree[tp][sub]
        for k in reversed(range(sub_item.childCount())):
            self._item_by_idx.pop(sub_item.child(k).data(0, ROLE_KEY), None)
            sub_item.removeChild(sub_item.child(k))
        n_finished = 0
        for info in infos:
            status, has_note = image_status(self.folder, info.path)
            n_finished += status == "finished"
            if not passes_filter(self._filter, status, has_note):
                continue
            idx = self._idx_by_name.get(info.path.name)
            if idx is None:
                continue
            text = f"{STATUS_ICONS_3[status]}  {info.path.name}" + ("  ❝" if has_note else "")
            it = QTreeWidgetItem([text])
            it.setData(0, ROLE_KIND, "img"); it.setData(0, ROLE_KEY, idx)
            it.setForeground(0, QBrush(QColor(self.P[STATUS_TO_COLOR[status]])))
            it.setToolTip(0, info.path.name)
            sub_item.addChild(it)
            self._item_by_idx[idx] = it
        name = (self.S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT
                else f"{self.S['pe_role_subject']} {sub}")
        sub_item.setText(0, f"{name}  ·  {n_finished}/{len(infos)}")
        state = ("state_finished" if infos and n_finished == len(infos)
                 else "state_in_progress" if n_finished else "state_unsegmented")
        sub_item.setForeground(0, QBrush(QColor(self.P[state])))
        if self._current is not None and self._group_of_idx.get(self._current) == (tp, sub):
            self._show_details((tp, sub))

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
        reached = " ✓" if d["n_active"] >= d["target"] else ""
        self.det_values["qt_det_cells"].setText(f"{_num(d['n_active'])} / {_num(d['target'])}{reached}")
        self.det_values["qt_det_counted"].setText(f"{d['n_images']} / {d['n_total']}")
        self.det_values["qt_det_changed"].setText(
            f"{d['pct_changed']:.1f} %".replace(".", self.S["qt_decimal_sep"]))
        chips = []
        for t in self.profile.cell_types:
            n = d["by_type"].get(t.key, 0)
            if n <= 0:
                continue
            bg = _mix(self.P["panel_bg"], t.color, 0.22)
            chips.append(f'<span style="background-color:{bg};">&nbsp;<span style="color:{t.color};">●</span>'
                         f'&nbsp;{t.name or t.key} {_num(n)}&nbsp;</span>')
        self.det_chips.setText("&nbsp; ".join(chips))
        self.det_chips.setVisible(bool(chips))
        self.details.show()

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
