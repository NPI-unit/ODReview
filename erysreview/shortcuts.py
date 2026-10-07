"""Keyboard and mouse shortcuts — one registry, per-computer overrides, and
the rule that every key or mouse button belongs to at most one action.

A binding is a string: a Qt key sequence ("1", "Left", "Ctrl+S") or a mouse
side button ("Mouse:Back", "Mouse:Forward"). "Ctrl" means ⌘ on macOS, as in
Qt. AppSettings.shortcuts stores only what differs from the defaults
({action_id: [bindings]}), so new actions and changed defaults reach every
computer that has not overridden them.
"""

from __future__ import annotations

from dataclasses import dataclass

MOUSE_BACK = "Mouse:Back"
MOUSE_FORWARD = "Mouse:Forward"
MOUSE_BINDINGS = (MOUSE_BACK, MOUSE_FORWARD)


@dataclass(frozen=True)
class Action:
    id: str
    label_key: str          # i18n key of the action's name
    defaults: tuple[str, ...]
    menu: bool = False      # lives in a menu (QAction) rather than as a window shortcut


ACTIONS: list[Action] = [
    Action("tool_add", "tool_add", ("1",)),
    Action("tool_remove", "tool_remove", ("2",)),
    Action("tool_type", "tool_type", ("3",)),
    Action("tool_note", "tool_note", ("4",)),
    Action("tool_cursor", "tool_cursor", ("5", "Esc")),
    Action("prev_image", "qt_sc_prev", ("Left", MOUSE_BACK)),
    Action("next_image", "qt_sc_next", ("Right", MOUSE_FORWARD)),
    Action("toggle_finished", "qt_sc_finished", ("F",)),
    Action("cycle_eval", "qt_sc_eval", ("E",)),
    Action("resegment", "tool_resegment", ("R",)),
    Action("info", "tool_info", ("I",)),
    Action("next_diff", "sp_next", ("N",)),
    Action("prev_diff", "sp_prev", ("Shift+N",)),
    Action("take_b", "sp_take", ("B",)),
    Action("keep_a", "sp_keep", ("A",)),
    Action("split_layout", "sp_layout_key", ("Space",)),
    Action("undo", "tool_undo", ("Z", "Ctrl+Z"), menu=True),
    Action("overlay", "tool_overlay", ("O",), menu=True),
    Action("reset_zoom", "tool_reset_zoom", ("0", "Home"), menu=True),
    Action("grid", "qt_grid", ("G",), menu=True),
    Action("export", "menu_export", ("S", "Ctrl+S"), menu=True),
    Action("batch", "batch_title", ("Ctrl+B",), menu=True),
    Action("open_study", "qt_studies_title", ("Ctrl+Shift+O",), menu=True),
    Action("open_folder", "menu_open_folder", ("Ctrl+O",), menu=True),
    Action("refresh", "menu_refresh", ("F5", "Ctrl+R"), menu=True),
    Action("settings", "settings_title", ("Ctrl+,",), menu=True),
    Action("quit", "menu_quit", ("Ctrl+Q",), menu=True),
]
BY_ID = {a.id: a for a in ACTIONS}


def normalize(binding: str) -> str:
    """Canonical spelling so "ctrl+s", "Ctrl+S" and "Escape"/"Esc" compare equal."""
    b = binding.strip()
    if b in MOUSE_BINDINGS or not b:
        return b
    parts = [p.strip() for p in b.split("+") if p.strip()] or [b]
    alias = {"escape": "Esc", "esc": "Esc", "return": "Return", "enter": "Enter",
             "cmd": "Ctrl", "command": "Ctrl", "control": "Ctrl", "ctrl": "Ctrl",
             "alt": "Alt", "option": "Alt", "shift": "Shift", "meta": "Meta"}
    mods = [alias.get(p.lower(), p) for p in parts[:-1]]
    key = alias.get(parts[-1].lower(), parts[-1])
    key = key.upper() if len(key) == 1 else key
    order = ["Ctrl", "Alt", "Shift", "Meta"]
    mods = sorted(dict.fromkeys(mods), key=lambda m: order.index(m) if m in order else 9)
    return "+".join(mods + [key])


def effective(overrides: dict | None) -> dict[str, list[str]]:
    """{action_id: bindings}: defaults, replaced per action by the overrides."""
    out = {a.id: [normalize(b) for b in a.defaults] for a in ACTIONS}
    for aid, bindings in (overrides or {}).items():
        if aid in out and isinstance(bindings, list):
            out[aid] = [normalize(b) for b in bindings if isinstance(b, str) and b.strip()]
    return out


def conflicts(mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    """{binding: [action ids]} for every binding used by more than one action."""
    owners: dict[str, list[str]] = {}
    for aid, bindings in mapping.items():
        for b in dict.fromkeys(bindings):
            owners.setdefault(b, []).append(aid)
    return {b: ids for b, ids in owners.items() if len(ids) > 1}


def owner_of(mapping: dict[str, list[str]], binding: str, except_id: str | None = None) -> str | None:
    """The action that already uses `binding` (other than `except_id`), if any."""
    b = normalize(binding)
    for aid, bindings in mapping.items():
        if aid != except_id and b in bindings:
            return aid
    return None


def overrides_from(mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    """What to store: only the actions whose bindings differ from the defaults."""
    defaults = effective({})
    return {aid: list(b) for aid, b in mapping.items() if b != defaults.get(aid)}


def resolve(overrides: dict | None) -> tuple[dict[str, list[str]], list[str]]:
    """The mapping to use at startup, guaranteed unique. Actions involved in a
    conflict (e.g. a hand-edited settings file) fall back to their defaults;
    returns the mapping and the ids that were reset."""
    mapping = effective(overrides)
    reset: list[str] = []
    for _ in range(len(ACTIONS)):
        clash = conflicts(mapping)
        if not clash:
            break
        defaults = effective({})
        for ids in clash.values():
            for aid in ids:
                if mapping[aid] != defaults[aid]:
                    mapping[aid] = list(defaults[aid])
                    reset.append(aid)
        if conflicts(mapping) == clash:          # defaults themselves clash: drop the extra
            for b, ids in clash.items():
                for aid in ids[1:]:
                    mapping[aid] = [x for x in mapping[aid] if x != b]
                    reset.append(aid)
    return mapping, list(dict.fromkeys(reset))
