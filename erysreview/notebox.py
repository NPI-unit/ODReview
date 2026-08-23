"""Floating remark editor — one box, placed over the canvas.

**One box, not one per remark.** A box anchored to every annotated cell sounds
appealing until a field holds 271 cells and a dozen remarks: the boxes overlap
each other and hide the specimen underneath. A single box shows whichever
remark is currently selected, so it can never occlude more than one spot, and
the user drags it out of the way once instead of continuously.

**A Tk widget, not a matplotlib artist.** Text inside the figure would need its
own caret, wrapping, selection and hit-testing, and the drag gesture would fight
pan/zoom, which are bound to the same mouse buttons on the same canvas. Placed
over the canvas with `place()` it is an ordinary Text widget with none of those
problems.

**Explicit saving.** Nothing is written until *Speichern* is pressed — unlike
the rest of the app, where a click is itself the edit. Prose is different: half
a sentence is not a state worth persisting, and an accidental keystroke while
reading somebody's remark should not rewrite it. An existing remark therefore
opens read-only; *Bearbeiten* unlocks it. Closing with unsaved text asks first.

**Keys stay inside.** The Text widget's bindtags drop the toplevel, so typing a
`1` cannot switch tools and an arrow key cannot jump to the next image — which
it did, mid-sentence, before this was fixed.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .persistence import AppSettings
from .theme import palette as _palette

MIN_W, MIN_H = 260, 170     # fits header + three text lines + button bar
GRIP = 14           # side of the resize corner, in px


class NoteBox(tk.Frame):
    """Movable, resizable remark editor. Hidden until `show_*` is called."""

    def __init__(self, parent: tk.Misc, settings: AppSettings, strings: dict,
                 on_save: Callable[[str | None, str], None],
                 confirm: Callable[[str, str], bool] | None = None,
                 on_closed: Callable[[], None] | None = None):
        P = _palette(settings.theme)
        super().__init__(parent, bg=P["sidebar_alt_bg"],
                         highlightbackground=P["accent_primary"],
                         highlightthickness=2)
        self.P, self.S, self.settings = P, strings, settings
        self._on_save = on_save
        self._confirm = confirm or (lambda _t, _m: True)
        # Fired when the box actually closes, so the caller can hand the
        # annotate tool back to the neutral cursor — but only then, never
        # while a remark is still being written.
        self._on_closed = on_closed or (lambda: None)
        self._target: str | None = None      # cell id as str, or None = image
        self._visible = False
        self._saved_text = ""                # what is on disk right now
        self._editing = False
        self._drag: tuple[int, int, int, int] | None = None

        # ── header: title + drag handle + close ──
        self._head = tk.Frame(self, bg=P["accent_primary"], cursor="fleur")
        self._head.pack(fill="x")
        self._title = tk.Label(self._head, text="", bg=P["accent_primary"],
                               fg=P["fg_on_accent"], font=("", 10, "bold"),
                               anchor="w", cursor="fleur")
        self._title.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=3)
        tk.Button(self._head, text="✕", bd=0, highlightthickness=0,
                  bg=P["accent_primary"], fg=P["fg_on_accent"],
                  activebackground=P["accent_primary_hover"],
                  command=self.request_close).pack(side="right", padx=(0, 4))
        for w in (self._head, self._title):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # ── text ──
        # width/height are the *requested* size. Left at the Tk default a Text
        # asks for 80x24 characters — some 400 px tall — and pack, which serves
        # slaves in packing order, handed it everything and left the button bar
        # 1x1 and unmapped. Small request, packed last, expands into the rest.
        self.text = tk.Text(self, wrap="word", bd=0, highlightthickness=0,
                            bg=P["sidebar_alt_bg"], fg=P["fg"], width=1, height=3,
                            insertbackground=P["fg"], padx=8, pady=6)
        # Widget and class bindings only: without dropping the toplevel tag,
        # every keystroke also reached the app's hotkeys — a digit switched
        # tools, an arrow key advanced to the next image mid-sentence.
        self.text.bindtags((str(self.text), "Text", "all"))

        # ── buttons ──
        bar = tk.Frame(self, bg=P["sidebar_alt_bg"])
        # Packed before the text and anchored south: fixed chrome must claim its
        # height first, or an expanding sibling swallows it.
        bar.pack(fill="x", side="bottom", padx=6, pady=(0, 6))
        self._btn_save = tk.Button(bar, text=self.S["note_save"], bd=0,
                                   bg=P["accent_primary"], fg=P["fg_on_accent"],
                                   activebackground=P["accent_primary_hover"],
                                   padx=10, command=self._on_primary)
        self._btn_save.pack(side="left")
        # Glyph only, and on the far side of the bar: at the 260 px minimum the
        # full wording plus "Speichern" left nothing for either. No Close button
        # here either — the ✕ in the header already does that, and two ways to
        # close one box is one too many.
        self._btn_del = tk.Button(bar, text="🗑", bd=0, font=("", 13),
                                  bg=P["sidebar_alt_bg"], fg=P["accent_error"],
                                  activeforeground=P["accent_error"],
                                  padx=6, command=self._delete)
        self._btn_del.pack(side="right")
        self.text.pack(fill="both", expand=True)

        # ── resize grip ──
        self._grip = tk.Frame(self, bg=P["accent_primary"],
                              cursor="bottom_right_corner", width=GRIP, height=GRIP)
        self._grip.place(relx=1.0, rely=1.0, anchor="se")
        self._grip.bind("<Button-1>", self._resize_start)
        self._grip.bind("<B1-Motion>", self._resize_move)

    # ─────────────────────────── show / hide ───────────────────────────
    def show_for_cell(self, cid: int, text: str) -> None:
        self._switch(str(cid), self.S["note_title_cell"].format(cid=cid), text)

    def show_for_image(self, text: str) -> None:
        self._switch(None, self.S["note_title_image"], text)

    def _switch(self, target: str | None, title: str, text: str) -> None:
        if self._visible and self._dirty() and not self._ask_discard():
            return                                # keep the unsaved remark open
        self._target = target
        self._saved_text = text
        self._title.config(text=title)
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)
        # An existing remark opens locked: the common case for an already
        # annotated cell is reading it, not rewriting it.
        self._set_editing(not text.strip())
        if not self._visible:
            self.place(x=self._clamped_x(), y=self._clamped_y(),
                       width=max(MIN_W, self.settings.notebox_w),
                       height=max(MIN_H, self.settings.notebox_h))
            self._visible = True
        self.lift()
        if self._editing:
            self.text.focus_set()

    def request_close(self) -> bool:
        """Close, asking first if there is unsaved text. False = user said no.

        The return value matters to the caller that wants to navigate away: an
        image switch that proceeds anyway would take the unsaved remark with it.
        """
        if self._dirty() and not self._ask_discard():
            return False
        self.hide()
        return True

    def hide(self) -> None:
        """Close without asking. Use request_close() for anything user-driven."""
        was_open = self._visible
        if self._visible:
            self.place_forget()
            self._visible = False
        self._target = None
        self._editing = False
        # Hand the focus back, or the keyboard stays trapped in a hidden widget.
        try:
            self.master.focus_set()
        except tk.TclError:
            pass
        if was_open:
            self._on_closed()

    @property
    def visible(self) -> bool:
        return self._visible

    # ─────────────────────────── editing ───────────────────────────────
    def _current(self) -> str:
        return self.text.get("1.0", "end").strip()

    def _dirty(self) -> bool:
        return self._visible and self._editing and self._current() != self._saved_text

    def _ask_discard(self) -> bool:
        return self._confirm(self.S["note_discard_title"], self.S["note_discard_msg"])

    def _set_editing(self, editing: bool) -> None:
        self._editing = editing
        self.text.config(state="normal" if editing else "disabled")
        self._btn_save.config(text=self.S["note_save"] if editing
                              else self.S["note_edit"])

    def _on_primary(self) -> None:
        """Save when editing, unlock when locked — one button, two states."""
        if not self._editing:
            self._set_editing(True)
            self.text.focus_set()
            return
        text = self._current()
        self._saved_text = text
        self._on_save(self._target, text)
        self._set_editing(False)

    def _delete(self) -> None:
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self._saved_text = ""
        self._on_save(self._target, "")
        self.hide()

    # ─────────────────────────── geometry ──────────────────────────────
    def _clamped_x(self) -> int:
        w = self.master.winfo_width() or 800
        return max(0, min(self.settings.notebox_x, w - MIN_W))

    def _clamped_y(self) -> int:
        h = self.master.winfo_height() or 600
        return max(0, min(self.settings.notebox_y, h - MIN_H))

    def _drag_start(self, e) -> None:
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())

    def _drag_move(self, e) -> None:
        if self._drag is None:
            return
        x0, y0, wx, wy = self._drag
        # Clamped to the parent: a box dragged off the edge could not be
        # dragged back, and its stored position would strand it next time too.
        mw, mh = self.master.winfo_width(), self.master.winfo_height()
        x = max(0, min(wx + e.x_root - x0, mw - self.winfo_width()))
        y = max(0, min(wy + e.y_root - y0, mh - self.winfo_height()))
        self.place(x=x, y=y)
        self.settings.notebox_x, self.settings.notebox_y = x, y

    def _resize_start(self, e) -> None:
        self._drag = (e.x_root, e.y_root, self.winfo_width(), self.winfo_height())

    def _resize_move(self, e) -> None:
        if self._drag is None:
            return
        x0, y0, w0, h0 = self._drag
        w = max(MIN_W, w0 + e.x_root - x0)
        h = max(MIN_H, h0 + e.y_root - y0)
        self.place(width=w, height=h)
        self.settings.notebox_w, self.settings.notebox_h = w, h
