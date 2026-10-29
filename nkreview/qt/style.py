"""Qt stylesheet built from the app palette (theme.palette) — taken over from
ErysReview so both apps share one set of colour tokens — light and dark alike. Look target:
mockup v3 (rounded controls, segmented tool groups, captioned toolbar groups)."""

from __future__ import annotations


def _mix(a: str, b: str, t: float) -> str:
    """Blend hex colour a toward b by t (0..1)."""
    pa = [int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    pb = [int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(pa, pb))


def tokens(P: dict) -> dict:
    """Palette plus the few derived shades the mockup uses."""
    return {**P,
            "accent_soft": _mix(P["panel_bg"], P["accent_primary"], 0.10),
            "hover": _mix(P["panel_bg"], P["fg"], 0.05),
            "line": P["sash"]}


def build_palette(P: dict):
    """A full QPalette from the app palette. Without it, everything the
    stylesheet does not colour itself (combobox popups, menus, message boxes,
    input dialogs, tooltips) follows the *system* appearance — a black filter
    list in the light theme when macOS is set to dark."""
    from PySide6.QtGui import QColor, QPalette
    T = tokens(P)
    pal = QPalette()
    roles = {
        QPalette.Window: T["bg"], QPalette.WindowText: T["fg"],
        QPalette.Base: T["panel_bg"], QPalette.AlternateBase: T["tree_alt_bg"],
        QPalette.Text: T["fg"], QPalette.PlaceholderText: T["fg_muted"],
        QPalette.Button: T["panel_bg"], QPalette.ButtonText: T["fg"],
        QPalette.Highlight: T["accent_primary"], QPalette.HighlightedText: T["fg_on_accent"],
        QPalette.ToolTipBase: T["tooltip_bg"], QPalette.ToolTipText: T["tooltip_fg"],
        QPalette.Link: T["accent_primary"], QPalette.BrightText: T["fg_inverse"],
        QPalette.Light: T["panel_bg"], QPalette.Midlight: T["border"],
        QPalette.Mid: T["sash"], QPalette.Dark: T["sash"], QPalette.Shadow: "#000000",
    }
    for role, col in roles.items():
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(T["fg_muted"]))
    return pal


def arrow_images(P: dict) -> dict[str, str]:
    """Small up/down triangles in the theme's text colour, as PNG files for the
    stylesheet (Qt draws spin-box arrows from images; the CSS border-triangle
    trick renders as bars). Needs a QGuiApplication; returns {} without one."""
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPolygonF
    if QGuiApplication.instance() is None:
        return {}
    from ..paths import app_support_dir
    out_dir = app_support_dir() / "qt-style"
    out_dir.mkdir(parents=True, exist_ok=True)
    col = tokens(P)["fg_secondary"]
    paths = {}
    for name, pts in (("up", [(1, 7), (5, 2), (9, 7)]), ("down", [(1, 3), (5, 8), (9, 3)])):
        img = QImage(20, 20, QImage.Format_ARGB32)
        img.fill(Qt.transparent)
        p = QPainter(img); p.setRenderHint(QPainter.Antialiasing)
        p.scale(2, 2); p.setPen(Qt.NoPen); p.setBrush(QColor(col))
        p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in pts])); p.end()
        path = out_dir / f"arrow-{name}-{col.lstrip('#')}@2x.png"
        img.save(str(path))
        paths[name] = Path(path).as_posix()
    return paths


def build_qss(P: dict) -> str:
    T = tokens(P)
    A = arrow_images(P)
    up = f'image: url("{A["up"]}"); width: 10px; height: 10px;' if A else "image: none;"
    down = f'image: url("{A["down"]}"); width: 10px; height: 10px;' if A else "image: none;"
    return f"""
QMainWindow, QDialog {{ background: {T['bg']}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {T['bg']}; }}
QWidget {{ color: {T['fg']}; }}
QToolTip {{ background: {T['tooltip_bg']}; color: {T['tooltip_fg']}; border: none; padding: 6px 10px; }}

#toolbar, #toolbarRow2 {{ background: {T['toolbar_bg']}; border-bottom: 1px solid {T['border']}; }}
#toolbarRow2 {{ background: {T['sidebar_bg']}; }}
QLabel#caption {{ color: {T['fg_muted']}; font-size: 10px; }}
QLabel#banner {{ color: {T['accent_warn']}; font-style: italic; }}

QPushButton#study {{
    border: 2px solid {T['accent_primary']}; border-radius: 10px;
    background: {T['accent_soft']}; min-height: 40px;
}}
QPushButton#study:hover {{ background: {T['hover']}; }}
#study QLabel {{ background: transparent; }}

#segment {{ border: 1px solid {T['line']}; border-radius: 9px; background: {T['panel_bg']}; }}
#segment QPushButton {{
    border: none; border-left: 1px solid {T['line']}; border-radius: 0;
    background: transparent; color: {T['fg']}; padding: 0 12px; min-height: 40px;
}}
#segment QPushButton[first="true"] {{ border-left: none;
    border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
#segment QPushButton[last="true"] {{
    border-top-right-radius: 8px; border-bottom-right-radius: 8px; }}
#segment QPushButton:hover {{ background: {T['hover']}; }}
#segment QPushButton:checked {{ background: {T['accent_primary']}; color: {T['fg_on_accent']}; font-weight: 600; }}

QPushButton {{
    border: 1px solid {T['border']}; border-radius: 8px; background: {T['panel_bg']};
    color: {T['fg']}; padding: 0 10px; min-height: 30px;
}}
QPushButton:hover {{ background: {T['hover']}; }}
QPushButton:disabled {{ color: {T['fg_muted']}; }}
QPushButton[class~="tool"] {{ min-height: 40px; }}
QPushButton[class~="tool"]:checked {{ border: 2px solid {T['accent_primary']}; background: {T['accent_soft']};
    color: {T['accent_primary']}; font-weight: 700; }}
QPushButton[class~="icon"] {{ min-width: 40px; max-width: 40px; padding: 0; font-size: 16px; }}
QPushButton[class~="primary"] {{ border: none; background: {T['accent_primary']};
    color: {T['fg_on_accent']}; font-weight: 600; }}
QPushButton[class~="primary"]:disabled {{ background: {T['sidebar_alt_bg']}; color: {T['fg_muted']}; }}
QPushButton#cellposeBtn[cpstate="stale"] {{ border: 2px solid {T['accent_warn']};
    background: {_mix(T['panel_bg'], T['accent_warn'], 0.12)}; }}
QPushButton#cellposeBtn[cpstate="unsegmented"] {{ border: none; background: {T['accent_primary']};
    color: {T['fg_on_accent']}; font-weight: 600; }}
QLabel#staleBox {{ color: {T['accent_warn']}; background: {_mix(T['panel_bg'], T['accent_warn'], 0.12)};
    border-radius: 8px; padding: 8px 10px; }}
QPushButton#rerunBtn {{ border: 1px solid {T['accent_warn']}; color: {T['accent_warn']};
    background: {_mix(T['panel_bg'], T['accent_warn'], 0.10)}; font-size: 12px; min-height: 28px; }}
QPushButton#keyCapture {{ font-family: Menlo, monospace; }}
QPushButton#keyCapture[capturing="true"] {{ border: 2px solid {T['accent_primary']};
    background: {T['accent_soft']}; color: {T['accent_primary']}; }}
QLabel#cpDot {{ background: {T['accent_warn']}; border: 2px solid {T['panel_bg']}; border-radius: 6px; }}
QPushButton#export {{ border: none; background: {T['state_finished']}; color: #FFFFFF;
    font-weight: 600; padding: 0 14px; min-height: 40px; }}
QPushButton[class~="chip"] {{ border-radius: 16px; min-height: 32px; padding: 0 10px; }}
QPushButton[class~="chip"]:checked {{ border: 2px solid {T['fg']}; font-weight: 600; }}

QComboBox {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 0 8px;
    min-height: 32px; background: {T['panel_bg']}; color: {T['fg']};
}}
QPushButton#filterButton {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 0 8px; min-height: 32px;
    background: {T['panel_bg']}; color: {T['fg']}; font-weight: normal; text-align: left;
}}
QComboBox QAbstractItemView {{
    background: {T['panel_bg']}; color: {T['fg']}; border: 1px solid {T['border']};
    selection-background-color: {T['accent_primary']}; selection-color: {T['fg_on_accent']};
    outline: 0;
}}
QMenu {{ background: {T['panel_bg']}; color: {T['fg']}; border: 1px solid {T['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {T['accent_primary']}; color: {T['fg_on_accent']}; }}
QMenu::item:disabled {{ color: {T['fg_muted']}; }}
QMessageBox, QInputDialog, QProgressDialog {{ background: {T['bg']}; }}
QCheckBox, QRadioButton {{ color: {T['fg']}; spacing: 8px; }}
QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid {T['fg_muted']}; background: {T['panel_bg']}; }}
QRadioButton::indicator:checked {{ border: 5px solid {T['accent_primary']}; width: 6px; height: 6px;
    background: {T['fg_on_accent']}; }}
QRadioButton::indicator:disabled {{ border-color: {T['border']}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {_mix(T['bg'], T['fg'], 0.28)}; border-radius: 4px; min-height: 28px; min-width: 28px; }}
QScrollBar::handle:hover {{ background: {_mix(T['bg'], T['fg'], 0.45)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
QHeaderView::section {{ background: {T['sidebar_bg']}; color: {T['fg_secondary']}; border: none;
    border-bottom: 1px solid {T['border']}; padding: 4px 6px; }}
QTreeWidget, QTreeView, QListView, QTableView {{ alternate-background-color: {T['tree_alt_bg']}; }}
QSpinBox, QDoubleSpinBox {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 3px 22px 3px 8px; min-height: 26px;
    background: {T['panel_bg']}; color: {T['fg']};
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; height: 15px; border: none; background: transparent; margin: 2px 2px 0 0; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; height: 15px; border: none; background: transparent; margin: 0 2px 2px 0; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{ background: {T['hover']}; border-radius: 4px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ {up} }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ {down} }}
QLineEdit, QPlainTextEdit {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 4px 8px;
    background: {T['panel_bg']}; color: {T['fg']};
}}

#sidebar {{ background: {T['sidebar_bg']}; }}
#paneHead {{ background: {T['panel_bg']}; border-bottom: 1px solid {T['border']}; }}
#paneFoot {{ background: {T['panel_bg']}; border-top: 1px solid {T['border']}; }}
#paneHead QProgressBar {{ border: none; border-radius: 3px; background: {T['line']}; }}
#paneHead QProgressBar::chunk {{ border-radius: 3px; background: #1B7A4D; }}
#sidebarFilter {{ background: {T['sidebar_bg']}; border-bottom: 1px solid {T['border']}; }}
#sidebarDetails {{ background: {T['panel_bg']}; border-top: 1px solid {T['border']}; }}
QTreeWidget {{ background: {T['sidebar_bg']}; border: none; outline: 0; padding: 4px; }}
QTreeWidget::item {{ padding: 4px 4px; border-radius: 6px; }}
QTreeWidget::item:selected {{ background: {T['accent_primary']}; color: {T['fg_on_accent']}; }}
QTreeWidget::item:hover:!selected {{ background: {T['sidebar_alt_bg']}; }}
QSplitter::handle {{ background: {T['border']}; width: 1px; }}

QStatusBar {{ background: {T['status_bg']}; border-top: 1px solid {T['border']}; }}
QStatusBar QLabel {{ color: {T['fg_secondary']}; padding: 0 6px; }}

#card {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 12px; }}
#card QLabel#cardTitle {{ font-size: 18px; font-weight: 600; }}
#sep {{ background: {T['border']}; max-height: 1px; }}
QProgressBar {{ border: none; background: {T['sidebar_alt_bg']}; border-radius: 4px; max-height: 8px; }}
QProgressBar::chunk {{ background: {T['state_finished']}; border-radius: 4px; }}

#settingsNav {{ background: {T['sidebar_bg']}; border-right: 1px solid {T['border']}; }}
QPushButton#navItem {{ border: none; text-align: left; padding: 0 10px; min-height: 34px;
    background: transparent; border-radius: 8px; }}
QPushButton#navItem:hover {{ background: {T['sidebar_alt_bg']}; }}
QPushButton#navItem:checked {{ background: {T['accent_primary']}; color: {T['fg_on_accent']}; font-weight: 600; }}
QPushButton#studySwitch {{ border: 2px solid {T['accent_primary']}; border-radius: 10px; min-height: 40px;
    text-align: left; padding: 0 12px; font-weight: 600; margin: 6px 0 4px 0; }}
QLabel#badgeStudy {{ color: {T['accent_primary']}; background: {T['accent_soft']}; border-radius: 9px;
    padding: 2px 8px; font-size: 11px; margin: 0 0 8px 0; }}
QLabel#badgeLocal {{ color: {T['fg_secondary']}; background: {T['sidebar_alt_bg']}; border-radius: 9px;
    padding: 2px 8px; font-size: 11px; margin: 4px 0 8px 0; }}
QFrame#settingsCard {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 12px; }}
QLabel#chip {{ color: {T['fg_secondary']}; background: {T['hover']}; border-radius: 6px; padding: 2px 8px; font-size: 11px; }}
QFrame#folderRow {{ border: none; border-top: 1px solid {T['border']}; background: transparent; }}
QLabel#runBig {{ color: {T['fg']}; font-size: 24px; font-weight: 700; background: transparent; }}
QProgressBar#runBar {{ background: {T['hover']}; border: none; border-radius: 5px; }}
QProgressBar#runBar::chunk {{ background: {T['accent_primary']}; border-radius: 5px; }}
QListWidget#runList {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 12px; padding: 4px; }}
QListWidget#runList::item {{ padding: 7px 8px; border-bottom: 1px solid {T['border']}; }}
QFrame#settingsTile {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 14px; }}
QFrame#settingsTile:hover {{ border: 1px solid {T['accent_primary']}; }}
QLabel#tileTitle {{ color: {T['fg']}; font-size: 16px; font-weight: 600; background: transparent; }}
QLabel#tileSub {{ color: {T['fg_secondary']}; font-size: 12px; background: transparent; }}
QLabel#settingsSummary {{ color: {T['fg']}; background: {T['accent_soft']}; border-radius: 8px; padding: 9px 12px; }}
QLabel#sampleImage {{ background: {T['hover']}; border: 1px solid {T['border']}; border-radius: 10px; color: {T['fg_muted']}; }}
QPushButton#objectiveHead {{ border: none; background: transparent; color: {T['accent_primary']};
    font-size: 19px; font-weight: 700; text-align: left; padding: 0; min-height: 0; }}
QPushButton#objectiveHead:hover {{ text-decoration: underline; }}
QPushButton#linkButton {{ border: none; background: transparent; color: {T['accent_primary']}; padding: 0 4px; }}
QPushButton#linkButton:hover {{ text-decoration: underline; }}
QFrame#settingsFooter {{ background: {T['panel_bg']}; border-top: 1px solid {T['border']}; }}
QLabel#checkLine {{ color: {T['state_finished']}; background: {_mix(T['panel_bg'], T['state_finished'], 0.10)};
    border-radius: 8px; padding: 8px 10px; }}
QLabel#checkLine[bad="true"] {{ color: {T['accent_warn']}; background: {_mix(T['panel_bg'], T['accent_warn'], 0.10)}; }}
QFrame#studyCard {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 14px; }}
QFrame#studyCard[selected="true"] {{ border: 2px solid {T['accent_primary']}; }}
QFrame#choiceCard {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 10px; }}
QFrame#choiceCard[on="true"] {{ border: 2px solid {T['accent_primary']}; background: {T['accent_soft']}; }}
QFrame#choiceCard QLabel, QFrame#choiceCard QRadioButton {{ background: transparent; }}
QFrame#probeBox {{ background: {T['sidebar_bg']}; border-radius: 10px; }}
QLabel#probeThumb {{ background: {T['sidebar_alt_bg']}; border-radius: 8px; }}
QLabel#detCells {{ color: {T['accent_error']}; font-size: 15px; font-weight: 700; }}
QLabel#detCells[reached="true"] {{ color: {T['state_finished']}; }}
QSplitter#paneSplit::handle {{ background: transparent; width: 16px; }}
QFrame#metricCard {{ background: {T['sidebar_bg']}; border-radius: 8px; }}
QFrame#metricCard QLabel {{ background: transparent; }}
#notebox {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 12px; }}
"""


def system_scheme() -> str:
    """'light' or 'dark': what the operating system shows right now."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication.instance()
    if app is None:
        return "light"
    hints = app.styleHints()
    # While we force a scheme (below), colorScheme() reports ours, not the
    # system's — so lift the override for the question.
    forced = hints.colorScheme()
    if hasattr(hints, "unsetColorScheme"):
        hints.unsetColorScheme()
    scheme = hints.colorScheme()
    if hasattr(hints, "setColorScheme") and forced != scheme and forced != Qt.ColorScheme.Unknown:
        hints.setColorScheme(forced)
    return "dark" if scheme == Qt.ColorScheme.Dark else "light"


def apply_look(app, theme: str = "system") -> str:
    """Palette and stylesheet for the whole application; 'system' follows the
    OS appearance. Returns the resolved scheme ('light' / 'dark')."""
    from ..theme import THEME_SYSTEM, palette, resolve_theme, set_system_scheme
    if theme == THEME_SYSTEM:
        set_system_scheme(system_scheme())
    P = palette(theme)
    app.setPalette(build_palette(P))
    app.setStyleSheet(build_qss(P))
    return resolve_theme(theme)
