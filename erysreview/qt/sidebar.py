"""Qt sidebar — same outline as the Tk one: open-folder button, status filter,
tree timepoint › subject (counter n/target) › image (status icon, ❝ marker).

Rules come from sidebar_model (shared with Tk). Refreshing is group-local on
the click path: an edit can only change its own group's rows and counter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup, QLabel, QPushButton, QRadioButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..grouping import UNGROUPED_SUBJECT, UNGROUPED_TIMEPOINT
from ..sidebar_model import FILTERS, image_status, passes_filter, subject_counter
from ..theme import STATUS_ICONS_3, STATUS_TO_COLOR

ROLE_KIND, ROLE_KEY = Qt.UserRole, Qt.UserRole + 1


class QtSidebar(QWidget):
    def __init__(self, P: dict, S: dict, profile, on_select: Callable[[int], None],
                 on_open_folder: Callable[[], None], parent=None):
        super().__init__(parent)
        self.P, self.S, self.profile = P, S, profile
        self._on_select, self._on_open_folder = on_select, on_open_folder
        self.folder: Path | None = None
        self.images: list[Path] = []
        self.grouped = None
        self._item_by_idx: dict[int, QTreeWidgetItem] = {}
        self._idx_by_name: dict[str, int] = {}
        self._group_items: dict[tuple[str, str], QTreeWidgetItem] = {}
        self._filter = "all"
        self._building = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.folder_label = QLabel("")
        self.folder_label.setWordWrap(True)
        self.folder_label.setStyleSheet(f"color: {P['fg_secondary']}; font-size: 12px;")
        btn = QPushButton(S["btn_open_folder"])
        btn.clicked.connect(lambda: self._on_open_folder())
        lay.addWidget(btn)
        lay.addWidget(self.folder_label)
        cap = QLabel(S["filter_label"]); cap.setObjectName("caption")
        lay.addWidget(cap)
        self._filters = QButtonGroup(self)
        for value, key in FILTERS:
            rb = QRadioButton(S[key]); rb.setChecked(value == "all")
            rb.toggled.connect(lambda on, v=value: on and self._set_filter(v))
            self._filters.addButton(rb)
            lay.addWidget(rb)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setFocusPolicy(Qt.NoFocus)    # ←/→ belong to the image, not the tree
        self.tree.itemClicked.connect(self._on_item_clicked)
        lay.addWidget(self.tree, 1)

    # ── public API ──
    def set_profile(self, profile) -> None:
        self.profile = profile

    def populate(self, folder: Path, images: list[Path], grouped) -> None:
        self.folder, self.images, self.grouped = folder, images, grouped
        self._idx_by_name = {p.name: i for i, p in enumerate(images)}
        self.folder_label.setText(str(folder))
        self._rebuild()

    def refresh_all(self) -> None:
        self._rebuild()

    def refresh_group_of(self, idx: int) -> None:
        """Repaint one image's group (rows + counter) after an edit."""
        if not self.grouped or not (0 <= idx < len(self.images)):
            return
        name = self.images[idx].name
        for (tp, sub), item in self._group_items.items():
            if any(i.path.name == name for i in self.grouped.tree[tp][sub]):
                self._fill_group(item, tp, sub)
                break
        self.set_idx(idx)

    def set_idx(self, idx: int) -> None:
        item = self._item_by_idx.get(idx)
        if item is None:
            self.tree.clearSelection()
            return
        self._building = True
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)
        self._building = False

    # ── internals ──
    def _set_filter(self, value: str) -> None:
        self._filter = value
        self._rebuild()

    def _rebuild(self) -> None:
        expanded = {(it.data(0, ROLE_KEY)) for it in self._iter_items()
                    if it.isExpanded()}
        self._building = True
        self.tree.clear()
        self._item_by_idx.clear()
        self._group_items.clear()
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
                self._fill_group(sub_item, tp, sub)
                sub_item.setExpanded(f"sub::{tp}::{sub}" in expanded)
            tp_item.setExpanded(not expanded or f"tp::{tp}" in expanded)
        self._building = False

    def _fill_group(self, sub_item: QTreeWidgetItem, tp: str, sub: str) -> None:
        infos = self.grouped.tree[tp][sub]
        n_active, target, _stems = subject_counter(self.folder, infos, self.profile)
        label = self.S["sb_ungrouped"] if sub == UNGROUPED_SUBJECT else sub
        sub_item.setText(0, f"{label}  ·  " + self.S["sb_group_closed_counter"].format(
            n_active=n_active, target=target))
        color = self.P["state_finished"] if n_active >= target else self.P["fg"]
        sub_item.setForeground(0, QBrush(QColor(color)))
        for k in reversed(range(sub_item.childCount())):
            idx = sub_item.child(k).data(0, ROLE_KEY)
            self._item_by_idx.pop(idx, None)
            sub_item.removeChild(sub_item.child(k))
        for info in infos:
            status, has_note = image_status(self.folder, info.path)
            if not passes_filter(self._filter, status, has_note):
                continue
            idx = self._idx_by_name.get(info.path.name)
            if idx is None:
                continue
            text = f"{STATUS_ICONS_3[status]}  {info.path.name}" + ("  ❝" if has_note else "")
            it = QTreeWidgetItem([text])
            it.setData(0, ROLE_KIND, "img"); it.setData(0, ROLE_KEY, idx)
            it.setForeground(0, QBrush(QColor(self.P[STATUS_TO_COLOR[status]])))
            sub_item.addChild(it)
            self._item_by_idx[idx] = it

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
