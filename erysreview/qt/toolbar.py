"""Qt toolbar: layout A (one row, captioned groups) or C (two rows, cell types
as colour chips), chosen by AppSettings.toolbar_layout — same setting and the
same CHIP_MAX_TYPES fallback as the Tk toolbar. Callbacks are passed in by the
main window; the toolbar holds no state of its own beyond what it displays."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton,
    QVBoxLayout, QWidget,
)

from ..persistence import TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE, TOOLS_ORDER
from ..theme import CHIP_MAX_TYPES, GLYPHS, TOOLBAR_TWO_ROW


def dot_icon(color: str, ring: str, size: int = 14) -> QIcon:
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(ring))
    p.setBrush(QColor(color))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


def bars_icon(color: str, size: int = 16) -> QIcon:
    """Statistik symbol (mockup choice D): three small bars in the text colour."""
    pm = QPixmap(size * 2, size * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    u = size / 16.0
    for x, y in ((2, 9), (6.5, 5), (11, 2)):
        p.drawRoundedRect(x * u, y * u, 3 * u, (14 - y) * u, 0.6 * u, 0.6 * u)
    p.end()
    return QIcon(pm)


def _set_class(w: QWidget, cls: str) -> QWidget:
    w.setProperty("class", cls)
    return w


class QtToolbar(QWidget):
    def __init__(self, P: dict, S: dict, settings, profile,
                 callbacks: dict[str, Callable], parent=None):
        super().__init__(parent)
        self.P, self.S, self.settings, self.profile = P, S, settings, profile
        self.cb = callbacks
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(0)
        self._build()
        self.setMinimumWidth(1000)

    # ── public API (same names as the Tk toolbar) ──
    def rebuild(self) -> None:
        while self._root.count():
            item = self._root.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._build()
        self._fit()

    def set_active_tool(self, key: str) -> None:
        btn = self.tool_buttons.get(key)
        if btn is not None:
            btn.setChecked(True)

    def set_param_mismatch(self, message: str | None) -> None:
        # Kept for API compatibility; the state is shown on the Cellpose
        # button now (set_cellpose_state), not as a text in the toolbar.
        self.banner.setText(message or "")

    def set_cellpose_state(self, state: str, tooltip: str) -> None:
        """'ok' — quiet; 'stale' — orange border + dot (masks made with other
        values); 'unsegmented' — the main action, blue "Rechnen"."""
        b = self.btn_resegment
        full = f"{GLYPHS['resegment']}  " + (self.S["qt_compute"] if state == "unsegmented"
                                             else self.S["tool_resegment"])
        self._compact = [(x, full if x is b else f, sh) for x, f, sh in self._compact]
        b.setText(full)
        b.setProperty("cpstate", state)
        b.style().unpolish(b); b.style().polish(b)
        b.setToolTip(tooltip)
        self._cp_dot.setVisible(state == "stale")
        self._fit()

    def set_abgleich(self, available: bool, active: bool = False) -> None:
        self.btn_abgleich.setVisible(available)
        self.btn_abgleich.setChecked(active)
        self.btn_stats.setVisible(available)
        self._fit()

    def set_profile(self, profile) -> None:
        self.profile = profile
        self._study_name.setText(profile.name)
        self.refresh_type_combo(profile)

    def refresh_type_combo(self, profile) -> None:
        self.profile = profile
        if profile.type_by_key(profile.active_type_key) is None:
            profile.active_type_key = profile.default_type_key()
        self._fill_type_selector()

    def resizeEvent(self, ev) -> None:
        super().resizeEvent(ev)
        self._fit()

    def _fit(self) -> None:
        """Full texts when they fit, otherwise only the symbols (the tooltips
        keep the names) — so the window fits a laptop screen."""
        row = self._root.itemAt(0).widget() if self._root.count() else None
        if row is None or not getattr(self, "_compact", None):
            return
        for b, full, _short in self._compact:
            b.setText(full)
        row.layout().activate()
        if row.sizeHint().width() > self.width():
            for b, _full, short in self._compact:
                b.setText(short)

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is getattr(self, "btn_resegment", None) and ev.type() == QEvent.Resize:
            self._cp_dot.move(obj.width() - 13, 1)
        return False

    # ── building ──
    def _build(self) -> None:
        # (button, full text, short text): shortened to the symbol when the
        # window is too narrow for the full row (see _fit)
        self._compact: list[tuple[QPushButton, str, str]] = []
        self.tool_buttons: dict[str, QPushButton] = {}
        self._tools = QButtonGroup(self)
        self._tools.setExclusive(True)
        if self.settings.toolbar_layout == TOOLBAR_TWO_ROW:
            self._build_two_row()
        else:
            self._build_grouped()
        self.set_active_tool(self.settings.active_tool)

    def _row(self, name: str) -> QHBoxLayout:
        fr = QFrame(); fr.setObjectName(name)
        h = QHBoxLayout(fr)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(12)
        self._root.addWidget(fr)
        return h

    def _build_grouped(self) -> None:
        """Layout A: one row, everything on one level — no group captions; the
        groups are kept apart by spacing and the segmented tool frames."""
        h = self._row("toolbar")
        h.setAlignment(Qt.AlignVCenter)
        h.addWidget(self._study_button())
        h.addSpacing(6)
        h.addWidget(self._segment([TOOL_ADD, TOOL_REMOVE, TOOL_TYPE]))
        self._type_holder = QHBoxLayout(); self._type_holder.setSpacing(4)
        h.addLayout(self._type_holder)
        h.addSpacing(6)
        h.addWidget(self._segment([TOOL_NOTE, TOOL_CURSOR]))
        h.addSpacing(6)
        h.addWidget(self._action("overlay", "tool_overlay", "overlay"))
        h.addWidget(self._action("reset_zoom", "tool_reset_zoom", "reset_zoom", icon_only=True))
        h.addStretch(1)
        self._finish(h)
        self._chips_mode = False
        self._fill_type_selector()

    def _build_two_row(self) -> None:
        top = self._row("toolbar")
        top.addWidget(self._study_button())
        top.addStretch(1)
        self._finish(top)
        bottom = self._row("toolbarRow2")
        bottom.addWidget(self._segment(list(TOOLS_ORDER)))
        self._type_holder = QHBoxLayout(); self._type_holder.setSpacing(4)
        bottom.addLayout(self._type_holder)
        bottom.addStretch(1)
        bottom.addWidget(self._action("overlay", "tool_overlay", "overlay"))
        bottom.addWidget(self._action("reset_zoom", "tool_reset_zoom", "reset_zoom", icon_only=True))
        self._chips_mode = True
        self._fill_type_selector()

    def _finish(self, h: QHBoxLayout) -> None:
        self.banner = QLabel("")            # no longer shown (see set_cellpose_state)
        self.banner.hide()
        # Comparison (Abgleich): only shown when the study has one.
        self.btn_abgleich = QPushButton("⇆  " + self.S["sp_button"])
        _set_class(self.btn_abgleich, "tool"); self.btn_abgleich.setCheckable(True)
        self.btn_abgleich.setToolTip(self.S["sp_button_tip"])
        self.btn_abgleich.clicked.connect(lambda on: self.cb.get("abgleich", lambda _o: None)(on))
        self.btn_stats = QPushButton(self.S["sp_stats"])
        _set_class(self.btn_stats, "tool"); self.btn_stats.setIcon(bars_icon(self.P["fg"]))
        self.btn_stats.setToolTip(self.S["sp_stats_tip"])
        self.btn_stats.clicked.connect(lambda: self.cb.get("stats", lambda: None)())
        for b in (self.btn_abgleich, self.btn_stats):
            b.setVisible(False); h.addWidget(b)
        self._compact += [(self.btn_abgleich, self.btn_abgleich.text(), "⇆"),
                          (self.btn_stats, self.btn_stats.text(), "")]
        h.addWidget(self._action("undo", "tool_undo", "undo", icon_only=True))
        self.btn_resegment = self._action("resegment", "tool_resegment", "resegment")
        self.btn_resegment.setObjectName("cellposeBtn")
        # The orange dot of the 'stale' state, pinned to the button's corner.
        self._cp_dot = QLabel(self.btn_resegment); self._cp_dot.setObjectName("cpDot")
        self._cp_dot.setFixedSize(12, 12); self._cp_dot.move(0, 0); self._cp_dot.hide()
        self.btn_resegment.installEventFilter(self)
        h.addWidget(self.btn_resegment)
        h.addWidget(self._action("info", "tool_info", "info", icon_only=True))
        exp = QPushButton(f"{GLYPHS['export']}  {self.S['tool_export']}")
        exp.setObjectName("export"); exp.setCursor(Qt.PointingHandCursor)
        exp.setToolTip(self.S["tool_export"])
        exp.clicked.connect(lambda: self.cb["export"]())
        h.addWidget(exp)

    def _group(self, caption: str, inner: QWidget) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(4)
        lab = QLabel(caption.upper()); lab.setObjectName("caption")
        v.addWidget(lab); v.addWidget(inner)
        return w

    def _segment(self, keys: list[str]) -> QFrame:
        fr = QFrame(); fr.setObjectName("segment")
        h = QHBoxLayout(fr); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        labels = {TOOL_ADD: "tool_add", TOOL_REMOVE: "tool_remove", TOOL_TYPE: "tool_type",
                  TOOL_NOTE: "tool_note", TOOL_CURSOR: "tool_cursor"}
        for i, key in enumerate(keys):
            # No key numbers on buttons or tooltips: keys can be rebound, and
            # Hilfe › Tastenkürzel lists the current ones.
            prefix = GLYPHS["note"] if key == TOOL_NOTE else (
                GLYPHS["cursor"] if key == TOOL_CURSOR else "")
            b = QPushButton(f"{prefix}  {self.S[labels[key]]}" if prefix else self.S[labels[key]])
            if prefix:
                self._compact.append((b, b.text(), prefix))
            b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(self.S[labels[key]])
            b.setProperty("first", i == 0); b.setProperty("last", i == len(keys) - 1)
            b.clicked.connect(lambda _c=False, k=key: self.cb["tool"](k))
            self._tools.addButton(b)
            self.tool_buttons[key] = b
            h.addWidget(b)
        return fr

    def _action(self, glyph: str, label_key: str, cb: str, icon_only: bool = False) -> QPushButton:
        text = GLYPHS[glyph] if icon_only else f"{GLYPHS[glyph]}  {self.S[label_key]}"
        b = _set_class(QPushButton(text), "tool icon" if icon_only else "tool")
        if not icon_only:
            self._compact.append((b, text, GLYPHS[glyph]))
        b.setToolTip(self.S[label_key]); b.setCursor(Qt.PointingHandCursor)
        if icon_only:
            b.setAccessibleName(self.S[label_key])
        b.clicked.connect(lambda: self.cb[cb]())
        return b

    def _study_button(self) -> QPushButton:
        b = QPushButton(); b.setObjectName("study"); b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(self.S["tb_study_tip"]); b.setAccessibleName(self.S["tb_study_tip"])
        h = QHBoxLayout(b); h.setContentsMargins(12, 2, 12, 2); h.setSpacing(8)
        dot = QLabel(); dot.setPixmap(dot_icon(self.P["accent_primary"], self.P["accent_primary"], 10).pixmap(10, 10))
        h.addWidget(dot)
        col = QVBoxLayout(); col.setSpacing(0)
        cap = QLabel(self.S["settings_section_study"]); cap.setStyleSheet(f"font-size: 10px; color: {self.P['fg_secondary']};")
        self._study_name = QLabel(self.profile.name); self._study_name.setStyleSheet("font-size: 14px; font-weight: 600;")
        col.addWidget(cap); col.addWidget(self._study_name)
        h.addLayout(col, 1)
        h.addWidget(QLabel("▾"))
        b.setMinimumWidth(150)
        b.clicked.connect(lambda: self.cb["study"](b))
        self.btn_study = b
        return b

    # ── cell-type selector ──
    def _fill_type_selector(self) -> None:
        while self._type_holder.count():
            item = self._type_holder.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.type_combo = None
        self.chips: dict[str, QPushButton] = {}
        types = self.profile.cell_types
        ring = self.P["fg"]
        if self._chips_mode and 0 < len(types) <= CHIP_MAX_TYPES:
            group = QButtonGroup(self); group.setExclusive(True)
            for t in types:
                b = _set_class(QPushButton(self._name(t)), "chip")
                b.setIcon(dot_icon(t.color, ring)); b.setCheckable(True)
                b.setChecked(t.key == self.profile.active_type_key)
                b.clicked.connect(lambda _c=False, k=t.key: self.cb["type"](k))
                group.addButton(b)
                self.chips[t.key] = b
                self._type_holder.addWidget(b)
            return
        combo = QComboBox(); combo.setMinimumWidth(190)
        combo.setAccessibleName(self.S["status_tool_type"])
        for t in types:
            combo.addItem(dot_icon(t.color, ring), self._name(t), t.key)
        idx = combo.findData(self.profile.active_type_key)
        combo.setCurrentIndex(max(0, idx))
        combo.activated.connect(lambda i: self.cb["type"](combo.itemData(i)))
        self.type_combo = combo
        self._type_holder.addWidget(combo)

    @staticmethod
    def _name(t) -> str:
        return t.name or t.key          # same display rule as the Tk toolbar
