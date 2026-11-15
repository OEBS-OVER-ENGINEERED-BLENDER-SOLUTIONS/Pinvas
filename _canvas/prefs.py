"""The framework's choices, kept in the product's own add-on preferences. A product that wants them mixes CanvasPreferences
into its AddonPreferences:

    class Preferences(CanvasPreferences, bpy.types.AddonPreferences):
        bl_idname = __package__
        def draw(self, context):
            self.draw_canvas(self.layout)

A product without them gets DEFAULTS. Every risky choice is off by default."""
import os
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty

from . import runtime

DEFAULTS = {'pop_out': True, 'cross_editors': False, 'resize_mode': 'OUTLINE', 'native_move': False, 'interface_size': 1.0}
CACHE_FOR = 1.0           # seconds a choice is kept (preferences are read on every redraw)
_cache = {}


class CanvasPreferences:
    pop_out: BoolProperty(
        name="Undock to a Window", default=True,
        description="Undock opens the board in a window of its own (Windows only); off, it floats over the viewport")
    cross_editors: BoolProperty(
        name="Float Across Editors", default=False,
        description="A floating board may hang over the other editors of its window, not only its own viewport")
    resize_mode: EnumProperty(
        name="Window Resize", default='OUTLINE',
        items=(('OUTLINE', "Outline", "An outline follows the mouse; the window takes its new size when you let go"),
               ('LIVE', "Live", "The window takes every size as you drag")),
        description="How a board's own window is resized")
    native_move: BoolProperty(
        name="System Window Move", default=False,
        description="Windows moves the board's own window with its own move loop: as smooth as any window, but the dock target "
                    "is shown only when you let go")
    interface_size: FloatProperty(
        name="Interface Size", default=1.0, min=0.6, max=2.5, soft_min=0.75, soft_max=2.0,
        description="The size of the board's bar, buttons and content, on top of Blender's Resolution Scale")

    def draw_canvas(self, layout):
        """The framework's choices, for the product's draw(). Choices that cannot apply here are left out."""
        col = layout.column(heading="Board")
        col.prop(self, 'interface_size')
        col.prop(self, 'cross_editors')
        if os.name == 'nt':
            col.prop(self, 'pop_out')
            if self.pop_out:
                col.prop(self, 'resize_mode')
                col.prop(self, 'native_move')


def package():
    """The product's name in preferences.addons: the longest enabled add-on name its Board class's module starts with."""
    module = runtime.board_class.__module__ if runtime.board_class is not None else ''
    names = [name for name in bpy.context.preferences.addons.keys() if module == name or module.startswith(name + '.')]
    return max(names, key=len) if names else None


def lookup(name, default, addons, cache, now=None, keep_for=CACHE_FOR, preferences=None):
    """The choice `name` from the preferences of the first add-on in `addons` that is enabled and has it, else `default`.
    An add-on is named by its module, matched on the last part (so 'my_tool' finds 'bl_ext.user_default.my_tool'). A value
    read is kept in `cache`, a dict the caller owns (clearing it reads afresh), for `keep_for` seconds: preferences are read
    on every redraw. `preferences` is the preferences object to read (default: Blender's, bpy.context.preferences); a product
    passes its own when its code, or a test, gives it another."""
    now = time.monotonic() if now is None else now
    hit = cache.get(name)
    if hit is not None and now - hit[0] < keep_for:
        return hit[1]
    value = default
    try:
        source = bpy.context.preferences if preferences is None else preferences
        enabled = {key.rpartition('.')[2]: addon for key, addon in source.addons.items()}
        for wanted in addons:
            found = getattr(enabled.get(wanted.rpartition('.')[2]), 'preferences', None)
            if found is not None and hasattr(found, name):
                value = getattr(found, name)
                break
    except Exception:
        value = default                     # preferences out of reach (an add-on being enabled): the default for now
    cache[name] = (now, value)
    return value


def choice(name, default=None, now=None):
    """The product's choice `name`, or its default when the product has no such preference."""
    return lookup(name, DEFAULTS.get(name) if default is None else default, [package() or ''], _cache, now)


def forget():
    """Read the preferences afresh at the next choice() (tests, and after the user changes one)."""
    _cache.clear()
