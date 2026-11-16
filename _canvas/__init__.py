"""OEBS Canvas: GPU-drawn boards for Blender add-ons, docked in the sidebar, floating over the viewport, or in a window of
their own. A product bundles its own copy (as `<addon>/_canvas`) and subclasses Board.

    from ._canvas import Board, Item, bind, prop, register_board, unregister_board

A product may bind its board class when its module is imported (`@bind` on the class), so its boards can be made before
the add-on registers.
"""
__version__ = "0.5.4"

from .board import Board                                  # noqa: E402
from .layer import Item, prop                             # noqa: E402
from .registry import register_board, unregister_board   # noqa: E402
from .runtime import bind                                 # noqa: E402

__all__ = ['Board', 'Item', 'bind', 'prop', 'register_board', 'unregister_board', '__version__']
