"""Workarounds for Tk platform bugs."""

from __future__ import annotations

import tkinter as tk

# On aqua, Tk runs a menu-bar command *inside* AppKit's menu tracking session
# (NSMenuTrackingSession → TkInvokeMenu). A command that opens a modal dialog
# sits in wait_window() there, so the session never finishes; on macOS 27 /
# Tk 9.0.3 the next native menu — any ttk.Combobox dropdown in that dialog —
# then hangs the app (endless spinner; sampled stack: tkwait under
# _performPostTrackingDismissalActions). Dialogs opened from the toolbar or a
# hotkey were fine. Fix: re-schedule every menu command with `after 0`, so the
# menu action returns and AppKit closes the session before the dialog opens.
_DEFER_PROC = r"""
namespace eval ::erys {}
proc ::erys::deferMenu {m} {
    set last [$m index end]
    if {$last eq "" || $last eq "none"} return
    for {set i 0} {$i <= $last} {incr i} {
        switch -- [$m type $i] {
            command - checkbutton - radiobutton {
                set c [$m entrycget $i -command]
                if {$c ne "" && [lindex $c 0] ne "after"} {
                    $m entryconfigure $i -command [list after 0 $c]
                }
            }
            cascade {
                set sub [$m entrycget $i -menu]
                if {$sub ne ""} { ::erys::deferMenu $sub }
            }
        }
    }
}
"""


def defer_menu_commands(menu: tk.Menu) -> None:
    """Make `menu`'s commands (and its cascades') run after the menu closed.
    Idempotent; call again after adding entries. No-op off macOS."""
    if menu.tk.call("tk", "windowingsystem") != "aqua":
        return
    if not menu.tk.call("info", "procs", "::erys::deferMenu"):
        menu.tk.eval(_DEFER_PROC)
    menu.tk.call("::erys::deferMenu", str(menu))
