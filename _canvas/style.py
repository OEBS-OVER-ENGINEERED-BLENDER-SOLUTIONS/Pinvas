"""Sizes (interface units, multiplied by Blender's Resolution Scale) and colours of the frame the framework paints
round a product's board. Colours are straight RGBA; draw.py premultiplies them."""
import math

BAR_HEIGHT = 30
BUTTON_HEIGHT = 22
PAD = 6
TAB_STRIP = 27            # the sidebar's own tab column, which stays Blender's
EDGE_GRIP = 12            # the sidebar's resize edge, which stays Blender's
CORNER = 16               # a floating board's resize corner
FLOAT_MIN = (220, 160)
FLOAT_MARGIN = 6
FLOAT_DEFAULT = (420, 360)
POP_ARROW = 34            # room left at a pop-out's top-right for Blender's arrow that brings its header back
NAV_ROOM = 104            # the viewport's navigation buttons, which a newly floated board leaves free
DRAG_START = 4            # pixels the mouse moves before a press becomes a drag (also a double-click's reach)
TIP_DELAY = 0.6           # seconds the mouse rests on a control before its tip shows
WHEEL_PAN = 40            # pixels a Ctrl/Shift + wheel notch pans
ZOOM_DRAG = 40.0          # pixels of Ctrl + middle drag per zoom step
ROAM_GRIP = 90            # a board crossing editors keeps this much of its bar's left end inside an editor, to drag it back
BANNER = 22               # the error banner under the bar
SIDE_MIN = 140            # the side panel is never narrower than this...
SIDE_MAX = 0.6            # ... nor wider than this share of the canvas
SIDE_GRIP = 8             # the width of the divider on its inner edge

BOARD = (0.155, 0.155, 0.16, 1.0)
GRID_MINOR = (1.0, 1.0, 1.0, 0.035)
GRID_MAJOR = (1.0, 1.0, 1.0, 0.075)
ORIGIN = (1.0, 1.0, 1.0, 0.16)
BAR = (0.105, 0.105, 0.11, 1.0)
BAR_RULE = (0.0, 0.0, 0.0, 0.5)
FRAME = (0.0, 0.0, 0.0, 0.85)
FRAME_LIGHT = (1.0, 1.0, 1.0, 0.08)
TEXT = (0.88, 0.88, 0.88, 1.0)
MUTED = (0.58, 0.58, 0.6, 1.0)
FAINT = (0.42, 0.42, 0.44, 1.0)
BUTTON = (0.19, 0.19, 0.2, 1.0)
BUTTON_HOVER = (0.25, 0.25, 0.27, 1.0)
CHIP = (0.0, 0.0, 0.0, 0.35)
CHIP_HOVER = (0.0, 0.0, 0.0, 0.55)
TIP_BACK = (0.05, 0.05, 0.055, 0.95)
ACCENT = (0.33, 0.56, 0.98, 1.0)
ERROR_BACK = (0.45, 0.1, 0.1, 0.92)
HANDLE_FILL = (1.0, 1.0, 1.0, 1.0)
HANDLE_EDGE = (0.1, 0.25, 0.55, 1.0)
MARQUEE_FILL = (0.33, 0.56, 0.98, 0.12)
LOCKED_EDGE = (1.0, 0.78, 0.3, 1.0)
GUIDE = (1.0, 0.35, 0.75, 0.9)        # a line a move snapped to
AXIS_X = (0.95, 0.3, 0.33, 1.0)        # Blender's own axis colours
AXIS_Y = (0.55, 0.86, 0.0, 1.0)
KEY_HINT_BACK = (0.07, 0.07, 0.075, 0.92)

TIPS = {
    ('fit',): "Show the whole board (Home)",
    ('undock',): "Open the board in a window of its own. Drag it onto a 3D View's sidebar to dock it again",
    ('float',): "Float the board over the viewport",
    ('dock',): "Put the board back in the sidebar",
    ('zoom100',): "Back to 100% (or press 1)",
    ('close',): "Close the board. Hiding and showing its tab opens it again",
    ('error',): "Click to hide this message",
}
TURN_STEP = math.radians(15)
HANDLE_SIZE = 5           # half the side of a corner handle
TURN_REACH = 22           # how far the turn handle stands out from the frame's top edge
GRAB = 8                  # how near a handle the mouse must be to take it
HIT_REACH = 6             # how near (screen pixels) a thin item (a stroke) must be to be hit
SCALE_REACH = 6          # a corner drag (Board.handles 'each') measures from the item's middle, never nearer than this
SNAP_PX = 8               # how near (screen pixels) an edge or centre must come to another's to snap to it
