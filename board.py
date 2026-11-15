"""What a board holds, saved with the .blend: the pictures on it and where each one sits.

The boards live on the scene (`scene.pinvas`). Each item points at its Image datablock, and that
pointer counts as a user, so Blender keeps and saves the picture (packed or linked) with the file.
Item order is drawing order: the last item is on top.
"""
import uuid
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
PLACEHOLDER_SIZE = 256
KINDS = [('IMAGE', 'Image', 'A picture')]

class PinvasItem(bpy.types.PropertyGroup):
    uid: StringProperty(name='Id', description='Stays the same for the life of the item')
    kind: EnumProperty(name='Kind', items=KINDS, default='IMAGE')
    image: PointerProperty(name='Image', type=bpy.types.Image)
    width: IntProperty(name='Width', description="The picture's width in pixels when it was added", default=PLACEHOLDER_SIZE, min=1)
    height: IntProperty(name='Height', description="The picture's height in pixels when it was added", default=PLACEHOLDER_SIZE, min=1)
    x: FloatProperty(name='X', description='Centre on the board')
    y: FloatProperty(name='Y', description='Centre on the board')
    scale: FloatProperty(name='Scale', description='Board units per picture pixel', default=1.0, min=0.0001)
    rotation: FloatProperty(name='Rotation', subtype='ANGLE')
    grey: BoolProperty(name='Greyscale', description='Show the picture in greys, to judge its values')
    opacity: FloatProperty(name='Opacity', default=1.0, min=0.0, max=1.0, subtype='FACTOR')

class PinvasBoard(bpy.types.PropertyGroup):
    name: StringProperty(name='Name', default='Board')
    items: CollectionProperty(type=PinvasItem)

class PinvasData(bpy.types.PropertyGroup):
    boards: CollectionProperty(type=PinvasBoard)
    active: IntProperty(name='Active Board', default=0, min=0)
classes = (PinvasItem, PinvasBoard, PinvasData)

def register_props():
    bpy.types.Scene.pinvas = PointerProperty(type=PinvasData)

def unregister_props():
    del bpy.types.Scene.pinvas

def stored(scene):
    """Has this scene ever held a board? Asked without creating one (reading `scene.pinvas` stores an empty group)."""
    props = scene.bl_system_properties_get() if hasattr(scene, 'bl_system_properties_get') else scene
    return props is not None and 'pinvas' in props

def board_of(scene, create=False):
    """The scene's open board, or None when it has none and `create` is False."""
    if scene is None or (not create and (not stored(scene))):
        return None
    data = scene.pinvas
    if not data.boards:
        if not create:
            return None
        data.boards.add().name = 'Board'
    return data.boards[min(data.active, len(data.boards) - 1)]

def new_uid():
    return uuid.uuid4().hex[:12]

def add_image(board, image, x, y, scale):
    """Put a picture on the board; returns the new item's uid (take the item from the board by uid afterwards:
    an item held across another add() may point at freed memory)."""
    width, height = image_size(image)
    item = board.items.add()
    uid = new_uid()
    item.uid = uid
    item.kind = 'IMAGE'
    item.image = image
    item.width, item.height = (width, height)
    item.x, item.y, item.scale = (x, y, scale)
    return uid

def image_size(image):
    """(width, height) of a picture, or the placeholder size when it cannot be read."""
    try:
        w, h = image.size[:]
    except (ReferenceError, AttributeError):
        w = h = 0
    if w <= 0 or h <= 0:
        return (PLACEHOLDER_SIZE, PLACEHOLDER_SIZE)
    return (w, h)

def item_by_uid(board, uid):
    return next((item for item in board.items if item.uid == uid), None)

def index_of(board, uid):
    return next((i for i, item in enumerate(board.items) if item.uid == uid), -1)

def placed(item):
    """The item as plain numbers for the board's math: (x, y, width, height, scale, rotation)."""
    return (item.x, item.y, item.width, item.height, item.scale, item.rotation)
COPIED = ('kind', 'image', 'width', 'height', 'x', 'y', 'scale', 'rotation', 'grey', 'opacity')

def uids_present(board):
    return {item.uid for item in board.items} if board is not None else set()

def uids_shown(board):
    """The pictures on the board (an item of a kind this edition cannot show is not among them)."""
    return {item.uid for item in board.items if item.kind == 'IMAGE'} if board is not None else set()
