"""Qt stylesheet built from the app palette (theme.palette), so the Qt and Tk
front ends share one set of colour tokens — light and dark alike. Look target:
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


def build_qss(P: dict) -> str:
    T = tokens(P)
    return f"""
QMainWindow, QDialog {{ background: {T['bg']}; }}
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
QPushButton[class~="icon"] {{ min-width: 40px; max-width: 40px; padding: 0; font-size: 16px; }}
QPushButton[class~="primary"] {{ border: none; background: {T['accent_primary']};
    color: {T['fg_on_accent']}; font-weight: 600; }}
QPushButton[class~="primary"]:disabled {{ background: {T['sidebar_alt_bg']}; color: {T['fg_muted']}; }}
QPushButton#export {{ border: none; background: {T['state_finished']}; color: #FFFFFF;
    font-weight: 600; padding: 0 14px; min-height: 40px; }}
QPushButton[class~="chip"] {{ border-radius: 16px; min-height: 32px; padding: 0 10px; }}
QPushButton[class~="chip"]:checked {{ border: 2px solid {T['fg']}; font-weight: 600; }}

QComboBox {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 0 8px;
    min-height: 32px; background: {T['panel_bg']}; color: {T['fg']};
}}
QLineEdit, QPlainTextEdit {{
    border: 1px solid {T['line']}; border-radius: 6px; padding: 4px 8px;
    background: {T['panel_bg']}; color: {T['fg']};
}}

#sidebar {{ background: {T['sidebar_bg']}; }}
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

#notebox {{ background: {T['panel_bg']}; border: 1px solid {T['border']}; border-radius: 12px; }}
"""
