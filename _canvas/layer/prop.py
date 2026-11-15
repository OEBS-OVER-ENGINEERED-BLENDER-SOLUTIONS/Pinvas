"""The fields of a product's items, declared on its Item classes and saved in the .blend as Blender properties:

    class Swatch(Item):
        colour = prop.colour((1, 0.5, 0.2))
        label = prop.text("Swatch")

Each takes its default first; any further keyword (name, description, min, max, soft_min, ...) goes to the Blender
property as it is."""
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty


class Field:
    """One declared field: its kind, its default and the options for its Blender property."""

    def __init__(self, kind, default, options):
        self.kind, self.default, self.options = kind, default, dict(options)

    def bpy_property(self, name):
        options = dict(self.options)
        options.setdefault('name', name.replace('_', ' ').title())
        kind, default = self.kind, self.default
        if kind == 'text':
            return StringProperty(default=default, **options)
        if kind == 'number':
            return FloatProperty(default=float(default), **options)
        if kind == 'integer':
            return IntProperty(default=int(default), **options)
        if kind == 'flag':
            return BoolProperty(default=bool(default), **options)
        if kind == 'colour':
            options.setdefault('min', 0.0)
            options.setdefault('max', 1.0)
            return FloatVectorProperty(subtype='COLOR', size=len(default), default=tuple(default), **options)
        if kind == 'choice':
            return EnumProperty(items=options.pop('items'), default=default, **options)
        if kind == 'picture':
            return PointerProperty(type=bpy.types.Image, **options)
        raise ValueError(f"unknown field kind {kind!r}")

    def to_plain(self, value):
        """The stored value as plain data (a snapshot, an export)."""
        if self.kind == 'colour':
            return [float(v) for v in value]
        if self.kind == 'picture':
            return value.name if value is not None else None
        return value

    def from_plain(self, value):
        """A plain value back to what the Blender property takes."""
        if self.kind == 'colour':
            return tuple(value)
        if self.kind == 'picture':
            return bpy.data.images.get(value) if value else None
        return value


def text(default="", **options):
    return Field('text', default, options)


def number(default=0.0, **options):
    return Field('number', default, options)


def integer(default=0, **options):
    return Field('integer', default, options)


def flag(default=False, **options):
    return Field('flag', default, options)


def colour(default=(1.0, 1.0, 1.0, 1.0), **options):
    """An RGB or RGBA colour (the default's length decides), each value 0 to 1."""
    if len(default) not in (3, 4):
        raise ValueError("a colour has 3 or 4 values")
    return Field('colour', tuple(float(v) for v in default), options)


def choice(items, default=None, **options):
    """One of `items`: strings, or (identifier, name, description) tuples. The default is the first unless named."""
    entries = [(i, i.replace('_', ' ').title(), "") if isinstance(i, str) else tuple(i) for i in items]
    if not entries:
        raise ValueError("a choice needs at least one item")
    return Field('choice', default if default is not None else entries[0][0], dict(options, items=entries))


def picture(default=None, **options):
    """An image (bpy.types.Image). It has no default but None."""
    return Field('picture', None, options)
