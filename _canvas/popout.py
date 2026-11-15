"""Undock: the board in a window of its own, anywhere on the screen, and docking it back by
dropping it on a 3D View.

The window is a Blender window (`wm.window_new` from a 3D View opens a temporary screen holding
just that view) restyled on Windows to have no frame, owned by the main window so it stays above
it. Its 3D View shows nothing but the board. Its title bar moves it, its edges resize it (an
outline follows the mouse and the window takes the new size on release; or, with Window Resize
set to Live, it takes every size, shown only as far as Blender has drawn it, so no black strip
flashes along the moving edge). While it is carried, the 3D View under the mouse offers a dock
target on its sidebar side; letting go there docks the board into that view's sidebar.

Only on Windows; elsewhere Undock floats the board over the viewport instead.
"""
import os
import time

import bpy

from . import draw
from . import compat, prefs, runtime, style
from .runtime import region_of, sessions, ui_scale

DOCK_PAD = (150, 96)                    # the dock target: a small pad, so carrying the window past a view does not dock it
POP_EDGE = 6                            # a pop-out resizes from the outer pixels of its border...
POP_CORNER = 18                         # ... and from its corners, this far along each edge
EDGE_CURSORS = {'L': 'MOVE_X', 'R': 'MOVE_X', 'T': 'MOVE_Y', 'B': 'MOVE_Y',
                'TL': 'SCROLL_XY', 'TR': 'SCROLL_XY', 'BL': 'SCROLL_XY', 'BR': 'SCROLL_XY'}
TARGET = (0.33, 0.56, 0.98)
CLIP_TICK = 0.016          # while a live resize shows only what was drawn: how often that is brought up to date (seconds)...
CLIP_LIMIT = 1.0           # ... and the longest it may stay so, whatever happens
CLIP_SLACK = 4             # a drawn size this close to the window's counts as all of it
CLIP_MIN_ROWS = 40         # a clip left with fewer rows than this is anchored at the bottom-left instead
clips = {}                 # area pointer of a window being resized live -> the time its clip must end by
native = {'on': False}     # Windows' own move loop is carrying a window now

dock = dict(owner=None, area=None, zone=None, armed=False, in_sidebar=False, checked=0.0)


def pop_screen():
    """The pop-out's screen name: one found in a loaded file that no board owns is a stray and is closed."""
    return f"{runtime.title()} Window"


# Window rectangles (screen pixels, y down)

def resized(rect, edge, dx, dy, least, work):
    """A window (x, y, width, height) after its `edge` ('L' 'R' 'T' 'B' or a corner such as 'BR') is dragged by (dx, dy):
    only the named sides move, it never gets smaller than `least` and never leaves the work area (left, top, right, bottom)."""
    x, y, w, h = rect
    left, top, right, bottom = work
    nx, ny, nw, nh = x, y, w, h
    if 'R' in edge:
        nw = min(max(least[0], w + dx), right - x)
    if 'L' in edge:
        nw = min(max(least[0], w - dx), x + w - left)
        nx = x + w - nw
    if 'B' in edge:
        nh = min(max(least[1], h + dy), bottom - y)
    if 'T' in edge:
        nh = min(max(least[1], h - dy), y + h - top)
        ny = y + h - nh
    return (nx, ny, nw, nh)


def clamp(rect, work):
    """Keep a window inside the work area: never larger than it, never past an edge."""
    x, y, w, h = rect
    left, top, right, bottom = work
    w, h = min(w, right - left), min(h, bottom - top)
    return (round(min(max(x, left), right - w)), round(min(max(y, top), bottom - h)), round(w), round(h))


def start_rect(client, anchor):
    """The screen rectangle covering `anchor` (Blender window pixels: x, y up, width, height) of a main window whose
    drawing area is `client` (left, top, width, height on the screen); None when either is unknown."""
    if not client or anchor is None or client[2] <= 0 or client[3] <= 0:
        return None
    left, top, _width, height = client
    x, y, w, h = anchor
    return (left + x, top + height - (y + h), w, h)


def edge_at(size, x, y, edge=POP_EDGE, corner=POP_CORNER):
    """Which resize zone of a pop-out of `size` the point (pixels from its bottom-left) is in: 'L' 'R' 'T' 'B', a corner
    such as 'TL', or None."""
    w, h = size
    if w <= 0 or h <= 0 or not (0 <= x <= w and 0 <= y <= h):
        return None
    if not (x < edge or x > w - edge or y < edge or y > h - edge):
        return None
    across = 'L' if x < corner else 'R' if x > w - corner else ''
    down = 'B' if y < corner else 'T' if y > h - corner else ''
    return (down + across) or None


def dock_zone(area, sidebar, scale=1.0):
    """Where a carried board docks into a 3D View (Blender window pixels, x, y, w, h): a small pad in the middle of its open
    sidebar, or by its right edge when the sidebar is closed. Only the pad docks: passing over the view does nothing.
    (`area` and `sidebar` are (x, y, width, height); sidebar None when closed.)"""
    pw, ph = DOCK_PAD[0] * scale, DOCK_PAD[1] * scale
    if sidebar is not None and sidebar[2] > 1:
        sx, sy, sw, sh = sidebar
        pw = min(pw, max(40 * scale, sw - 24 * scale))
        return (sx + (sw - pw) / 2, sy + (sh - ph) / 2, pw, ph), True
    ax, ay, aw, ah = area
    pw = min(pw, max(40 * scale, aw / 3))
    return (ax + aw - pw - 14 * scale, ay + (ah - ph) / 2, pw, ph), False


# The little of Windows the pop-out needs

class Win32:

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.c, self.w = ctypes, wintypes
        u = self.user32 = ctypes.windll.user32
        u.GetWindowLongPtrW.restype, u.GetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype, u.SetWindowLongPtrW.argtypes = ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        u.GetWindow.restype, u.GetWindow.argtypes = wintypes.HWND, [wintypes.HWND, wintypes.UINT]
        u.MonitorFromWindow.restype, u.MonitorFromWindow.argtypes = wintypes.HANDLE, [wintypes.HWND, wintypes.DWORD]
        u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        u.IsWindow.argtypes = [wintypes.HWND]
        u.SetWindowRgn.restype, u.SetWindowRgn.argtypes = ctypes.c_int, [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        u.CreateWindowExW.restype = wintypes.HWND
        u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        u.DestroyWindow.argtypes = [wintypes.HWND]
        g = self.gdi32 = ctypes.windll.gdi32
        g.CreateRectRgn.restype, g.CreateRectRgn.argtypes = wintypes.HRGN, [ctypes.c_int] * 4
        g.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        g.CombineRgn.restype, g.CombineRgn.argtypes = ctypes.c_int, [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
        u.SendMessageW.restype, u.SendMessageW.argtypes = ctypes.c_ssize_t, [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        u.GetAsyncKeyState.restype, u.GetAsyncKeyState.argtypes = ctypes.c_short, [ctypes.c_int]

    def hwnds(self):
        """This Blender's own windows."""
        c, w, found, pid = self.c, self.w, [], os.getpid()

        def each(hwnd, _lparam):
            owner = w.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, c.byref(owner))
            if owner.value == pid:
                name = c.create_unicode_buffer(64)
                self.user32.GetClassNameW(hwnd, name, 64)
                if name.value == 'GHOST_WindowClass':
                    found.append(int(hwnd))
            return True
        self.user32.EnumWindows(c.WINFUNCTYPE(c.c_bool, w.HWND, w.LPARAM)(each), 0)
        return found

    def owner(self, hwnd):
        return int(self.user32.GetWindow(hwnd, 4) or 0)             # GW_OWNER

    def exists(self, hwnd):
        return bool(hwnd) and bool(self.user32.IsWindow(hwnd))

    def restyle(self, hwnd):
        """No caption, frame or buttons, no taskbar button; owned by the main window, so it stays above it."""
        u = self.user32
        style = u.GetWindowLongPtrW(hwnd, -16) & 0xffffffff
        u.SetWindowLongPtrW(hwnd, -16, (style & ~(0x00C00000 | 0x00040000 | 0x00020000 | 0x00010000 | 0x00080000)) | 0x80000000)
        u.SetWindowLongPtrW(hwnd, -20, ((u.GetWindowLongPtrW(hwnd, -20) & 0xffffffff) & ~0x00040000) | 0x80)
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x2 | 0x1 | 0x4 | 0x10 | 0x20)       # frame changed; no move, size, order or focus change

    def rect(self, hwnd):
        r = self.w.RECT()
        self.user32.GetWindowRect(hwnd, self.c.byref(r))
        return (r.left, r.top, r.right - r.left, r.bottom - r.top)

    def place(self, hwnd, rect):
        x, y, w, h = (round(v) for v in rect)
        self.user32.SetWindowPos(hwnd, None, x, y, w, h, 0x4 | 0x10)                 # no order change, no focus

    def cursor(self):
        p = self.w.POINT()
        self.user32.GetCursorPos(self.c.byref(p))
        return (p.x, p.y)

    def client(self, hwnd):
        """(left, top, width, height) on the screen of a window's drawing area, where Blender's pixels start."""
        size, origin = self.w.RECT(), self.w.POINT(0, 0)
        self.user32.GetClientRect(hwnd, self.c.byref(size))
        self.user32.ClientToScreen(hwnd, self.c.byref(origin))
        return (origin.x, origin.y, size.right - size.left, size.bottom - size.top)

    def work_area(self, hwnd):
        """(left, top, right, bottom) of the usable part of the screen the window is on (the taskbar left out)."""
        c, w = self.c, self.w

        class MONITORINFO(c.Structure):
            _fields_ = [('cbSize', w.DWORD), ('rcMonitor', w.RECT), ('rcWork', w.RECT), ('dwFlags', w.DWORD)]
        info = MONITORINFO()
        info.cbSize = c.sizeof(MONITORINFO)
        self.user32.GetMonitorInfoW(self.user32.MonitorFromWindow(hwnd, 2), c.byref(info))
        r = info.rcWork
        return (r.left, r.top, r.right, r.bottom)

    def outline_open(self, owner):
        """A bare topmost frame of the system's own 'Static' class: where a resize will end. 0 when it cannot be made."""
        return int(self.user32.CreateWindowExW(0x08 | 0x80 | 0x08000000, 'Static', None, 0x80000000 | 0x6, 0, 0, 1, 1,
                                               owner, None, None, None) or 0)

    def outline_place(self, hwnd, rect, thickness=3):
        x, y, w, h = (round(v) for v in rect)
        t = max(1, min(thickness, w // 2, h // 2))
        outer, inner = self.gdi32.CreateRectRgn(0, 0, w, h), self.gdi32.CreateRectRgn(t, t, w - t, h - t)
        self.gdi32.CombineRgn(outer, outer, inner, 4)                  # the ring between the two
        self.gdi32.DeleteObject(inner)
        if not self.user32.SetWindowRgn(hwnd, outer, False):           # the window owns the region once it is set
            self.gdi32.DeleteObject(outer)
        self.user32.SetWindowPos(hwnd, -1, x, y, w, h, 0x10 | 0x40)    # topmost, no focus, shown

    def outline_close(self, hwnd):
        if hwnd:
            self.user32.DestroyWindow(hwnd)

    def clip(self, hwnd, width=None, height=None, left=0, top=0):
        """Show only width x height pixels of the window at (left, top) from its top-left corner (the rest is see-through and
        takes no clicks); no size: all of it again."""
        if width is None:
            self.user32.SetWindowRgn(hwnd, None, False)
            return
        region = self.gdi32.CreateRectRgn(round(left), round(top), round(left) + max(1, round(width)), round(top) + max(1, round(height)))
        if not self.user32.SetWindowRgn(hwnd, region, False):          # the window owns the region once it is set
            self.gdi32.DeleteObject(region)

    def button_down(self):
        """Is the physical left mouse button held?"""
        return bool(self.user32.GetAsyncKeyState(0x01) & 0x8000)       # VK_LBUTTON

    def native_move(self, hwnd):
        """Windows carries the window with its own move loop, the one a title bar starts. It returns when the button is let go
        (at once when it is not held). Blender is blocked meanwhile."""
        self.user32.ReleaseCapture()                                   # the window holds the mouse from the press; the loop needs it free
        return self.user32.SendMessageW(hwnd, 0x00A1, 2, 0)            # WM_NCLBUTTONDOWN, HTCAPTION

    def post_button_up(self, hwnd):
        """The mouse-up the move loop swallowed, posted to the window so Blender sees the release."""
        x, y = self.cursor()
        left, top, _width, _height = self.client(hwnd)
        self.user32.PostMessageW(hwnd, 0x0202, 0, ((y - top) & 0xffff) << 16 | ((x - left) & 0xffff))       # WM_LBUTTONUP


_api = []


def win32():
    """The Windows helper, or None (another system, or it would not load)."""
    if not _api:
        api = None
        if os.name == 'nt':
            try:
                api = Win32()
            except Exception:
                api = None
        _api.append(api)
    return _api[0]


def possible():
    """Can Undock open a window of its own here?"""
    return not bpy.app.background and win32() is not None and bool(prefs.choice('pop_out', True))


# Opening and closing the window

def prepare_area(area):
    """The pop-out's 3D View shows nothing but the board: no header, sidebar, toolbar, gizmos, overlays or objects
    (a heavy scene is not drawn under it at all)."""
    space = area.spaces.active
    for attr in ('show_region_header', 'show_region_ui', 'show_region_toolbar', 'show_region_tool_header',
                 'show_region_asset_shelf', 'show_gizmo'):
        try:
            setattr(space, attr, False)
        except (AttributeError, TypeError, RuntimeError):
            pass
    try:
        space.overlay.show_overlays = False
        space.shading.type = 'WIREFRAME'
    except (AttributeError, TypeError, RuntimeError):
        pass
    for attr in dir(space):
        if attr.startswith('show_object_viewport_'):
            try:
                setattr(space, attr, False)
            except (AttributeError, TypeError, RuntimeError):
                pass


def hide_chrome(area):
    """Blender may bring a region of the new window back while it settles: hide it again (three flags looked at)."""
    space = area.spaces.active
    if space.show_region_header or space.show_region_ui or space.show_region_toolbar:
        prepare_area(area)


def anchor_of(canvas, area):
    """Where the board is now, in its Blender window's pixels (x, y up, width, height), so the window opens over it."""
    host = canvas.host(area)
    if host is None:
        return None
    region, x, y, w, h = host
    return (region.x + x, region.y + y, w, h)


def pop_out(canvas, area):
    """Open the board in a window of its own. False when that cannot be done (the caller floats it instead). Never raises."""
    api = win32()
    if api is None or canvas.popout or canvas.popped or not possible():
        return False
    wm = bpy.context.window_manager
    known = {w.as_pointer() for w in wm.windows}
    before = set(api.hwnds())
    new_win = popup = None
    try:
        home = next((w for w in wm.windows if any(a.as_pointer() == area.as_pointer() for a in w.screen.areas)), None)
        region = region_of(area, 'WINDOW')
        if home is None or region is None:
            return False
        k = ui_scale()
        anchor = anchor_of(canvas, area)
        width, height = (anchor[2], anchor[3]) if anchor else (420 * k, 560 * k)
        with bpy.context.temp_override(window=home, area=area, region=region, screen=home.screen):
            bpy.ops.wm.window_new()
        new_win = next((w for w in wm.windows if w.as_pointer() not in known), None)
        hwnd = next((h for h in api.hwnds() if h not in before), None)
        if new_win is None or hwnd is None:
            raise RuntimeError("Blender made no window")
        api.restyle(hwnd)
        new_win.screen.name = pop_screen()
        main = api.owner(hwnd) or hwnd
        frame = api.rect(main)
        start = start_rect(api.client(main), anchor) or (frame[0] + frame[2] - width - 24, frame[1] + 60, width, height)
        api.place(hwnd, clamp(start, api.work_area(hwnd)))
        parea = next(a for a in new_win.screen.areas if a.type == 'VIEW_3D')
        prepare_area(parea)
        pregion = region_of(parea, 'WINDOW')
        with bpy.context.temp_override(window=new_win, area=parea, region=pregion, screen=new_win.screen):
            runtime.session_op()('INVOKE_DEFAULT')
        popup = sessions.get(parea.as_pointer())
        if popup is None:
            raise RuntimeError("the window started no board")
        popup.popout, popup.hwnd, popup.origin_id, popup.win_ptr = True, hwnd, canvas.area_id, new_win.as_pointer()
        popup.carry_from(canvas)
        popup.fit_pending = False
        canvas.popped = popup.area_id
        runtime.hold(popup)
        canvas.drag = canvas.hover = None
        parea.tag_redraw()
        area.tag_redraw()
        return True
    except Exception as exc:
        print(f"{runtime.title()}: could not open a window of its own, floating over the viewport instead:", exc)
        if popup is not None and popup.alive:
            popup.popout = False
            popup.close()
        if new_win is not None:
            close_window(new_win.as_pointer(), force=True)
        canvas.popped = None
        return False


def close_window(win_ptr, force=False):
    """Close the window that held a pop-out (only a window with the pop-out's screen name, or `force`: the one just opened)."""
    wm = bpy.context.window_manager
    win = next((w for w in wm.windows if w.as_pointer() == win_ptr), None) if wm else None
    if win is not None and len(wm.windows) > 1 and (force or win.screen.name.startswith(pop_screen())):
        try:
            with bpy.context.temp_override(window=win, screen=win.screen, area=win.screen.areas[0]):
                bpy.ops.wm.window_close()
        except (RuntimeError, ReferenceError, IndexError):
            pass
    return None


def schedule_close(win_ptr):
    """Close the window a moment later, outside any event of its own (a hook of its own so tests can stand in for it)."""
    bpy.app.timers.register(lambda: close_window(win_ptr), first_interval=0.05)


def close_strays():
    """Timer: windows with the pop-out's screen name that no board owns (a saved file brought one back) are closed."""
    wm = bpy.context.window_manager
    owned = {c.win_ptr for c in sessions.values() if c.popout}
    for win in list(wm.windows) if wm else ():
        if win.screen.name.startswith(pop_screen()) and win.as_pointer() not in owned:
            close_window(win.as_pointer())
    return None


def return_board(popup):
    """A pop-out ends (docked, closed, or its window went away): its view and what it showed go to the 3D View it was docked
    into, else the one it came from; that view shows the board again; the window closes."""
    if not popup.popout or popup.returned:
        return
    popup.returned = True
    dest_id = popup.dock_to or popup.origin_id
    dest = sessions.get(dest_id)
    if dest is not None and dest is not popup and dest.alive:
        dest.carry_from(popup)
        dest.floating, dest.fit_pending = False, False
        runtime.hold(dest)               # only that view shows the board; the one it came from steps aside
    else:
        runtime.places[dest_id] = dict(view=popup.view, floating=False, float_rect=None)     # the view opens its board with them
    for other in sessions.values():
        if other is not popup and other.popped == popup.area_id:
            other.popped = None
    if dock.get('owner') == popup.area_id:
        target_off()
    schedule_close(popup.win_ptr)
    redraw_all()


def dock_into(popup, area, window=None):
    """Dock a pop-out into the sidebar of the 3D View `area`. True when it did; never raises."""
    try:
        if area is None or area.type != 'VIEW_3D':
            return False
        space = area.spaces.active
        if not space.show_region_ui:
            space.show_region_ui = True
        ui = region_of(area, 'UI')
        if ui is not None:
            compat.set_active_tab(ui, runtime.tab())
        if area.as_pointer() not in sessions and ui is not None:
            open_session(window or (find_area(area.as_pointer()) or (None, None))[0], area, ui)
        popup.dock_to = area.as_pointer()
        popup.close()
        area.tag_redraw()
        return True
    except Exception as exc:
        print(f"{runtime.title()}: could not dock the board into that view:", exc)
        popup.dock_to = None
        return False


def open_session(window, area, ui):
    """That view has no board session yet: open one now, so a docked board lands there and nowhere else
    (a hook of its own so tests can stand in for it)."""
    if window is not None:
        with bpy.context.temp_override(window=window, area=area, region=ui, screen=window.screen):
            runtime.session_op()('INVOKE_DEFAULT')


def dock_all(except_window=None):
    """Every pop-out goes back to its sidebar and its window closes now (before a save, or when the add-on is turned off)."""
    for canvas in list(sessions.values()):
        if canvas.popout and canvas.win_ptr != except_window:
            win_ptr = canvas.win_ptr
            canvas.close()
            close_window(win_ptr)


def resave_from_main(as_new=False):
    """Timer: Ctrl+S was pressed in a pop-out, which docked first; save from the main window, as the key asked."""
    wm = bpy.context.window_manager
    win = next((w for w in wm.windows if not w.screen.name.startswith(pop_screen())), None) if wm else None
    if win is not None:
        try:
            with bpy.context.temp_override(window=win, screen=win.screen, area=win.screen.areas[0]):
                (bpy.ops.wm.save_as_mainfile if as_new else bpy.ops.wm.save_mainfile)('INVOKE_DEFAULT')
        except (RuntimeError, ReferenceError, IndexError):
            pass
    return None


def redraw_all():
    wm = bpy.context.window_manager
    for win in wm.windows if wm else ():
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# Carrying the window: move, resize, and the dock target

def begin_carry(popup, kind, edge='BR', api=None):
    """The title bar (kind 'pop_move') or an edge ('pop_resize', `edge`) was grabbed: remember the cursor and the window
    in screen pixels. A move also starts the dock target; a resize shows an outline."""
    api = api or win32()
    if api is None or not popup.hwnd:
        return None
    carry = dict(kind=kind, edge=edge, cursor=api.cursor(), rect=api.rect(popup.hwnd), work=api.work_area(popup.hwnd), last=None)
    if kind == 'pop_resize' and prefs.choice('resize_mode', 'OUTLINE') != 'LIVE':
        try:
            popup.outline = api.outline_open(popup.hwnd)
            if popup.outline:
                api.outline_place(popup.outline, carry['rect'])
        except Exception:
            popup.outline = 0
    else:
        target_off()
        dock.update(owner=popup.area_id, area=None, zone=None, armed=False, in_sidebar=False, checked=0.0)
    return carry


def follow(popup, carry, cursor=None, api=None):
    """The mouse moved while carrying: the window (or its outline) follows by the distance the cursor travelled."""
    api = api or win32()
    if api is None or not popup.hwnd or not carry:
        return None
    cx, cy = cursor or api.cursor()
    x, y, w, h = carry['rect']
    dx, dy = cx - carry['cursor'][0], cy - carry['cursor'][1]
    k = ui_scale()
    least = (style.FLOAT_MIN[0] * k, style.FLOAT_MIN[1] * k)
    rect = (x + dx, y + dy, w, h) if carry['kind'] == 'pop_move' else resized((x, y, w, h), carry['edge'], dx, dy, least, carry['work'])
    rect = clamp(rect, carry['work'])
    if rect != carry['last']:
        carry['last'] = rect
        if carry['kind'] == 'pop_resize' and popup.outline:
            api.outline_place(popup.outline, rect)           # the window itself keeps its size until the mouse is let go
        else:
            if carry['kind'] == 'pop_resize':
                clip_to_shown(popup, api, rect)              # a live resize: show only what Blender has drawn
            api.place(popup.hwnd, rect)
    return rect


def finish_carry(popup, carry, api=None, windows=None, cursor=None):
    """The mouse was let go: a resize takes its size now; a move docks when it was let go on a dock target."""
    api = api or win32()
    if not carry:
        return False
    if carry['kind'] == 'pop_resize':
        end_outline(popup, api)
        if api is not None and carry['last']:
            api.place(popup.hwnd, carry['last'])
        return False
    if dock.get('owner') != popup.area_id:
        return False
    update_target(popup, force=True, cursor=cursor, api=api, windows=windows)
    target = dock['area'] if dock['armed'] else None
    target_off()
    if target is None:
        return False
    found = find_area(target, windows)
    return dock_into(popup, found[1], found[0]) if found else False


def cancel_carry(popup, carry, api=None):
    api = api or win32()
    end_outline(popup, api)
    target_off()
    if api is not None and carry and popup.hwnd:
        api.place(popup.hwnd, carry['rect'])


def native_carry(popup, carry, api=None):
    """System Window Move (a preference, off by default): Windows carries the window with its own move loop, as smooth as any
    window. The dock target cannot follow the cursor while it runs: the dock is decided once, when the loop returns, as a
    release decides it. True when the move was carried out here (the caller starts no drag); False leaves the ordinary drag
    to run (no button held, an error, or the loop ended with the button still down, as Esc does). Never raises.

    Blender keeps handling events inside the loop, so the press that started it can reach the board again: that second call
    must do nothing (True), because starting the loop again releases the mouse from the first one, and Windows then puts
    the window back where it was."""
    if native['on']:
        return True
    api = api or win32()
    if api is None or not carry or carry['kind'] != 'pop_move' or not popup.hwnd or not prefs.choice('native_move', False):
        return False
    try:
        if not api.button_down():
            return False
        native['on'] = True
        try:
            api.native_move(popup.hwnd)
        finally:
            native['on'] = False
    except Exception as exc:
        print(f"{runtime.title()}: the system move loop could not run, dragging the usual way:", exc)
        target_off()
        return False
    if api.button_down():
        target_off()
        return False
    try:
        rect = api.rect(popup.hwnd)
        kept = clamp(rect, carry['work'])                     # the system loop does not keep the window inside the work area
        if kept != rect:
            api.place(popup.hwnd, kept)
        finish_carry(popup, carry, api)                       # docks when it was let go on a dock target
    except Exception as exc:
        print(f"{runtime.title()}: could not finish the carried window:", exc)
    repair_button(popup, api)
    return True


def repair_button(popup, api):
    """The move loop swallowed the mouse-up: tell Blender the button is up (its own event when it can make one, else the
    window message)."""
    try:
        wm = bpy.context.window_manager
        window = next((w for w in wm.windows if w.as_pointer() == popup.win_ptr), None) or bpy.context.window
        left, top, _width, height = api.client(popup.hwnd)
        cx, cy = api.cursor()
        window.event_simulate(type='LEFTMOUSE', value='RELEASE', x=cx - left, y=height - (cy - top))
    except Exception:
        try:
            api.post_button_up(popup.hwnd)
        except Exception:
            pass


def end_outline(popup, api=None):
    api = api or win32()
    outline, popup.outline = popup.outline, 0
    if outline and api is not None:
        try:
            api.outline_close(outline)
        except Exception:
            pass


def clip_to_shown(popup, api, rect):
    """The window is about to grow: until Blender has drawn at the new size, show only what it has drawn. Windows paints the
    newly exposed strip black and Blender learns the new size a frame or more later, so without this a black bar flashes along
    the moving edge at every step of a drag. Host.shown_size is the size last drawn."""
    shown = popup.shown_size
    if not shown or (rect[2] <= shown[0] and rect[3] <= shown[1]):
        return
    try:
        clip_now(popup, api, rect[2], rect[3], shown)
    except Exception:
        return
    first = popup.area_id not in clips
    clips[popup.area_id] = time.monotonic() + CLIP_LIMIT
    if first and not bpy.app.background:
        bpy.app.timers.register(lambda: unclip_step(popup), first_interval=0.0)


def unclip_step(popup, api=None, now=None):
    """Timer while a window is clipped: show each size as soon as it has been drawn, and all of it once Blender has caught up
    (or after CLIP_LIMIT seconds, whatever happens). Returns the next interval, or None when done."""
    api = api or win32()
    now = time.monotonic() if now is None else now
    try:
        until = clips.get(popup.area_id)
        if api is None or until is None or not popup.alive or not popup.hwnd or not api.exists(popup.hwnd):
            clips.pop(popup.area_id, None)
            return None
        _x, _y, w, h = api.rect(popup.hwnd)
        shown = popup.shown_size or (0, 0)
        if (shown[0] >= w - CLIP_SLACK and shown[1] >= h - CLIP_SLACK) or now > until:
            api.clip(popup.hwnd)
            clips.pop(popup.area_id, None)
            return None
        clip_now(popup, api, w, h, shown)
    except Exception:
        clips.pop(popup.area_id, None)
        return None
    return CLIP_TICK


def clip_now(popup, api, width, height, shown):
    """Clip a width x height window to the picture drawn (`shown`). Right after a resize Windows keeps the old picture at the
    window's top-left; once Blender has drawn its old size into the resized window it sits at the bottom-left. Only the rows
    both agree on are shown."""
    sw, sh = min(shown[0], width), min(shown[1], height)
    top, bottom = max(0, height - sh), min(height, sh)
    if bottom - top >= CLIP_MIN_ROWS:
        api.clip(popup.hwnd, sw, bottom - top, 0, top)
    else:
        api.clip(popup.hwnd, sw, sh, 0, height - sh)


def find_area(pointer, windows=None):
    """(window, area) for an area pointer, or None."""
    for win in (windows if windows is not None else bpy.context.window_manager.windows):
        for area in win.screen.areas:
            if area.as_pointer() == pointer:
                return win, area
    return None


def update_target(popup, force=False, cursor=None, api=None, windows=None):
    """Which 3D View of the main window is the cursor over, and is it on that view's dock target? At most 30 lookups a second."""
    if dock.get('owner') != popup.area_id:
        return
    now = time.monotonic()
    if not force and now - dock['checked'] < 0.033:
        return
    dock['checked'] = now
    api = api or win32()
    found = None
    try:
        main = api.owner(popup.hwnd)
        home = find_area(popup.origin_id, windows)
        if main and home is not None:
            left, top, _width, height = api.client(main)
            cx, cy = cursor or api.cursor()
            bx, by = cx - left, height - (cy - top)                  # Blender's pixels: from the window's bottom-left
            for area in home[0].screen.areas:
                if area.type == 'VIEW_3D' and area.x <= bx < area.x + area.width and area.y <= by < area.y + area.height:
                    ui = region_of(area, 'UI')
                    sidebar = (ui.x, ui.y, ui.width, ui.height) if ui is not None and area.spaces.active.show_region_ui else None
                    zone, in_sidebar = dock_zone((area.x, area.y, area.width, area.height), sidebar, ui_scale())
                    armed = zone[0] <= bx < zone[0] + zone[2] and zone[1] <= by < zone[1] + zone[3]
                    found = (area.as_pointer(), zone, armed, in_sidebar)
                    break
    except (OSError, ReferenceError, ValueError, TypeError):
        found = None                           # the window or the view went away while it was carried
    area, zone, armed, in_sidebar = found if found else (None, None, False, False)
    if (area, zone, armed) != (dock['area'], dock['zone'], dock['armed']):
        dock.update(area=area, zone=zone, armed=armed, in_sidebar=in_sidebar)
        popup.dock_armed = armed
        redraw_all()


def target_off():
    owner = dock.get('owner')
    had = dock.get('area') is not None
    dock.update(owner=None, area=None, zone=None, armed=False, in_sidebar=False)
    for canvas in sessions.values():
        canvas.dock_armed = False
    if had or owner is not None:
        try:
            redraw_all()
        except (AttributeError, RuntimeError):
            pass


def draw_target(area, region):
    """In the 3D View under a carried board: its dock target (the sidebar, or a strip on the right), brighter and the
    whole view framed when letting go would dock there."""
    if dock.get('area') is None or area.as_pointer() != dock['area'] or dock['zone'] is None:
        return
    zx, zy, zw, zh = dock['zone']
    armed = dock['armed']
    k = ui_scale()
    draw.begin()
    try:
        if region.type == ('UI' if dock['in_sidebar'] else 'WINDOW'):
            x, y = zx - region.x, zy - region.y
            draw.box(x, y, zw, zh, (*TARGET, 0.7 if armed else 0.3), 8 * k)
            draw.outline(x, y, zw, zh, (*TARGET, 1.0 if armed else 0.75), 8 * k, 2.0)
            cx, cy = x + zw / 2, y + zh / 2
            draw.text_centred(cx, cy + 8 * k, "Dock here", 13 * k, (1, 1, 1, 1), zw - 8 * k)
            draw.text_centred(cx, cy - 10 * k, "let go on this pad" if not armed else "let go to dock", 9 * k, (1, 1, 1, 0.8), zw - 8 * k)
        if armed and region.type == 'WINDOW':
            draw.outline(2, 2, region.width - 4, region.height - 4, (*TARGET, 0.9), 0, 3.0)
    finally:
        draw.end()


def draw_banner(x, y, w, h, k):
    """On the carried board itself while it is over a dock target: what letting go does."""
    draw.box(x, y, w, h, (*TARGET, 0.34))
    draw.outline(x, y, w, h, (*TARGET, 1.0), 0, 3.0)
    draw.text_centred(x + w / 2, y + h / 2, "Release to dock in this view", 13 * k, (1, 1, 1, 1), w - 16 * k)
