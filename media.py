"""Putting pictures on the board and working on them: Import, Paste, drag-and-drop and the
right-click menu.

Every operator works on the board of the 3D View it runs in, through that view's Canvas.
"""
import os
import tempfile

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, StringProperty

from . import board as board_module
from . import clipboard, geometry
from . import upgrade
from .canvas import TAB, sessions

STILL_EXTENSIONS = (".png", ".jpg", ".jpeg", ".jp2", ".j2c", ".bmp", ".tga", ".tif", ".tiff", ".exr", ".hdr",
                    ".webp", ".psd", ".dds", ".cin", ".dpx", ".sgi", ".rgb")
PICTURE_EXTENSIONS = STILL_EXTENSIONS          # everything the board takes
NEW_HEIGHT = 0.45             # new pictures come in this share of the visible board's height,
NEW_WIDTH = 0.9               # smaller when their row would be wider than this share of its width,
NEW_GAP = 0.06                # and this share of their height apart


def canvas_for(context):
    area = context.area
    return sessions.get(area.as_pointer()) if area is not None else None


def is_picture_path(path):
    return os.path.splitext(path)[1].lower() in PICTURE_EXTENSIONS


def load_picture(path, relative):
    """(image, problem): the picture at `path` as an Image datablock, reusing one already loaded from it."""
    name = os.path.basename(path)
    if not is_picture_path(path):
        return None, f"Not a picture: {name}"
    known = {img.name_full for img in bpy.data.images}
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return None, f"Could not read {name}"
    if image.name_full not in known and relative and bpy.data.filepath:
        try:
            image.filepath_raw = bpy.path.relpath(path)
        except ValueError:
            pass                    # on another drive: the path stays absolute
    return image, None


def place(context, images_to_add, at=None):
    """Put pictures on the board in a row centred on `at` (canvas pixels), or on the middle of the view.
    Selects them; returns their uids."""
    canvas = canvas_for(context)
    board = board_module.board_of(context.scene, create=True)
    sizes = [board_module.image_size(img) for img in images_to_add]
    if canvas is not None and canvas.board_rect[2] > 1:
        bx, by, bw, bh = canvas.board_rect
        point = at if at is not None else (bx + bw / 2, by + bh / 2)
        centre = canvas.board_point(*point)
        seen_w, seen_h = canvas.visible_size()
        # The whole row in view: a narrow docked board shrinks a row of wide pictures rather than cutting it off.
        across = sum(w / h for w, h in sizes) + NEW_GAP * (len(sizes) - 1)
        height = min(seen_h * NEW_HEIGHT, seen_w * NEW_WIDTH / max(across, 1e-6))
    else:
        centre, height = (0.0, 0.0), 400.0
    uids = []
    for image, (x, y, scale) in zip(images_to_add, geometry.row_layout(sizes, centre, height, height * NEW_GAP)):
        uid = board_module.add_image(board, image, x, y, scale)
        uids.append(uid)
    if canvas is not None:
        canvas.selection = set(uids)
    tag_redraw(context)
    return uids


def tag_redraw(context):
    if context.area is not None:
        context.area.tag_redraw()


def add_paths(operator, context, paths, at=None, relative=True):
    loaded, problems = [], []
    for path in paths:
        image, problem = load_picture(path, relative)
        if problem:
            problems.append(problem)
        elif image is not None:
            loaded.append(image)
    for problem in problems[:5]:
        operator.report({'WARNING'}, problem)
    if not loaded:
        return {'CANCELLED'}
    place(context, loaded, at)
    return {'FINISHED'}


def mouse_on_board(canvas, area, x, y):
    """Window pixels to canvas pixels, when they fall on the board."""
    host = canvas.host(area) if canvas is not None else None
    if host is None:
        return None
    region, ox, oy, w, h = host
    cx, cy = x - region.x - ox, y - region.y - oy
    return (cx, cy) if geometry.inside(canvas.board_rect, cx, cy) else None


def asked_at(canvas, area, event):
    """Where a picture asked for now goes: where the menu was opened when a menu asked, else at the mouse."""
    at = canvas.menu_at if canvas is not None else None
    if at is None:
        return mouse_on_board(canvas, area, event.mouse_x, event.mouse_y)
    canvas.menu_at = None
    return at if geometry.inside(canvas.board_rect, *at) else None


class PINVAS_OT_import_media(bpy.types.Operator):
    """Add pictures from disk to the board"""
    bl_idname = "pinvas.import_media"
    bl_label = "Import Pictures"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    relative_path: BoolProperty(name="Relative Path", description="Store the pictures' paths relative to the .blend",
                                default=True)

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self.drop_at = asked_at(canvas_for(context), context.area, event)
        if self.directory and len(self.files) and self.files[0].name:
            return self.execute(context)            # dropped files arrive with their names already set
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        return add_paths(self, context, paths, getattr(self, 'drop_at', None), self.relative_path)


def existing_stills():
    """List still-image datablocks without loading their pixels during search."""
    return (image for image in bpy.data.images if image.source in {'FILE', 'GENERATED'})


def search_existing_images(_operator, _context, _text):
    return (image.name_full for image in existing_stills())


class PINVAS_OT_add_existing_image(bpy.types.Operator):
    """Add an image already loaded in this Blender file, including packed images"""
    bl_idname = "pinvas.add_existing_image"
    bl_label = "Existing Blend Images"
    bl_options = {'UNDO'}

    image_name: StringProperty(name="Image", search=search_existing_images, search_options={'SORT'}, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return PINVAS_OT_import_media.poll(context)

    def invoke(self, context, event):
        self.at = asked_at(canvas_for(context), context.area, event)
        if next(existing_stills(), None) is None:
            self.report({'INFO'}, "This Blender file has no still images")
            return {'CANCELLED'}
        return context.window_manager.invoke_props_dialog(self, confirm_text="Add to Board")

    def draw(self, context):
        self.layout.prop(self, "image_name", icon='IMAGE_DATA')

    def execute(self, context):
        matches = [image for image in existing_stills() if image.name_full == self.image_name]
        if len(matches) != 1:
            self.report({'WARNING'}, "Choose an existing still image")
            return {'CANCELLED'}
        image = matches[0]
        if min(image.size) <= 0:
            self.report({'WARNING'}, f"Could not read {image.name_full}")
            return {'CANCELLED'}
        place(context, [image], getattr(self, 'at', None))
        return {'FINISHED'}


class PINVAS_OT_drop_media(bpy.types.Operator):
    """Put the dropped pictures on the board, where they were dropped"""
    bl_idname = "pinvas.drop_media"
    bl_label = "Add to Pinvas Board"            # how Blender names this choice when more than one importer takes the drop
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})
    relative_path: BoolProperty(name="Relative Path", description="Store the pictures' paths relative to the .blend",
                                default=True)

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self.drop_at = mouse_on_board(canvas_for(context), context.area, event.mouse_x, event.mouse_y)
        return self.execute(context)

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        return add_paths(self, context, paths, getattr(self, 'drop_at', None), self.relative_path)


def paste_picture(name, width, height, rgba):
    """A new packed picture from raw 8-bit RGBA pixels (bottom row first)."""
    import numpy as np
    image = bpy.data.images.new(name, width, height, alpha=True)
    image.pixels.foreach_set((np.frombuffer(rgba, dtype=np.uint8).astype(np.float32) / 255.0))
    pack_new(image)
    return image


def paste_png(name, data):
    """A new packed picture from PNG data."""
    handle, path = tempfile.mkstemp(prefix="pinvas_paste_", suffix=".png")
    try:
        with os.fdopen(handle, 'wb') as out:
            out.write(data)
        image = bpy.data.images.load(path, check_existing=False)
        image.name = name
        image.pack()
        pack_new(image)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return image


def pack_new(image):
    """Pack a pasted picture and give it a plain path of its own: no folder of this machine is kept in the file."""
    clean = '//' + bpy.path.clean_name(image.name) + '.png'
    image.filepath_raw = clean
    image.file_format = 'PNG'
    if not image.packed_file:
        image.pack()
    for packed in image.packed_files:
        packed.filepath = clean


class PINVAS_OT_paste(bpy.types.Operator):
    """Put the picture on the clipboard on the board (a copied image, a screenshot, or picture files copied in your file manager)"""
    bl_idname = "pinvas.paste"
    bl_label = "Paste"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        self.at = asked_at(canvas_for(context), context.area, event)
        return self.execute(context)

    def execute(self, context):
        at = getattr(self, 'at', None)
        kind, value = clipboard.read()
        if kind == 'files':
            pictures = [p for p in value if is_picture_path(p)]
            if pictures:
                return add_paths(self, context, pictures, at)
        elif kind == 'png':
            try:
                return self.done(context, paste_png("Pasted", value), at)
            except RuntimeError as exc:
                self.report({'WARNING'}, f"Could not read the pasted picture ({exc})")
                return {'CANCELLED'}
        elif kind == 'dib':
            try:
                width, height, rgba = clipboard.dib_pixels(value)
            except ValueError as exc:
                self.report({'WARNING'}, f"Could not read the pasted picture ({exc})")
                return {'CANCELLED'}
            return self.done(context, paste_picture("Pasted", width, height, rgba), at)
        paths = clipboard.picture_paths(context.window_manager.clipboard)
        if paths:
            return add_paths(self, context, paths, at)
        self.report({'INFO'}, "There is no picture on the clipboard")
        return {'CANCELLED'}

    def done(self, context, image, at):
        place(context, [image], at)
        return {'FINISHED'}


EDITS = [
    ('GREY', "Greyscale", "Show the selected pictures in greys, to judge their values (again to show their colour)"),
    ('REMOVE', "Remove from Board", "Take the selected pictures off the board (the pictures themselves are not deleted until the file is saved without them)"),
]
EDIT_NAMES = {key: name for key, name, _d in EDITS}


class PINVAS_OT_edit(bpy.types.Operator):
    """Change the selected pictures"""
    bl_idname = "pinvas.edit"
    bl_label = "Edit Pictures"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=EDITS)

    @classmethod
    def poll(cls, context):
        canvas = canvas_for(context)
        return canvas is not None and bool(canvas.selection)

    @classmethod
    def description(cls, context, properties):
        return next((d for key, _n, d in EDITS if key == properties.action), cls.__doc__)

    def execute(self, context):
        board = board_module.board_of(context.scene)
        if board is None:
            return {'CANCELLED'}
        apply_edit(canvas_for(context), board, self.action)
        tag_redraw(context)
        return {'FINISHED'}


LAYER_EDITS = {'REMOVE': 'delete'}


def apply_edit(canvas, board, action):
    """Do one of EDITS to the pictures selected on `canvas`."""
    edit_items(canvas, board, action)


def edit_items(canvas, board, action):
    """Removing is the board layer's; Blender makes the operator calling this one undo step, so the layer pushes none of its own."""
    layer = getattr(canvas, 'layer', None)
    if layer is not None and action in LAYER_EDITS:
        with layer.quiet():
            getattr(layer, LAYER_EDITS[action])()
        return
    chosen = [uid for uid in (item.uid for item in board.items) if uid in canvas.selection]      # bottom to top
    if action == 'GREY':
        pictures = [board_module.item_by_uid(board, uid) for uid in chosen]
        pictures = [item for item in pictures if item.kind == 'IMAGE']
        grey = not all(item.grey for item in pictures)          # a mixed selection goes grey first
        for item in pictures:
            item.grey = grey
        return


ARRANGE = [('PACK', "Tidy the Board", "Pack the pictures into one tight block shaped like the board (Ctrl+P)")]
LAYOUTS = {'PACK'}


def arrange(canvas, board, action, gap=10.0, order='PLACED'):
    """Do one of ARRANGE to the selected items (a layout with fewer than two selected lays out the whole board). Returns the number of
    items moved, or a message saying why nothing could be done."""
    result = arrange_items(canvas, board, action, gap, order)
    return result


def arrange_items(canvas, board, action, gap, order):
    pictures = [item for item in board.items if item.kind == 'IMAGE']
    chosen = [item for item in pictures if item.uid in canvas.selection]
    everything = False
    if len(chosen) < 2:
        if len(pictures) >= 2:
            chosen, everything = pictures, True
        else:
            return "Select 2 or more pictures first"
    placed = {item.uid: board_module.placed(item) for item in chosen}
    boxes = {uid: geometry.items_bounds([p]) for uid, p in placed.items()}
    uids = [item.uid for item in chosen]
    centres = [((boxes[u][0] + boxes[u][2]) / 2, (boxes[u][1] + boxes[u][3]) / 2) for u in uids]
    sizes = [(boxes[u][2] - boxes[u][0], boxes[u][3] - boxes[u][1]) for u in uids]
    bw, bh = canvas.board_rect[2:]
    moved = geometry.pack(centres, sizes, gap, bw / bh if bw > 1 and bh > 1 else 1.0)
    for uid, (cx, cy), (nx, ny) in zip(uids, centres, moved):
        item = board_module.item_by_uid(board, uid)
        item.x, item.y = item.x + nx - cx, item.y + ny - cy    # a turned item's box is centred on it: it moves with its box
    if everything:
        canvas.fit()
    return len(uids)


class PINVAS_OT_arrange(bpy.types.Operator):
    """Arrange the selected pictures"""
    bl_idname = "pinvas.arrange"
    bl_label = "Arrange"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(items=ARRANGE)
    gap: FloatProperty(name="Gap", description="Room left between pictures, in board units (pixels at 100%)",
                       default=10.0, min=0.0, soft_max=200.0)

    @classmethod
    def poll(cls, context):
        return canvas_for(context) is not None and board_module.board_of(context.scene) is not None

    @classmethod
    def description(cls, context, properties):
        return next((d for key, _n, d in ARRANGE if key == properties.action), cls.__doc__)

    def draw(self, context):
        layout = self.layout
        if self.action in LAYOUTS:
            layout.prop(self, "gap")

    def execute(self, context):
        canvas = canvas_for(context)
        done = arrange(canvas, board_module.board_of(context.scene), self.action, self.gap)    # Free has no Order: it packs
        if isinstance(done, str):
            self.report({'INFO'}, done)
            return {'CANCELLED'}
        tag_redraw(context)
        return {'FINISHED'}


class PINVAS_OT_opacity(bpy.types.Operator):
    """See through the selected pictures"""
    bl_idname = "pinvas.opacity"
    bl_label = "Opacity"
    bl_options = {'REGISTER', 'UNDO'}

    opacity: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0, subtype='FACTOR')

    @classmethod
    def poll(cls, context):
        canvas = canvas_for(context)
        return canvas is not None and bool(canvas.selection)

    def invoke(self, context, event):
        board = board_module.board_of(context.scene)
        first = next((item for item in board.items if item.uid in canvas_for(context).selection), None) if board else None
        if first is not None:
            self.opacity = first.opacity
        return context.window_manager.invoke_props_dialog(self, width=220)

    def execute(self, context):
        board = board_module.board_of(context.scene)
        if board is None:
            return {'CANCELLED'}
        for item in board.items:
            if item.uid in canvas_for(context).selection:
                item.opacity = self.opacity
        tag_redraw(context)
        return {'FINISHED'}


VIEWS = [
    ('SELECT_ALL', "Select All", "Select every picture on the board"),
    ('DESELECT', "Select None", "Clear the selection"),
    ('FIT_ALL', "Fit All", "Show the whole board"),
    ('FIT_SELECTED', "Fit Selected", "Show the selected pictures"),
]


class PINVAS_OT_view(bpy.types.Operator):
    """Change what the board shows or selects"""
    bl_idname = "pinvas.view"
    bl_label = "Board View"
    bl_options = {'INTERNAL'}

    action: EnumProperty(items=VIEWS)

    @classmethod
    def poll(cls, context):
        return canvas_for(context) is not None

    @classmethod
    def description(cls, context, properties):
        return next((d for key, _n, d in VIEWS if key == properties.action), cls.__doc__)

    def execute(self, context):
        canvas = canvas_for(context)
        if self.action == 'SELECT_ALL':
            canvas.select_all()
        elif self.action == 'DESELECT':
            canvas.selection.clear()
        elif self.action == 'FIT_ALL':
            canvas.fit()
        elif self.action == 'FIT_SELECTED':
            canvas.fit_selected()
        tag_redraw(context)
        return {'FINISHED'}


class PINVAS_MT_board(bpy.types.Menu):
    bl_label = "Pinvas"
    bl_idname = "PINVAS_MT_board"

    def draw(self, context):
        layout = self.layout
        # Opened from Python (wm.call_menu), a menu's own items would run execute() and skip invoke(): no file browser, no
        # dialog. Invoking them makes Import and Opacity open their windows as they do anywhere else.
        layout.operator_context = 'INVOKE_REGION_WIN'
        canvas = canvas_for(context)
        layout.operator("pinvas.import_media", text="Import Pictures…", icon='FILE_IMAGE')
        layout.operator("pinvas.add_existing_image", text="Existing Blend Images…", icon='IMAGE_DATA')
        layout.operator("pinvas.paste", text="Paste", icon='PASTEDOWN')
        if canvas is not None and canvas.selection:
            layout.separator()
            layout.menu("PINVAS_MT_appearance")
            layout.separator()
            layout.operator("pinvas.edit", text=EDIT_NAMES['REMOVE']).action = 'REMOVE'
        layout.separator()
        layout.operator("pinvas.arrange", text="Tidy the Board", icon='ALIGN_JUSTIFY').action = 'PACK'
        layout.separator()
        layout.menu("PINVAS_MT_view")
        upgrade.draw(layout)


class PINVAS_MT_appearance(bpy.types.Menu):
    bl_label = "Appearance"
    bl_idname = "PINVAS_MT_appearance"

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_REGION_WIN'        # Opacity opens its window
        layout.operator("pinvas.edit", text=EDIT_NAMES['GREY']).action = 'GREY'
        layout.operator("pinvas.opacity", text="Opacity…")


class PINVAS_MT_view(bpy.types.Menu):
    bl_label = "View"
    bl_idname = "PINVAS_MT_view"

    def draw(self, context):
        layout = self.layout
        for key, name, _d in VIEWS:
            layout.operator("pinvas.view", text=name).action = key


class PINVAS_MT_add(bpy.types.Menu):
    bl_label = "Add"
    bl_idname = "PINVAS_MT_add"

    def draw(self, context):
        layout = self.layout
        # Opened from Python (wm.call_menu), a menu's own items would run execute() and skip invoke(): no file browser, no
        # dialog. Invoking them makes Import and Opacity open their windows as they do anywhere else.
        layout.operator_context = 'INVOKE_REGION_WIN'
        layout.operator("pinvas.import_media", text="Import Pictures…", icon='FILE_IMAGE')
        layout.operator("pinvas.add_existing_image", text="Existing Blend Images…", icon='IMAGE_DATA')
        layout.operator("pinvas.paste", text="Paste", icon='PASTEDOWN')


# Drag and drop

def board_under_drop(context):
    """Is a file being dropped on a Pinvas board (docked in this sidebar, or floating where the mouse last was)?"""
    area, region = context.area, context.region
    if area is None or region is None or area.type != 'VIEW_3D':
        return False
    canvas = sessions.get(area.as_pointer())
    if canvas is None:
        return False
    host = canvas.host(area)
    if host is None or host[0].as_pointer() != region.as_pointer():
        return False
    if not (canvas.floating or canvas.popout):
        return region.type == 'UI' and region.active_panel_category == TAB
    return bool(canvas.mouse and canvas.mouse[1])          # floating or in its own window: where the mouse last was over it


# Blender's own picture drops into a 3D View take a drop anywhere in the view, the sidebar
# included, so over the board Blender asks which one is meant
# ("Add to Pinvas Board" among them). Blender's handlers are never changed: replacing their
# poll_drop after they are registered crashes Blender as soon as a file is dragged over a window.
class PINVAS_FH_board(bpy.types.FileHandler):
    bl_idname = "PINVAS_FH_board"
    bl_label = "Add to Pinvas Board"
    bl_import_operator = "pinvas.drop_media"
    bl_file_extensions = ";".join(PICTURE_EXTENSIONS)

    @classmethod
    def poll_drop(cls, context):
        return board_under_drop(context)


classes = (PINVAS_OT_import_media, PINVAS_OT_add_existing_image, PINVAS_OT_drop_media, PINVAS_OT_paste, PINVAS_OT_edit, PINVAS_OT_view, PINVAS_OT_arrange,
           PINVAS_OT_opacity, PINVAS_MT_add, PINVAS_MT_board, PINVAS_MT_appearance, PINVAS_MT_view, PINVAS_FH_board)
