"""A floating board that may cross editors (the Float Across Editors preference): the draw handlers that put it over the other
editors of its window, and over the regions every area paints on top of its main one, present only while a board crosses.
registry.py sets `draw` (its draw callback). session.open_where_shown calls sync() every tick, and the session calls it
the moment a board starts or stops hanging out of its viewport."""
import bpy

from . import runtime
from .runtime import area_by_pointer, region_of

# Area type -> the space class that takes its draw handlers (the Timeline is a Dope Sheet).
EDITORS = {'IMAGE_EDITOR': 'SpaceImageEditor', 'NODE_EDITOR': 'SpaceNodeEditor', 'SEQUENCE_EDITOR': 'SpaceSequenceEditor',
           'CLIP_EDITOR': 'SpaceClipEditor', 'DOPESHEET_EDITOR': 'SpaceDopeSheetEditor', 'GRAPH_EDITOR': 'SpaceGraphEditor',
           'NLA_EDITOR': 'SpaceNLA', 'TEXT_EDITOR': 'SpaceTextEditor', 'OUTLINER': 'SpaceOutliner',
           'PROPERTIES': 'SpaceProperties', 'SPREADSHEET': 'SpaceSpreadsheet'}
AREAS = frozenset({'VIEW_3D', *EDITORS})
# Blender paints these after an area's main region, over it: a crossing board is drawn in them too, so it lies on top.
OVERLAYS = ('HEADER', 'TOOL_HEADER', 'TOOLS', 'TOOL_PROPS', 'UI', 'HUD', 'FOOTER', 'NAVIGATION_BAR', 'CHANNELS',
            'ASSET_SHELF', 'ASSET_SHELF_HEADER', 'PREVIEW', 'EXECUTE')
DRAWN_REGIONS = frozenset({'WINDOW', *OVERLAYS})

draw = None               # the draw callback (registry.draw_board)
handlers = {}             # (space class name, region type) -> draw handler
missing = set()           # (space class name, region type) this Blender does not have: tried once, never again


def drawn_over(areas, x, y):
    """Is the window point (x, y) over a region a crossing board is drawn in: an editor's main region, or a header, toolbar,
    sidebar... painted over it? Where it is not (between editors, over a kind of editor it is not drawn in), the mouse belongs
    to what is there."""
    for area in areas:
        if area.type not in AREAS:
            continue
        for region in area.regions:
            if (region.type in DRAWN_REGIONS and region.width > 1 and region.height > 1
                    and region.x <= x < region.x + region.width and region.y <= y < region.y + region.height):
                return True
    return False


def roaming_hosts():
    """The boards hanging out of their own viewport now. A board that may cross editors but sits inside its viewport needs
    no handlers in the others."""
    return [host for host in runtime.sessions.values()
            if host.alive and host.reach is not None and host.crossing and host.roaming()]


def wanted():
    """The handlers needed now: none while no board crosses editors."""
    if not roaming_hosts():
        return []
    keys = [(name, region) for name in EDITORS.values() for region in ('WINDOW', *OVERLAYS)]
    keys += [('SpaceView3D', region) for region in OVERLAYS if region != 'UI']     # the 3D View's main and sidebar regions have their own
    return keys


def sync():
    want = set(wanted())
    for key in [k for k in handlers if k not in want]:
        try:
            getattr(bpy.types, key[0]).draw_handler_remove(handlers[key], key[1])
        except (RuntimeError, ValueError, ReferenceError, AttributeError):
            pass
        del handlers[key]
    if draw is None:
        return
    for key in want:
        if key in handlers or key in missing:
            continue
        try:
            handlers[key] = getattr(bpy.types, key[0]).draw_handler_add(draw, (), key[1], 'POST_PIXEL')
        except (RuntimeError, ValueError, TypeError, AttributeError):
            missing.add(key)              # this kind of editor has no such region (Blender says TypeError)


def clear():
    for key in list(handlers):
        try:
            getattr(bpy.types, key[0]).draw_handler_remove(handlers[key], key[1])
        except (RuntimeError, ValueError, ReferenceError, AttributeError):
            pass
    handlers.clear()


def paint_over(host, area, region):
    """Paint a crossing board in another area's region: placed by where it floats in its own viewport."""
    screen = getattr(area, 'id_data', None)
    home = area_by_pointer(screen, host.area_id) if screen is not None else None
    window = region_of(home, 'WINDOW') if home is not None else None
    if window is None or host.float_rect is None:
        return
    fx, fy, fw, fh = host.float_rect
    x0, y0 = window.x + fx - region.x, window.y + fy - region.y
    if x0 >= region.width or y0 >= region.height or x0 + fw <= 0 or y0 + fh <= 0:
        return
    host.paint_board(x0, y0, fw, fh)
