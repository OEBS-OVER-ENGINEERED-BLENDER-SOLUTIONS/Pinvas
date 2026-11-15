"""Pictures on a board: loading and packing them, drawing them in true colour, reading their values and single pixels (the
eyedropper), and keeping a packed picture's recorded path plain whenever the file is saved.

Colour: Blender hands image textures over in scene-linear colour (an 8-bit sRGB picture is an SRGB8_A8 texture, decoded to
linear when sampled). A colour picture is shown through the display transform; a Non-Color one (data: normal maps, masks)
shows its stored values as they are. Where the result goes matters too: the sidebar takes display (sRGB) values, while the
viewport's own region encodes linear values to sRGB by itself, so a picture drawn there in display values would come out
washed out (encoded twice). The shader also multiplies the colour by the alpha, so pictures blend like everything else."""
import os
import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader
from . import draw, runtime
STILLS = ('.png', '.jpg', '.jpeg', '.jp2', '.j2c', '.bmp', '.tga', '.tif', '.tiff', '.exr', '.hdr', '.webp', '.dds', '.cin', '.dpx', '.sgi', '.rgb', '.bw')
FULL = (0.0, 0.0, 1.0, 1.0)
LINEAR_FORMATS = {'SRGB8_A8', 'RGBA16F', 'RGBA32F', 'RGB16F', 'RGB32F', 'R16F', 'R32F', 'RG16F', 'RG32F'}
VERTEX = '\nvoid main()\n{\n    uv_interp = uv;\n    gl_Position = ModelViewProjectionMatrix * vec4(pos, 0.0, 1.0);\n}\n'
FRAGMENT = '\nfloat to_display(float c)\n{\n    return c <= 0.0031308 ? 12.92 * c : 1.055 * pow(c, 1.0 / 2.4) - 0.055;\n}\n\nfloat to_linear(float c)\n{\n    return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4);\n}\n\nvoid main()\n{\n    vec4 c = texture(image, uv_interp);\n    vec3 rgb = c.rgb;\n    if (premultiplied == 1 && c.a > 0.0) {\n        rgb /= c.a;\n    }\n    /* The picture as it is seen (display values): the looks below work on what the eye judges. */\n    if (linear_in == 1) {\n        rgb = vec3(to_display(max(rgb.r, 0.0)), to_display(max(rgb.g, 0.0)), to_display(max(rgb.b, 0.0)));\n    }\n    if (grey == 1) {\n        rgb = vec3(dot(rgb, vec3(0.2126, 0.7152, 0.0722)));\n    }\n    if (display_out == 0) {\n        rgb = vec3(to_linear(max(rgb.r, 0.0)), to_linear(max(rgb.g, 0.0)), to_linear(max(rgb.b, 0.0)));\n    }\n    float a = c.a * opacity;\n    FragColor = vec4(rgb * a, a);\n}\n'
_shader = []
_failed = set()
_pixels = {}

def is_picture_path(path):
    return os.path.splitext(path)[1].lower() in STILLS

def load_picture(path, relative=True):
    """The picture at `path` as an Image (one already loaded from it is reused). Relative to the .blend when `relative` and the
    file is saved (on another drive the path stays absolute). Raises ValueError with a message for the user."""
    name = os.path.basename(path)
    if not is_picture_path(path):
        raise ValueError(f"Not a picture: {name}")
    known = {image.name_full for image in bpy.data.images}
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        raise ValueError(f"Could not read {name}") from None
    if image.name_full not in known and relative and bpy.data.filepath:
        try:
            image.filepath_raw = bpy.path.relpath(path)
        except ValueError:
            pass
    if runtime.board_class is not None and owner_key() not in image:
        image[owner_key()] = name
    return image

def owner_key(folder=None):
    return f"{folder or runtime.prefix()}_picture"

def plain_path(name, folder=None):
    """What is recorded for a packed picture: //<folder>/<name> (the folder is the product's prefix unless named), never a
    folder of this machine."""
    return f"//{folder or runtime.prefix()}/{name}"

def pack(image, folder=None, name=None):
    """Pack a picture into the .blend (kept with a fake user) and give it a plain recorded path, //<folder>/<name>: the
    product's prefix and a clean form of the image's name unless they are given. Returns the image."""
    name = name or bpy.path.clean_name(os.path.splitext(image.name)[0]) + (os.path.splitext(image.filepath_raw)[1] or '.png')
    if not image.packed_file:
        if image.source == 'GENERATED':
            import numpy as np
            data = np.empty(len(image.pixels), dtype=np.float32)
            image.pixels.foreach_get(data)
            image.pixels.foreach_set(data)
            image.file_format = 'PNG'
        try:
            image.pack()
        except RuntimeError as exc:
            raise ValueError(f"Could not pack {image.name}: {str(exc).strip()}") from None
        if not image.packed_file:
            raise ValueError(f"Could not pack {image.name}")
    image[owner_key(folder)] = name
    image.use_fake_user = True
    plain = plain_path(name, folder)
    for packed in packed_data(image):
        packed.filepath = plain
    image.filepath_raw = plain
    retry(image)
    return image

def packed_data(image):
    """The image's packed files, each with its recorded path (a tiled or multi-view picture has several)."""
    return list(image.packed_files)

def packed_pictures():
    """(image, its plain file name) for every packed picture this product packed."""
    key = owner_key()
    return [(image, image[key]) for image in bpy.data.images if key in image and image.packed_file]

def is_missing(image):
    """Is there nothing to show for this picture (deleted datablock, file gone, unreadable)?"""
    if image is None:
        return True
    try:
        if image.name_full in _failed:
            return True
        return image.size[0] <= 0 or image.size[1] <= 0
    except ReferenceError:
        return True

def retry(image):
    """Look at a picture again (after packing, reloading or a new path)."""
    if image is not None:
        _failed.discard(image.name_full)
        for key in [k for k in _pixels if k[0] == image.name_full]:
            del _pixels[key]

def forget():
    _shader.clear()
    _failed.clear()
    _pixels.clear()

def display_target(image):
    """True when the picture is colour (shown through the display transform), False for Non-Color data (shown as stored)."""
    settings = image.colorspace_settings
    data = getattr(settings, 'is_data', None)
    if data is None:
        data = settings.name in {'Non-Color', 'Raw', 'Generic Data'}
    return not data

def pixel_array(image, frame=None):
    import numpy as np
    key = (image.name_full, tuple(image.size), frame)
    data = _pixels.get(key)
    if data is None:
        w, h = image.size
        data = np.empty(w * h * image.channels, dtype=np.float32)
        image.pixels.foreach_get(data)
        data = data.reshape(h, w, image.channels)
        if len(_pixels) > 6:
            _pixels.clear()
        _pixels[key] = data
    return data

def pixel(image, u, v, frame=None):
    """The colour (r, g, b, a) at (u, v) of the picture itself (not of the screen), as it is seen: a float picture's linear
    values are turned into display values, a Non-Color one is read as stored. None when it cannot be read."""
    if is_missing(image):
        return None
    try:
        data = pixel_array(image, frame)
    except (ReferenceError, RuntimeError, ValueError):
        return None
    h, w = data.shape[:2]
    px = data[min(h - 1, max(0, int(v * h))), min(w - 1, max(0, int(u * w)))]
    channels = len(px)
    rgb = [float(px[i]) if i < channels else float(px[0]) for i in range(3)]
    alpha = float(px[3]) if channels > 3 else 1.0
    if image.is_float and display_target(image):
        rgb = [12.92 * c if c <= 0.0031308 else 1.055 * max(c, 0.0) ** (1 / 2.4) - 0.055 for c in rgb]
    return tuple((min(1.0, max(0.0, c)) for c in rgb)) + (alpha,)

def shader():
    if not _shader:
        info = gpu.types.GPUShaderCreateInfo()
        between = gpu.types.GPUStageInterfaceInfo('oebs_canvas_picture')
        between.smooth('VEC2', 'uv_interp')
        info.vertex_in(0, 'VEC2', 'pos')
        info.vertex_in(1, 'VEC2', 'uv')
        info.vertex_out(between)
        info.push_constant('MAT4', 'ModelViewProjectionMatrix')
        info.push_constant('FLOAT', 'opacity')
        info.push_constant('INT', 'linear_in')
        info.push_constant('INT', 'display_out')
        info.push_constant('INT', 'premultiplied')
        info.push_constant('INT', 'grey')
        info.sampler(0, 'FLOAT_2D', 'image')
        info.fragment_out(0, 'VEC4', 'FragColor')
        info.vertex_source(VERTEX)
        info.fragment_source(FRAGMENT)
        _shader.append(gpu.shader.create_from_info(info))
    return _shader[0]

def texture(image):
    """The picture's GPU texture, or None when it cannot be had (then it is drawn as missing). A background Blender has no
    GPU: None there."""
    if bpy.app.background or is_missing(image):
        return None
    try:
        return gpu.texture.from_image(image)
    except (RuntimeError, ValueError, SystemError):
        _failed.add(image.name_full)
        return None

def image_texture(image):
    """texture(image), under a name draw_picture's `texture` keyword does not hide."""
    return texture(image)

def linear_in(image, tex):
    """Do the texture's values need the display transform? Only for colour pictures whose texture hands over linear values."""
    return display_target(image) and tex.format in LINEAR_FORMATS

def draw_picture(image, corners, uvs, opacity=1.0, display_out=True, grey=False, texture=None):
    """Draw a picture filling `corners` (screen pixels, bottom-left round) with the image's `uvs` at them, faded by `opacity`.
    Returns False when the picture is missing."""
    tex = texture if texture is not None else image_texture(image)
    if tex is None:
        return False
    s = shader()
    batch = batch_for_shader(s, 'TRI_FAN', {'pos': corners, 'uv': uvs})
    draw.own_blend()
    s.bind()
    s.uniform_float('ModelViewProjectionMatrix', gpu.matrix.get_projection_matrix() @ gpu.matrix.get_model_view_matrix())
    s.uniform_float('opacity', opacity)
    s.uniform_int('linear_in', 1 if linear_in(image, tex) else 0)
    s.uniform_int('display_out', 1 if display_out else 0)
    s.uniform_int('premultiplied', 1 if getattr(image, 'alpha_mode', 'STRAIGHT') == 'PREMUL' else 0)
    s.uniform_int('grey', 1 if grey else 0)
    s.uniform_sampler('image', tex)
    batch.draw(s)
    return True

@persistent
def keep_paths_before_save(filepath='', *_unused):
    """What is written for a packed picture is //<prefix>/<file> and nothing else, wherever the file is saved.

    A packed picture has two recorded paths: the one on its packed data is set plain here. The image's own path is relative,
    and Blender rewrites every relative path when a file is saved into another folder (Save As, Save Copy), which would leave
    the folders between the two places in it: so before such a save it is pointed at the NEW folder's <prefix>, and rewritten
    it comes out plain."""
    if runtime.board_class is None:
        return
    here = os.path.dirname(bpy.data.filepath)
    there = os.path.dirname(bpy.path.abspath(filepath)) if isinstance(filepath, str) and filepath else here
    moving = bool(here) and os.path.normcase(os.path.normpath(here)) != os.path.normcase(os.path.normpath(there))
    for image, name in packed_pictures():
        plain = plain_path(name)
        for packed in packed_data(image):
            if packed.filepath != plain:
                packed.filepath = plain
        path = plain
        if moving:
            try:
                path = '//' + os.path.relpath(os.path.join(there, runtime.prefix(), name), here).replace('\\', '/')
            except ValueError:
                path = f"{runtime.prefix()}/{name}"
        if image.filepath_raw != path:
            image.filepath_raw = path

@persistent
def keep_paths_after_save(*_unused):
    """After a copy was saved into another folder, the open file still has the paths set for the copy: back to plain."""
    if runtime.board_class is None:
        return
    for image, name in packed_pictures():
        if image.filepath_raw != plain_path(name):
            image.filepath_raw = plain_path(name)
HANDLERS = (('save_pre', keep_paths_before_save), ('save_post', keep_paths_after_save))

def register_handlers():
    """For a product with pictures (Board.uses_pictures): keep packed paths plain across saves. Called by register_board."""
    unregister_handlers()
    for name, func in HANDLERS:
        getattr(bpy.app.handlers, name).append(func)

def unregister_handlers():
    """Remove this copy's handlers (by name and module: a reloaded module has new function objects)."""
    for name, func in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        for handler in [h for h in handlers if getattr(h, '__name__', '') == func.__name__ and getattr(h, '__module__', '') == func.__module__]:
            handlers.remove(handler)
