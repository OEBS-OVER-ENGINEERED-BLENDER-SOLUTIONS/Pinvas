"""Host: one open board in one 3D View. Where it is drawn (docked in the sidebar, floating over the viewport, or in a
window of its own), its view (pan and zoom), what is under the mouse, its tips, and the frame the framework paints
round the product's board: background, grid, bar, zoom chip, resize corner, tips."""
import math
import time
import traceback
from collections import deque

from . import board as board_module, cache, compat, draw, idle, roam, runtime, style, theme, ui as controls, ui_input, view
from .paint import Painter
from .runtime import region_of, sessions, ui_scale
from .ui import BarUi


class Host:

    def __init__(self, area):
        self.area_id = area.as_pointer()
        self.area = area                               # the session updates it on every event
        self.alive = True
        place = runtime.places.get(self.area_id, {})
        self.floating = place.get('floating', False)
        self.float_rect = place.get('float_rect')      # floating: (x, y, w, h) in the viewport region's pixels
        self.view = place.get('view', view.HOME_VIEW)
        self.fit_pending = not place                   # first time in this view: fit once the board's size is known
        self.last_press = None                         # (time, x, y) of the last left press: two quick ones make a double-click
        self.drag = None                               # the drag in progress: a dict with 'kind'
        self.hover = None                              # the hit key under the mouse
        self.hover_since = 0.0
        self.cursor = None
        self.hits = []                                 # [(rect, key)] in canvas pixels, rebuilt by every paint
        self.ui_tips = {}                              # tips the product's bar controls gave, by key
        self.bar_layout = None                         # (right, y, height, k) where the product's bar controls start
        self.size = (0, 0)
        self.board_rect = (0, 0, 0, 0)                 # the board area inside the canvas (under the bar)
        self.draw_ms = deque(maxlen=240)
        self.mouse = None                              # the last mouse position in window pixels, and whether it was over the board
        self.popout = False                            # this host is a board in a window of its own...
        self.hwnd = 0                                  # ... that window (Windows' handle) and Blender's
        self.win_ptr = None
        self.origin_id = None                          # the 3D View it came from
        self.dock_to = None                            # the 3D View it is being docked into
        self.returned = False
        self.outline = 0                               # the outline window of a resize in progress
        self.dock_armed = False                        # carried over a dock target: letting go docks it
        self.popped = None                             # this view's board is out in the pop-out of this area pointer
        self.error = ''                                # the message the banner under the bar shows ('' for none)
        self.reported = set()                          # messages whose traceback has been printed
        self.stamp = 0                                 # bumped whenever what the board shows may have changed
        self.frame = None                              # the stored picture (cache.Frame) of a cached board
        self.colours = theme.FIXED                     # the palette of the last paint
        self.reach = None                              # a board crossing editors: where it is, in window pixels...
        self.reach_screen = None                       # ... in the window of this screen
        self.crossing = False                          # ... and it hangs out of its own viewport now
        self.shown_in = set()                          # the editors it was over at the last redraw
        self.shown_size = None                         # a window of its own: the size last drawn
        self.side_width = place.get('side_width')     # the side panel's width in board units, once the divider was dragged
        self.side_rect = None                          # the side panel in canvas pixels (None without one)
        self.side_layout = None                        # (rect, k) of the last paint, for the event pass
        self.menu = None                               # the open board-drawn menu (ui_input owns it)
        self.capture = False                           # an input handler wants every event, on the board or not
        self.pointer = None                            # the mouse in canvas pixels while it is over the board area
        self.inputs = [ui_input.Input(self)]           # asked about every event on the board before the framework
        self.layer = None                              # the board layer's input, when the board has items
        if board_module.has_layer(runtime.board_class):
            from .layer import input as layer_input
            self.layer = layer_input.Input(self)
            self.inputs.append(self.layer)
        self.board = runtime.board_class(self)

    # Where it lives

    def tab_shown(self, area, ui):
        tab = compat.active_tab(ui)
        if tab is not None:
            return tab == runtime.tab()
        return time.monotonic() - runtime.tab_seen.get(area.as_pointer(), 0.0) < 1.0     # the product's panel drew lately

    def host(self, area):
        """(region, x, y, width, height) where the board is drawn now, in that region's pixels; None while it is not shown."""
        if not self.alive:
            return None
        self.reach = None
        self.crossing = False
        if self.popout:
            window = region_of(area, 'WINDOW')
            if window is None or window.width < 2 or window.height < 2:
                return None
            return (window, 0, 0, window.width, window.height)
        if self.popped:
            out = sessions.get(self.popped)
            if out is not None and out.alive:
                return None                                              # the board is out in its own window
            self.popped = None
        if sessions.get(self.area_id) is self:
            shown = runtime.shown()                                      # on screen in one place only
            if shown is None:
                runtime.hold(self)
            elif shown is not self:
                return None
        if self.floating:
            window = region_of(area, 'WINDOW')
            if window is None or window.width < 2 or window.height < 2:
                return None
            least = self.float_min()
            if self.float_rect is None:
                self.float_rect = self.first_float_rect(area, window)
            screen = getattr(area, 'id_data', None)
            if self.roaming() and screen is not None and getattr(screen, 'areas', None):
                areas = list(screen.areas)
                views = [(a.x - window.x, a.y - window.y, a.width, a.height) for a in areas if a.type in roam.AREAS]
                left, bottom = min(a.x for a in areas), min(a.y for a in areas)
                whole = (max(a.x + a.width for a in areas) - left, max(a.y + a.height for a in areas) - bottom)
                bounds = (left - window.x, bottom - window.y, *whole)
                self.float_rect = view.clamp_roaming(self.float_rect, views or [(0, 0, window.width, window.height)], least,
                                                     self.float_grip(), self.float_strip(), self.float_margin(), whole, bounds)
                fx, fy, fw, fh = self.float_rect
                self.reach, self.reach_screen = (window.x + fx, window.y + fy, fw, fh), screen.as_pointer()
                self.crossing = not (0 <= fx and 0 <= fy and fx + fw <= window.width and fy + fh <= window.height)
            else:
                self.float_rect = view.clamp_float(self.float_rect, self.float_bounds(area, window), least, self.float_margin())
            return (window, *self.float_rect)
        ui = region_of(area, 'UI')
        if ui is None or not area.spaces.active.show_region_ui or ui.width < 2 or not self.tab_shown(area, ui):
            return None
        strip = round(style.TAB_STRIP * ui_scale())
        if ui.alignment == 'LEFT':
            return (ui, strip, 0, ui.width - strip, ui.height)
        return (ui, 0, 0, ui.width - strip, ui.height)

    # A floating board's measures, in region pixels (a product's Host may give its own)

    def float_min(self):
        """The least (width, height) of a floating board."""
        k = runtime.board_scale()
        return (style.FLOAT_MIN[0] * k, style.FLOAT_MIN[1] * k)

    def float_margin(self):
        """How much of a floating board stays inside its viewport (or the screen, crossing editors)."""
        return style.FLOAT_MARGIN * runtime.board_scale()

    def float_strip(self):
        """The height of a floating board's title strip, which stays reachable when it crosses editors."""
        return style.BAR_HEIGHT * runtime.board_scale()

    def float_grip(self):
        """How much of a board crossing editors stays over a view it can be grabbed in."""
        return style.ROAM_GRIP * runtime.board_scale()

    def float_bounds(self, area, window):
        """The part of the viewport a floating board may use: clear of the header, toolbar, sidebar and asset shelf."""
        covers = [(r.x, r.y, r.width, r.height, r.alignment) for r in area.regions if r.type != 'WINDOW']
        return view.open_rect((window.x, window.y, window.width, window.height), covers)

    def first_float_rect(self, area, window):
        """Where the board appears when it starts to float: the sidebar's size, just left of the sidebar."""
        k = runtime.board_scale()
        bounds = self.float_bounds(area, window)
        ui = region_of(area, 'UI')
        if ui is not None and area.spaces.active.show_region_ui and ui.width > 1:
            w, h = ui.width - style.TAB_STRIP * ui_scale(), min(ui.height, style.FLOAT_DEFAULT[1] * 1.6 * k)
        else:
            w, h = style.FLOAT_DEFAULT[0] * k, style.FLOAT_DEFAULT[1] * k
        bx, by, bw, bh = bounds
        space = area.spaces.active
        # Keep clear of the viewport's navigation buttons, which sit along the right edge: a click on them would land on the board.
        beside = style.NAV_ROOM if getattr(space, 'show_gizmo', False) and getattr(space, 'show_gizmo_navigate', False) else 16
        return (bx + bw - w - beside * k, by + bh - h - 16 * k, w, h)

    def resize_edge(self, region, x):
        """Is canvas x on the sidebar's own resize edge? That strip keeps working as Blender's."""
        if self.floating or self.popout:
            return False
        grip = style.EDGE_GRIP * ui_scale()
        if region.alignment == 'LEFT':
            return x >= self.size[0] - grip
        return x < grip

    def set_floating(self, area, floating):
        if floating == self.floating:
            return
        if floating:
            window = region_of(area, 'WINDOW')
            if window is None:
                raise ValueError("This view has no viewport to float over")
            self.float_rect = self.first_float_rect(area, window)
        else:
            space = area.spaces.active
            if not space.show_region_ui:
                space.show_region_ui = True          # only when closed: setting it again replays the sidebar's opening slide
            ui = region_of(area, 'UI')
            if ui is not None:
                compat.set_active_tab(ui, runtime.tab())
        self.floating = floating
        self.drag = None
        self.hover = None

    def roaming(self):
        """May this floating board leave its viewport and hang over the other editors (the Float Across Editors preference)?"""
        if not self.floating or self.popout:
            return False
        return bool(self.call(self.board.cross_editors, failed=False, touch=False))     # Board.cross_editors

    def over(self, area):
        """Is this board, crossing editors, over part of `area` now?"""
        screen = getattr(area, 'id_data', None)
        if self.reach is None or screen is None or screen.as_pointer() != self.reach_screen:
            return False
        x, y, w, h = self.reach
        return x < area.x + area.width and x + w > area.x and y < area.y + area.height and y + h > area.y

    def redraw_reached(self, area):
        """Redraw the other editors the board is over now, and those it was over at the last redraw (to clear it from them)."""
        screen = getattr(area, 'id_data', None)
        if screen is None:
            return
        now = set()
        for other in screen.areas:
            pointer = other.as_pointer()
            if pointer == self.area_id or other.type not in roam.AREAS:
                continue
            if self.over(other):
                now.add(pointer)
            if pointer in now or pointer in self.shown_in:
                other.tag_redraw()
        self.shown_in = now

    def close(self):
        if not self.alive:
            return
        self.alive = False
        idle.stop(self)
        if self.frame is not None:
            self.frame.free()
            self.frame = None
        if self.popout:
            sessions.pop(self.area_id, None)
            from . import popout
            popout.return_board(self)          # its view goes back to a sidebar and its window closes
            return
        self.remember()
        sessions.pop(self.area_id, None)

    def remember(self):
        runtime.places[self.area_id] = dict(view=self.view, floating=self.floating, float_rect=self.float_rect,
                                            side_width=self.side_width)

    def dismiss(self):
        """The × in the bar: close the board in this view until its tab is hidden and shown again (or Open is clicked)."""
        runtime.dismissed[self.area_id] = self.call(lambda: self.board.dismiss_token(self.area), failed=True, touch=False)
        self.close()
        place = runtime.places.get(self.area_id)
        if place is not None:
            place['floating'] = False          # a closed floating board does not float back on the next tick

    def call(self, fn, *args, failed=None, touch=True):
        """Run one of the product's hooks. A hook that raises does not end the session: its message shows in the banner
        under the bar, its traceback prints once, and `failed` is returned."""
        if touch:
            self.touch()
        try:
            return fn(*args)
        except Exception as exc:
            self.fail(exc)
            return failed

    def fail(self, exc):
        message = str(exc) or type(exc).__name__
        if message not in self.reported:
            self.reported.add(message)
            print(f"{runtime.title()}: {type(exc).__name__}: {message}")
            traceback.print_exc()
        self.error = message

    def touch(self):
        """What the board shows may have changed: a cached board paints afresh at the next redraw."""
        self.stamp += 1

    def elsewhere(self):
        """The host that shows the board when it is not this one (another view, or a window of its own), else None."""
        shown = runtime.shown()
        if shown is None or shown is self or sessions.get(self.area_id) is not self:
            return None
        return shown

    def carry_from(self, other):
        """Take over what another host showed (its view, and the product's own state through Board.adopt)."""
        self.view = other.view
        self.board.adopt(other.board)

    def take_board(self, area):
        """Dock It Here: the board leaves the view that shows it and opens in this sidebar, as it was."""
        shown = self.elsewhere()
        if shown is not None and not shown.popout:
            self.carry_from(shown)
            shown.floating, shown.drag, shown.hover, shown.fit_pending = False, None, None, False
        runtime.hold(self)
        self.fit_pending = False
        self.floating = True                   # so set_floating opens this sidebar on the product's tab
        self.set_floating(area, False)

    # Clicks

    def double_click_at(self, x, y, now=None):
        """Is a left press at canvas (x, y) the second of a double-click? Blender sends no DOUBLE_CLICK to a handler that
        takes every press (the board must, or the press would reach the viewport), so the host tells them apart itself: two
        presses within Blender's double-click time and a few pixels of each other."""
        now = time.monotonic() if now is None else now
        limit = compat.double_click_ms() / 1000.0
        last, self.last_press = self.last_press, (now, x, y)
        if last is not None and now - last[0] <= limit and math.hypot(x - last[1], y - last[2]) <= style.DRAG_START * ui_scale():
            self.last_press = None                         # a third press starts afresh
            return True
        return False

    def edge_hit(self, x, y):
        """A pop-out's resize edge or corner under canvas (x, y), or None."""
        if not self.popout:
            return None
        from . import popout
        k = ui_scale()
        edge = popout.edge_at(self.size, x, y, popout.POP_EDGE * k, popout.POP_CORNER * k)
        return ('edge', edge) if edge else None

    def hit(self, x, y):
        """What is under canvas (x, y): a pop-out's edges, the frame's and the product's controls, then the board area."""
        edge = self.edge_hit(x, y)
        if edge is not None:
            return edge
        for rect, key in reversed(self.hits):
            if view.inside(rect, x, y):
                return key
        if view.inside(self.board_rect, x, y):
            return ('board',)
        return None

    def on_controls(self, x, y):
        """Is canvas (x, y) on one of the board's own controls (its bar, buttons, corner or a pop-out's edges)?"""
        return self.edge_hit(x, y) is not None or any(view.inside(rect, x, y) for rect, _key in self.hits)

    def run_ui_event(self, key, **extra):
        """A click (or a committed field, a slider move) on one of the product's controls: lay the bar and the side panel out
        again with no drawing, telling them which (`clicked=key`) and passing `extra` as ui.event. True when a control with
        that key was laid out, so it answered."""
        kept = list(self.hits)
        self.hits = []
        try:
            if self.bar_layout is not None:
                right, y, height, k = self.bar_layout
                ui = BarUi(self, 'event', 0, 0, right, y, height, k, clicked=key)
                ui.event = extra
                self.call(self.board.draw_bar, ui)
            if self.side_layout is not None and self.draws_side():
                rect, k = self.side_layout
                ui = controls.ColumnUi(self, 'event', 0, 0, rect, k, clicked=key)
                ui.event = extra
                self.call(self.board.draw_side, ui)
            return any(found == key for _rect, found in self.hits)
        finally:
            self.hits = kept

    def run_bar_event(self, key):
        """A click landed on one of the product's bar controls (run_ui_event without extras)."""
        self.run_ui_event(key)

    def draws_side(self):
        """Does the product draw controls in its side panel (it overrides Board.draw_side)?"""
        return type(self.board).draw_side is not board_module.Board.draw_side

    def side_pixels(self, w, k):
        """The side panel's width in canvas pixels for a canvas `w` wide: 0 without one; else the remembered or the product's
        width, at least SIDE_MIN and at most SIDE_MAX of the canvas."""
        if self.board.side not in ('LEFT', 'RIGHT'):
            return 0
        units = self.side_width if self.side_width is not None else self.board.side_width
        return min(max(units * k, style.SIDE_MIN * k), style.SIDE_MAX * w)

    def drag_side(self, drag, dx):
        """The divider dragged `dx` pixels from where it was pressed: the panel's new width, within its limits."""
        k = runtime.board_scale()
        grow = -dx if self.board.side == 'RIGHT' else dx
        width = min(max(drag['side_px'] + grow, style.SIDE_MIN * k), style.SIDE_MAX * self.size[0])
        self.side_width = width / k

    def menu_builders(self, kind):
        """The sections of a board-drawn menu, in order, each a callable taking a menus.Menu. 'context' (right-click): the
        product's menu hook, the input handlers' sections, then the view section. 'add' (Shift+A): the add_menu hook. A hook
        the product does not have is left out."""
        board = self.board
        if kind == 'add':
            return [board.add_menu] if callable(getattr(board, 'add_menu', None)) else []
        built = [board.menu] if callable(getattr(board, 'menu', None)) else []
        for handler in self.inputs:
            sections = getattr(handler, 'menu_sections', None)
            if callable(sections):
                built.extend(sections())
        built.append(self.view_menu)
        return built

    def view_menu(self, menu):
        """The framework's section of the context menu: Fit, 100%, and where the board lives."""
        if menu.item("Fit", key=('fit',), shortcut="Home"):
            self.fit()
        if menu.item("100%", key=('zoom100',), shortcut="1"):
            self.zoom_100()
        if self.floating or self.popout:
            if menu.item("Dock", key=('dock',)):
                self.command(('dock',), self.area)
            return
        from . import popout
        if popout.possible() and menu.item("Undock", key=('undock',)):
            self.command(('undock',), self.area)
        if menu.item("Float", key=('float',)):
            self.command(('float',), self.area)

    def command(self, key, area):
        """The frame's own controls."""
        if key == ('fit',):
            self.fit()
        elif key == ('zoom100',):
            self.zoom_100()
        elif key in {('undock',), ('float',)}:
            from . import popout
            if key == ('float',) or not popout.pop_out(self, area):
                self.set_floating(area, True)          # no window of its own here: it floats over the viewport
        elif key == ('close',):
            self.dismiss()
        elif key == ('dock',):
            if self.popout:
                from . import popout
                found = popout.find_area(self.origin_id)
                if found is None or not popout.dock_into(self, found[1], found[0]):
                    self.close()
            else:
                self.set_floating(area, False)

    def hover_to(self, key):
        """The mouse is over `key` now. True when that changed (the canvas must be redrawn)."""
        if key == self.hover:
            return False
        self.hover = key
        self.hover_since = time.monotonic()
        return True

    def tip_wanted(self):
        return self.tip_text(self.hover) is not None

    def tip_text(self, key):
        if key is None:
            return None
        if key == ('undock',):
            from . import popout
            if not popout.possible():
                return style.TIPS[('float',)]
        return style.TIPS.get(key) or self.ui_tips.get(key) or self.board.tip_for(key)

    def tip(self, now=None):
        """The tip of the control under the mouse, once the mouse has rested on it for TIP_DELAY."""
        if not self.hover or self.drag:
            return None
        now = time.monotonic() if now is None else now
        if now - self.hover_since < style.TIP_DELAY:
            return None
        return self.tip_text(self.hover)

    # The view

    def board_centre(self):
        return (self.board_rect[2] / 2, self.board_rect[3] / 2)

    def to_board_area(self, x, y):
        """Canvas pixels to pixels inside the board area."""
        return (x - self.board_rect[0], y - self.board_rect[1])

    def set_view(self, value):
        self.view = value

    def pan(self, dx, dy):
        self.set_view(view.pan_by(self.view, dx, dy))

    def zoom_at(self, x, y, zoom):
        """Zoom to `zoom` keeping the board point under canvas (x, y) in place."""
        self.set_view(view.zoom_about(self.view, self.to_board_area(x, y), self.board_centre(), zoom))

    def zoom_steps_at(self, x, y, steps):
        self.zoom_at(x, y, view.zoom_steps(self.view[2], steps))

    def zoom_100(self):
        bx, by, bw, bh = self.board_rect
        self.zoom_at(bx + bw / 2, by + bh / 2, 1.0)

    def board_point(self, x, y):
        """Canvas pixels to a board point."""
        bx, by, bw, bh = self.board_rect
        return view.canvas_to_board(self.view, (x - bx, y - by), (bw / 2, bh / 2), runtime.board_scale())

    def canvas_point(self, point):
        """A board point to canvas pixels."""
        bx, by, bw, bh = self.board_rect
        cx, cy = view.board_to_canvas(self.view, point, (bw / 2, bh / 2), runtime.board_scale())
        return (bx + cx, by + cy)

    def fit(self):
        """Show everything on the board (the product's content_bounds), or its origin at 100% when it is empty."""
        self.fit_bounds(self.board.content_bounds())

    def fit_bounds(self, bounds):
        if bounds is None:
            self.set_view(view.HOME_VIEW)
        else:
            self.set_view(view.fit_view(bounds, self.board_rect[2:], runtime.board_scale()))

    # Painting

    def draw_in(self, area, region):
        """Paint this board in `region` of `area` if it belongs there: its home region (where host() puts it), or, for a board
        crossing editors, another editor's region it hangs over (placed by where it floats, and only where the window touches
        it: host() is not asked for another area). The draw callback asks every board; a product's own draw calls it with
        its own area and region. Returns True when the board was drawn there."""
        with runtime.scale_held():                            # one read of the interface scale for the paint
            if self.area_id == area.as_pointer():
                placed = self.host(area)
                if placed is not None and placed[0].as_pointer() == region.as_pointer():
                    _region, x, y, w, h = placed
                    self.paint_board(x, y, w, h)
                    return True
            if (area.type in roam.AREAS and region.type in roam.DRAWN_REGIONS and self.alive and self.reach is not None
                    and self.crossing and self.roaming() and self.over(area)):
                roam.paint_over(self, area, region)           # a board crossing editors, on top of this one
                return True
            return False

    def paint_board(self, x0, y0, w, h):
        """What the draw callbacks call to draw the board where it lives (a product's Host may draw its own way)."""
        return self.paint(x0, y0, w, h)

    def paint(self, x0, y0, w, h):
        """Draw the whole canvas with its bottom-left corner at region pixel (x0, y0): from the stored picture when the board
        is cached and nothing it shows has changed, else painted afresh."""
        started = time.perf_counter()
        if self.popout:
            self.shown_size = (w, h)
        try:
            if not (self.board.cache and self.paint_cached(x0, y0, w, h)):
                self.paint_direct(x0, y0, w, h)
        finally:
            self.draw_ms.append((time.perf_counter() - started) * 1000)

    def paint_cached(self, x0, y0, w, h):
        """False when there is no stored picture to use yet (its buffer is made by a timer): the caller paints directly."""
        if self.frame is None:
            self.frame = cache.Frame(on_built=lambda: runtime.redraw_area(self.area_id))
        frame = self.frame
        width, height = math.ceil(w), math.ceil(h)
        if not frame.fit(width, height):
            return False
        key = (w, h, runtime.board_scale(), self.view, self.hover, self.tip() is not None, self.floating, self.popout,
               self.dock_armed, self.error, self.stamp, self.board.cache_key())
        now = time.monotonic()
        if key != frame.key or now - frame.painted >= cache.MAX_AGE:
            frame.render(lambda: self.paint_direct(0, 0, w, h))
            frame.key, frame.painted = key, now
        frame.blit(round(x0), round(y0), width, height)
        return True

    def paint_direct(self, x0, y0, w, h):
        k = runtime.board_scale()
        c = self.colours = theme.colours(self.board.palette)
        self.size = (w, h)
        self.hits = []
        self.ui_tips = {}
        self.side_layout = None
        bar = style.BAR_HEIGHT * k
        under = max(0, h - bar)
        side = self.side_pixels(w, k)
        if not side:
            self.side_rect, self.board_rect = None, (0, 0, w, under)
        elif self.board.side == 'LEFT':
            self.side_rect, self.board_rect = (0, 0, side, under), (side, 0, w - side, under)
        else:
            self.side_rect, self.board_rect = (w - side, 0, side, under), (0, 0, w - side, under)
        lifted = self.floating or self.popout
        radius = 6 * k if self.floating and not self.popout else 0
        try:
            draw.begin()
            draw.box(x0, y0, w, h, c['board'], radius)
            if self.fit_pending and self.board_rect[2] > 1 and self.board_rect[3] > 1:
                self.fit_pending = False
                self.fit()
            bx, by, bw, bh = self.board_rect
            with draw.clipped(x0 + bx, y0 + by, bw, bh):
                self.paint_grid(x0, y0, k)
                painter = Painter(self, x0, y0, k)
                self.call(self.board.draw_content, painter, touch=False)
                if self.layer is not None:
                    self.layer.paint_board(painter, x0, y0, k)        # the items, the selection and its handles
            if self.side_rect:
                self.paint_side(x0, y0, k)
            self.paint_bar(x0, y0, w, h, bar, k)
            if self.error:
                self.paint_error(x0, y0, w, h, bar, k)
            self.paint_zoom_chip(x0, y0, k)
            for handler in self.inputs:
                self.call(handler.paint, x0, y0, w, h, k, touch=False)      # menus and toasts, over all but the tip
            if lifted:
                self.paint_corner(x0, y0, w, k)
                draw.outline(x0, y0, w, h, style.FRAME, radius, 1.0)
                draw.outline(x0 + 1, y0 + 1, w - 2, h - 2, style.FRAME_LIGHT, max(0, radius - 1), 1.0)
            if self.popout and self.dock_armed:
                from . import popout
                bh = 44 * k
                popout.draw_banner(x0 + 10 * k, y0 + (h - bar) / 2 - bh / 2, w - 20 * k, bh, k)
            if self.popout and self.hover and self.hover[0] == 'edge':
                self.paint_edge(x0, y0, w, h, self.hover[1], k)
            self.paint_tip(x0, y0, w, h, k)
        finally:
            draw.end()

    def paint_error(self, x0, y0, w, h, bar, k):
        """The banner under the bar: what went wrong. A click on it hides it."""
        bh = style.BANNER * k
        y = h - bar - bh
        draw.box(x0, y0 + y, w, bh, style.ERROR_BACK)
        draw.text(x0 + 8 * k, y0 + y + bh / 2 - 4 * k, self.error, 10 * k, (1.0, 1.0, 1.0, 1.0), w - 16 * k)
        self.hits.append(((0, y, w, bh), ('error',)))

    def paint_grid(self, x0, y0, k):
        bx, by, bw, bh = self.board_rect
        columns, rows = view.grid_lines(self.view, (bw, bh), k)
        minor, major = [], []
        for at, is_major in columns:
            (major if is_major else minor).extend(((x0 + bx + at, y0 + by), (x0 + bx + at, y0 + by + bh)))
        for at, is_major in rows:
            (major if is_major else minor).extend(((x0 + bx, y0 + by + at), (x0 + bx + bw, y0 + by + at)))
        draw.lines(tuple(minor), style.GRID_MINOR, 1.0)
        draw.lines(tuple(major), style.GRID_MAJOR, 1.0)
        ox, oy = view.board_to_canvas(self.view, (0, 0), self.board_centre(), k)
        ox, oy, arm = x0 + bx + ox, y0 + by + oy, 10 * k
        draw.lines(((ox - arm, oy), (ox + arm, oy), (ox, oy - arm), (ox, oy + arm)), style.ORIGIN, 1.0)

    def button(self, x0, y0, x, y, w, h, label, key, k, on=False):
        hovered = self.hover == key
        c = self.colours
        fill = (*c['accent'][:3], 0.85 if hovered else 0.65) if on else (c['button_hover'] if hovered else c['button'])
        draw.box(x0 + x, y0 + y, w, h, fill, 4 * k)
        draw.text_centred(x0 + x + w / 2, y0 + y + h / 2, label, 11 * k, c['text'], w - 6 * k)
        self.hits.append(((x, y, w, h), key))

    def paint_bar(self, x0, y0, w, h, bar, k):
        y = h - bar
        lifted = self.floating or self.popout
        radius = 6 * k if self.floating and not self.popout else 0
        draw.box(x0, y0 + y, w, bar, self.colours['bar'], radius)
        if radius:
            draw.box(x0, y0 + y, w, radius, self.colours['bar'])          # square off the bar's lower corners
        draw.lines(((x0, y0 + y), (x0 + w, y0 + y)), style.BAR_RULE, 1.0)
        if lifted:
            self.hits.append(((0, y, w, bar), ('move',)))     # a lifted board is dragged by its bar
        bh = style.BUTTON_HEIGHT * k
        by = y + (bar - bh) / 2
        # A window of its own shows Blender's small arrows for its hidden regions; the top-right one sits over the bar's end.
        right = w - (style.PAD + (style.POP_ARROW if self.popout else 0)) * k
        from . import popout
        first = ("Dock", ('dock',), 52) if lifted else (("Undock", ('undock',), 60) if popout.possible() else ("Float", ('float',), 52))
        buttons = [first, ("Fit", ('fit',), 40)]
        if self.board.closable and not self.popout:
            buttons.insert(0, ("×", ('close',), 26))           # rightmost
        for label, key, width in buttons:
            bw = width * k
            right -= bw
            self.button(x0, y0, right, by, bw, bh, label, key, k)
            right -= 4 * k
        self.bar_layout = (right, by, bh, k)
        ui = BarUi(self, 'paint', x0, y0, right, by, bh, k)
        self.call(self.board.draw_bar, ui, touch=False)
        right = ui.right
        title_x = (12 if not lifted else 26) * k
        if lifted:
            self.paint_grip(x0 + 10 * k, y0 + y + bar / 2, k)
        draw.text(x0 + title_x, y0 + y + bar / 2 - 4 * k, runtime.title(), 12 * k, self.colours['text'], right - title_x - 6 * k)

    def paint_grip(self, x, y, k):
        """Six dots: the floating board can be dragged by its bar."""
        dots = []
        for col in (0, 1):
            for row in (-1, 0, 1):
                px, py = x + col * 4 * k, y + row * 4 * k
                dots.extend(((px - 1 * k, py), (px + 1 * k, py)))
        draw.lines(tuple(dots), self.colours['faint'], 2.0 * k)

    def paint_side(self, x0, y0, k):
        """The side panel: its background, the divider on its inner edge, and the product's controls in it."""
        sx, sy, sw, sh = self.side_rect
        draw.box(x0 + sx, y0 + sy, sw, sh, self.colours['bar'])
        edge = sx if self.board.side == 'RIGHT' else sx + sw
        draw.lines(((x0 + edge, y0 + sy), (x0 + edge, y0 + sy + sh)), style.BAR_RULE, 1.0)
        grip = style.SIDE_GRIP * k
        self.hits.append(((edge - grip / 2, sy, grip, sh), ('side',)))
        self.side_layout = (self.side_rect, k)
        if self.draws_side():
            ui = controls.ColumnUi(self, 'paint', x0, y0, self.side_rect, k)
            ui.event = {}
            self.call(self.board.draw_side, ui, touch=False)

    def paint_zoom_chip(self, x0, y0, k):
        label = view.zoom_label(self.view[2])
        w, h = draw.text_width(label, 10 * k) + 14 * k, 18 * k
        x, y = self.board_rect[0] + 8 * k, 8 * k
        c = self.colours
        draw.box(x0 + x, y0 + y, w, h, c['chip_hover'] if self.hover == ('zoom100',) else c['chip'], 4 * k)
        draw.text_centred(x0 + x + w / 2, y0 + y + h / 2, label, 10 * k, c['text'])
        self.hits.append(((x, y, w, h), ('zoom100',)))

    def paint_corner(self, x0, y0, w, k):
        """Three short diagonals in the bottom-right corner: drag there to resize."""
        size = style.CORNER * k
        segments = []
        for n in (1, 2, 3):
            d = n * 4 * k
            segments.extend(((x0 + w - 3 * k - d, y0 + 3 * k), (x0 + w - 3 * k, y0 + 3 * k + d)))
        draw.lines(tuple(segments), self.colours['faint'] if self.hover != ('resize',) else self.colours['text'], 1.0)
        self.hits.append(((w - size, 0, size, size), ('resize',)))

    def paint_edge(self, x0, y0, w, h, edge, k):
        """The pop-out's edge or corner under the mouse, lit along the border: dragging there resizes it."""
        a = 24 * k
        parts = {'L': [((2, 2), (2, h - 2))], 'R': [((w - 2, 2), (w - 2, h - 2))], 'B': [((2, 2), (w - 2, 2))],
                 'T': [((2, h - 2), (w - 2, h - 2))],
                 'BL': [((2, 2), (2, a)), ((2, 2), (a, 2))], 'BR': [((w - 2, 2), (w - 2, a)), ((w - 2, 2), (w - a, 2))],
                 'TL': [((2, h - 2), (2, h - a)), ((2, h - 2), (a, h - 2))], 'TR': [((w - 2, h - 2), (w - 2, h - a)), ((w - 2, h - 2), (w - a, h - 2))]}
        segments = []
        for (ax, ay), (bx, by) in parts.get(edge, ()):
            segments += [(x0 + ax, y0 + ay), (x0 + bx, y0 + by)]
        draw.lines(tuple(segments), self.colours['accent'], 2.5 * k)

    def paint_tip(self, x0, y0, w, h, k):
        tip = self.tip()
        if not tip:
            return
        size, line_h, pad = 10 * k, 13 * k, 7 * k
        room = max(40 * k, w - 12 * k)
        lines = draw.wrap(tip, size, room - 2 * pad)
        tw = min(room, max(draw.text_width(line, size) for line in lines) + 2 * pad)
        th = 7 * k + line_h * len(lines)
        top = h - style.BAR_HEIGHT * k - (style.BANNER * k if self.error else 0)     # under the bar, and under the banner
        x, y = max(6 * k, w - tw - 6 * k), top - th - 6 * k
        draw.box(x0 + x, y0 + y, tw, th, self.colours['tip_back'], 4 * k)
        for i, line in enumerate(lines):
            draw.text(x0 + x + 7 * k, y0 + y + th - 13.5 * k - line_h * i, line, size, self.colours['text'], tw - 14 * k)
