"""Board: the class a product subclasses. It says what the board is called and draws, and what clicks on it mean;
the framework does the rest (hosting, docking, the session, pan and zoom, tips).

One Board instance lives in each open view (its `host`). Class attributes a product must set: `prefix` (lower-case,
unique per add-on: it names every operator and class the framework registers) and `tab` (the sidebar tab)."""
import re
from . import runtime
PREFIX = re.compile('^[a-z][a-z0-9_]{1,30}$')

class Board:
    prefix = None
    tab = None
    title = None
    tips = {}
    palette = 'fixed'
    cache = False
    closable = False
    side = None
    side_width = 220
    items = ()
    storage = None
    uses_pictures = False
    host_class = None
    snap = 'edges'
    own_history = True
    live_drag = False
    toggle_on = 'release'
    box_select = 'overlap'
    box_ctrl = 'remove'
    snap_to = 'edges'
    handles = 'selection'
    shift_frees = False
    carries_on_place = True
    drag_start = None
    undo_before = False
    align_centre = 'bounds'
    distribute = 'gaps'
    turn_handle = True
    hit_order = 'top'
    handle_grab = 'circle'
    box_start = None
    snap_ties = 'items'
    session_class = None
    dock_class = None
    panel_class = None
    session_name = 'session'
    panel_name = 'board'
    registry_key = None

    def __init__(self, host):
        self.host = host

    def draw_content(self, paint):
        """Draw the board's own things (under the bar, clipped to the board area). `paint` draws in board units."""

    def draw_bar(self, ui):
        """The product's controls in the bar, right to left after the framework's. Immediate mode: called on every redraw,
        and again when a click lands on the bar, when `ui.button(...)` returns True for the button clicked."""

    def content_bounds(self):
        """(min_x, min_y, max_x, max_y) in board units of everything on the board, for Fit; None when empty."""
        return None

    def on_click(self, point, event, item=None):
        """A left click on the board area at `point` (board units). On a board with items, `item` is the one under the
        point (None on the empty board) and the layer has already selected it. Return True when it meant something."""
        return False

    def on_double_click(self, point, event, item=None):
        """A double-click on the board area at `point` (board units). Return True when it meant something; else the second
        click is treated as a plain click."""
        return False

    def draw_item(self, item, paint):
        """How one item looks (boards with items). `item` has its fields as attributes, and `rect` / `shape` where it is
        shown now (during a drag, where the drag would put it)."""
        paint.box(item.rect, (0.5, 0.5, 0.55, 1.0), radius=4)

    def on_selection_changed(self):
        """The selected items changed (Board.selected() gives them)."""

    def can_transform(self, item):
        """May this item be moved, scaled and turned? (A locked item never is.)"""
        return True

    def can_turn(self, item):
        """May this item be turned? (One that may not keeps its turn while the rest of a selection turns.)"""
        return True

    def carries(self, item):
        """The uids of the items that move with this one (a frame's contents). They move with it; they are not scaled or
        turned with it."""
        return ()

    def item_contains(self, item, shape, point, reach):
        """Is the board `point` on this item, shown as `shape` (x, y, width, height, rotation)? `reach` (board units) is how
        near counts for something thin: a stroke is hit along its line, not over its whole box."""
        from .layer import geometry
        return geometry.contains(shape, point)

    def handle_box(self, item):
        """Where an item's corner handles sit, (min_x, min_y, max_x, max_y) in board units, for an item drawn bigger than its
        shape; None: its shape (the default). A frame of handles round a selection is built from these too."""
        return None

    def canvas_px(self, n):
        """`n` interface pixels as the board's canvas pixels: by Blender's UI scale. A product whose canvas has its own
        scale divided out already returns n. The layer's handle sizes, reaches and drag start all go through it."""
        return n * runtime.ui_scale()

    def handle_reach(self):
        """How near (canvas pixels) a press must come to a handle to grab it."""
        from . import style
        return self.canvas_px(style.GRAB)

    def snap_reach(self):
        """How near (canvas pixels) a move must come to a line to snap to it."""
        from . import style
        return self.canvas_px(style.SNAP_PX)

    def snap_lines(self):
        """Extra board lines a move snaps to, ([x, ...], [y, ...]) in board units (a picture's centre line)."""
        return ((), ())

    @classmethod
    def interface_size(cls):
        """The size the board's own frame and content are drawn at (0.6 to 2.5): the Interface Size preference."""
        from . import prefs
        return prefs.choice('interface_size', 1.0)

    @classmethod
    def cross_editors(cls):
        """May a floating board leave its viewport and hang over the other editors: the Float Across Editors preference."""
        from . import prefs
        return bool(prefs.choice('cross_editors', False))

    @classmethod
    def should_open(cls, area):
        """Should the timer open a board in this 3D View now? Default: its tab shows (or its board floats there) and the
        board was not dismissed there (session.should_open_default)."""
        from . import session
        return session.should_open_default(area)

    @classmethod
    def should_replace(cls, host, area):
        """Should the timer close this open board and open a fresh one in its view (what it shows has changed)?"""
        return False

    @classmethod
    def after_open(cls, host, previous):
        """A board has opened in a view; `previous` is the board it replaced (should_replace), else None."""

    @classmethod
    def dismiss_token(cls, area):
        """What a dismissal (the bar's x) holds for: while this stays the same the board stays closed in that view. Default
        True: closed until the tab is hidden and shown again."""
        return True

    @classmethod
    def registry_extra(cls):
        """More entries for this copy's one-copy entry in driver_namespace (what another add-on's loader reads)."""
        return {}

    @classmethod
    def draw_viewport(cls, area, region):
        """Drawn in every 3D View's viewport region, wherever the board is (marks in the scene); nothing by default."""

    def snap_mode(self):
        """How a move snaps, asked at every step of a drag: 'edges', 'grid' or None. Default: `snap`. A product with a Snap
        toggle of its own returns it here."""
        return self.snap

    def pivot_point(self, items, kind):
        """The board point S ('scale'), R and the turn handle ('turn') work about, or None for each item about its own
        middle. Default: the middle of the selection's frame."""
        from .layer import geometry
        frame = geometry.selection_frame([item.shape for item in items])
        return frame[:2] if frame is not None else None

    def after_transform(self, shapes, kind, final):
        """A move, scale or turn ('move', 'scale', 'turn') shows `shapes` (uid -> (x, y, width, height, rotation)) at this
        step of the drag, or (`final`) is about to write them. The product may change or add entries (a mirrored twin):
        what is in `shapes` shows, and on the final call is written, all in the one undo step."""

    def press_first(self, point, event):
        """A left press on the board at `point`, before the layer looks at it (a drawing tool, a handle of the product's
        own). Return True to take it; the layer then does nothing with it."""
        return False

    def undo(self):
        """Ctrl+Z over the board, when the board keeps its own history (own_history). Default: the layer's history."""
        if self.host.layer is not None:
            self.host.layer.undo()

    def redo(self):
        """Ctrl+Shift+Z over the board, likewise."""
        if self.host.layer is not None:
            self.host.layer.redo()

    def action_label(self, name):
        """The undo step's name for a layer action ('Move', 'Scale', 'Rotate', 'Delete', ...)."""
        return name

    def push_undo(self, label, before=None):
        """Push one Blender undo step (every finished action does). With undo_before, `before` is the store's snapshot from
        before the action."""
        from .layer import history
        history.push_undo(label)

    def tip_for(self, key):
        """The tip for a control `key` this board drew, or None."""
        return self.tips.get(key)

    def draw_side(self, ui):
        """The product's controls in the side panel (Board.side), top to bottom: `ui` is a ui.ColumnUi, immediate mode as
        in draw_bar."""

    @classmethod
    def on_tick(cls, window):
        """The product's own work four times a second while Blender runs (following the scene, picking up a finished
        render): `window` is the first window. Keep it quick; there is no board instance here."""

    def adopt(self, other):
        """The board moves here from another view or a pop-out (`other` is that view's Board): take over what it showed."""

    def cache_key(self):
        """For a cached board: a value that changes whenever what draw_content draws changes for a reason the framework cannot
        see (a timer, another add-on, the scene). The framework already repaints after every hook it calls and on its own changes."""
        return None

    def fit(self):
        self.host.fit()

    def redraw(self):
        self.host.touch()
        runtime.redraw_area(self.host.area_id)

    def show_error(self, text):
        """Show `text` in the banner under the bar (a click hides it)."""
        self.host.error = str(text)
        self.redraw()

    def clear_error(self):
        self.host.error = ''
        self.redraw()

    def start_idle(self, step, first=1.2):
        """Run step() a piece at a time while nobody uses the board (idle.py); it returns True while there is more."""
        from . import idle
        return idle.start(self.host, step, first)

    def stop_idle(self):
        from . import idle
        idle.stop(self.host)

    def toast(self, text, seconds=2.0):
        """Show `text` near the bottom of the board for a moment."""
        from . import ui_input
        ui_input.toast(self.host, text, seconds)
        self.redraw()

    @property
    def mouse(self):
        """The board point under the mouse, or None when it is not over the board."""
        pointer = self.host.pointer
        return self.host.board_point(*pointer) if pointer is not None else None

    def selected(self):
        """The selected items, in the order they were selected (boards with items)."""
        return self.host.layer.selected_items() if self.host.layer is not None else []

    def select(self, items):
        """Select these items (or uids) only."""
        if self.host.layer is not None:
            self.host.layer.select([getattr(item, 'uid', item) for item in items])

    def store(self):
        """Where the board's items live (a layer.Store). The default keeps them on the Scene, saved with the .blend; a product
        with property groups of its own returns an adapter over them."""
        from .layer import SceneStore
        return SceneStore(type(self))

    def add(self, kind, at=None, **values):
        """Put a new item of type `kind` (an Item class, or an instance carrying values) on top of the board, centred on
        the board point `at` when given. Returns it."""
        if at is not None:
            values.setdefault('x', at[0])
            values.setdefault('y', at[1])
        item = self.store().add(kind, **values)
        self.redraw()
        return item

def has_layer(cls):
    """Does this board have the layer: item types of its own (Board.items), or a store of its own (Board.store)?"""
    return bool(getattr(cls, 'items', None)) or getattr(cls, 'store', None) is not Board.store

def validate(cls):
    """A product's Board class, checked before anything is registered."""
    if not isinstance(cls, type) or not issubclass(cls, Board):
        raise TypeError('register_board wants a Board subclass')
    if not isinstance(cls.prefix, str) or not PREFIX.match(cls.prefix):
        raise ValueError(f"{cls.__name__}.prefix must be lower-case letters, digits and _ (2-31 long), got {cls.prefix!r}")
    if not isinstance(cls.tab, str) or not cls.tab.strip():
        raise ValueError(f"{cls.__name__}.tab must name the sidebar tab")
    if cls.palette not in ('fixed', 'theme'):
        raise ValueError(f"{cls.__name__}.palette must be 'fixed' or 'theme', got {cls.palette!r}")
    from .layer import Item
    kinds = (cls.items,) if isinstance(cls.items, type) else tuple(cls.items or ())
    if not all((isinstance(kind, type) and issubclass(kind, Item) for kind in kinds)):
        raise TypeError(f"{cls.__name__}.items must be layer.Item subclasses")
    cls.items = kinds
