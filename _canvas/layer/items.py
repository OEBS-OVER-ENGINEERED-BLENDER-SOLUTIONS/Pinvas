"""Item: the base of a product's item types. Its class attributes are its default size and its prop fields; an instance
carries values for a new item (`board.add(Swatch(label="Red"))`)."""
from .prop import Field

# Every item has these, whatever its type: its id, its type's name, its place, size and turn (board units, radians), and
# whether it is locked or hidden. Its order (z) is its place in the store.
BUILTIN = ('uid', 'kind', 'x', 'y', 'width', 'height', 'rotation', 'locked', 'hidden')
# Kept for the framework: fields an import did not know (JSON), so a later export gives them back.
RESERVED = BUILTIN + ('kept', 'scale')       # 'scale': the field of a scaled item type (Item.scaled)


class Item:
    size = (100.0, 100.0)     # a new item's width and height, in board units
    scaled = False            # True: it keeps its width and height (a picture's pixels) and is shown `scale` times as big;
                              # scaling it changes the scale, in proportion

    def __init__(self, **values):
        self.values = values

    @classmethod
    def fields(cls):
        """{name: Field} declared on this type and its bases."""
        found = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, Field):
                    if name in RESERVED:
                        raise ValueError(f"{cls.__name__}.{name}: {name} is a field every item has already")
                    found[name] = value
        return found
