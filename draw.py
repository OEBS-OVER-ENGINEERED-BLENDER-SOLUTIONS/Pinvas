"""Pinvas draws with OEBS Canvas's drawing primitives: this module is the framework's draw module itself, under the name the
rest of Pinvas (and its tests) use, so a stub put on one is on the other. It adds two primitives of its own to it: `faded_triangles`
(the soft edge of a filled shape) and `soft_polyline` (a thick line with soft edges).

Both keep their batch built round the shape's own first point and draw it moved there, so panning the board (every point moves by the
same amount) finds the batch it already has; only a zoom or an edit builds a new one."""
import sys

from . import geometry
from ._canvas import draw as _draw


def _drawn_at(batch, shader, origin):
    """Draw a batch built relative to `origin`, at `origin`."""
    if origin == (0.0, 0.0):
        batch.draw(shader)
        return
    matrix = _draw.gpu.matrix
    matrix.push()
    try:
        matrix.translate(origin)
        batch.draw(shader)
    finally:
        matrix.pop()


def _soft_batch(band, colour):
    shader = _draw.shader('SMOOTH_COLOR')
    solid = _draw.premultiplied(colour)
    return shader, _draw.batch_for_shader(shader, 'TRIS', {"pos": [p for p, _ in band], "color": [solid if opaque else (0.0, 0.0, 0.0, 0.0) for _, opaque in band]})


def faded_triangles(band, colour, origin=(0.0, 0.0), key=None):
    """Triangles whose corners are each opaque (the colour) or not (clear): the colour fades out across them. `band` is (point, opaque),
    the points relative to `origin`. With a `key` the batch is kept and found again by it."""
    if not band:
        return
    if key is None:
        shader, batch = _soft_batch(band, colour)
    else:
        shader = _draw.shader('SMOOTH_COLOR')
        batch = _draw.cached(('fade', key, colour), lambda: _soft_batch(band, colour)[1])
    _draw.own_blend()
    _drawn_at(batch, shader, origin)


_native_polyline = _draw.polyline


def soft_polyline(points, colour, width=1.0):
    """One connected line through the points like `polyline`, but with soft edges: the GPU's own thick line is stair-stepped. A closed
    line repeats its first point at the end. A see-through colour is drawn as a plain line (the pieces of a soft one would overlap)."""
    if len(points) < 2:
        return
    if colour[3] < 0.999:
        _native_polyline(points, colour, width)
        return
    shader = _draw.shader('SMOOTH_COLOR')
    again = _draw.cached(('soft_exact', tuple(points), width, colour), lambda: _build(points, width, colour))       # the very same line, as on a still redraw
    _draw.own_blend()
    _drawn_at(again[0], shader, again[1])


def _build(points, width, colour):
    """(the batch, where it is drawn) of a soft line: the batch is kept by the line's shape alone, so a pan finds it."""
    ox, oy = points[0]
    relative = tuple((round(x - ox, 2), round(y - oy, 2)) for x, y in points)
    batch = _draw.cached(('soft_polyline', relative, round(width, 2), colour), lambda: _soft_batch(geometry.ribbon(relative, max(width, 1.0) / 2), colour)[1])
    return batch, (float(ox), float(oy))


# --- Smooth edges everywhere -----------------------------------------------------------------------------------------------------------------
# The framework's shapes are stair-stepped wherever an edge is not square to the pixels: a rounded box's corners, a turned box's sides, a disc, a
# slanted line, an outline with rounded corners. Each is drawn again here with the soft edge the tool strip's pictures already have (a fill pulled
# in by half a pixel and a one-pixel band that fades out; a line as a ribbon whose edges fade). Anything square to the pixels (a plain box, a
# horizontal or vertical line) is drawn as before: it is already sharp. The names stay the framework's, so every caller gets the soft version.

GROW = 1.0                       # the GPU's own thick line draws about a pixel fatter than it is asked: a soft line is asked for that pixel, so the look stays

_hard = dict(box=_draw.box, outline=_draw.outline, lines=_draw.lines, polyline=_draw.polyline, quad=_draw.quad, polygons=_draw.polygons,
             polygon_outline=_draw.polygon_outline)


def _drawn_smooth(key, build, colour, origin=(0.0, 0.0)):
    """Draw the triangles `build()` makes (each corner opaque or clear), kept by `key`, at `origin`."""
    shader = _draw.shader('SMOOTH_COLOR')
    batch = _draw.cached(key, lambda: _soft_batch(build(), colour)[1])
    _draw.own_blend()
    _drawn_at(batch, shader, origin)


def _square(a, b):
    """Is the segment a-b horizontal or vertical (so the GPU draws it sharp)?"""
    return abs(a[0] - b[0]) < 1e-6 or abs(a[1] - b[1]) < 1e-6


def _corners(radius):
    """How many segments make a corner of this radius."""
    return max(4, min(12, int(radius * 0.9) + 3))


def _ribbon_line(points, colour, width):
    """One connected line through the points with soft edges, whatever its colour (a see-through one too: a ribbon does not overlap itself)."""
    if len(points) < 2:
        return
    if colour[3] < 0.999 and width > 6.0:
        _native_polyline(points, colour, width)          # (a thick see-through line, a highlighter's: its corners would overlap and show)
        return
    shader = _draw.shader('SMOOTH_COLOR')
    again = _draw.cached(('aa_line', tuple(points), width, tuple(colour)), lambda: _build(tuple(points), max(width, 1.0) + GROW, tuple(colour)))
    _draw.own_blend()
    _drawn_at(again[0], shader, again[1])


def smooth_box(x, y, w, h, color, radius=0.0):
    if w <= 0 or h <= 0:
        return
    if radius < 0.5:
        _hard['box'](x, y, w, h, color, radius)
        return
    key = ('aa_box', round(w, 2), round(h, 2), round(radius, 2), tuple(color))
    _drawn_smooth(key, lambda: geometry.soft_fill(_draw.rounded_points(0.0, 0.0, w, h, radius, _corners(radius))), color, (x, y))


def smooth_outline(x, y, w, h, color, radius=0.0, width=1.0):
    if w <= 0 or h <= 0:
        return
    if radius < 0.5:
        _hard['outline'](x, y, w, h, color, radius, width)
        return
    inset = 0.5 + GROW / 2                       # (in by the half of the line's width, so a hairline covers two whole pixels, as the GPU's own does)
    points = _draw.rounded_points(x + inset, y + inset, w - 2 * inset, h - 2 * inset, radius, _corners(radius))
    _ribbon_line(tuple(points) + (tuple(points[0]),), color, width)


def smooth_lines(segments, color, width=1.0):
    """Straight lines, two points each: the sharp ones as before, the slanted ones as soft ribbons (segments that continue one another are one line)."""
    if not segments:
        return
    if color[3] < 0.999 and width > 6.0:
        _hard['lines'](segments, color, width)
        return
    pairs = [(segments[i], segments[i + 1]) for i in range(0, len(segments) - 1, 2)]
    square = [point for a, b in pairs if _square(a, b) for point in (a, b)]
    if square:
        _hard['lines'](tuple(square), color, width)
    runs = []
    for a, b in pairs:
        if _square(a, b):
            continue
        if runs and runs[-1][-1] == a:
            runs[-1].append(b)
        else:
            runs.append([a, b])
    if not runs:
        return

    def build():
        band = []
        for run in runs:
            band += geometry.ribbon(tuple(run), (max(width, 1.0) + GROW) / 2)
        return band
    _drawn_smooth(('aa_lines', tuple(segments), width, tuple(color)), build, color)


def _all_square(points, closed=False):
    """Is every side of this line horizontal or vertical (a box's outline, a ruled line)?"""
    pairs = list(zip(points, points[1:])) + ([(points[-1], points[0])] if closed else [])
    return all(_square(a, b) for a, b in pairs)


def smooth_polyline(points, color, width=1.0):
    if len(points) >= 2 and _all_square(points):
        _hard['polyline'](points, color, width)
        return
    _ribbon_line(tuple(points), color, width)


def smooth_polygon_outline(points, color, width=1.0):
    if len(points) < 2:
        return
    if _all_square(points, True):
        _hard['polygon_outline'](points, color, width)          # a box square to the pixels: the GPU's own line is sharp there
        return
    _ribbon_line(tuple(points) + (tuple(points[0]),), color, width)


def smooth_polygons(shapes, color):
    """Filled convex polygons (each a list of points) with soft edges."""
    shapes = [tuple(shape) for shape in shapes if len(shape) >= 3]
    if not shapes:
        return
    _drawn_smooth(('aa_polys', tuple(shapes), tuple(color)), lambda: [t for shape in shapes for t in geometry.soft_fill(list(shape))], color)


def smooth_quad(corners, color):
    """A filled four-sided shape: a box square to the pixels as before, a turned one with soft edges."""
    a, b, c, d = corners
    if _square(a, b) and _square(b, c) and _square(c, d) and _square(d, a):
        _hard['quad'](corners, color)
        return
    smooth_polygons([[a, b, c, d]], color)


_draw.polygons_hard = _hard['polygons']                 # a fill that must stay as it is (the pieces of a shape that is already soft)
_draw.box = smooth_box
_draw.outline = smooth_outline
_draw.lines = smooth_lines
_draw.polyline = smooth_polyline
_draw.polygon_outline = smooth_polygon_outline
_draw.polygons = smooth_polygons
_draw.quad = smooth_quad
_draw.smooth = dict(box=smooth_box, outline=smooth_outline, lines=smooth_lines, polyline=smooth_polyline, polygon_outline=smooth_polygon_outline,
                    polygons=smooth_polygons, quad=smooth_quad)          # (by their own names, for what is drawn when a caller has replaced the framework's)
_draw.smooth_scope = globals()


_draw.faded_triangles = faded_triangles
_draw.soft_polyline = soft_polyline
sys.modules[__name__] = _draw
