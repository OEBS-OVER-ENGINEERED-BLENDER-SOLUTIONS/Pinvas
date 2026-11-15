"""Plain math for the board: where a board point lands on screen, panning, zooming, and where a
floating board may sit. Nothing here touches Blender, so all of it can be tested anywhere.

Conventions
- Canvas pixels are measured from the bottom-left corner of the board area, y up (Blender's way).
- A view is (offset_x, offset_y, zoom): the board's origin sits `offset` pixels from the centre of
  the board area, and one board unit is `zoom * scale` pixels, `scale` being Blender's
  Resolution Scale. Board units are therefore "pixels at 100% zoom".
- Rectangles are (x, y, width, height).
- A placed item is (x, y, width, height, scale, rotation): its centre on the board, its picture's
  size in pixels, board units per picture pixel, and its turn in radians (counter-clockwise).
- A frame is (centre_x, centre_y, half_width, half_height, rotation): the box drawn round a
  selection, with the handles that scale and turn it.
"""
import math


def _framework():
    """The framework's view maths and item geometry: inside the add-on its bundled copy; loaded on its own by path (the
    plain-Python test) they are found the way a build finds them (OEBS_CANVAS_PACKAGE when set, else the _canvas beside this
    file)."""
    if __package__:
        from ._canvas import view
        from ._canvas.layer import geometry as items
        return view, items
    import importlib.util
    import os
    root = os.environ.get('OEBS_CANVAS_PACKAGE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), '_canvas')

    def load(name, *parts):
        spec = importlib.util.spec_from_file_location(name, os.path.join(root, *parts))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return load('oebs_canvas_view', 'view.py'), load('oebs_canvas_layer_geometry', 'layer', 'geometry.py')


_view, _items = _framework()
ZOOM_MIN, ZOOM_MAX, ZOOM_STEP, HOME_VIEW = _view.ZOOM_MIN, _view.ZOOM_MAX, _view.ZOOM_STEP, _view.HOME_VIEW
clamp_zoom, board_to_canvas, canvas_to_board, pan_by = _view.clamp_zoom, _view.board_to_canvas, _view.canvas_to_board, _view.pan_by
zoom_about, zoom_steps, zoom_label, grid_spacing = _view.zoom_about, _view.zoom_steps, _view.zoom_label, _view.grid_spacing
grid_lines, inside, open_rect, clamp_float = _view.grid_lines, _view.inside, _view.open_rect, _view.clamp_float
resize_from_corner, bounds_of, fit_view = _view.resize_from_corner, _view.bounds_of, _view.fit_view
frame_corners, to_local, snap, wrap_angle = _items.frame_corners, _items.to_local, _items.snap, _items.wrap_angle
scale_factor, transform_icon = _items.scale_factor, _items.transform_icon


# Items on the board


def scaled_about(items, anchor, factor, minimum=1e-4):
    """Every item scaled by `factor` about the board point `anchor`: [(x, y, scale)]."""
    ax, ay = anchor
    factor = max(factor, minimum / min(item[4] for item in items)) if items else factor
    return [(ax + (x - ax) * factor, ay + (y - ay) * factor, s * factor) for x, y, _w, _h, s, _r in items]


def turned_about(items, centre, angle):
    """Every item turned by `angle` about `centre`: [(x, y, rotation)]."""
    cx, cy = centre
    c, s = math.cos(angle), math.sin(angle)
    out = []
    for x, y, _w, _h, _s, r in items:
        dx, dy = x - cx, y - cy
        out.append((cx + dx * c - dy * s, cy + dx * s + dy * c, r + angle))
    return out


def row_layout(sizes, centre, height, gap):
    """New pictures in a row centred on `centre`, each `height` board units tall: [(x, y, scale)] in the order given."""
    if not sizes:
        return []
    scales = [height / max(1, h) for _w, h in sizes]
    widths = [w * s for (w, _h), s in zip(sizes, scales)]
    total = sum(widths) + gap * (len(sizes) - 1)
    x = centre[0] - total / 2
    out = []
    for width, s in zip(widths, scales):
        out.append((x + width / 2, centre[1], s))
        x += width + gap
    return out


def rects_overlap(a, b):
    """Do two (min_x, min_y, max_x, max_y) boxes overlap?"""
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


# Arranging (PureRef's Arrange menu). Sizes are the items' upright boxes (width, height) on the board;
# every function returns new centres, in the order given, and keeps the group's top-left corner.

def _top_left_anchor(centres, sizes):
    left = min(cx - w / 2 for (cx, _cy), (w, _h) in zip(centres, sizes))
    top = max(cy + h / 2 for (_cx, cy), (_w, h) in zip(centres, sizes))
    return left, top


def _maxrects(sizes, width, gap):
    """Place rectangles (each grown by `gap`) in a bin `width` wide and unbounded downwards: MaxRects, lowest bottom first.
    Returns their corners (x, y) measured down from the bin's top (y grows downwards), or None when one does not fit."""
    free = [(0.0, 0.0, width, 1e18)]
    placed = [None] * len(sizes)
    order = sorted(range(len(sizes)), key=lambda i: -max(sizes[i]))
    for i in order:
        w, h = sizes[i][0] + gap, sizes[i][1] + gap
        best, best_score = None, None
        for fx, fy, fw, fh in free:
            if w <= fw + 1e-9 and h <= fh + 1e-9:
                score = (fy + h, min(fw - w, fh - h))           # lowest bottom first, then the snuggest fit
                if best_score is None or score < best_score:
                    best, best_score = (fx, fy), score
        if best is None:
            return None
        bx, by = best
        placed[i] = (bx, by)
        split = []
        for fx, fy, fw, fh in free:
            if bx >= fx + fw or bx + w <= fx or by >= fy + fh or by + h <= fy:
                split.append((fx, fy, fw, fh))
                continue
            if bx > fx:
                split.append((fx, fy, bx - fx, fh))
            if bx + w < fx + fw:
                split.append((bx + w, fy, fx + fw - bx - w, fh))
            if by > fy:
                split.append((fx, fy, fw, by - fy))
            if by + h < fy + fh:
                split.append((fx, by + h, fw, fy + fh - by - h))
        free = [r for r in split if not any(o is not r and o[0] <= r[0] and o[1] <= r[1] and o[0] + o[2] >= r[0] + r[2]
                                            and o[1] + o[3] >= r[1] + r[3] for o in split)]
    return placed


def pack(centres, sizes, gap, aspect=1.0):
    """Autopacking: the items packed into one tight block with `gap` between them, its shape as near `aspect`
    (width / height) as the sizes allow, at the group's top-left corner. New centres in the order given."""
    if not sizes:
        return []
    left, top = _top_left_anchor(centres, sizes)
    area = sum((w + gap) * (h + gap) for w, h in sizes)
    widest = max(w for w, _h in sizes) + gap
    widths = [max(widest, math.sqrt(area * aspect) * f) for f in (0.6, 0.75, 0.9, 1.0, 1.1, 1.25, 1.45, 1.7, 2.0, 2.5, 3.2)]
    widths += [widest, sum(w + gap for w, _h in sizes)]           # one column, one row: never worse than lining them up
    tried = []
    for width in widths:
        spots = _maxrects(sizes, width, gap)
        if spots is None:
            continue
        right = max(x + w + gap for (x, _y), (w, _h) in zip(spots, sizes)) - gap
        depth = max(y + h + gap for (_x, y), (_w, h) in zip(spots, sizes)) - gap
        # What counts is how big the pictures show once the view fits them: the area of the view-shaped box round the
        # block. A long row of same-height pictures is the tightest block, but in a tall view it shows them tiny.
        fit = max(right, depth * aspect) * max(depth, right / aspect)
        tried.append((fit, right * depth, spots))
    _fit, _area, best = min(tried, key=lambda t: (round(t[0], 6), t[1]))
    return [(left + x + w / 2, top - y - h / 2) for (x, y), (w, h) in zip(best, sizes)]


def picture_uv(placed, point):
    """The image's (u, v) under a board point on a placed picture, or None when the point is off it."""
    frame = item_frame(placed)
    lx, ly = to_local(frame, point)
    hw, hh = frame[2], frame[3]
    if abs(lx) > hw or abs(ly) > hh or hw <= 0 or hh <= 0:
        return None
    return ((lx + hw) / (2 * hw), (ly + hh) / (2 * hh))


def ribbon(points, half_width, feather=1.0):
    """A thick line as triangles with soft edges: [(point, opaque)] where the colour is full on opaque corners and clear on the others,
    so it fades out across a `feather`-wide band centred on the line's true edge. A line whose first and last points are the same is
    closed. Corners are mitred (at most twice the width); the caller rounds sharp ones with discs."""
    pts = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
    closed = len(pts) > 2 and pts[0] == pts[-1]
    if closed:
        pts.pop()
    n = len(pts)
    if n < 2:
        return []
    edges = []
    for i in range(n if closed else n - 1):
        a, b = pts[i], pts[(i + 1) % n]
        length = math.hypot(b[0] - a[0], b[1] - a[1])
        edges.append((-(b[1] - a[1]) / length, (b[0] - a[0]) / length) if length > 1e-9 else (0.0, 1.0))
    inner = max(half_width - feather / 2, 0.0)
    outer = half_width + feather / 2
    left_in, right_in, left_out, right_out = [], [], [], []
    for i, p in enumerate(pts):
        before = edges[i - 1] if (closed or i > 0) else edges[0]
        after = edges[i] if (closed or i < n - 1) else edges[-1]
        mx, my = before[0] + after[0], before[1] + after[1]
        length = math.hypot(mx, my)
        if length < 1e-9:
            mx, my, scale = after[0], after[1], 1.0
        else:
            mx, my = mx / length, my / length
            scale = 1.0 / max(0.5, mx * after[0] + my * after[1])
        for side, near, far in ((1.0, left_in, left_out), (-1.0, right_in, right_out)):
            near.append((p[0] + side * mx * inner * scale, p[1] + side * my * inner * scale))
            far.append((p[0] + side * mx * outer * scale, p[1] + side * my * outer * scale))
    out = []
    for i in range(n if closed else n - 1):
        j = (i + 1) % n
        out += [(left_in[i], True), (right_in[i], True), (left_in[j], True), (left_in[j], True), (right_in[i], True), (right_in[j], True)]
        for near, far in ((left_in, left_out), (right_in, right_out)):
            out += [(near[i], True), (far[i], False), (far[j], False), (near[i], True), (far[j], False), (near[j], True)]
    return out


TURN_ARC = (math.radians(138.0), math.radians(402.0))       # the rotate cursor's arc, counter-clockwise through the bottom: open at the top


def turn_cursor(pivot, mouse, ui=1.0):
    """The picture of a rotate cursor, in pixels: {'dark': [...], 'light': [...]} convex polygons (the dark rim first). A curved arrow
    round `mouse`: an arc of about 260 degrees, open at the top, with an arrowhead on each end (about 270 degrees with the heads). It
    reads as turning whichever way the mouse is from the pivot, so it is not turned with it (`pivot` stays for the callers). Blender
    has no rotate cursor of its own, so the real one is hidden and this is drawn."""
    cx, cy = mouse
    radius = 10.5 * ui

    def at(angle, r):
        return (cx + r * math.cos(angle), cy + r * math.sin(angle))

    def build(half, pad):
        first, last = TURN_ARC
        steps = 20
        shapes = []
        for i in range(steps):
            a0 = first + (last - first) * i / steps
            a1 = first + (last - first) * (i + 1) / steps
            shapes.append([at(a0, radius - half), at(a0, radius + half), at(a1, radius + half), at(a1, radius - half)])
        for angle, side in ((first, -1.0), (last, 1.0)):
            base = at(angle, radius)
            along = (-side * math.sin(angle), side * math.cos(angle))           # on round the arc, past its end
            across = (math.cos(angle), math.sin(angle))
            wide, tip = 3.8 * ui + pad, 6.0 * ui + pad
            back = (base[0] - along[0] * pad * 0.5, base[1] - along[1] * pad * 0.5)
            shapes.append([(base[0] + along[0] * tip, base[1] + along[1] * tip),
                           (back[0] + across[0] * wide, back[1] + across[1] * wide), (back[0] - across[0] * wide, back[1] - across[1] * wide)])
        return shapes
    return dict(dark=build(1.2 * ui + 2.0 * ui, 2.0 * ui), light=build(1.2 * ui, 0.0))


# What Blender shows at the mouse while G / S / R runs: arrows, and for S / R a dashed line from the pivot.


# A Pinvas item is placed as (x, y, width, height, scale, rotation): its picture's size in pixels, shown `scale` times as
# big. The framework's item geometry takes (x, y, width, height, rotation) in board units.

def shape(item):
    x, y, w, h, s, r = item
    return (x, y, w * s, h * s, r)


def item_corners(item):
    """The four corners of a placed item on the board: bottom-left, bottom-right, top-right, top-left of its picture."""
    return _items.item_corners(shape(item))


def item_frame(item):
    return _items.frame_of(shape(item))


def item_contains(item, point):
    return _items.contains(shape(item), point)


def items_bounds(items):
    return _items.items_bounds([shape(item) for item in items])


def selection_frame(items):
    """The frame round a selection: one item's own turned box, or the upright box round several."""
    return _items.selection_frame([shape(item) for item in items])


def offset_shape(points, distance):
    """A polygon's points moved along their corners' outward bisectors by `distance` (negative: inward). No two points in a row alike."""
    pts = [p for i, p in enumerate(points) if p != points[i - 1]]
    n = len(pts)
    if n < 3:
        return list(pts)
    area = sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    turn = 1.0 if area > 0 else -1.0                       # counter-clockwise: the outward side of an edge is on its right
    normals = []
    for i in range(n):
        dx, dy = pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]
        length = math.hypot(dx, dy)
        normals.append((turn * dy / length, -turn * dx / length))
    out = []
    for i in range(n):
        a, b = normals[i - 1], normals[i]
        mx, my = a[0] + b[0], a[1] + b[1]
        length = math.hypot(mx, my)
        if length < 1e-9:
            out.append(pts[i])
            continue
        mx, my = mx / length, my / length
        scale = distance / max(0.5, mx * b[0] + my * b[1])         # a miter, kept from spiking at a sharp corner
        out.append((pts[i][0] + mx * scale, pts[i][1] + my * scale))
    return out


def soft_fill(shape, feather=1.0):
    """A filled convex shape with smooth edges, as triangles [(point, opaque)]: the body pulled in by half a feather, as a fan from its middle,
    and a band round it that fades to clear half a feather beyond the true edge. The GPU fills by pixel centres, so a plain fill is stair-stepped."""
    xs, ys = [p[0] for p in shape], [p[1] for p in shape]
    size = min(max(xs) - min(xs), max(ys) - min(ys))
    inner = offset_shape(shape, -min(feather / 2, size / 4))
    outer = offset_shape(shape, feather / 2)
    count = min(len(inner), len(outer))
    if count < 3:
        return []
    middle = (sum(p[0] for p in inner) / count, sum(p[1] for p in inner) / count)
    out = []
    for i in range(count):
        j = (i + 1) % count
        out += [(middle, True), (inner[i], True), (inner[j], True)]
        out += [(inner[i], True), (inner[j], True), (outer[j], False), (inner[i], True), (outer[j], False), (outer[i], False)]
    return out
