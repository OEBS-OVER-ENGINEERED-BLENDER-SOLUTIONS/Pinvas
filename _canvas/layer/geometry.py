"""The board layer's geometry: where an item's corners are, what a point hits, the frame round a selection, scaling and
turning about a point, snapping, align and distribute, match size and nudge. No Blender inside, so all of it is tested in
plain Python.

Conventions
- An item is (x, y, width, height, rotation): its centre, its size and its turn (radians, anticlockwise), in board units.
- A frame is (centre_x, centre_y, half_width, half_height, rotation).
- A box is upright: (min_x, min_y, max_x, max_y).
"""
import math
LEAST = 0.0001

def frame_of(item):
    x, y, w, h, rotation = item
    return (x, y, w / 2, h / 2, rotation)

def frame_corners(frame):
    """The four corners of a frame: bottom-left, bottom-right, top-right, top-left of it before it was turned."""
    cx, cy, hw, hh, rotation = frame
    c, s = (math.cos(rotation), math.sin(rotation))
    return [(cx + dx * c - dy * s, cy + dx * s + dy * c) for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]

def item_corners(item):
    return frame_corners(frame_of(item))

def to_local(frame, point):
    """A board point in a frame's own unturned axes, measured from its centre."""
    cx, cy, _hw, _hh, rotation = frame
    dx, dy = (point[0] - cx, point[1] - cy)
    c, s = (math.cos(-rotation), math.sin(-rotation))
    return (dx * c - dy * s, dx * s + dy * c)

def contains(item, point):
    """Is the board point on the item (its turned box, edges included)?"""
    frame = frame_of(item)
    lx, ly = to_local(frame, point)
    return abs(lx) <= frame[2] and abs(ly) <= frame[3]

def bounds_of(points):
    """(min_x, min_y, max_x, max_y) of some points, None for none."""
    points = list(points)
    if not points:
        return None
    xs, ys = ([p[0] for p in points], [p[1] for p in points])
    return (min(xs), min(ys), max(xs), max(ys))

def items_bounds(items):
    """The upright box round some items (their turned corners included), None for none."""
    return bounds_of((corner for item in items for corner in item_corners(item)))

def selection_frame(items):
    """The frame round a selection: one item's own turned frame, or the upright frame round several."""
    if not items:
        return None
    if len(items) == 1:
        return frame_of(items[0])
    x0, y0, x1, y1 = items_bounds(items)
    return ((x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2, 0.0)

def scaled_about(items, anchor, fx, fy=None):
    """Every item scaled about the board point `anchor`, by `fx` across and `fy` up (fy defaults to fx): [(x, y, w, h)].

    An upright item's width takes fx and its height fy. A turned item has no axis of its own along x or y, so it keeps its
    shape and scales by the mean of the two. No item becomes smaller than LEAST."""
    fy = fx if fy is None else fy
    ax, ay = anchor
    out = []
    for x, y, w, h, rotation in items:
        if abs(math.sin(rotation)) < 1e-09:
            sw, sh = (abs(fx), abs(fy))
        else:
            sw = sh = math.sqrt(abs(fx * fy))
        out.append((ax + (x - ax) * fx, ay + (y - ay) * fy, max(LEAST, w * sw), max(LEAST, h * sh)))
    return out

def turned_about(items, centre, angle):
    """Every item turned by `angle` about the board point `centre`: [(x, y, rotation)]."""
    cx, cy = centre
    c, s = (math.cos(angle), math.sin(angle))
    out = []
    for x, y, _w, _h, rotation in items:
        dx, dy = (x - cx, y - cy)
        out.append((cx + dx * c - dy * s, cy + dx * s + dy * c, rotation + angle))
    return out

def scale_factor(anchor, start, now, least=0.01):
    """How much a drag from `start` to `now` scales something held at `anchor`: the drag measured along the line from the
    anchor to where it began (1 when it began on the anchor)."""
    dx, dy = (start[0] - anchor[0], start[1] - anchor[1])
    length = dx * dx + dy * dy
    if length < 1e-12:
        return 1.0
    along = ((now[0] - anchor[0]) * dx + (now[1] - anchor[1]) * dy) / length
    return max(least, along)

def scale_factors(anchor, start, now, least=0.01):
    """(fx, fy): a corner dragged from `start` to `now` with the opposite corner held at `anchor` scales across and up apart.
    An axis the drag began on (no distance from the anchor) does not scale."""
    out = []
    for i in (0, 1):
        span = start[i] - anchor[i]
        out.append(1.0 if abs(span) < 1e-12 else max(least, (now[i] - anchor[i]) / span))
    return tuple(out)

def snap(angle, step):
    """`angle` to the nearest whole number of `step`s."""
    return round(angle / step) * step

def wrap_angle(angle):
    """The same turn, between -pi and pi."""
    return (angle + math.pi) % (2 * math.pi) - math.pi

def snap_axis(value, candidates, threshold):
    """(value snapped to the nearest candidate line within `threshold`, that line); (value, None) when none is near."""
    best = min(candidates, key=lambda c: abs(c - value), default=None)
    if best is not None and abs(best - value) <= threshold:
        return (best, best)
    return (value, None)

def snap_move(box, dx, dy, others, threshold, lines_x=(), lines_y=(), ties='items'):
    """A move of the upright `box` by (dx, dy), snapped so one of its edges or its centre lines up with an edge or centre of
    one of the `others` boxes (or one of the extra board lines `lines_x`, `lines_y`) when within `threshold`, on each axis
    apart: (dx, dy, (line_x, line_y)), a line None where nothing snapped."""
    mine = ((box[0], (box[0] + box[2]) / 2, box[2]), (box[1], (box[1] + box[3]) / 2, box[3]))
    targets = ([v for o in others for v in (o[0], (o[0] + o[2]) / 2, o[2])], [v for o in others for v in (o[1], (o[1] + o[3]) / 2, o[3])])
    return snap_points(mine, dx, dy, targets, threshold, (lines_x, lines_y), ties)

def snap_centre(centre, dx, dy, others, threshold, lines_x=(), lines_y=(), ties='items'):
    """A move of a point (the lead item's centre) by (dx, dy), snapped to the centres of the `others` boxes or the extra
    board lines, on each axis apart: (dx, dy, (line_x, line_y))."""
    targets = ([(o[0] + o[2]) / 2 for o in others], [(o[1] + o[3]) / 2 for o in others])
    return snap_points(((centre[0],), (centre[1],)), dx, dy, targets, threshold, (lines_x, lines_y), ties)

def snap_points(mine, dx, dy, targets, threshold, lines=((), ()), ties='items'):
    """(dx, dy, (line_x, line_y)): the move by (dx, dy) of the positions `mine` ((xs), (ys)), snapped on each axis apart to
    the nearest of `targets` ((xs), (ys)) or the extra `lines` within `threshold`. Of equal distances, `ties` 'items' keeps
    the targets' first (within 1e-12), 'lines' tries the lines first and keeps the first exact minimum."""
    moved, found = ([dx, dy], [None, None])
    margin = 0.0 if ties == 'lines' else 1e-12
    for axis in (0, 1):
        shift = (dx, dy)[axis]
        best = None
        mine_now = [v + shift for v in mine[axis]]
        order = list(lines[axis]) + list(targets[axis]) if ties == 'lines' else list(targets[axis]) + list(lines[axis])
        for at in mine_now:
            for line in order:
                gap = line - at
                if abs(gap) <= threshold and (best is None or abs(gap) < abs(best[0]) - margin):
                    best = (gap, line)
        if best is not None:
            moved[axis] = shift + best[0]
            found[axis] = best[1]
    return (moved[0], moved[1], tuple(found))

def nudge(points, dx, dy):
    """Every point moved by (dx, dy)."""
    return [(x + dx, y + dy) for x, y in points]

def _bar(a, b, width):
    """A thick segment from a to b as four corners."""
    dx, dy = (b[0] - a[0], b[1] - a[1])
    length = math.hypot(dx, dy) or 1.0
    px, py = (-dy / length * width / 2, dx / length * width / 2)
    return [(a[0] + px, a[1] + py), (b[0] + px, b[1] + py), (b[0] - px, b[1] - py), (a[0] - px, a[1] - py)]

def _double_arrow(centre, direction, reach, ui, pad):
    cx, cy = centre
    ux, uy = direction
    nx, ny = (-uy, ux)
    tip, half = (reach + 5 * ui + pad, 3.4 * ui + pad)
    shapes = [_bar((cx - ux * reach, cy - uy * reach), (cx + ux * reach, cy + uy * reach), 2 * ui + pad)]
    for side in (1, -1):
        shapes.append([(cx + ux * side * tip, cy + uy * side * tip), (cx + ux * side * reach + nx * half, cy + uy * side * reach + ny * half), (cx + ux * side * reach - nx * half, cy + uy * side * reach - ny * half)])
    return shapes

def transform_icon(mode, pivot, mouse, ui=1.0):
    """Blender's feedback for a running G / S / R, in pixels, as {'dark': [...], 'light': [...]} convex polygons (the dark rim
    drawn first): four-way arrows at the mouse for a move ('G'); for a scale ('S') a double arrow along the line from the
    pivot, for a rotation ('R') one across it, with a dashed line from the marked pivot that stops short of the arrow."""
    dark, light = ([], [])
    if mode != 'G':
        dx, dy = (mouse[0] - pivot[0], mouse[1] - pivot[1])
        length = math.hypot(dx, dy)
        along = (dx / length, dy / length) if length > 1e-06 else (1.0, 0.0)
        end, on, off, at = (max(0.0, length - 12 * ui), 7 * ui, 5 * ui, 0.0)
        while at < end:
            stop = min(end, at + on)
            p0 = (pivot[0] + along[0] * at, pivot[1] + along[1] * at)
            p1 = (pivot[0] + along[0] * stop, pivot[1] + along[1] * stop)
            dark.append(_bar(p0, p1, 3.4 * ui))
            light.append(_bar(p0, p1, 1.5 * ui))
            at += on + off

        def ring(radius):
            return [(pivot[0] + radius * math.cos(i * math.tau / 16), pivot[1] + radius * math.sin(i * math.tau / 16)) for i in range(16)]
        dark.append(ring(5 * ui))
        light.append(ring(3 * ui))
        arrows = [(along, 8)] if mode == 'S' else [((-along[1], along[0]), 7)]
    else:
        arrows = [((1.0, 0.0), 7), ((0.0, 1.0), 7)]
    for direction, reach in arrows:
        dark += _double_arrow(mouse, direction, reach * ui, ui, 2.2 * ui)
        light += _double_arrow(mouse, direction, reach * ui, ui, 0.0)
    return dict(dark=dark, light=light)
