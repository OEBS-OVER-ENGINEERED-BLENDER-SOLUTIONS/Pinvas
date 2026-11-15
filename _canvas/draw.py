"""Low-level drawing: filled and outlined boxes, lines and text, in region pixels.

Every shape is turned into a GPU batch once and kept, so a board that did not change costs a few
draw calls per redraw rather than rebuilding geometry.

Colours are given straight (r, g, b, a) and drawn premultiplied, with the blend mode set by
`begin()`. Docked, the sidebar is put over the viewport by the alpha it was painted with, and
straight alpha blending leaves that alpha lowered wherever something translucent was drawn: the
viewport showed through a half-transparent box on a solid board. Premultiplied blending keeps
the board solid.
"""
import math
from contextlib import contextmanager

import blf
import gpu
from gpu_extras.batch import batch_for_shader

_shaders = {}
_batches = {}
BATCH_LIMIT = 2000


def shader(name):
    found = _shaders.get(name)
    if found is None:
        found = _shaders[name] = gpu.shader.from_builtin(name)
    return found


def cached(key, make):
    batch = _batches.get(key)
    if batch is None:
        if len(_batches) > BATCH_LIMIT:
            _batches.clear()
        batch = _batches[key] = make()
    return batch


def premultiplied(color):
    r, g, b, a = color
    return (r * a, g * a, b * a, a)


def begin():
    gpu.state.blend_set('ALPHA_PREMULT')


def own_blend():
    """Text drawing sets a blend mode of its own and leaves it set, so every shape sets premultiplied blending again before it draws."""
    gpu.state.blend_set('ALPHA_PREMULT')


def end():
    gpu.state.blend_set('NONE')


def clear_cache():
    _batches.clear()
    _shaders.clear()


def rounded_points(x, y, w, h, radius, segments=4):
    """The outline of a box with rounded corners, counter-clockwise from the bottom-left corner's arc."""
    r = max(0.0, min(radius, w / 2, h / 2))
    if r < 0.5:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    corners = ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180))
    points = []
    for cx, cy, start in corners:
        for i in range(segments + 1):
            a = math.radians(start + 90 * i / segments)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def box(x, y, w, h, color, radius=0.0):
    if w <= 0 or h <= 0:
        return
    s = shader('UNIFORM_COLOR')
    key = ('box', round(x, 2), round(y, 2), round(w, 2), round(h, 2), round(radius, 2))

    def make():
        outline = rounded_points(x, y, w, h, radius)
        centre = (x + w / 2, y + h / 2)
        tris = []
        for i in range(len(outline)):
            tris += [centre, outline[i], outline[(i + 1) % len(outline)]]
        return batch_for_shader(s, 'TRIS', {"pos": tris})
    batch = cached(key, make)
    own_blend()
    s.bind()
    s.uniform_float("color", premultiplied(color))
    batch.draw(s)


def lines(segments, color, width=1.0):
    """Straight lines: `segments` is a flat sequence of points, two per line."""
    if not segments:
        return
    s = shader('POLYLINE_UNIFORM_COLOR')
    key = ('lines', tuple(segments))
    batch = cached(key, lambda: batch_for_shader(s, 'LINES', {"pos": list(segments)}))
    own_blend()
    s.bind()
    s.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    s.uniform_float("lineWidth", width)
    s.uniform_float("color", premultiplied(color))
    batch.draw(s)


def polyline(points, color, width=1.0):
    """One connected line through the points (its joins stay whole, unlike separate segments)."""
    if len(points) < 2:
        return
    s = shader('POLYLINE_UNIFORM_COLOR')
    key = ('polyline', tuple(points))
    batch = cached(key, lambda: batch_for_shader(s, 'LINE_STRIP', {"pos": list(points)}))
    own_blend()
    s.bind()
    s.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    s.uniform_float("lineWidth", width)
    s.uniform_float("color", premultiplied(color))
    batch.draw(s)


def outline(x, y, w, h, color, radius=0.0, width=1.0):
    if w <= 0 or h <= 0:
        return
    # Half a pixel in, so a one-pixel outline lands on whole pixels instead of smearing over two.
    points = rounded_points(x + 0.5, y + 0.5, w - 1, h - 1, radius)
    segments = []
    for i in range(len(points)):
        segments += [points[i], points[(i + 1) % len(points)]]
    lines(tuple(segments), color, width)


def quad(corners, color):
    """A filled four-sided shape (a turned picture's box), corners in order round it."""
    s = shader('UNIFORM_COLOR')
    a, b, c, d = corners
    batch = batch_for_shader(s, 'TRIS', {"pos": (a, b, c, a, c, d)})
    own_blend()
    s.bind()
    s.uniform_float("color", premultiplied(color))
    batch.draw(s)


def polygons(shapes, color):
    """Filled convex polygons (each a list of points), in one batch."""
    tris = []
    for shape in shapes:
        for i in range(1, len(shape) - 1):
            tris += [shape[0], shape[i], shape[i + 1]]
    if not tris:
        return
    s = shader('UNIFORM_COLOR')
    batch = batch_for_shader(s, 'TRIS', {"pos": tris})
    own_blend()
    s.bind()
    s.uniform_float("color", premultiplied(color))
    batch.draw(s)


def text_turned(x, y, value, size, color, angle):
    """Text with its baseline starting at (x, y), turned by `angle` (radians, counter-clockwise)."""
    if not value:
        return
    blf.size(0, size)
    blf.enable(0, blf.ROTATION)
    try:
        blf.rotation(0, angle)
        blf.position(0, x, y, 0)
        blf.color(0, *color)
        blf.draw(0, value)
    finally:
        blf.disable(0, blf.ROTATION)


def wrap(value, size, width):
    """The lines `value` makes at `size` within `width` pixels: its own line breaks kept, long lines broken between words."""
    lines = []
    for paragraph in str(value).split('\n'):
        line = ""
        for word in paragraph.split(' '):
            trial = word if not line else line + ' ' + word
            if line and text_width(trial, size) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


def polygon_outline(points, color, width=1.0):
    """A closed outline through the points, in order."""
    segments = []
    for i in range(len(points)):
        segments += [points[i], points[(i + 1) % len(points)]]
    lines(tuple(segments), color, width)


def text_width(value, size):
    blf.size(0, size)
    return blf.dimensions(0, str(value))[0]


def fit_text(value, size, max_width):
    """`value`, shortened with an ellipsis until it fits `max_width` pixels."""
    value = str(value)
    if max_width is None or text_width(value, size) <= max_width:
        return value
    while value and text_width(value + "…", size) > max_width:
        value = value[:-1]
    return value + "…" if value else ""


def text(x, y, value, size, color, max_width=None):
    value = fit_text(value, size, max_width)
    if not value:
        return
    blf.size(0, size)
    blf.position(0, round(x), round(y), 0)
    blf.color(0, *color)
    blf.draw(0, value)


def text_centred(cx, cy, value, size, color, max_width=None):
    """Text centred on (cx, cy): the middle of a capital letter sits on cy."""
    value = fit_text(value, size, max_width)
    blf.size(0, size)
    width = blf.dimensions(0, value)[0]
    cap = blf.dimensions(0, "H")[1]
    text(cx - width / 2, cy - cap / 2, value, size, color)


@contextmanager
def clipped(x, y, w, h):
    """Draw only inside (x, y, w, h), given in region pixels, and never outside the region itself."""
    old = gpu.state.scissor_get()
    vx, vy, vw, vh = gpu.state.viewport_get()
    left, bottom = max(vx, vx + int(math.floor(x))), max(vy, vy + int(math.floor(y)))
    right, top = min(vx + vw, vx + int(math.ceil(x + w))), min(vy + vh, vy + int(math.ceil(y + h)))
    gpu.state.scissor_test_set(True)
    gpu.state.scissor_set(left, bottom, max(1, right - left), max(1, top - bottom))
    try:
        yield
    finally:
        gpu.state.scissor_set(*old)
