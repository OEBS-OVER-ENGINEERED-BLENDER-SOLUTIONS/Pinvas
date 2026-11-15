"""The board's view maths: where a board point lands on screen, panning, zooming, fitting, the grid, and where a
floating board may sit. No Blender inside, so all of it is tested in plain Python.

Conventions
- Canvas pixels are measured from the bottom-left corner of the board area, y up (Blender's way).
- A view is (offset_x, offset_y, zoom): the board's origin sits `offset` pixels from the centre of the board area,
  and one board unit is `zoom * scale` pixels, `scale` being Blender's Resolution Scale.
- Rectangles are (x, y, width, height); bounds are (min_x, min_y, max_x, max_y).
"""
import math

ZOOM_MIN = 0.02
ZOOM_MAX = 32.0
ZOOM_STEP = 1.2            # one wheel notch
HOME_VIEW = (0.0, 0.0, 1.0)


def clamp_zoom(zoom):
    return min(ZOOM_MAX, max(ZOOM_MIN, zoom))


def board_to_canvas(view, point, centre, scale):
    ox, oy, zoom = view
    k = zoom * scale
    return (centre[0] + ox + point[0] * k, centre[1] + oy + point[1] * k)


def canvas_to_board(view, point, centre, scale):
    ox, oy, zoom = view
    k = zoom * scale
    return ((point[0] - centre[0] - ox) / k, (point[1] - centre[1] - oy) / k)


def pan_by(view, dx, dy):
    return (view[0] + dx, view[1] + dy, view[2])


def zoom_about(view, anchor, centre, zoom):
    """The view at a new zoom, with the board point under `anchor` (canvas pixels) left where it is."""
    ox, oy, old = view
    new = clamp_zoom(zoom)
    ratio = new / old
    ax, ay = anchor[0] - centre[0], anchor[1] - centre[1]
    return (ax - (ax - ox) * ratio, ay - (ay - oy) * ratio, new)


def zoom_steps(zoom, steps):
    """`steps` wheel notches in (positive) or out (negative) from `zoom`, snapping to 100% when passing it."""
    new = clamp_zoom(zoom * ZOOM_STEP ** steps)
    if (zoom - 1.0) * (new - 1.0) < 0:
        return 1.0          # crossing 100% stops on it, so 100% is always one notch away
    return new


def zoom_label(zoom):
    percent = zoom * 100
    return f"{percent:.0f}%" if percent >= 10 else f"{percent:.1f}%"


def grid_spacing(zoom, scale, minimum=24.0):
    """Board units between minor grid lines: the smallest of 1, 2, 5, 10, 20, 50... at least `minimum` pixels apart."""
    pixels = max(1e-9, zoom * scale)
    want = minimum / pixels
    power = 10 ** math.floor(math.log10(want))
    for step in (1, 2, 5, 10):
        if step * power >= want:
            return step * power
    return 10 * power


def grid_lines(view, size, scale, minimum=24.0, major_every=5):
    """The grid's vertical and horizontal lines inside a board area of `size` (width, height):
    ([(x, is_major)], [(y, is_major)]) in canvas pixels."""
    width, height = size
    centre = (width / 2, height / 2)
    spacing = grid_spacing(view[2], scale, minimum)
    left, bottom = canvas_to_board(view, (0, 0), centre, scale)
    right, top = canvas_to_board(view, (width, height), centre, scale)

    def run(low, high, axis):
        lines = []
        first = math.ceil(low / spacing)
        last = math.floor(high / spacing)
        for n in range(first, last + 1):
            at = board_to_canvas(view, (n * spacing, n * spacing), centre, scale)[axis]
            lines.append((at, n % major_every == 0))
        return lines
    return run(left, right, 0), run(bottom, top, 1)


def inside(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def open_rect(window, covers):
    """The part of a viewport's main region that nothing lies over: (x, y, width, height) in the region's own pixels.

    `window` is the main region as (x, y, width, height) in window pixels; `covers` are the regions drawn over
    it (header, toolbar, sidebar, asset shelf) as (x, y, width, height, alignment). Only regions along an edge
    trim it; a region that would leave too little room is ignored.
    """
    wx, wy, ww, wh = window
    left, bottom, right, top = wx, wy, wx + ww, wy + wh
    for cx, cy, cw, ch, alignment in covers:
        if cw <= 1 or ch <= 1:
            continue
        if cx >= wx + ww or cx + cw <= wx or cy >= wy + wh or cy + ch <= wy:
            continue
        if alignment == 'TOP':
            top = min(top, cy)
        elif alignment == 'BOTTOM':
            bottom = max(bottom, cy + ch)
        elif alignment == 'LEFT':
            left = max(left, cx + cw)
        elif alignment == 'RIGHT':
            right = min(right, cx)
    if right - left < 1 or top - bottom < 1:
        return (0, 0, ww, wh)
    return (left - wx, bottom - wy, right - left, top - bottom)


def clamp_float(rect, bounds, min_size, margin, keep_min=False):
    """Keep a floating board usable and inside `bounds`: no smaller than `min_size`, no larger than the bounds. Where the
    bounds are too small for both, the bounds win; with `keep_min` the board keeps its least size and its bottom-left corner
    stays at the margin instead."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    if keep_min:
        w = max(min_size[0], min(w, max(min_size[0], bw - 2 * margin)))
        h = max(min_size[1], min(h, max(min_size[1], bh - 2 * margin)))
        x = min(max(x, bx + margin), max(bx + margin, bx + bw - w - margin))
        y = min(max(y, by + margin), max(by + margin, by + bh - h - margin))
        return (x, y, w, h)
    room_w, room_h = max(1.0, bw - 2 * margin), max(1.0, bh - 2 * margin)
    w = min(max(w, min_size[0]), room_w)
    h = min(max(h, min_size[1]), room_h)
    x = min(max(x, bx + margin), bx + bw - margin - w)
    y = min(max(y, by + margin), by + bh - margin - h)
    return (x, y, w, h)


def resize_from_corner(rect, dx, dy, bounds, min_size, margin):
    """Drag the bottom-right corner by (dx, dy) pixels. The top-left corner stays put; the size stops at
    `min_size` and at the edges of `bounds`."""
    x, y, w, h = rect
    bx, by, bw, bh = bounds
    top = y + h
    new_w = min(max(w + dx, min_size[0]), max(min_size[0], bx + bw - margin - x))
    new_h = min(max(h - dy, min_size[1]), max(min_size[1], top - (by + margin)))
    return clamp_float((x, top - new_h, new_w, new_h), bounds, min_size, margin)


def clamp_roaming(rect, areas, min_size, grip, strip, margin, limit, bounds=None):
    """Where a floating board that may cross editors may sit: (x, y, width, height).

    `areas` are the (x, y, width, height) of every editor it may enter, in the same pixels as `rect`. The left end of its bar
    (`grip` wide, `strip` tall) stays inside one of them (the one it is over, or the nearest), so it can always be dragged
    back; the rest may hang over anything. `limit` (width, height) is the largest it may be; `bounds` (x, y, width, height),
    when given, is the window, which it never leaves."""
    x, y, w, h = rect
    w = max(min_size[0], min(w, max(min_size[0], limit[0] - 2 * margin)))
    h = max(min_size[1], min(h, max(min_size[1], limit[1] - 2 * margin)))
    grip = min(w, grip)
    hold_x, hold_y = x + grip / 2, y + h - strip / 2

    def away(area):
        ax, ay, aw, ah = area
        return math.hypot(hold_x - min(max(hold_x, ax), ax + aw), hold_y - min(max(hold_y, ay), ay + ah))
    ax, ay, aw, ah = min(areas, key=away)
    x = min(max(x, ax + margin), max(ax + margin, ax + aw - grip - margin))
    top = min(max(y + h, ay + strip + margin), max(ay + strip + margin, ay + ah - margin))
    y = top - h
    if bounds is not None:
        bx, by, bw, bh = bounds
        x = min(max(x, bx + margin), max(bx + margin, bx + bw - w - margin))
        y = min(max(y, by + margin), max(by + margin, by + bh - h - margin))
    return (x, y, w, h)


def bounds_of(points):
    """(min_x, min_y, max_x, max_y) of some points, None for none."""
    points = list(points)
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def fit_view(bounds, size, scale, margin=0.06):
    """The view that shows `bounds` (board units) whole inside a board area of `size` pixels, centred, with a margin each side."""
    if bounds is None:
        return HOME_VIEW
    x0, y0, x1, y1 = bounds
    width, height = size
    room_w, room_h = max(1.0, width * (1 - 2 * margin)), max(1.0, height * (1 - 2 * margin))
    span_w, span_h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    zoom = clamp_zoom(min(room_w / (span_w * scale), room_h / (span_h * scale)))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return (-cx * zoom * scale, -cy * zoom * scale, zoom)
