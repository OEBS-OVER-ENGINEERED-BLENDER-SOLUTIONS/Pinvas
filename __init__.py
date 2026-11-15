"""Pinvas Pro: a reference board in the 3D View sidebar."""
import bpy
from bpy.app.handlers import persistent

from . import board, canvas, edition, images, media, operators
from . import upgrade
from ._canvas import register_board, registry, unregister_board

_handlers = registry._registered['handlers']        # the board's draw handlers (register_board adds them)
draw_board = registry.draw_board
before_save = registry.before_save
on_load = registry.on_load           # a file load closes every board and forgets their places (the framework's)


@persistent
def forget_media(_unused):
    """A file load: what was playing stops and the pictures' textures go (they belong to the file being closed)."""
    images.forget()


@persistent
def on_undo(_unused):
    """Undo and redo can add, remove or move pictures: drop any half-made drag and redraw every board."""
    for shown in canvas.sessions.values():
        shown.preview = {}
    wm = bpy.context.window_manager
    for window in wm.windows if wm else ():
        for area in window.screen.areas:
            if area.as_pointer() in canvas.sessions:
                area.tag_redraw()


CLASSES = (*board.classes, *operators.classes, *media.classes, *upgrade.classes)
HANDLER_LISTS = (('load_pre', forget_media), ('undo_post', on_undo), ('redo_post', on_undo))


def register():
    edition.refuse_if_other_is_on()           # Pinvas Pro and Pinvas (free) share their boards: the second one to start says so and stays off
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    board.register_props()
    canvas.PinvasBoard.session_class = operators.PinvasSession
    register_board(canvas.PinvasBoard)
    operators.PINVAS_OT_session = bpy.types.PINVAS_OT_session
    for name, func in HANDLER_LISTS:
        getattr(bpy.app.handlers, name).append(func)


def unregister():
    for name, func in HANDLER_LISTS:
        handlers = getattr(bpy.app.handlers, name)
        for handler in [h for h in handlers if getattr(h, '__name__', '') == func.__name__ and getattr(h, '__module__', '') == func.__module__]:
            handlers.remove(handler)          # by name: a reloaded module has a new function object
    unregister_board(canvas.PinvasBoard)
    operators.PINVAS_OT_session = None
    images.forget()
    board.unregister_props()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
