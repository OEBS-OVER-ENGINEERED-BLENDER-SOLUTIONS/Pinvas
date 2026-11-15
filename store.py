"""Pinvas's items as the framework's board layer sees them: a layer Store over Scene.pinvas (so every board Pinvas has saved
keeps loading), and each item's shape as it shows (its size times its scale)."""
from . import board as board_module
from ._canvas.layer import Store

class Shaped:
    """A Pinvas item seen by the layer: its own fields, and `shape` (x, y, width, height, rotation) as it shows."""
    __slots__ = ('item',)
    hidden = False
    locked = False

    def __init__(self, item):
        object.__setattr__(self, 'item', item)

    def __getattr__(self, name):
        return getattr(self.item, name)

    def __setattr__(self, name, value):
        setattr(self.item, name, value)

    @property
    def shape(self):
        x, y, w, h, s, r = board_module.placed(self.item)
        return (x, y, w * s, h * s, r)

def raw(item):
    return item.item if isinstance(item, Shaped) else item

class PinvasStore(Store):
    """The items of one Pinvas board (Scene.pinvas's current board), bottom to top."""

    def __init__(self, board):
        self.board = board
        self.copies = {}

    def items(self):
        return [Shaped(item) for item in self.board.items if item.kind == 'IMAGE'] if self.board is not None else []

    def get(self, uid):
        item = board_module.item_by_uid(self.board, uid) if self.board is not None else None
        return Shaped(item) if item is not None and item.kind == 'IMAGE' else None

    def remove(self, uid):
        i = board_module.index_of(self.board, uid)
        if i >= 0:
            self.board.items.remove(i)

    def keeps_aspect(self, item):
        return True

    def set_shape(self, item, x, y, w, h, rotation):
        item = raw(item)
        shown = board_module.placed(item)[2]
        item.x, item.y, item.rotation = (x, y, rotation)
        item.scale = w / max(shown, 1e-09)
