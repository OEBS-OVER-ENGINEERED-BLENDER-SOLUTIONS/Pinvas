"""register_board / unregister_board: everything Blender needs for a product's board, named by its prefix."""
import traceback

import bpy
from bpy.app.handlers import persistent

from . import __version__, board as board_module, draw, popout, roam, runtime, session
from .layer import store as layer_store

_registered = {'classes': (), 'handlers': []}
_reported = set()
COPY = 'oebs_canvas_copy'  # the key of the entry that says which copy of OEBS Canvas wrote it (owner is the product's)
_token = object()        # this copy of OEBS Canvas: tells its own driver_namespace entry from another copy's


def draw_board():
    """Draw callback for the 3D View's sidebar and viewport regions: paint the board if it lives in this one. A broken
    draw prints its traceback once and draws nothing, never repeating on every redraw."""
    context = bpy.context
    area, region = context.area, context.region
    if area is None or region is None or runtime.capturing['on']:
        return
    home = area.as_pointer()
    try:
        with runtime.scale_held():                                   # one read of the interface scale for the whole draw
            if region.type == 'WINDOW' and area.type == 'VIEW_3D':
                runtime.board_class.draw_viewport(area, region)      # Board.draw_viewport: marks in the scene, under the board
            for host in sorted(runtime.sessions.values(), key=lambda each: each.area_id != home):     # this view's own board first
                host.draw_in(area, region)                           # then the boards crossing over it, on top
            if popout.dock['area'] is not None:
                popout.draw_target(area, region)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if message not in _reported:
            _reported.add(message)
            print(f"{runtime.title()} could not draw the board:")
            traceback.print_exc()


def namespace_key():
    """This product's one-copy entry in driver_namespace: Board.registry_key, or 'oebs_canvas:<prefix>'."""
    return registry_key_of(runtime.board_class)


def registry_key_of(board_class):
    """A board's one-copy key: what tells copies of the same product (Board.registry_key, or 'oebs_canvas:<prefix>')."""
    return getattr(board_class, 'registry_key', None) or f"oebs_canvas:{board_class.prefix}"


def serves(board_class):
    """Is this board the one this copy of OEBS Canvas serves now? A product's own unregister asks it, so that a copy which
    a newer one replaced leaves the newer one's things alone."""
    return runtime.registered and runtime.board_class is board_class


def claim():
    """Make this copy the one copy of the product. An older copy still registered under the same prefix (installed twice,
    reloaded, or run from a text block) is unregistered first; then this copy is recorded in driver_namespace so the next
    one can do the same. Blender empties driver_namespace on a file load: after_load claims again."""
    namespace = bpy.app.driver_namespace
    previous = namespace.get(namespace_key())
    if isinstance(previous, dict) and previous.get(COPY) is not _token and callable(previous.get('unregister')):
        try:
            previous['unregister']()
        except Exception as exc:
            print(f"{runtime.title()}: could not unregister an older copy ({exc})")
    extra = runtime.board_class.registry_extra() if hasattr(runtime.board_class, 'registry_extra') else {}
    # The product's entries (registry_extra) may give its own 'owner' and 'unregister' (all of it, calling unregister_board
    # itself); which copy wrote the entry, the version and the module are always the framework's.
    namespace[namespace_key()] = {'owner': _token, 'unregister': _unregister_self, **(extra or {}), COPY: _token,
                                  'version': __version__, 'module': runtime.board_class.__module__}


def _unregister_self():
    if runtime.registered:
        unregister_board(runtime.board_class)


@persistent
def on_load(_unused):
    session.close_all()
    runtime.forget()


@persistent
def after_load(*_unused):
    if runtime.registered:
        claim()
    bpy.app.timers.register(popout.close_strays, first_interval=0.5)


@persistent
def after_undo(*_unused):
    """Blender's undo or redo changed the scene: the board layer reads its items again."""
    if runtime.board_class is not None and runtime.board_class.items:
        from .layer import input as layer_input
        layer_input.reread_all()


@persistent
def before_save(*_unused):
    """A saved file never holds a board's own window: every one docks first (except the window the save runs in)."""
    here = bpy.context.window.as_pointer() if bpy.context.window else None
    popout.dock_all(except_window=here)


HANDLER_LISTS = (('load_pre', on_load), ('load_post', after_load), ('save_pre', before_save),
                 ('undo_post', after_undo), ('redo_post', after_undo))


def register_board(board_class):
    """Register a product's board. Call it from the add-on's register()."""
    board_module.validate(board_class)
    current = runtime.board_class
    if runtime.registered:
        if current is board_class or registry_key_of(current) != registry_key_of(board_class):
            raise RuntimeError(f"this copy of OEBS Canvas already serves {current.__name__}")
        take_over(current)                 # a newer copy of the same product replaces the one running
    runtime.board_class = board_class
    runtime.registered = True
    claim()
    roam.draw = draw_board
    if board_class.items:
        layer_store.register(board_class)
    if board_class.uses_pictures:
        from . import media
        media.register_handlers()
    classes = session.make_classes()
    for cls in classes:
        bpy.utils.register_class(cls)
    _registered['classes'] = classes
    for region in ('UI', 'WINDOW'):
        _registered['handlers'].append((region, bpy.types.SpaceView3D.draw_handler_add(draw_board, (), region, 'POST_PIXEL')))
    for name, func in HANDLER_LISTS:
        getattr(bpy.app.handlers, name).append(func)
    if not bpy.app.timers.is_registered(session.open_where_shown):
        bpy.app.timers.register(session.open_where_shown, first_interval=0.3, persistent=True)


def take_over(old):
    """A newer copy of the product (the same registry key) is registering: the copy running is unregistered first, all of
    it through its entry's own unregister while the entry is still its own, else the framework's part."""
    entry = bpy.app.driver_namespace.get(registry_key_of(old))
    own = entry.get('unregister') if isinstance(entry, dict) and entry.get('module') == old.__module__ else None
    if callable(own) and own is not _unregister_self:
        try:
            own()
        except Exception as exc:                    # noqa: BLE001 - another copy's code can fail any way; this one still registers
            print(f"{old.title or old.tab}: could not unregister the copy that was running ({exc})")
    if serves(old):
        unregister_board(old)


def unregister_board(board_class):
    """Undo register_board. Call it from the add-on's unregister(). A copy that a newer copy replaced (or one that never
    registered) has nothing left to undo: the board running stays."""
    if not serves(board_class):
        return
    if bpy.app.timers.is_registered(session.open_where_shown):
        bpy.app.timers.unregister(session.open_where_shown)
    if not bpy.app.background:
        popout.dock_all()
    popout.target_off()
    roam.clear()
    for name, func in HANDLER_LISTS:
        handlers = getattr(bpy.app.handlers, name)
        for handler in [h for h in handlers if getattr(h, '__name__', '') == func.__name__ and getattr(h, '__module__', '') == func.__module__]:
            handlers.remove(handler)          # by name: a reloaded module has a new function object
    for region, handler in _registered['handlers']:
        bpy.types.SpaceView3D.draw_handler_remove(handler, region)
    _registered['handlers'].clear()
    session.close_all()
    draw.clear_cache()
    for cls in reversed(_registered['classes']):
        bpy.utils.unregister_class(cls)
    _registered['classes'] = ()
    layer_store.unregister(board_class)
    from . import media
    media.unregister_handlers()               # safe when none were registered
    entry = bpy.app.driver_namespace.get(namespace_key())
    if isinstance(entry, dict) and entry.get(COPY) is _token:
        bpy.app.driver_namespace.pop(namespace_key(), None)
    runtime.forget()
    runtime.registered = False             # the class stays bound: boards can still be made, as before register_board
