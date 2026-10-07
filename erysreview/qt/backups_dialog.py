"""Sicherungen of study A (backups.py): list, load (with a warning; the current
state is saved first), delete, back up now."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout,
)

from .. import backups


class BackupsDialog(QDialog):
    def __init__(self, parent, P, S, folder: Path):
        super().__init__(parent)
        self.P, self.S, self.folder = P, S, folder
        self.restored = False
        self.setWindowTitle(S["bk_title"])
        self.resize(760, 480)
        v = QVBoxLayout(self); v.setContentsMargins(22, 18, 22, 16); v.setSpacing(10)
        t = QLabel(S["bk_title"]); t.setStyleSheet("font-size: 18px; font-weight: 700;")
        sub = QLabel(S["bk_sub"]); sub.setWordWrap(True); sub.setStyleSheet(f"color: {P['fg_secondary']};")
        v.addWidget(t); v.addWidget(sub)
        self.tree = QTreeWidget(); self.tree.setRootIsDecorated(False); self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([S["bk_col_name"], S["bk_col_kind"], S["bk_col_date"], S["bk_col_n"]])
        self.tree.setColumnWidth(0, 300); self.tree.setColumnWidth(1, 130); self.tree.setColumnWidth(2, 150)
        self.tree.itemSelectionChanged.connect(self._buttons)
        v.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.btn_new = QPushButton(S["sp_save_now"]); self.btn_new.clicked.connect(lambda: self.backup_now())
        self.btn_del = QPushButton(S["bk_delete"]); self.btn_del.clicked.connect(lambda: self.delete())
        self.btn_load = QPushButton(S["bk_load"]); self.btn_load.setProperty("class", "primary")
        self.btn_load.clicked.connect(lambda: self.load())
        close = QPushButton(S["qt_close"]); close.clicked.connect(self.accept)
        row.addWidget(self.btn_new); row.addWidget(self.btn_del); row.addStretch(1)
        row.addWidget(close); row.addWidget(self.btn_load)
        v.addLayout(row)
        self._fill()

    def _fill(self) -> None:
        self.tree.clear()
        kinds = {"abgleich": self.S["bk_kind_abgleich"], "manual": self.S["bk_kind_manual"],
                 "auto": self.S["bk_kind_auto"]}
        for b in backups.list_backups(self.folder):
            it = QTreeWidgetItem([b.name, kinds.get(b.kind, b.kind), b.created.replace("T", " ")[:16], str(b.n_images)])
            it.setData(0, Qt.UserRole, b.id)
            self.tree.addTopLevelItem(it)
        self._buttons()

    def _selected(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it is not None and it.isSelected() else None

    def _buttons(self) -> None:
        has = self._selected() is not None
        self.btn_load.setEnabled(has); self.btn_del.setEnabled(has)

    def backup_now(self, name: str | None = None) -> None:
        if name is None:
            name, ok = QInputDialog.getText(self, self.S["bk_title"], self.S["bk_name_prompt"],
                                            text=self.S["bk_default_name"])
            if not ok or not name.strip():
                return
        backups.create(self.folder, name.strip(), "manual")
        self._fill()

    def delete(self, confirm=None) -> None:
        bid = self._selected()
        if bid is None:
            return
        b = backups.get(self.folder, bid)
        ask = confirm or (lambda: QMessageBox.question(self, self.S["bk_title"],
                                                       self.S["bk_delete_q"].format(name=b.name)) == QMessageBox.Yes)
        if b.kind == "abgleich":
            ask = confirm or (lambda: QMessageBox.question(self, self.S["bk_title"],
                                                           self.S["bk_delete_abgleich_q"].format(name=b.name))
                              == QMessageBox.Yes)
        if ask():
            backups.delete(self.folder, bid)
            self._fill()

    def load(self, confirm=None) -> None:
        bid = self._selected()
        if bid is None:
            return
        b = backups.get(self.folder, bid)
        ask = confirm or (lambda: QMessageBox.warning(
            self, self.S["bk_title"], self.S["bk_load_q"].format(name=b.name, date=b.created.replace("T", " ")[:16]),
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) == QMessageBox.Yes)
        if not ask():
            return
        backups.restore(self.folder, bid, self.S["bk_auto_name"])
        self.restored = True
        self._fill()
        QMessageBox.information(self, self.S["bk_title"], self.S["bk_loaded"].format(name=b.name)) \
            if confirm is None else None
