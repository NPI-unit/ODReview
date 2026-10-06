"""Image view without a UI toolkit: matplotlib figure, layers and all drawing.

Originally canvas.py (Tk). Split for the Qt port: everything here works on a
plain matplotlib Figure plus the ReviewEditor; the front-end class adds the
widget and the FigureCanvas. Design notes from the original module:
Matplotlib canvas with tool-palette-aware click handling.

Performance design:
  - compute_cell_infos uses skimage.measure.regionprops_table (~22 ms for 300 cells
    vs. ~1400 ms in the previous per-cell np.where loop)
  - Three persistent AxesImage handles are kept alive (base image, overlay RGBA,
    margin overlay). Updates use set_data() / set_visible() rather than ax.cla()+rebuild,
    so overlay-toggle / data-edit redraws stay in the tens-of-ms range.

Visual scheme (Frage 3A — three-stage gray fill, no dashed circles):
  - Active cells  -> their type color
  - Border cells  -> mid-gray fill
  - Excluded cells -> dark-gray fill
  - Force-included border cells -> type color (count as active)

Tool semantics (Frage 1A):
  - "select"  : click on cell selects it (used for sidebar info)
  - "add"     : click anywhere creates a new manual cell
  - "remove"  : click toggles effective active-state (handles border re-include)
  - "type"    : click on cell assigns settings.active_type_key
"""

from __future__ import annotations

from typing import Callable

import sys

import numpy as np
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse
from matplotlib.patheffects import Stroke, Normal
from matplotlib.transforms import Bbox

from .editor import ReviewEditor
from .persistence import (
    AppSettings, ReviewState,
    TOOL_ADD, TOOL_CURSOR, TOOL_NOTE, TOOL_REMOVE, TOOL_TYPE,
)
from .profile import ProfileData
from .theme import OUTLINE_STYLES, palette as _palette


from .overlay import (  # noqa: F401 — re-exported; these used to live here
    ALPHA_ACTIVE_U8, ALPHA_INACTIVE_U8, COLOR_INACTIVE_U8, DEFAULT_OVERLAY_ALPHA,
    INACTIVE_ALPHA_BONUS, RGBA, CellInfo, OverlayColors, build_margin_rgba,
    build_overlay_rgba, cell_contours, compute_cell_infos, is_border_cell,
    normalize_image_for_display, patch_overlay_cell,
)


# ─────────────────────────────────────────────
# Canvas widget
# ─────────────────────────────────────────────
class CanvasCore:
    """Toolkit-free part of the image view: the matplotlib figure and every
    drawing rule. A front end subclasses it together with its own widget class,
    creates a FigureCanvas for `self.fig` and hands it to `_attach_canvas`, and
    implements `_widget_size`. Tk: canvas.EryCanvas. Qt: qt/canvas.py.
    """

    def __init__(
        self,
        settings: AppSettings,
        profile: ProfileData,
        save_callback: Callable[[], None],
        status_callback: Callable[[str], None] | None = None,
        note_callback: Callable[[int], None] | None = None,
        on_overlay_autoshown: Callable[[], None] | None = None,
        on_edit_overlay_hidden: Callable[[], None] | None = None,
        new_circle_radius: int = 17,
    ):
        P = _palette(settings.theme)
        self.settings = settings
        self.profile = profile
        # The data side — masks, review, per-cell state, edits, undo. This
        # widget only draws what the editor says changed.
        self.editor = ReviewEditor(profile, new_circle_radius)
        self.save_callback = save_callback
        # Right-click on a cell -> the app opens the remark editor for it.
        self.note_callback = note_callback or (lambda _cid: None)
        # Fired after an edit had to reveal a hidden overlay, so the app can
        # persist the setting and say why the view changed.
        self.on_overlay_autoshown = on_overlay_autoshown or (lambda: None)
        # Fired after an edit with the overlay hidden and auto-show turned off,
        # so the app can say why nothing visibly changed.
        self.on_edit_overlay_hidden = on_edit_overlay_hidden or (lambda: None)
        self._theme_palette = P
        self.status_callback = status_callback or (lambda s: None)

        # Display state
        self.image: np.ndarray | None = None
        self.image_norm: np.ndarray | None = None
        self.cursor_xy: tuple[float, float] | None = None
        self.selected_id: int | None = None

        # Helper grid (n × n fields, 0 = off) — drawn above everything else
        self.grid_n = 0
        self._grid_artists: list = []

        # Pan state
        self._pan_start: tuple[float, float, float, float, float, float] | None = None

        # Figure — theme-aware background
        self.fig = Figure(figsize=(10, 7), facecolor=P["canvas_bg"])
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(P["canvas_bg"])
        self.ax.axis("off")
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.canvas = None                     # set by _attach_canvas

        # Persistent image handles (set on first set_image)
        self._im_handle = None
        self._overlay_handle = None
        self._margin_handle = None
        # The live overlay array is kept so a single-cell edit can repaint just
        # that cell's bbox instead of rebuilding all H*W*4 bytes.
        self._overlay_rgba: np.ndarray | None = None
        # cell_id -> Text artist, alive for as long as the image is. Cells come
        # and go only via _act_add / undo; activity changes just flip visibility.
        self._number_artists: dict[int, object] = {}
        self._number_active: set[int] = set()
        # cell_id -> the Line2D pair(s) drawing that cell's type contour. Only
        # cells whose type defines an outline get an entry, which in practice is
        # a handful per image (~0.2% of cells are not "normal").
        self._outline_artists: dict[int, list] = {}
        # cell_id -> marker Text for cells carrying a remark. Same bookkeeping as
        # the numbers: created per image, individually removable.
        self._note_artists: dict[int, object] = {}
        self._image_shape: tuple[int, int] | None = None  # to detect re-init

        # ── Blit cache for single-cell edits ────────────────────────────────
        # A full figure redraw re-resamples all three full-size image layers.
        # Measured on 1200x1920 review data at a 1920x944 canvas that is ~510 ms
        # per click — the whole reason toggling a cell felt sluggish, and ~97 %
        # of the click. Toggling one cell changes a rectangle a few dozen pixels
        # across, so the fast path restores the last full rendering, rebuilds
        # only that rectangle, and blits it. See _blit_cell().
        self._blit_bg = None                        # Agg region of the last render
        self._blit_key: tuple | None = None         # viewport it was captured at
        self._blit_sub: dict[str, object] = {}      # small reusable sub-artists

        # LOD: drive number visibility from zoom level
        self.ax.callbacks.connect("xlim_changed", lambda _ax: self._apply_number_visibility())

    def _attach_canvas(self, figure_canvas) -> None:
        """Take the toolkit's FigureCanvas for self.fig and wire mouse events."""
        self.canvas = figure_canvas
        self.canvas.mpl_connect("button_press_event", self._on_button_press)
        self.canvas.mpl_connect("button_release_event", self._on_button_release)
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("scroll_event", self._on_scroll)

    def _widget_size(self) -> tuple[int, int]:
        """Current pixel size of the drawing widget (front end supplies it)."""
        raise NotImplementedError

    # ─────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────
    # Read-through views of the editor's state, for the drawing code below
    # and for the app, which reads border/duplicate ids for the status line.
    masks = property(lambda self: self.editor.masks)
    review = property(lambda self: self.editor.review)
    cell_infos = property(lambda self: self.editor.cell_infos)
    border_ids = property(lambda self: self.editor.border_ids)
    duplicate_ids = property(lambda self: self.editor.duplicate_ids)
    history = property(lambda self: self.editor.history)

    def clear(self) -> None:
        self.image = self.image_norm = None
        self.editor.clear()
        self.selected_id = None
        self.ax.cla()
        self.ax.set_facecolor(self._theme_palette["canvas_bg"])
        self.ax.axis("off")
        self._im_handle = self._overlay_handle = self._margin_handle = None
        self._overlay_rgba = None
        self._number_artists = {}
        self._number_active = set()
        self._outline_artists = {}
        self._note_artists = {}
        self._image_shape = None
        self._full_redraw()

    def _colors(self) -> OverlayColors:
        return self.editor.colors()

    def set_image(self, image: np.ndarray, masks: np.ndarray, review: ReviewState) -> None:
        self.image = image
        self.image_norm = normalize_image_for_display(image)
        self.selected_id = None
        # Manual cells, geometry, border and size-filter ids — all resolved
        # before anything renders, so the first paint is already consistent.
        self.editor.load(masks, review)

        h, w = self.image.shape[:2]
        same_shape = (self._image_shape == (h, w)) and self._im_handle is not None

        if same_shape:
            self._im_handle.set_data(self.image_norm)
        else:
            self.ax.cla()          # drops every artist, including the numbers
            self.ax.set_facecolor(self._theme_palette["canvas_bg"])
            self.ax.axis("off")
            self._im_handle = self.ax.imshow(self.image_norm, interpolation="nearest", zorder=0)
            blank_overlay = np.zeros((h, w, 4), dtype=np.uint8)
            self._overlay_handle = self.ax.imshow(blank_overlay, interpolation="nearest", zorder=1)
            self._margin_handle = self.ax.imshow(
                np.zeros((h, w, 4), dtype=np.float32), interpolation="nearest", zorder=2)
            self._number_artists = {}
            self._number_active = set()
            self._note_artists = {}
            self._image_shape = (h, w)
            self.ax.set_xlim(-0.5, w - 0.5)
            self.ax.set_ylim(h - 0.5, -0.5)

        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self._refresh_outlines()
        self._refresh_notes()
        self._refresh_grid()
        self._full_redraw()

    # ── helper grid ──
    GRID_CHOICES = (0, 2, 3, 4, 5)

    def set_grid(self, n: int) -> None:
        """Show an n × n grid of fields over the image (0 hides it). Fields
        are labelled A1, A2 … (row letter, column number) in their corner."""
        self.grid_n = int(n) if n else 0
        self._refresh_grid()
        self._full_redraw()

    def _refresh_grid(self) -> None:
        for a in self._grid_artists:
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass
        self._grid_artists = []
        if not self.grid_n or self.image is None:
            return
        from matplotlib.collections import LineCollection
        h, w = self.image.shape[:2]
        n = self.grid_n
        xs = [-0.5 + w * k / n for k in range(1, n)]
        ys = [-0.5 + h * k / n for k in range(1, n)]
        segs = [[(x, -0.5), (x, h - 0.5)] for x in xs] + [[(-0.5, y), (w - 0.5, y)] for y in ys]
        col = self._theme_palette.get("accent_primary", "#2161D9")
        # light halo under a solid accent line: readable on dark and busy images
        halo = LineCollection(segs, colors=["#FFFFFF"], linewidths=3.2, alpha=0.55, zorder=6)
        lines = LineCollection(segs, colors=[col], linewidths=1.4, alpha=0.95, zorder=6)
        for lc in (halo, lines):
            self.ax.add_collection(lc)
            self._grid_artists.append(lc)
        fs = 11
        for r in range(n):
            for c in range(n):
                t = self.ax.text(-0.5 + w * c / n + w * 0.008, -0.5 + h * r / n + h * 0.008,
                                 f"{chr(65 + r)}{c + 1}", color=col, fontsize=fs, fontweight="bold",
                                 ha="left", va="top", zorder=7,
                                 bbox=dict(boxstyle="round,pad=0.2", fc=self._theme_palette.get("canvas_bg", "#FFFFFF"),
                                           ec="none", alpha=0.7))
                self._grid_artists.append(t)

    def reset_zoom(self) -> None:
        if self.image is None:
            return
        h, w = self.image.shape[:2]
        self.ax.set_xlim(-0.5, w - 0.5)
        self.ax.set_ylim(h - 0.5, -0.5)
        self._full_redraw()

    def set_overlay_visible(self, visible: bool) -> None:
        if self._overlay_handle is not None:
            self._overlay_handle.set_visible(visible)
        # Margin overlay is coupled to the main overlay (per user spec):
        # one switch controls both. Margin still respects the profile-level
        # `show_margin_overlay` preference (so user can disable it in profile).
        if self._margin_handle is not None:
            self._margin_handle.set_visible(
                visible and self.profile.show_margin_overlay and self.profile.margin_n > 0
            )
        self._apply_number_visibility()
        self._apply_outline_visibility()
        self._apply_note_visibility()
        self._full_redraw()

    def set_margin_n(self, n: int) -> None:
        self.editor.set_margin_n(n)
        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()   # borders moved -> which cells count moved with them
        self._refresh_outlines()
        self._full_redraw()

    def set_profile(self, profile: ProfileData) -> None:
        """Swap to a different study profile. Re-renders with new colors/margin/types."""
        self.profile = profile
        self.editor.set_profile(profile)
        self._refresh_overlay()
        self._refresh_margin()
        self._refresh_numbers()
        self._refresh_outlines()
        self._full_redraw()

    def refresh_appearance(self) -> None:
        """Called after Settings change: re-render margin overlay + cell numbers."""
        self._refresh_margin()
        self._refresh_numbers()
        self._full_redraw()

    def refresh_overlay_from_data(self) -> None:
        """Called externally if review data changed without going through canvas methods."""
        self._refresh_overlay()
        self._refresh_numbers()
        self._refresh_outlines()
        self._full_redraw()

    # ─────────────────────────────────────────
    # Tool-aware actions
    # ─────────────────────────────────────────
    def _act_add(self, x: float, y: float) -> bool:
        cid = self.editor.add(x, y)
        if cid is None:
            return False
        self.save_callback()
        # One new cell -> one new artist and one patched bbox, not a full rebuild.
        self._number_artists[cid] = self._make_number(cid, self.cell_infos[cid])
        self._patch_cell(cid)
        # Same shape as a toggle: one cell's rectangle changed, and its number
        # artist already exists, so the blit rebuild picks it up.
        self._draw_after_cell_edit(cid)
        return True

    def _act_remove_toggle(self, x: float, y: float) -> bool:
        cid = self.editor.toggle_active(x, y)
        if cid is None:
            return False
        self.save_callback()
        # Patching also flips this cell's number — previously the number stayed
        # on screen after the cell was switched inactive.
        self._patch_cell(cid)
        self._draw_after_cell_edit(cid)
        return True

    def _act_type(self, x: float, y: float) -> bool:
        cid = self.editor.set_type(x, y, self.profile.active_type_key)
        if cid is None:
            return False
        self.save_callback()
        self._patch_cell(cid)
        self._draw_after_cell_edit(cid)
        return True

    def undo(self) -> bool:
        done = self.editor.undo()
        if done is None:
            return False
        self.save_callback()
        if done.kind == "add":
            # The label vanished from `masks`, so there is nothing left to patch
            # against — and undoing an add is rare. Rebuild both layers.
            self._refresh_overlay()
            self._refresh_numbers()
            self._refresh_outlines()
        else:
            self._patch_cell(done.cell_id)
        self._full_redraw()
        return True

    # ─────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────
    def _find_cell_at(self, x: float, y: float) -> int | None:
        return self.editor.find_cell_at(x, y)

    def _refresh_overlay(self) -> None:
        """Full rebuild. Use when many cells can change at once (new image,
        margin, profile); a single edited cell should go through _patch_cell."""
        if self.masks is None or self.review is None or self._overlay_handle is None:
            return
        self._overlay_rgba = build_overlay_rgba(
            self.masks, self.cell_infos, self.review, self.profile,
            self.border_ids, self.duplicate_ids,
        )
        self._overlay_handle.set_data(self._overlay_rgba)

    def _patch_cell(self, cid: int, colors: OverlayColors | None = None) -> None:
        """Repaint exactly the one cell an edit touched, and sync its number."""
        info = self.cell_infos.get(cid)
        if info is None or self.masks is None or self._overlay_handle is None:
            return
        if self._overlay_rgba is None:      # nothing built yet — nothing to patch
            self._refresh_overlay()
            return
        colors = colors or self._colors()
        patch_overlay_cell(self._overlay_rgba, self.masks, info, colors.of(cid))
        self._overlay_handle.set_data(self._overlay_rgba)
        self._set_number_active(cid, colors.is_active(cid))
        self._set_cell_outline(cid, colors)

    # ─────────────────────────────────────────
    # Drawing: full redraw vs. single-cell blit
    # ─────────────────────────────────────────
    def _full_redraw(self) -> None:
        """Redraw the whole figure and drop the blit cache.

        Everything that is not one cell changing appearance goes through here:
        a new image, a new margin, zoom, pan, theme, overlay on/off. The cache
        is a snapshot of a rendering, so any of those makes it a lie.
        """
        self._blit_bg = None
        self.canvas.draw_idle()

    # The fast path is Windows-only, and that is a deliberate exception to the
    # rule that these performance changes carry no platform branch.
    #
    # Measured on macOS (Agg buffer, real study images, 1400x900): the rebuilt
    # rectangle differs from a full redraw by up to 103 of 255 per channel over
    # ~5700 pixels — the edited cell renders visibly darker and stays that way
    # until the next full redraw. Reproducible at integer scales 1.0 and 2.0 as
    # well, so it is not a resampling phase problem; data, norm object, colormap,
    # alpha, zorder and interpolation were each checked to be identical between
    # the full-size artists and their stand-ins. Cause not found.
    #
    # Windows is where the fast path earns its keep (575 ms -> 85 ms per click,
    # against 30-45x slower filesystem operations on NTFS), and it was verified
    # there. A macOS click was already fast, so switching it off costs nothing
    # that anyone would notice. tests/test_blit_equivalence.py keeps guarding it
    # wherever it is on.
    BLIT_ENABLED = sys.platform == "win32"

    def _draw_after_cell_edit(self, cid: int) -> None:
        """Cheap repaint for a one-cell edit, with the full redraw as fallback."""
        if not self.BLIT_ENABLED or not self._blit_cell(cid):
            self._full_redraw()

    # Padding around the cell, in display pixels, so a number or an outline
    # that reaches past its own bbox still lands inside the repainted rectangle.
    BLIT_PAD_PX = 48

    def _blit_viewport_key(self) -> tuple:
        return (*self._widget_size(),
                tuple(self.ax.get_xlim()), tuple(self.ax.get_ylim()))

    def _blit_subartists(self) -> dict:
        """The three small stand-ins for the full-size layers, created once.

        They are `animated`, so a normal draw skips them — otherwise a full
        redraw would paint a stale postage stamp over the figure. They share the
        real image layer's colormap and norm object, so the rectangle they
        rebuild is colour-identical to what a full redraw would produce.
        """
        if self._blit_sub:
            return self._blit_sub
        blank_f = np.zeros((2, 2), dtype=np.float32)
        blank_rgba = np.zeros((2, 2, 4), dtype=np.uint8)
        image = self.ax.imshow(blank_f, interpolation="nearest", animated=True, zorder=0)
        image.set_cmap(self._im_handle.get_cmap())
        image.set_norm(self._im_handle.norm)
        overlay = self.ax.imshow(blank_rgba, interpolation="nearest", animated=True, zorder=1)
        margin = self.ax.imshow(np.zeros((2, 2, 4), dtype=np.float32),
                                interpolation="nearest", animated=True, zorder=2)
        self._blit_sub = {"image": image, "overlay": overlay, "margin": margin}
        return self._blit_sub

    def _blit_cell(self, cid: int) -> bool:
        """Repaint only the rectangle around one cell. False -> caller must full-draw.

        The rectangle is rebuilt from scratch — image, overlay and margin
        sub-arrays plus every cell artist reaching into it — so it does not
        matter what the restored background happened to show there. Only that
        rectangle is copied to the screen, so the rest of the canvas keeps the
        pixels of the last full rendering, which is why the cached region is
        re-taken afterwards: it must keep describing what is actually on screen.
        """
        info = self.cell_infos.get(cid)
        if (info is None or self.masks is None or self.image_norm is None
                or self._overlay_rgba is None or self._im_handle is None
                or self._overlay_handle is None or self._margin_handle is None):
            return False
        try:
            key = self._blit_viewport_key()
            if self._blit_bg is None or self._blit_key != key:
                # First edit after any other change pays for one full render;
                # every following one is the cheap path.
                self.canvas.draw()
                self._blit_bg = self.canvas.copy_from_bbox(self.ax.bbox)
                self._blit_key = key

            h, w = self.masks.shape
            inv = self.ax.transData.inverted()
            origin = inv.transform((0.0, 0.0))
            padded = inv.transform((self.BLIT_PAD_PX, self.BLIT_PAD_PX))
            pad_x = abs(padded[0] - origin[0])
            pad_y = abs(padded[1] - origin[1])
            x0 = max(0, int(np.floor(info.bbox_x0 - pad_x)))
            x1 = min(w - 1, int(np.ceil(info.bbox_x1 + pad_x)))
            y0 = max(0, int(np.floor(info.bbox_y0 - pad_y)))
            y1 = min(h - 1, int(np.ceil(info.bbox_y1 + pad_y)))
            if x1 < x0 or y1 < y0:
                return False

            subs = self._blit_subartists()
            extent = (x0 - 0.5, x1 + 0.5, y1 + 0.5, y0 - 0.5)
            subs["image"].set_data(self.image_norm[y0:y1 + 1, x0:x1 + 1])
            subs["image"].set_extent(extent)
            subs["overlay"].set_data(self._overlay_rgba[y0:y1 + 1, x0:x1 + 1])
            subs["overlay"].set_extent(extent)
            margin_data = self._margin_handle.get_array()
            subs["margin"].set_data(np.asarray(margin_data)[y0:y1 + 1, x0:x1 + 1])
            subs["margin"].set_extent(extent)

            self.canvas.restore_region(self._blit_bg)
            self.ax.draw_artist(subs["image"])
            if self._overlay_handle.get_visible():
                self.ax.draw_artist(subs["overlay"])
            if self._margin_handle.get_visible():
                self.ax.draw_artist(subs["margin"])
            # Neighbours whose number, outline or remark marker reaches into the
            # rectangle were wiped by the rebuild above and have to come back.
            for other, other_info in self.cell_infos.items():
                if (other_info.bbox_x1 < x0 or other_info.bbox_x0 > x1
                        or other_info.bbox_y1 < y0 or other_info.bbox_y0 > y1):
                    continue
                number = self._number_artists.get(other)
                if number is not None and number.get_visible():
                    self.ax.draw_artist(number)
                for artist in self._outline_artists.get(other, ()) or ():
                    if artist.get_visible():
                        self.ax.draw_artist(artist)
                for artist in self._note_artists.get(other, ()) or ():
                    if artist.get_visible():
                        self.ax.draw_artist(artist)

            for artist in self._grid_artists:      # the helper grid stays on top
                self.ax.draw_artist(artist)

            corners = self.ax.transData.transform(
                [[x0 - 0.5, y1 + 0.5], [x1 + 0.5, y0 - 0.5]])
            region = Bbox([[corners[:, 0].min(), corners[:, 1].min()],
                           [corners[:, 0].max(), corners[:, 1].max()]])
            region = Bbox.intersection(region, self.ax.bbox)
            if region is None or region.width < 1 or region.height < 1:
                return False
            self.canvas.blit(region)
            self.canvas.flush_events()
            # The Agg buffer now holds the previous rendering plus this rebuilt
            # rectangle — exactly what the screen shows. Keep it as the new
            # background so overlapping edits cannot undo each other.
            self._blit_bg = self.canvas.copy_from_bbox(self.ax.bbox)
            return True
        except Exception:
            # Any surprise (a resize mid-blit, a backend without blitting) must
            # cost correctness nothing: drop the cache and let the caller redraw.
            self._blit_bg = None
            return False

    def _refresh_margin(self) -> None:
        if self.image is None or self._margin_handle is None:
            return
        h, w = self.image.shape[:2]
        # Coupled to overlay visibility (single toggle controls both layers).
        overlay_on = self._overlay_handle is not None and self._overlay_handle.get_visible()
        if overlay_on and self.profile.show_margin_overlay and self.profile.margin_n > 0:
            rgba = build_margin_rgba(h, w, self.profile.margin_n)
            self._margin_handle.set_data(rgba)
            self._margin_handle.set_visible(True)
        else:
            self._margin_handle.set_visible(False)

    # LOD: numbers only show once the visible viewport is below this fraction of image width.
    LOD_VIEWPORT_FRACTION = 0.40

    def _make_number(self, cid: int, info: CellInfo):
        """One number artist. White text, thin black outline so it reads on any
        background."""
        t = self.ax.text(
            info.cx, info.cy, str(cid),
            color="white", fontsize=9, ha="center", va="center",
            fontweight="bold", zorder=3,
        )
        t.set_path_effects([Stroke(linewidth=2.0, foreground="black"), Normal()])
        return t

    def _refresh_numbers(self) -> None:
        """Rebuild every number artist — one per cell, kept for the life of the
        image. *Which* of them show is decided by _apply_number_visibility.

        Numbers appear on active cells only (Frage 3a). Activity now comes from
        the same OverlayColors rule the fill color uses, so a duplicate-flagged
        cell no longer keeps a number it isn't counted for.
        """
        for a in self._number_artists.values():
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass          # already gone with an ax.cla()
        self._number_artists = {}
        self._number_active = set()
        if self.review is None:
            return
        colors = self._colors()
        for cid, info in self.cell_infos.items():
            self._number_artists[cid] = self._make_number(cid, info)
            if colors.is_active(cid):
                self._number_active.add(cid)
        self._apply_number_visibility()

    # ─────────────────────────────────────────
    # Remark markers
    # ─────────────────────────────────────────
    NOTE_GLYPH = "❝"          # U+275D — the same mark the tool carries, so
                              # button and marker read as one thing
    NOTE_COLOR = "#5B9CFF"    # accent_primary; see _make_note_marker()

    def _refresh_notes(self) -> None:
        """Rebuild the remark markers from review.notes."""
        for artists in self._note_artists.values():
            for a in artists:
                try:
                    a.remove()
                except (ValueError, NotImplementedError):
                    pass
        self._note_artists = {}
        if self.review is None:
            return
        for key in self.review.notes:
            try:
                cid = int(key)
            except (TypeError, ValueError):
                continue
            if self.review.notes[key].strip():
                self._make_note_marker(cid)
        self._apply_note_visibility()

    def _make_note_marker(self, cid: int) -> None:
        """Blue ring plus the ✎ glyph on one annotated cell.

        Blue, not red: the cell type `stechapfel` is #FF6B6B and the error
        accent #F26B6B, so a red ring would be indistinguishable from a cell
        typed as an echinocyte. Blue is unused by every type and is the safest
        signal under red-green deficiency.

        A ring rather than an arrow to the centre: a field holds ~271 cells, and
        a dozen arrows cross each other and cover the neighbours they point past.
        The ring scales with the zoom and hides nothing.
        """
        info = self.cell_infos.get(cid)
        if info is None:
            return
        w = max(info.bbox_x1 - info.bbox_x0, 6)
        h = max(info.bbox_y1 - info.bbox_y0, 6)
        ring = Ellipse(
            (info.cx, info.cy), width=w * 1.45, height=h * 1.45,
            fill=False, edgecolor=self.NOTE_COLOR, linewidth=1.8, zorder=4,
        )
        ring.set_path_effects([Stroke(linewidth=3.2, foreground="black"), Normal()])
        self.ax.add_patch(ring)
        t = self.ax.text(
            info.cx + w * 0.32, info.cy - h * 0.32,
            self.NOTE_GLYPH, color=self.NOTE_COLOR, fontsize=11, ha="center",
            va="center", fontweight="bold", zorder=5,
        )
        t.set_path_effects([Stroke(linewidth=2.4, foreground="black"), Normal()])
        for a in (ring, t):
            a.set_visible(self._notes_shown())
        self._note_artists[cid] = [ring, t]

    def set_cell_note_marker(self, cid: int, has_note: bool) -> None:
        """Add or drop one cell's marker after its remark changed."""
        for a in self._note_artists.pop(cid, []) or []:
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass
        if has_note:
            self._make_note_marker(cid)
        self._full_redraw()

    def _notes_shown(self) -> bool:
        """Markers ride the overlay switch, like the numbers and the contours —
        one toggle for the whole annotation layer."""
        return self._overlay_handle is not None and self._overlay_handle.get_visible()

    def _apply_note_visibility(self) -> None:
        shown = self._notes_shown()
        for artists in self._note_artists.values():
            for a in artists:
                a.set_visible(shown)

    def _numbers_shown(self) -> bool:
        overlay_on = self._overlay_handle is not None and self._overlay_handle.get_visible()
        return overlay_on and self._is_zoomed_in()

    def _set_number_active(self, cid: int, active: bool) -> None:
        """Flip one cell's number without touching the other ~200 artists."""
        if active:
            self._number_active.add(cid)
        else:
            self._number_active.discard(cid)
        a = self._number_artists.get(cid)
        if a is not None:
            a.set_visible(active and self._numbers_shown())

    def _apply_number_visibility(self) -> None:
        """Hide numbers when the overlay is off, when zoomed out beyond the LOD
        threshold, or on cells that don't count."""
        if not self._number_artists:
            return
        shown = self._numbers_shown()
        for cid, a in self._number_artists.items():
            a.set_visible(shown and cid in self._number_active)

    # ─────────────────────────────────────────
    # Type contours (WCAG 1.4.1 — type not carried by fill colour alone)
    # ─────────────────────────────────────────
    OUTLINE_LW = 1.4          # white line; a black one sits under it, wider

    def _outlines_shown(self) -> bool:
        """Contours share the overlay switch — one toggle for the whole
        annotation layer. Unlike the numbers they have NO zoom threshold: at
        full-image view a cell is ~41 px across, which is exactly where the
        reviewer needs to see at a glance which cells are not normal."""
        return (self.profile.type_outlines_enabled
                and self._overlay_handle is not None
                and self._overlay_handle.get_visible())

    def _outline_style_for(self, cid: int, colors: OverlayColors):
        """Line style for this cell's type, or None if it should have no contour."""
        if not self.profile.type_outlines_enabled:
            return None       # switched off -> build no artists at all
        if not colors.is_active(cid):
            return None       # inactive cells render grey — their type is moot
        td = self.profile.type_by_key(colors.type_key(cid))
        return OUTLINE_STYLES.get(getattr(td, "outline", "") or "") if td else None

    def _make_outline(self, cid: int, style) -> list:
        """Black line under a white one, so the contour reads on any background."""
        info = self.cell_infos.get(cid)
        if info is None or self.masks is None:
            return []
        artists = []
        visible = self._outlines_shown()
        for c in cell_contours(self.masks, info):
            for color, lw, z in (("black", self.OUTLINE_LW + 1.6, 3.5),
                                 ("white", self.OUTLINE_LW, 3.6)):
                (line,) = self.ax.plot(c[:, 0], c[:, 1], ls=style, color=color,
                                       lw=lw, zorder=z, solid_capstyle="butt")
                line.set_visible(visible)
                artists.append(line)
        return artists

    def _drop_outline(self, cid: int) -> None:
        for a in self._outline_artists.pop(cid, []):
            try:
                a.remove()
            except (ValueError, NotImplementedError):
                pass          # already gone with an ax.cla()

    def _set_cell_outline(self, cid: int, colors: OverlayColors) -> None:
        """Rebuild just this cell's contour after an edit."""
        self._drop_outline(cid)
        style = self._outline_style_for(cid, colors)
        if style is not None:
            self._outline_artists[cid] = self._make_outline(cid, style)

    def _refresh_outlines(self) -> None:
        """Rebuild every contour — after a new image, margin or profile change."""
        for cid in list(self._outline_artists):
            self._drop_outline(cid)
        if self.review is None or self.masks is None:
            return
        colors = self._colors()
        for cid in self.cell_infos:
            style = self._outline_style_for(cid, colors)
            if style is not None:
                self._outline_artists[cid] = self._make_outline(cid, style)

    def _apply_outline_visibility(self) -> None:
        shown = self._outlines_shown()
        for artists in self._outline_artists.values():
            for a in artists:
                a.set_visible(shown)

    def _is_zoomed_in(self) -> bool:
        if self.image is None:
            return False
        h, w = self.image.shape[:2]
        xl = self.ax.get_xlim()
        viewport_w = abs(xl[1] - xl[0])
        return viewport_w < (w * self.LOD_VIEWPORT_FRACTION)

    # ─────────────────────────────────────────
    # Mouse handlers
    # ─────────────────────────────────────────
    def _autoshow_overlay(self) -> bool:
        """Reveal a hidden overlay before an edit. True if it had been hidden.

        Every editing gesture draws its result *into* the overlay — the cell
        colour, the number, the outline, the ✎ marker. `set_overlay_visible`
        hides all four together, so with the overlay off a click still lands
        and still saves, but nothing on screen moves. That is indistinguishable
        from a broken click, and it is exactly how this was first reported.
        Picking a tool and clicking a cell is unambiguous intent to edit, so
        the view follows the intent; `o` stays available for looking at the
        raw image.
        """
        if self.settings.overlay_visible or not self.settings.overlay_autoshow:
            return False
        self.settings.overlay_visible = True
        self.set_overlay_visible(True)
        return True

    def _on_button_press(self, event):
        if event.inaxes != self.ax or self.image is None:
            return
        x, y = event.xdata, event.ydata
        if x is None or y is None:
            return
        if event.button == 1:
            revealed = self._autoshow_overlay()
            tool = self.settings.active_tool
            if tool == TOOL_ADD:
                self._act_add(x, y)
            elif tool == TOOL_REMOVE:
                self._act_remove_toggle(x, y)
            elif tool == TOOL_TYPE:
                self._act_type(x, y)
            elif tool == TOOL_NOTE:
                # None means "no cell here" — the app reads that as the remark
                # for the whole image, so one tool covers both targets and the
                # user never has to hunt for a second entry point.
                self.note_callback(self._find_cell_at(x, y))
            elif tool == TOOL_CURSOR:
                # Read-only mode: only a cell that already carries a remark
                # responds, and it only opens that remark. Everything else is
                # inert on purpose — this is the one mode where a stray click
                # cannot alter the review.
                cid = self._find_cell_at(x, y)
                if cid is not None and self.review is not None \
                        and self.review.notes.get(str(cid), "").strip():
                    self.note_callback(cid)
            # After the dispatch: the edit runs save_callback -> _update_status,
            # which rewrites the status line and would swallow an earlier note.
            if revealed:
                self.on_overlay_autoshown()
            elif not self.settings.overlay_visible and tool in (TOOL_ADD, TOOL_REMOVE, TOOL_TYPE):
                # Auto-show is off: the edit saved but nothing on screen moved.
                self.on_edit_overlay_hidden()
        elif event.button == 3:
            # Remarks are occasional, so they get their own gesture instead of a
            # fourth tool: no mode switch, available whatever tool is active.
            # matplotlib normalises button numbers (3 = right) on every platform,
            # unlike raw Tk where macOS reports right-click as Button-2.
            cid = self._find_cell_at(x, y)
            if cid is not None:
                revealed = self._autoshow_overlay()
                self.note_callback(cid)
                if revealed:
                    self.on_overlay_autoshown()
        elif event.button == 2:
            self._pan_start = (
                event.x, event.y, *self.ax.get_xlim(), *self.ax.get_ylim(),
            )

    def _on_button_release(self, event):
        if event.button == 2:
            self._pan_start = None

    def _on_motion(self, event):
        if event.inaxes == self.ax and event.xdata is not None:
            self.cursor_xy = (event.xdata, event.ydata)
        if self._pan_start is not None and event.x is not None and event.y is not None:
            x0, y0, xl0, xl1, yl0, yl1 = self._pan_start
            inv = self.ax.transData.inverted()
            d0 = inv.transform((x0, y0))
            d1 = inv.transform((event.x, event.y))
            dx = d0[0] - d1[0]
            dy = d0[1] - d1[1]
            self.ax.set_xlim(xl0 + dx, xl1 + dx)
            self.ax.set_ylim(yl0 + dy, yl1 + dy)
            self._full_redraw()

    def _on_scroll(self, event):
        if event.inaxes != self.ax or self.image is None or event.xdata is None:
            return
        scale = 0.85 if event.button == "up" else 1.176
        xl = self.ax.get_xlim()
        yl = self.ax.get_ylim()
        self.ax.set_xlim(
            event.xdata + (xl[0] - event.xdata) * scale,
            event.xdata + (xl[1] - event.xdata) * scale,
        )
        self.ax.set_ylim(
            event.ydata + (yl[0] - event.ydata) * scale,
            event.ydata + (yl[1] - event.ydata) * scale,
        )
        self._full_redraw()
