"""The controls' input handler. The host asks it first about every event on the board (Host.inputs), lets it carry its own
drags, and lets it paint over the bar. In order it handles:
1. an open menu (every event while one is open);
2. a focused text field (typing; a click elsewhere commits);
3. a press on a slider (a 'ui_slider' drag);
4. a right-click on the board area (the context menu);
5. Shift+A over the board (the add menu, when the board has one);
6. the wheel over a list.
It also shows toasts (Board.toast)."""
import time

import bpy

from . import draw, menus, runtime

MOUSE = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE',
         'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE',
         'WINDOW_DEACTIVATE', 'TIMER', 'TIMER_REPORT', 'TIMERREGION', 'NONE'}
MOVES = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
WHEEL_UP = {'WHEELUPMOUSE', 'WHEELINMOUSE'}
WHEEL_DOWN = {'WHEELDOWNMOUSE', 'WHEELOUTMOUSE'}
TOAST_FONT = 11
RUNNING = {'RUNNING_MODAL'}


def of(host):
    """The host's controls input handler, or None."""
    return next((handler for handler in getattr(host, 'inputs', ()) if isinstance(handler, Input)), None)


def toast(host, text, seconds=2.0):
    """Show `text` near the bottom of the board for `seconds` (Board.toast)."""
    handler = of(host)
    if handler is not None:
        handler.show_toast(text, seconds)


class Input:

    def __init__(self, host):
        self.host = host
        self.controls = {}        # key -> what the last paint recorded about it (kind, rect, ...)
        self.recording = {}       # filled during a paint, becomes `controls` when the paint ends
        self.scroll = {}          # list key -> the first row shown
        self.focus = None         # a focused text field: dict(key, text, caret, original, all)
        self.toast = None         # dict(text, until)

    # What the controls tell it (ui.py)

    def register(self, key, **info):
        self.recording[key] = info

    def scroll_of(self, key, count, rows):
        top = max(0, min(self.scroll.get(key, 0), max(0, count - rows)))
        self.scroll[key] = top
        return top

    def focus_field(self, key, text):
        self.focus = dict(key=key, text=text, caret=len(text), original=text, all=False)
        self.captured()
        self.host.touch()

    def captured(self):
        """An open menu or a focused field takes every event, on the board or off it (Host.capture)."""
        self.host.capture = getattr(self.host, 'menu', None) is not None or self.focus is not None

    def open_menu(self, builds, anchor, title=None):
        menu = menus.Menu(builds, anchor, title)
        self.host.menu = menu
        self.captured()
        self.host.touch()
        return menu

    def show_toast(self, text, seconds=2.0):
        self.toast = dict(text=str(text), until=time.monotonic() + seconds)
        self.host.touch()
        if not bpy.app.background:
            host = self.host

            def gone():
                host.touch()
                runtime.redraw_area(host.area_id)
            bpy.app.timers.register(gone, first_interval=seconds + 0.05)
        runtime.redraw_area(self.host.area_id)

    def toast_text(self, now=None):
        """The toast showing now, or None once its time is over."""
        if self.toast is None:
            return None
        now = time.monotonic() if now is None else now
        if now >= self.toast['until']:
            self.toast = None
            return None
        return self.toast['text']

    # The host's calls (C-route)

    def handle(self, session, context, area, event, x, y):
        host = self.host
        if getattr(host, 'menu', None) is not None:
            return self.on_menu(event, x, y)
        if self.focus is not None:
            taken = self.on_typing(context, event, x, y)
            if taken is not None:
                return taken
        kind, value = event.type, event.value
        if kind == 'LEFTMOUSE' and value == 'PRESS':
            key = host.hit(x, y)
            info = self.controls.get(key)
            if info is not None and info.get('kind') == 'slider':
                drag = session.begin_drag(context, host, event, 'ui_slider', x, y, cursor='MOVE_X')
                drag.update(owner=self, key=key, start=getattr(info['owner'], info['attr']), width=info['rect'][2])
                return RUNNING
            return None
        if kind == 'RIGHTMOUSE' and value == 'PRESS' and host.hit(x, y) == ('board',):
            builders = host.menu_builders('context') if hasattr(host, 'menu_builders') else []
            if builders:
                self.open_menu(builders, (x, y))
                return RUNNING
            return None
        if kind == 'A' and value == 'PRESS' and event.shift and not (event.ctrl or event.alt or event.oskey):
            if getattr(host.board, 'add_menu', None) is not None and hasattr(host, 'menu_builders'):
                builders = host.menu_builders('add')
                if builders:
                    self.open_menu(builders, (x, y))
                    return RUNNING
            return None
        if kind in WHEEL_UP | WHEEL_DOWN:
            for key, info in self.controls.items():
                if info.get('kind') == 'list' and _inside(info['rect'], x, y):
                    step = -1 if kind in WHEEL_UP else 1
                    self.scroll[key] = max(0, min(self.scroll.get(key, 0) + step, max(0, info['count'] - info['rows'])))
                    host.touch()
                    return RUNNING
        return None

    def drag(self, session, context, area, event, x, y, drag):
        if drag.get('kind') != 'ui_slider':
            return None
        host = self.host
        kind, value = event.type, event.value
        if (kind == 'ESC' and value == 'PRESS') or (kind == 'RIGHTMOUSE' and value == 'PRESS'):
            host.run_ui_event(drag['key'], value=drag['start'])
            session.end_drag(context, host)
        elif kind == 'LEFTMOUSE' and value == 'RELEASE':
            session.end_drag(context, host)
        elif kind in MOVES:
            dx = runtime.window_mouse(event, x, y)[0] - drag['mouse'][0]          # the region point for a script's event
            host.run_ui_event(drag['key'], drag=dx, ctrl=bool(event.ctrl), start=drag['start'])
        area.tag_redraw()
        return RUNNING

    def paint(self, x0, y0, w, h, k):
        self.controls, self.recording = self.recording, {}
        host = self.host
        colours = host.colours
        text = self.toast_text()
        if text:
            bx, by, bw, bh = host.board_rect
            size = TOAST_FONT * k
            tw = min(bw - 16 * k, draw.text_width(text, size) + 24 * k)
            th = 26 * k
            tx, ty = bx + (bw - tw) / 2, by + 18 * k
            draw.box(x0 + tx, y0 + ty, tw, th, colours['tip_back'], 6 * k)
            draw.text_centred(x0 + tx + tw / 2, y0 + ty + th / 2, text, size, colours['text'], tw - 12 * k)
        menu = getattr(host, 'menu', None)
        if menu is not None:
            host.call(self.lay_out_menu, menu, k, touch=False)
            menu.paint(x0, y0, k, colours)

    # An open menu

    def lay_out_menu(self, menu, k):
        menu.collect()
        menu.layout(k, self.host.size)
        child = menu
        while child.child is not None:                      # open submenus are laid out again beside their entries
            row = next((r for r in child.rows if r['key'] == child.child_key), None)
            if row is None:
                child.child = child.child_key = None
                break
            child.child.collect()
            child.child.layout(k, self.host.size)
            child = child.child

    def close_menu(self):
        self.host.menu = None
        self.captured()
        self.host.touch()

    def choose(self, menu, key):
        """Run `menu`'s builds again with `key` chosen, then close the whole menu."""
        root = self.host.menu
        self.host.call(menu.collect, 'event', key)
        if self.host.menu is root:            # a branch may have opened another menu: leave that one open
            self.close_menu()

    def on_menu(self, event, x, y):
        host = self.host
        root = host.menu
        k = runtime.board_scale() if hasattr(runtime, 'board_scale') else runtime.ui_scale()
        if not root.rows:
            self.lay_out_menu(root, k)
        kind, value = event.type, event.value
        if kind in MOVES:
            menu, row = root.row_at(x, y)
            if menu is not None:
                menu.highlight = row['key'] if row else None
                if row is not None and row['kind'] == 'submenu':
                    menu.open_child(row, k, host.size)
                elif row is not None and menu.child is not None and menu.child_key != row['key']:
                    menu.child = menu.child_key = None
                host.touch()
            return RUNNING
        if value == 'PRESS' and kind in {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE'}:
            menu, row = root.row_at(x, y)
            if menu is None:
                self.close_menu()                 # a click outside closes the menu and goes no further
            elif row is not None and row['kind'] == 'item' and kind == 'LEFTMOUSE':
                self.choose(menu, row['key'])
            elif row is not None and row['kind'] == 'submenu':
                menu.open_child(row, k, host.size)
                host.touch()
            return RUNNING
        if value == 'PRESS' and kind in {'UP_ARROW', 'DOWN_ARROW'}:
            root.step(-1 if kind == 'UP_ARROW' else 1)
            host.touch()
            return RUNNING
        if value == 'PRESS' and kind in {'RET', 'NUMPAD_ENTER'}:
            menu = root.menus()[-1]
            row = next((r for r in menu.selectable() if r['key'] == menu.highlight), None)
            if row is not None and row['kind'] == 'item':
                self.choose(menu, row['key'])
            elif row is not None:
                menu.open_child(row, k, host.size)
                host.touch()
            return RUNNING
        if value == 'PRESS' and kind == 'ESC':
            self.close_menu()
            return RUNNING
        return RUNNING                            # everything else waits while the menu is open

    # A focused text field

    def on_typing(self, context, event, x, y):
        focus = self.focus
        host = self.host
        kind, value = event.type, event.value
        if kind == 'LEFTMOUSE' and value == 'PRESS':
            if host.hit(x, y) == focus['key']:
                return RUNNING                    # a click in the field keeps typing
            self.commit()
            return None                           # the click goes on to whatever it was on
        if kind in MOUSE:
            return None
        if value != 'PRESS':
            return RUNNING
        text, caret = focus['text'], focus['caret']
        if kind in {'RET', 'NUMPAD_ENTER'}:
            self.commit()
        elif kind == 'ESC':
            self.focus = None
            self.captured()
            host.touch()
        elif kind == 'A' and event.ctrl:
            focus['all'] = True
        elif kind == 'C' and event.ctrl:
            context.window_manager.clipboard = text
        elif kind == 'V' and event.ctrl:
            self.insert(context.window_manager.clipboard.replace('\n', ' ').replace('\r', ''))
        elif kind == 'BACK_SPACE':
            if focus['all']:
                focus.update(text='', caret=0, all=False)
            elif caret > 0:
                focus.update(text=text[:caret - 1] + text[caret:], caret=caret - 1)
        elif kind == 'DEL':
            if focus['all']:
                focus.update(text='', caret=0, all=False)
            else:
                focus['text'] = text[:caret] + text[caret + 1:]
        elif kind == 'LEFT_ARROW':
            focus.update(caret=max(0, caret - 1), all=False)
        elif kind == 'RIGHT_ARROW':
            focus.update(caret=min(len(text), caret + 1), all=False)
        elif kind == 'HOME':
            focus.update(caret=0, all=False)
        elif kind == 'END':
            focus.update(caret=len(text), all=False)
        elif event.unicode and event.unicode.isprintable() and not (event.ctrl or event.alt or event.oskey):
            self.insert(event.unicode)
        host.touch()
        return RUNNING                            # every other key belongs to the field while it has the focus

    def insert(self, piece):
        focus = self.focus
        if focus['all']:
            focus.update(text='', caret=0, all=False)
        text, caret = focus['text'], focus['caret']
        focus.update(text=text[:caret] + piece + text[caret:], caret=caret + len(piece))

    def commit(self):
        focus, self.focus = self.focus, None
        self.captured()
        if focus is not None:
            self.host.run_ui_event(focus['key'], text=focus['text'])
            self.host.touch()


def _inside(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh
