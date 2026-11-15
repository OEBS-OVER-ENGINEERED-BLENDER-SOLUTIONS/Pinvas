"""Where a board's items live. Store is what the layer works through; SceneStore keeps them in property groups generated
for the product (<PREFIX>_PG_item, <PREFIX>_PG_board) on the Scene, under Board.storage ('<prefix>_board' by default), so
they are saved with the .blend and Blender's undo covers them. A product with property groups of its own supplies its own
Store from Board.store()."""
import bpy
from bpy.props import BoolProperty, CollectionProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from .items import RESERVED, Item
_generated = {}

class Store:
    """The interface the layer uses. Items come back as objects with the built-in fields and the product's fields as
    attributes; their order is their z order, bottom first."""

    def items(self):
        raise NotImplementedError

    def get(self, uid):
        raise NotImplementedError

    def add(self, kind, **values):
        raise NotImplementedError

    def remove(self, uid):
        raise NotImplementedError

    def move_to(self, uid, index):
        raise NotImplementedError

    def snapshot(self):
        """Everything stored, as plain data (JSON-safe)."""
        raise NotImplementedError

    def restore(self, snapshot):
        raise NotImplementedError

    def set_shape(self, item, x, y, w, h, rotation):
        """Place an item: its centre, its shown size and its turn (what the layer writes after a move, scale or turn)."""
        item.x, item.y, item.width, item.height, item.rotation = (x, y, w, h, rotation)

    def keeps_aspect(self, item):
        """Does this item scale only in proportion (a picture shown at a scale)?"""
        return False

class ItemShape:
    """Mixed into the generated item property group: the shapes the layer's geometry works with."""

    @property
    def shape(self):
        """(x, y, width, height, rotation) as it shows (a scaled item's size times its scale), as layer/geometry takes it."""
        s = getattr(self, 'scale', 1.0)
        return (self.x, self.y, self.width * s, self.height * s, self.rotation)

    @property
    def rect(self):
        """The upright box (min_x, min_y, max_x, max_y) of the item as it shows, before it is turned."""
        x, y, w, h, _r = self.shape
        return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)

def attribute(board_class):
    """The Scene attribute a board's data hangs on."""
    return board_class.storage or f"{board_class.prefix}_board"

def all_fields(board_class):
    """{name: Field} of every item type of the board. Two types may share a field only when it is the same kind."""
    fields = {}
    for kind in board_class.items:
        for name, field in kind.fields().items():
            if name in fields and fields[name].kind != field.kind:
                raise ValueError(f"{kind.__name__}.{name} is a {field.kind}; another item type has it as a {fields[name].kind}")
            fields.setdefault(name, field)
    return fields

def register(board_class):
    """Generate and register the board's property groups and hang them on the Scene."""
    prefix = board_class.prefix
    upper = prefix.upper()
    fields = all_fields(board_class)
    annotations = {'uid': StringProperty(), 'kind': StringProperty(), 'x': FloatProperty(), 'y': FloatProperty(), 'width': FloatProperty(default=100.0), 'height': FloatProperty(default=100.0), 'rotation': FloatProperty(subtype='ANGLE'), 'locked': BoolProperty(), 'hidden': BoolProperty(), 'kept': StringProperty(options={'HIDDEN'})}
    if any((kind.scaled for kind in board_class.items)):
        annotations['scale'] = FloatProperty(default=1.0, min=0.0001, description='How many times its own size the item shows')
    annotations.update({name: field.bpy_property(name) for name, field in fields.items()})
    item_group = type(f"{upper}_PG_item", (ItemShape, bpy.types.PropertyGroup), {'__annotations__': annotations})
    board_group = type(f"{upper}_PG_board", (bpy.types.PropertyGroup,), {'__annotations__': {'items': CollectionProperty(type=item_group), 'next_uid': IntProperty(default=1, description="The number the next new item's uid takes"), 'kept': StringProperty(options={'HIDDEN'})}})
    bpy.utils.register_class(item_group)
    bpy.utils.register_class(board_group)
    name = attribute(board_class)
    setattr(bpy.types.Scene, name, PointerProperty(type=board_group))
    _generated[prefix] = dict(classes=(item_group, board_group), attribute=name, fields=fields)

def unregister(board_class):
    made = _generated.pop(board_class.prefix, None)
    if made is None:
        return
    if hasattr(bpy.types.Scene, made['attribute']):
        delattr(bpy.types.Scene, made['attribute'])
    for cls in reversed(made['classes']):
        bpy.utils.unregister_class(cls)

class SceneStore(Store):
    """The board's items on a Scene (the current one unless given)."""

    def __init__(self, board_class, scene=None):
        self.board_class, self.scene = (board_class, scene)
        made = _generated.get(board_class.prefix)
        if made is None:
            raise RuntimeError(f"{board_class.__name__} has no item store: give it items before register_board")
        self.fields = made['fields']

    @property
    def data(self):
        return getattr(self.scene or bpy.context.scene, attribute(self.board_class))

    def items(self):
        return list(self.data.items)

    def index(self, uid):
        return next((i for i, item in enumerate(self.data.items) if item.uid == uid), None)

    def get(self, uid):
        i = self.index(uid)
        return self.data.items[i] if i is not None else None

    def kind_of(self, name):
        return next((kind for kind in self.board_class.items if kind.__name__ == name), None)

    def add(self, kind, **values):
        """A new item of type `kind` (an Item class, or an instance carrying values) on top of the others."""
        if isinstance(kind, Item):
            values = {**kind.values, **values}
            kind = type(kind)
        if kind not in self.board_class.items:
            raise ValueError(f"{getattr(kind, '__name__', kind)} is not one of {self.board_class.__name__}'s item types")
        data = self.data
        number = data.next_uid
        data.next_uid = number + 1
        data.items.add()
        item = data.items[-1]
        item.uid, item.kind = (str(number), kind.__name__)
        item.width, item.height = kind.size
        for name, field in kind.fields().items():
            if field.kind != 'picture':
                setattr(item, name, field.from_plain(field.default))
        for name, value in values.items():
            if name in ('uid', 'kind'):
                raise ValueError(f"{name} is given by the store")
            setattr(item, name, value)
        return data.items[-1]

    def remove(self, uid):
        i = self.index(uid)
        if i is not None:
            self.data.items.remove(i)

    def keeps_aspect(self, item):
        kind = self.kind_of(item.kind)
        return bool(kind is not None and kind.scaled)

    def set_shape(self, item, x, y, w, h, rotation):
        if self.keeps_aspect(item):
            item.x, item.y, item.rotation = (x, y, rotation)
            item.scale = max(0.0001, w / max(item.width, 1e-09))
        else:
            item.x, item.y, item.width, item.height, item.rotation = (x, y, w, h, rotation)

    def move_to(self, uid, index):
        i = self.index(uid)
        if i is not None:
            self.data.items.move(i, max(0, min(index, len(self.data.items) - 1)))

    def plain(self, item):
        """One item as plain data: its built-in fields and every field of the board's item types."""
        out = {name: getattr(item, name) for name in RESERVED if hasattr(item, name)}
        out.update({name: field.to_plain(getattr(item, name)) for name, field in self.fields.items()})
        return out

    def put(self, item, values):
        """Set an item's fields from plain data (unknown names are left alone)."""
        for name, value in values.items():
            if name in RESERVED:
                if hasattr(item, name):
                    setattr(item, name, value)
            elif name in self.fields:
                setattr(item, name, self.fields[name].from_plain(value))

    def snapshot(self):
        data = self.data
        return {'next_uid': data.next_uid, 'items': [self.plain(item) for item in data.items]}

    def restore(self, snapshot):
        data = self.data
        data.items.clear()
        for values in snapshot['items']:
            data.items.add()
            self.put(data.items[-1], values)
        data.next_uid = max(data.next_uid, snapshot.get('next_uid', 1))
