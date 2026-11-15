"""The board as it was last painted, kept on the graphics card (an off-screen buffer), and put on screen as one image until
something it shows changes. A viewport redraws on every playback frame and every orbit; a board rarely changes in between.

A product turns it on with Board.cache = True. Host.paint decides when to paint afresh; this module only holds the picture.
Contract: Frame(on_built), .key, .painted, .fit(w, h) -> bool, .render(paint), .blit(x, y, w, h), .free()."""
import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

STEP = 256                # buffer sizes are rounded up to this, so a resize drag asks for a new buffer rarely
MAX_AGE = 1.0             # seconds after which an unchanged board is painted afresh anyway
_shader = []


def pixel_projection(width, height):
    """One unit to one pixel, measured from the bottom-left corner."""
    return Matrix(((2 / width, 0, 0, -1), (0, 2 / height, 0, -1), (0, 0, -1, 0), (0, 0, 0, 1)))


def image_shader():
    if not _shader:
        _shader.append(gpu.shader.from_builtin('IMAGE'))
    return _shader[0]


class Frame:

    def __init__(self, on_built=None):
        self.buffer = None
        self.size = (0, 0)
        self.key = None                 # what the stored picture shows (Host.paint's key)
        self.painted = 0.0
        self.unavailable = False
        self.quad = None
        self.want = None                # the size the next buffer is to have
        self.building = False           # a timer is about to build it
        self.on_built = on_built        # called after a build: the owner redraws

    def fit(self, width, height):
        """True when the stored picture can show width x height pixels right now.

        A buffer made INSIDE a redraw leaves the graphics context pointing at a 1x1 target for the rest of that redraw, so
        everything drawn after it lands in the wrong place. So a buffer is never made here: a timer makes it between redraws,
        and until it exists the caller paints straight to the screen."""
        if self.unavailable or bpy.app.background:
            return False
        if (self.buffer is not None and width <= self.size[0] and height <= self.size[1]
                and self.size[0] <= 2 * width + STEP and self.size[1] <= 2 * height + STEP):
            return True
        self.want = tuple(-(-int(v) // STEP) * STEP for v in (width, height))
        if not self.building:
            self.building = True
            bpy.app.timers.register(self.build, first_interval=0.0)
        return False

    def build(self):
        """Timer: make the buffer fit() asked for."""
        self.building = False
        want = self.want
        if want is None or self.unavailable or (self.buffer is not None and self.size == want):
            return None
        self.free()
        try:
            self.buffer = gpu.types.GPUOffScreen(want[0], want[1], format='RGBA8')
        except (RuntimeError, ValueError) as exc:
            print(f"OEBS Canvas: no drawing buffer ({exc}); painting directly")
            self.unavailable = True     # the board still works, painted straight to the screen on every redraw
            return None
        self.size = want
        if self.on_built is not None:
            self.on_built()
        return None

    def render(self, paint):
        """Run paint() with the buffer as the screen (its bottom-left corner at 0, 0)."""
        with self.buffer.bind():
            gpu.state.active_framebuffer_get().clear(color=(0.0, 0.0, 0.0, 0.0))
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(pixel_projection(*self.size))
                paint()

    def blit(self, x, y, width, height):
        """Put the stored picture on screen with its corner at (x, y): one textured quad."""
        shader = image_shader()
        place = (x, y, width, height, self.size)
        if self.quad is None or self.quad[0] != place:
            u, v = width / self.size[0], height / self.size[1]
            self.quad = (place, batch_for_shader(shader, 'TRI_FAN', {
                "pos": ((x, y), (x + width, y), (x + width, y + height), (x, y + height)),
                "texCoord": ((0, 0), (u, 0), (u, v), (0, v))}))
        gpu.state.blend_set('ALPHA_PREMULT')            # the buffer holds colour already multiplied by its alpha (draw.py)
        try:
            shader.bind()
            shader.uniform_sampler("image", self.buffer.texture_color)
            self.quad[1].draw(shader)
        finally:
            gpu.state.blend_set('NONE')

    def free(self):
        if self.buffer is not None:
            self.buffer.free()
        self.buffer = None
        self.key = None
        self.quad = None
        self.want = None                # a build still waiting for its timer has nothing to build
