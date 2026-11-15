"""The board one 3D View shows: where it sits (docked in the sidebar or floating over the viewport),
what it looks like, and what each part of it does when clicked.

One Canvas per 3D View area, kept in `sessions` by the area's pointer. The modal operator in
`operators.py` feeds it mouse events; the draw callbacks registered in `__init__.py` paint it.
"""
import math
import time

import bpy

from . import board as board_module
from . import edition
from . import draw, geometry, images
from ._canvas import Board, runtime
from ._canvas.host import Host
from ._canvas.runtime import region_of, ui_scale  # noqa: F401 (names the rest of Pinvas and its tests use)

TAB = "Pinvas"

# Sizes in interface units; multiplied by Blender's Resolution Scale.
BAR_HEIGHT = 30
BUTTON_HEIGHT = 22
PAD = 6
TAB_STRIP = 27            # the sidebar's own tab column, which stays Blender's
EDGE_GRIP = 12            # the sidebar's resize edge, which stays Blender's
CORNER = 16               # the floating board's resize corner
FLOAT_MIN = (220, 160)
FLOAT_MARGIN = 6
FLOAT_DEFAULT = (420, 360)
POP_ARROW = 34            # room left at a pop-out's top-right for Blender's arrow that brings its header back
NAV_ROOM = 104           # the viewport's navigation buttons, which a newly floated board leaves free

BOARD = (0.155, 0.155, 0.16, 1.0)
GRID_MINOR = (1.0, 1.0, 1.0, 0.035)
GRID_MAJOR = (1.0, 1.0, 1.0, 0.075)
ORIGIN = (1.0, 1.0, 1.0, 0.16)
BAR = (0.105, 0.105, 0.11, 1.0)
BAR_RULE = (0.0, 0.0, 0.0, 0.5)
FRAME = (0.0, 0.0, 0.0, 0.85)
FRAME_LIGHT = (1.0, 1.0, 1.0, 0.08)
TEXT = (0.88, 0.88, 0.88, 1.0)
MUTED = (0.58, 0.58, 0.6, 1.0)
FAINT = (0.42, 0.42, 0.44, 1.0)
BUTTON = (0.19, 0.19, 0.2, 1.0)
BUTTON_HOVER = (0.25, 0.25, 0.27, 1.0)
CHIP = (0.0, 0.0, 0.0, 0.35)
CHIP_HOVER = (0.0, 0.0, 0.0, 0.55)
TIP_BACK = (0.05, 0.05, 0.055, 0.95)
KEY_HINT_BACK = (0.07, 0.07, 0.075, 0.92)
AXIS_X = (0.95, 0.3, 0.33, 1.0)         # Blender's own axis colours
TOAST_TIME = 3.0          # seconds a picked colour stays shown
AXIS_Y = (0.55, 0.86, 0.0, 1.0)
ACCENT = (0.33, 0.56, 0.98, 1.0)
HOVER_EDGE = (1.0, 1.0, 1.0, 0.35)
HANDLE_FILL = (1.0, 1.0, 1.0, 1.0)
HANDLE_EDGE = (0.1, 0.25, 0.55, 1.0)
MARQUEE_FILL = (0.33, 0.56, 0.98, 0.12)
MISSING_FILL = (0.24, 0.12, 0.13, 1.0)
MISSING_EDGE = (0.75, 0.32, 0.34, 1.0)
HANDLE_SIZE = 5           # half the side of a corner handle
TURN_REACH = 22           # how far the turn handle stands out from the frame's top edge
GRAB = 8                  # how near a handle the mouse must be to take it
DRAG_START = 4            # pixels the mouse moves before a press on a picture becomes a move
TURN_STEP = math.radians(15)
TIP_DELAY = 0.6           # seconds the mouse rests on a control before its tip shows

TIPS = {
    ('import',): "Add pictures from disk. You can also drop them here or paste with Ctrl+V",
    ('fit',): "Show the whole board (Home)",
    ('undock',): "Open the board in a window of its own. Drag it onto a 3D View's sidebar to dock it again",
    ('dock',): "Put the board back in the sidebar",
    ('zoom100',): "Back to 100% (or press 1)",
}

# The open boards, the remembered places and the one view whose board is on screen are the framework's.
sessions = runtime.sessions               # area pointer -> Canvas
capturing = runtime.capturing             # the viewport is being drawn off screen: the board stays out of it
_places = runtime.places
holder = runtime.shown                    # the canvas that shows the board now, or None
hold = runtime.hold
forget_places = runtime.forget


class Canvas(Host):
    """One open board in one 3D View: the framework's Host, with what Pinvas adds (its own items and their painting)."""

    def __init__(self, area):
        super().__init__(area)
        self.toast = None                   # (until, text, rgb or [rgb]): what the eyedropper picked
        self.toast_pick = None              # which swatch of the strip was clicked last
        self.menu_at = None                 # where the mouse was when a menu opened (canvas pixels): what is added from it goes there
        self.drop_at = None                 # where the mouse was at the last event (canvas pixels): a drag that ends there drops what it carries

    # The board layer's state, under the names Pinvas has always used

    @property
    def selection(self):
        """The uids of the selected items: the layer's selection, as a live set."""
        return self.layer.selection_set

    @selection.setter
    def selection(self, uids):
        self.layer.select(list(uids))

    @property
    def preview(self):
        """uid -> (x, y, scale, rotation) while a drag is under way: the layer's preview, in Pinvas's form."""
        return self.layer_preview()

    @preview.setter
    def preview(self, values):
        view = self.layer_preview()
        self.layer.preview = {}
        for uid, value in dict(values).items():
            view[uid] = value

    def layer_preview(self):
        from ._canvas.layer.input import PreviewView

        def shown(uid):
            item = board_module.item_by_uid(self.board(), uid) if self.board() is not None else None
            return board_module.placed(item)[2:4] if item is not None else (1.0, 1.0)

        def to_view(uid, shape):
            x, y, w, _h, r = shape
            return (x, y, w / max(shown(uid)[0], 1e-9), r)

        def from_view(uid, value):
            x, y, s, r = value
            w, h = shown(uid)
            return (x, y, w * s, h * s, r)
        return PreviewView(self.layer, to_view, from_view)

    @property
    def marquee(self):
        """((x0, y0), (x1, y1)) in canvas pixels while box-selecting (the layer's)."""
        return self.layer.marquee

    @marquee.setter
    def marquee(self, value):
        self.layer.marquee = value

    def handles(self):
        """The frame and handles of the selected items that may move, in canvas pixels: (frame, [corner points], turn point),
        or None (the layer's)."""
        return self.layer.handles()

    def item_at(self, x, y):
        """The uid of the topmost item under canvas (x, y), or None (the layer's hit; PinvasBoard.item_contains says where an
        item is taken)."""
        item = self.layer.item_at(self.board_point(x, y))
        return item.uid if item is not None else None

    # The view

    def visible_size(self):
        """How much of the board shows, in board units."""
        k = ui_scale() * self.view[2]
        return (max(1.0, self.board_rect[2]) / k, max(1.0, self.board_rect[3]) / k)

    def fit(self):
        """Show the whole board: every picture on it, or its origin at 100% when there are none."""
        board = self.board()
        placed = [p for _uid, p in self.placed_items(board)] if board else []
        self.fit_bounds(geometry.items_bounds(placed))

    def fit_selected(self):
        placed = [p for _uid, p in self.selected_placed()]
        if placed:
            self.fit_bounds(geometry.items_bounds(placed))

    # The pictures

    def placed_items(self, board):
        """[(uid, (x, y, width, height, scale, rotation))] bottom to top, with any drag in progress applied."""
        out = []
        if board is None:
            return out
        for item in board.items:
            if item.kind == 'IMAGE':
                out.append((item.uid, self.shown_placed(item)))
        return out

    def shown_placed(self, item):
        """The item as it shows now: (x, y, width, height, scale, rotation) with any drag in progress applied."""
        placed = board_module.placed(item)
        moved = self.preview.get(item.uid)
        if moved is not None:
            placed = (moved[0], moved[1], placed[2], placed[3], moved[2], moved[3])
        return placed

    def selected_placed(self):
        return [(uid, p) for uid, p in self.placed_items(self.board()) if uid in self.selection]

    def prune_selection(self, board):
        """Forget selected pictures that are no longer on the board (removed, or undone)."""
        if self.selection:
            self.selection &= board_module.uids_present(board)

    def select_all(self):
        board = self.board()
        shown = board_module.uids_shown(board)
        self.selection = shown

    # Clicks

    def hit(self, x, y):
        """What is under canvas (x, y): a pop-out's edges, the board's own controls, the selection's handles, then pictures."""
        edge = self.edge_hit(x, y)
        if edge is not None:
            return edge
        for rect, key in reversed(self.hits):
            if geometry.inside(rect, x, y):
                return key
        if not geometry.inside(self.board_rect, x, y):
            return None
        found = self.handles()
        if found is not None:
            _frame, corners, turn = found
            grab = GRAB * ui_scale()
            if math.hypot(x - turn[0], y - turn[1]) <= grab:
                return ('turn',)
            for i, corner in enumerate(corners):
                if math.hypot(x - corner[0], y - corner[1]) <= grab:
                    return ('scale', i)
        uid = self.item_at(x, y)
        if uid is not None:
            return ('item', uid)
        return ('board',)

    def command(self, key, area):
        if key == ('import',):
            self.menu_at = None                                       # a menu closed earlier says nothing about this
            bpy.ops.pinvas.import_media('INVOKE_DEFAULT')
        elif key == ('fit',):
            self.fit()
        elif key[0] == 'toast':
            pass                                                      # the strip itself: a click on it is not a click on the board
        elif key[0] == 'swatch':
            strip = self.toast[2] if self.toast else None
            hexed = self.take_swatch(key[1])
            if hexed is not None:
                self.show_toast(f"{hexed}  copied", strip, key[1])
        else:
            super().command(key, area)        # 100%, Undock, Dock: the framework's

    def show_toast(self, label, rgb, pick=None):
        """Show the colour strip for TOAST_TIME more seconds, and redraw when they are up."""
        if self.toast is not None and self.toast[0] - time.monotonic() > TOAST_TIME * 0.5 and self.toast[1:] == (label, rgb):
            self.toast_pick = pick
            return                                                    # already fresh: no second timer
        self.toast = (time.monotonic() + TOAST_TIME, label, rgb)
        self.toast_pick = pick
        bpy.app.timers.register(lambda: runtime.redraw_area(self.area_id), first_interval=TOAST_TIME + 0.05)

    def swatch_colour(self, index):
        rgb = self.toast[2] if self.toast else None
        swatches = [rgb] if rgb is not None else []
        return tuple(swatches[index]) if 0 <= index < len(swatches) else None

    def take_swatch(self, index):
        """A click on a colour of the strip: its #RRGGBB goes to the clipboard."""
        colour = self.swatch_colour(index)
        if colour is None:
            return None
        hexed = "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02X}" for c in colour)
        bpy.context.window_manager.clipboard = hexed
        self.toast = None                                             # (replaced below, with the whole strip kept)
        return hexed

    def tip_wanted(self):
        return self.hover in TIPS or (self.hover is not None and self.hover[0] == 'swatch')

    def tip(self, now=None):
        """The tip of the control under the mouse, once the mouse has rested on it for TIP_DELAY."""
        if not self.hover or self.drag:
            return None
        now = time.monotonic() if now is None else now
        if now - self.hover_since < TIP_DELAY:
            return None
        if self.hover == ('undock',) and not self.can_pop_out():
            return "Float the board over the viewport"
        if self.hover[0] == 'swatch':
            return "Click to copy this colour"
        return TIPS.get(self.hover)

    def can_pop_out(self):
        from . import popout
        return popout.possible()

    # Painting

    def paint(self, x0, y0, w, h):
        """Draw the whole canvas with its bottom-left corner at region pixel (x0, y0)."""
        started = time.perf_counter()
        k = ui_scale()
        self.size = (w, h)
        self.hits = []
        bar = BAR_HEIGHT * k
        self.board_rect = (0, 0, w, max(0, h - bar))
        lifted = self.floating or self.popout
        radius = 6 * k if self.floating and not self.popout else 0
        try:
            draw.begin()
            board = self.board()
            draw.box(x0, y0, w, h, BOARD, radius)
            self.prune_selection(board)
            if self.fit_pending and self.board_rect[2] > 1 and self.board_rect[3] > 1:
                self.fit_pending = False
                if board is not None and len(board.items):
                    self.fit()
            with draw.clipped(x0, y0, w, h - bar):
                self.paint_grid(x0, y0, k)
                shown = self.paint_items(x0, y0, k, board)
                if not shown and (board is None or not len(board.items)):
                    self.paint_empty(x0, y0, k)
                self.paint_selection(x0, y0, k)
                self.paint_marquee(x0, y0, k)
                self.paint_key_transform(x0, y0, k)
                self.paint_turn_cursor(x0, y0, k)
                self.paint_toast(x0, y0, k)
            self.paint_bar(x0, y0, w, h, bar, k)
            if not (self.drag and self.drag['kind'].startswith('key_')):
                self.paint_zoom_chip(x0, y0, k)              # the hint of a running G / S / R has the board's foot
            if lifted:
                self.paint_corner(x0, y0, w, k)
                draw.outline(x0, y0, w, h, FRAME, radius, 1.0)
                draw.outline(x0 + 1, y0 + 1, w - 2, h - 2, FRAME_LIGHT, max(0, radius - 1), 1.0)
            if self.popout and self.dock_armed:
                from . import popout
                bh = 44 * k
                popout.draw_banner(x0 + 10 * k, y0 + (h - bar) / 2 - bh / 2, w - 20 * k, bh, k)
            if self.popout and self.hover and self.hover[0] == 'edge':
                self.paint_edge(x0, y0, w, h, self.hover[1], k)
            self.paint_tip(x0, y0, w, h, k)
        finally:
            draw.end()
            self.draw_ms.append((time.perf_counter() - started) * 1000)

    def paint_empty(self, x0, y0, k):
        bx, by, bw, bh = self.board_rect
        cx, cy = x0 + bx + bw / 2, y0 + by + bh / 2
        draw.text_centred(cx, cy + 18 * k, "Empty board", 14 * k, MUTED, bw - 24 * k)
        draw.text_centred(cx, cy - 4 * k, "Drop pictures here, or paste with Ctrl+V", 10 * k, FAINT, bw - 24 * k)
        draw.text_centred(cx, cy - 20 * k, "Middle-drag to pan  ·  Scroll to zoom", 10 * k, FAINT, bw - 24 * k)

    def paint_items(self, x0, y0, k, board):
        """Draw every picture, bottom to top; returns how many were on screen."""
        if board is None:
            return 0
        bx, by, bw, bh = self.board_rect
        view_box = (bx, by, bx + bw, by + bh)
        shown = 0
        for item in board.items:
            if item.kind != 'IMAGE':
                continue
            placed = self.shown_placed(item)
            corners = [self.canvas_point(c) for c in geometry.item_corners(placed)]
            if not geometry.rects_overlap(geometry.bounds_of(corners), view_box):
                continue
            shown += 1
            screen = tuple((x0 + cx, y0 + cy) for cx, cy in corners)
            tex = images.texture(item.image)
            if tex is None:
                self.paint_missing(screen, item, k)
            else:
                images.draw_image(tex, item.image, screen, item.opacity, display_target=not (self.floating or self.popout),      # only the sidebar wants display colour
                                  grey=item.grey)
            if self.hover == ('item', item.uid) and item.uid not in self.selection and not self.drag:
                draw.polygon_outline(screen, HOVER_EDGE, 1.0)
        return shown

    def paint_missing(self, screen, item, k):
        """A picture that cannot be shown: a red box with its name, so it is not lost from view."""
        draw.quad(screen, MISSING_FILL)
        draw.polygon_outline(screen, MISSING_EDGE, 1.0)
        xs, ys = [p[0] for p in screen], [p[1] for p in screen]
        width = max(xs) - min(xs)
        name = item.image.name if item.image is not None else "Deleted picture"
        cx, cy = sum(xs) / 4, sum(ys) / 4
        draw.text_centred(cx, cy + 7 * k, "Missing", 11 * k, MISSING_EDGE, width - 8 * k)
        draw.text_centred(cx, cy - 9 * k, name, 10 * k, TEXT, width - 8 * k)

    def paint_selection(self, x0, y0, k):
        for uid, placed in self.selected_placed():
            corners = tuple((x0 + cx, y0 + cy) for cx, cy in (self.canvas_point(c) for c in geometry.item_corners(placed)))
            draw.polygon_outline(corners, ACCENT, 1.5)
        found = self.handles()
        if found is None:
            return                                        # nothing selected that may move: outlines only
        if self.drag and self.drag['kind'].startswith('key_'):
            return                                        # while G / S / R runs: the outline stays, the handles go (as in Blender)
        frame, corners, turn = found
        screen = tuple((x0 + cx, y0 + cy) for cx, cy in corners)
        if len(self.selection) > 1:
            draw.polygon_outline(screen, (*ACCENT[:3], 0.6), 1.0)
        top_mid = ((screen[2][0] + screen[3][0]) / 2, (screen[2][1] + screen[3][1]) / 2)
        tx, ty = x0 + turn[0], y0 + turn[1]
        draw.lines((top_mid, (tx, ty)), ACCENT, 1.0)
        r = HANDLE_SIZE * k
        hot = self.hover if self.hover and self.hover[0] in {'scale', 'turn'} else None
        for i, (cx, cy) in enumerate(screen):
            grow = 1.3 if hot == ('scale', i) else 1.0
            draw.box(cx - r * grow, cy - r * grow, 2 * r * grow, 2 * r * grow, HANDLE_FILL, 1.5 * k)
            draw.outline(cx - r * grow, cy - r * grow, 2 * r * grow, 2 * r * grow, HANDLE_EDGE, 1.5 * k)
        grow = 1.3 if hot == ('turn',) else 1.0
        draw.box(tx - r * grow, ty - r * grow, 2 * r * grow, 2 * r * grow, HANDLE_FILL, r * grow)
        draw.outline(tx - r * grow, ty - r * grow, 2 * r * grow, 2 * r * grow, HANDLE_EDGE, r * grow)

    def paint_turn_cursor(self, x0, y0, k):
        """The rotate cursor: over the turn handle, and all through a drag of it, the real cursor is hidden and a double arrow across the
        line from the pivot is drawn at the mouse."""
        turning = bool(self.drag and self.drag['kind'] == 'turn')
        if not turning and (self.drag or self.hover != ('turn',)):
            return
        found = self.handles()
        mouse = self.drop_at
        if found is None or mouse is None:
            return
        pivot = self.canvas_point(found[0][:2])
        shapes = geometry.turn_cursor((x0 + pivot[0], y0 + pivot[1]), (x0 + mouse[0], y0 + mouse[1]), k)
        draw.polygons(shapes['dark'], (0.0, 0.0, 0.0, 0.8))
        draw.polygons(shapes['light'], (1.0, 1.0, 1.0, 1.0))

    def paint_key_transform(self, x0, y0, k):
        """While G / S / R runs: Blender's arrows at the mouse (the cursor itself is hidden), the dashed line from the pivot
        for S / R, the locked axis through the selection, and what the keys do, along the board's foot."""
        drag = self.drag
        if not drag or not drag['kind'].startswith('key_'):
            return
        mouse = drag.get('mouse_canvas')
        if mouse is not None:
            mode = {'key_move': 'G', 'key_scale': 'S', 'key_turn': 'R'}[drag['kind']]
            pivot = self.canvas_point(drag['frame'][:2])
            shapes = geometry.transform_icon(mode, (x0 + pivot[0], y0 + pivot[1]), (x0 + mouse[0], y0 + mouse[1]), k)
            draw.polygons(shapes['dark'], (0.0, 0.0, 0.0, 0.8))
            draw.polygons(shapes['light'], (1.0, 1.0, 1.0, 1.0))
        bx, by, bw, bh = self.board_rect
        axis = drag.get('axis')
        if axis:
            cx, cy = self.canvas_point(drag['frame'][:2])
            if axis == 'X':
                draw.lines(((x0 + bx, y0 + cy), (x0 + bx + bw, y0 + cy)), AXIS_X, 1.5)
            else:
                draw.lines(((x0 + cx, y0 + by), (x0 + cx, y0 + by + bh)), AXIS_Y, 1.5)
        name = {'key_move': "Move", 'key_scale': "Scale", 'key_turn': "Rotate"}[drag['kind']]
        if axis:
            name += f"  along {axis}"
        keys = {'key_move': ["X / Y: axis", "Ctrl: snap", "Shift: precise"], 'key_scale': ["Ctrl: snap", "Shift: precise"],
                'key_turn': ["Ctrl: 15° steps", "Shift: precise"]}[drag['kind']] + ["Click or Enter: done", "Esc: cancel"]
        # As many lines as the board's width needs: a docked board is narrow.
        size, room, lines = 9 * k, bw - 20 * k, [""]
        for part in keys:
            joined = lines[-1] + ("   " if lines[-1] else "") + part
            if lines[-1] and draw.text_width(joined, size) > room:
                lines.append(part)
            else:
                lines[-1] = joined
        line_h = 13 * k
        h = 24 * k + line_h * len(lines)
        draw.box(x0 + bx, y0 + by, bw, h, KEY_HINT_BACK)
        draw.text(x0 + bx + 10 * k, y0 + by + h - 17 * k, name, 11 * k, TEXT, room)
        for i, line in enumerate(lines):
            draw.text(x0 + bx + 10 * k, y0 + by + h - 17 * k - line_h * (i + 1), line, size, MUTED, room)

    def paint_toast(self, x0, y0, k):
        """The colour the eyedropper picked: a swatch and its hex value, for a few seconds."""
        if not self.toast:
            return
        until, label, rgb = self.toast
        if time.monotonic() > until and not (self.hover is not None and self.hover[0] in {'toast', 'swatch'}):
            self.toast = None                                       # timed out, and the mouse is not on it
            return
        swatches = [rgb]
        bx, by, bw, bh = self.board_rect
        room = 18 * k * len(swatches)
        w, h = min(bw - 16 * k, draw.text_width(label, 11 * k) + room + 28 * k), 26 * k
        x, y = x0 + bx + 8 * k, y0 + by + 32 * k
        draw.box(x, y, w, h, TIP_BACK, 5 * k)
        self.hits.append(((x - x0, y - y0, w, h), ('toast',)))          # the strip takes its clicks: they never fall through to the board
        for i, colour in enumerate(swatches):
            sx = x + 5 * k + 18 * k * i
            draw.box(sx, y + 5 * k, 16 * k, 16 * k, (*colour, 1.0), 3 * k)
            if i == self.toast_pick or self.hover == ('swatch', i):
                draw.outline(sx - k, y + 4 * k, 18 * k, 18 * k, ACCENT if i == self.toast_pick else TEXT, 3 * k, 1.5)
            self.hits.append(((sx - x0, y + 5 * k - y0, 16 * k, 16 * k), ('swatch', i)))
        draw.text(x + 10 * k + room, y + 9 * k, label, 11 * k, TEXT, w - room - 14 * k)
        if self.hover is not None and self.hover[0] in {'toast', 'swatch'}:
            self.show_toast(label, rgb, self.toast_pick)               # the mouse is on it: it stays

    def paint_marquee(self, x0, y0, k):
        if not self.marquee:
            return
        (ax, ay), (bx, by) = self.marquee
        left, bottom = min(ax, bx), min(ay, by)
        w, h = abs(bx - ax), abs(by - ay)
        draw.box(x0 + left, y0 + bottom, w, h, MARQUEE_FILL)
        draw.outline(x0 + left, y0 + bottom, w, h, ACCENT, 0, 1.0)

    def paint_bar(self, x0, y0, w, h, bar, k):
        y = h - bar
        lifted = self.floating or self.popout
        radius = 6 * k if self.floating and not self.popout else 0
        draw.box(x0, y0 + y, w, bar, BAR, radius)
        if radius:
            draw.box(x0, y0 + y, w, radius, BAR)            # square off the bar's lower corners
        draw.lines(((x0, y0 + y), (x0 + w, y0 + y)), BAR_RULE, 1.0)
        if lifted:
            self.hits.append(((0, y, w, bar), ('move',)))     # the bar is where a lifted board is dragged
        bh = BUTTON_HEIGHT * k
        by = y + (bar - bh) / 2
        # A window of its own shows Blender's small arrows for its hidden regions; the top-right one sits over the bar's end.
        right = w - (PAD + (POP_ARROW if self.popout else 0)) * k
        first = ("Dock", ('dock',), 52) if lifted else ("Undock" if self.can_pop_out() else "Float", ('undock',), 60)
        buttons = [first]
        buttons += [("Fit", ('fit',), 40), ("Import", ('import',), 56)]
        for label, key, width in buttons:
            bw = width * k
            if right - bw < 70 * k and key in {('import',), ('fit',)}:
                continue                                  # a narrow board keeps the buttons that matter most
            right -= bw
            self.button(x0, y0, right, by, bw, bh, label, key, k)
            right -= 4 * k
        title_x = (12 if not lifted else 26) * k
        if lifted:
            self.paint_grip(x0 + 10 * k, y0 + y + bar / 2, k)
        # The title is the board's name.
        board = self.board()
        name = board.name if board is not None else edition.NAMES[edition.EDITION]       # (the sidebar's tab is TAB; the bar says which edition this is)
        room = right - title_x - 6 * k
        label = draw.fit_text(name, 12 * k, max(10.0, room - 14 * k))
        draw.text(x0 + title_x, y0 + y + bar / 2 - 4 * k, label, 12 * k, TEXT, room)

    def tip_box(self, tip, w, h, k):
        """Where a tip goes and how it breaks into lines: (x, y, width, height, [lines]) in canvas pixels. A bar button's tip
        hangs under the bar at the right."""
        size, line_h, pad = 10 * k, 13 * k, 7 * k
        room = max(40 * k, w - 12 * k)
        lines = draw.wrap(tip, size, room - 2 * pad)
        tw = min(room, max(draw.text_width(line, size) for line in lines) + 2 * pad)
        th = 7 * k + line_h * len(lines)
        bar = BAR_HEIGHT * k
        return max(6 * k, w - tw - 6 * k), h - bar - th - 6 * k, tw, th, lines

    def paint_tip(self, x0, y0, w, h, k):
        tip = self.tip()
        if not tip:
            return
        size, line_h = 10 * k, 13 * k
        x, y, tw, th, lines = self.tip_box(tip, w, h, k)
        draw.box(x0 + x, y0 + y, tw, th, TIP_BACK, 4 * k)
        for i, line in enumerate(lines):
            draw.text(x0 + x + 7 * k, y0 + y + th - 13.5 * k - line_h * i, line, size, TEXT, tw - 14 * k)


LABELS = {'Move': "Move Pictures", 'Scale': "Scale Pictures", 'Rotate': "Rotate Pictures", 'Delete': "Remove Pictures"}


class PinvasBoard(Board):
    # (`host.drop_at`: where the mouse is on the canvas, kept by the session for a drag's end)
    """Pinvas on OEBS Canvas: its prefix and tab, its own Canvas and session, the scene's board data (canvas.board()) and its
    items for the board layer (a Store over Scene.pinvas)."""
    prefix = "pinvas"
    tab = TAB
    host_class = Canvas
    session_class = None                  # the session operator's class, set by operators.py (it imports this module)
    snap = 'grid'                         # Ctrl while moving: whole steps of the grid on show
    own_history = False                   # Ctrl+Z is Blender's undo; every action is one Blender undo step

    def store(self):
        from .store import PinvasStore
        return PinvasStore(self())

    def action_label(self, name):
        return LABELS.get(name, name)

    def push_undo(self, label):
        from . import operators
        operators.push_undo(label)            # by name: the tests stand in for it

    def placed_as(self, item, shape):
        """The item placed as Pinvas's maths takes it, (x, y, width, height, scale, rotation), when it shows as `shape`."""
        _x, _y, w, h, s, _r = board_module.placed(item.item)
        scale = shape[2] / max(w, 1e-9)
        return (shape[0], shape[1], w, h, scale, shape[4])

    def item_contains(self, item, shape, point, reach):
        """A picture is taken by its box."""
        p = self.placed_as(item, shape)
        return geometry.item_contains(p, point)

    def __call__(self, create=False):
        """The scene's current board (Scene.pinvas), made when `create` and there is none."""
        return board_module.board_of(bpy.context.scene, create)

    def adopt(self, other):
        """The board moves here from another view or its own window: it keeps what was selected; the drag there is dropped."""
        self.host.selection = set(other.host.selection)
        other.host.preview = {}

