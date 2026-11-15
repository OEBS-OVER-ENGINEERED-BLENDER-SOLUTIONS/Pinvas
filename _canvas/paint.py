"""Painter: drawing in board units for Board.draw_content. Positions and sizes follow the board's pan and zoom."""
from contextlib import contextmanager
from . import draw, media

class Painter:

    def __init__(self, host, x0, y0, k):
        self.host, self.x0, self.y0, self.k = (host, x0, y0, k)

    @property
    def zoom(self):
        """Pixels per board unit now (pan/zoom times Resolution Scale)."""
        return self.host.view[2] * self.k

    def to_screen(self, point):
        cx, cy = self.host.canvas_point(point)
        return (self.x0 + cx, self.y0 + cy)

    def box(self, bounds, colour, radius=0.0):
        """A filled box over board bounds (min_x, min_y, max_x, max_y); `radius` in board units."""
        (ax, ay), (bx, by) = (self.to_screen(bounds[:2]), self.to_screen(bounds[2:]))
        draw.box(ax, ay, bx - ax, by - ay, colour, radius * self.zoom)

    def outline(self, bounds, colour, radius=0.0, width=1.0):
        (ax, ay), (bx, by) = (self.to_screen(bounds[:2]), self.to_screen(bounds[2:]))
        draw.outline(ax, ay, bx - ax, by - ay, colour, radius * self.zoom, width)

    def line(self, points, colour, width=1.0):
        """A line through board points; `width` in screen pixels."""
        draw.polyline(tuple((self.to_screen(p) for p in points)), colour, width)

    def text(self, point, value, size, colour):
        """Text with its baseline starting at a board point; `size` in board units (it grows with the zoom)."""
        x, y = self.to_screen(point)
        draw.text(x, y, value, size * self.zoom, colour)

    @contextmanager
    def turned(self, centre, angle):
        """Everything drawn inside the `with` block is turned by `angle` (radians, counter-clockwise) about the board point
        `centre`: an item drawn as its upright box shows turned. Nothing changes in a background Blender (no GPU)."""
        import bpy
        if not angle or bpy.app.background:
            yield self
            return
        import gpu
        from mathutils import Matrix
        cx, cy = self.to_screen(centre)
        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(Matrix.Translation((cx, cy, 0.0)) @ Matrix.Rotation(angle, 4, 'Z') @ Matrix.Translation((-cx, -cy, 0.0)))
            yield self
