"""This copy's state. Every add-on bundles its own copy of OEBS Canvas, so the board class, the open boards and the
remembered places here belong to that add-on alone; two add-ons never share or overwrite each other's."""
import contextlib

import bpy

board_class = None        # the Board subclass this copy serves: bound at import (bind) or by register_board
registered = False        # register_board has run for board_class (and unregister_board not since)
sessions = {}             # area pointer -> Host
places = {}               # area pointer -> dict(view, floating, float_rect): a board closed and opened again comes back as it was
holder = {'id': None}     # the one view whose board is on screen; every other view's tab says where it is
tab_seen = {}             # area pointer -> time the product's panel last drew (for Blender without active_panel_category)
capturing = {'on': False}  # the viewport is being drawn off screen (a snapshot): boards stay out of it
dismissed = {}            # area pointer -> what its board was dismissed for (Board.dismiss_token): closed while that holds
replaced = {}             # area pointer -> the board closed to open a fresh one there (Board.should_replace), until it opens
open_errors = {}          # area pointer -> the last error opening a board there (printed once, not on every tick)


def prefix():
    return board_class.prefix


def tab():
    return board_class.tab


def title():
    return board_class.title or board_class.tab


def bind(cls):
    """Bind a product's Board class to this copy of OEBS Canvas when its module is imported, before register_board: boards
    (Hosts) can be made from then on without the add-on registered, as a product's own tests do. While another copy's
    board is registered it keeps serving; the newer copy takes over when it registers. Returns the class (a decorator)."""
    global board_class
    if not registered:
        board_class = cls
    return cls


def window_mouse(event, x, y):
    """Where the mouse is, for a drag's moves: the event's window pixels; for an event made by a script or a test without
    them, the region point (x, y) the caller passes. The moves come out the same: the region's offset cancels."""
    mx, my = getattr(event, 'mouse_x', None), getattr(event, 'mouse_y', None)
    return (x, y) if mx is None or my is None else (mx, my)


def session_op():
    """The product's session operator (bpy.ops.<prefix>.<Board.session_name>)."""
    return getattr(ops(), getattr(board_class, 'session_name', 'session'))


def ops():
    """This product's operators: bpy.ops.<prefix>."""
    return getattr(bpy.ops, board_class.prefix)


def shown():
    """The host that shows the board now, or None."""
    host = sessions.get(holder['id'])
    return host if host is not None and host.alive else None


def hold(host):
    """This host shows the board from now on; any other view's board steps aside."""
    holder['id'] = host.area_id


def forget():
    """A new file: the views and windows belong to the file being closed."""
    places.clear()
    tab_seen.clear()
    dismissed.clear()
    replaced.clear()
    open_errors.clear()
    holder['id'] = None


def ui_scale():
    return bpy.context.preferences.system.ui_scale or 1.0          # 0 while Blender has no window


_held = {'depth': 0, 'scale': None}      # scale_held(): how many holds are open, and the scale read in them


@contextlib.contextmanager
def scale_held():
    """While held (a paint, a modal event), board_scale() is read once and reused: every board point goes through it, and
    reading a preference costs far more than the arithmetic after it. It is read afresh on the next hold, and by every call
    outside one, so a changed preference shows on the next paint or event. A product with its own modal() or paint holds it
    the same way."""
    _held['depth'] += 1
    try:
        yield
    finally:
        _held['depth'] -= 1
        if not _held['depth']:
            _held['scale'] = None


def board_scale(size=None):
    """Pixels per unit of the board's own frame and content: Blender's Resolution Scale times the product's Interface Size
    preference, or `size` (an interface size of the product's own), held to 0.6 to 2.5. Blender's own parts (the sidebar's
    tab column and resize edge, a window's border) keep ui_scale(). Read once while scale_held()."""
    held = _held['depth'] > 0 and size is None
    if held and _held['scale'] is not None:
        return _held['scale']
    if size is None:
        asked = getattr(board_class, 'interface_size', None)
        if asked is not None:
            size = asked()
        else:
            from . import prefs
            size = prefs.choice('interface_size', 1.0)
    try:
        size = float(size)
    except (TypeError, ValueError):
        size = 1.0
    scale = ui_scale() * min(2.5, max(0.6, size))
    if held:
        _held['scale'] = scale
    return scale


def region_of(area, kind):
    return next((r for r in area.regions if r.type == kind), None)


def area_by_pointer(screen, pointer):
    return next((a for a in screen.areas if a.as_pointer() == pointer), None)


def redraw_area(pointer):
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        area = area_by_pointer(window.screen, pointer)
        if area is not None:
            area.tag_redraw()


def redraw_views():
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
