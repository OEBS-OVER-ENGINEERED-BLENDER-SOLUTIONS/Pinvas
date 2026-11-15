"""The layer's input handler (C-layer): selecting, moving, scaling and turning a board's items, by mouse and by Blender's
G / S / R keys, snapping, align and distribute, lock and hide, delete, nudge, and the board's own history. The host asks it
about every event on the board after the controls' handler (Host.inputs); it paints the items, the selection and its
handles inside the board area (paint_board), and the feedback of a running G / S / R over everything (paint).

While a drag runs, what it would do is a preview (uid -> shape) drawn in place of the stored values; only a finished action
writes the store, as one checkpoint of the history and one Blender undo step."""
import collections.abc
import contextlib
import math
from .. import draw, runtime, style, view
from . import geometry
from .geometry import transform_icon
from .history import History
MOVES = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
CONFIRM = {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER', 'SPACE'}
KEY_TRANSFORMS = {'G': ('key_move', 'Move'), 'S': ('key_scale', 'Scale'), 'R': ('key_turn', 'Rotate')}
ARROWS = {'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0), 'UP_ARROW': (0, 1), 'DOWN_ARROW': (0, -1)}
DIGITS = {**{name: str(i) for i, name in enumerate(('ZERO', 'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE'))}, **{f'NUMPAD_{i}': str(i) for i in range(10)}, 'PERIOD': '.', 'NUMPAD_PERIOD': '.', 'MINUS': '-', 'NUMPAD_MINUS': '-'}
PRECISE = 0.1
SCALE_SNAP = 0.1
ALIGN = (('LEFT', 'Left'), ('CENTER_X', 'Centre (Across)'), ('RIGHT', 'Right'), ('TOP', 'Top'), ('CENTER_Y', 'Centre (Up)'), ('BOTTOM', 'Bottom'))
TRANSFORM_NAMES = {'items': 'move', 'scale': 'scale', 'turn': 'turn'}

class Shown:
    """An item as it is shown during a drag: its stored fields, with the preview's place, size and turn."""

    def __init__(self, item, shape):
        self._item, self._shape = (item, shape)

    def __getattr__(self, name):
        shape = self.__dict__['_shape']
        if name in ('x', 'y', 'width', 'height', 'rotation'):
            return shape[('x', 'y', 'width', 'height', 'rotation').index(name)]
        if name == 'shape':
            return shape
        if name == 'rect':
            x, y, w, h, _r = shape
            return (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        return getattr(self.__dict__['_item'], name)

class Input:

    def __init__(self, host):
        self.host = host
        self.selection = []
        self.preview = {}
        self.marquee = None
        self.guides = (None, None)
        self.history = None
        self.focus = None
        self.quiet_depth = 0
        self.live = {}
        self.live_snapshot = None
        self.grabbed = None

    @property
    def board(self):
        return self.host.board

    def store(self):
        return self.board.store()

    def px(self, n):
        """`n` interface pixels in canvas pixels (Board.canvas_px)."""
        return self.host.call(lambda: self.board.canvas_px(n), failed=n * runtime.ui_scale(), touch=False)

    def visible(self):
        """The items shown, bottom to top: not hidden, and only the one in focus while there is one."""
        return [item for item in self.store().items() if not item.hidden and (self.focus is None or item.uid == self.focus)]

    def shape(self, item):
        return self.preview.get(item.uid, item.shape)

    def item_at(self, point):
        """The topmost item shown at a board point, or None. Hidden items are never hit; the product may say what counts
        as a hit (Board.item_contains)."""
        reach = self.px(style.HIT_REACH) / max(1e-09, self.host.view[2] * runtime.board_scale())
        nearest = self.board.hit_order == 'nearest'
        best = None
        for item in reversed(self.visible()):
            shape = self.shape(item)
            if self.host.call(lambda i=item, s=shape: self.board.item_contains(i, s, point, reach), failed=False, touch=False):
                if not nearest:
                    return item
                gap = math.hypot(point[0] - shape[0], point[1] - shape[1])
                if best is None or gap < best[0] - 1e-12:
                    best = (gap, item)
        return best[1] if best is not None else None

    def moving(self):
        """{uid: shape} of what a move takes: the selection that may move, and what each of those carries (Board.carries),
        unless it is locked or hidden."""
        chosen = self.movable()
        out = {item.uid: self.shape(item) for item in chosen}
        shown = {item.uid: item for item in self.store().items() if not item.hidden}
        for item in chosen:
            for uid in self.host.call(lambda i=item: self.board.carries(i), failed=(), touch=False) or ():
                held = shown.get(uid)
                if held is not None and uid not in out and (not held.locked):
                    out[uid] = self.shape(held)
        return out

    @property
    def selection_set(self):
        """The selection as a live set (add, discard, clear, compare with a set), for a product that names it its own way."""
        return SelectionView(self)

    def selected_items(self):
        by_uid = {item.uid: item for item in self.visible()}
        return [by_uid[uid] for uid in self.selection if uid in by_uid]

    def movable(self):
        """The selected items that may be transformed: not locked, and allowed by the product's can_transform."""
        return [item for item in self.selected_items() if not item.locked and self.host.call(lambda i=item: self.board.can_transform(i), failed=False, touch=False)]

    def select(self, uids):
        """Set the selection; the product hears of it (on_selection_changed) once, and only when it changed."""
        uids = list(dict.fromkeys(uids))
        if uids == self.selection:
            return
        self.selection = uids
        self.host.touch()
        self.host.call(self.board.on_selection_changed, touch=False)

    def remap(self, uids):
        """The product renumbered its items (`uids`: old uid -> new uid; one left out is gone). The selection, the focus,
        the previews and a running drag follow; the layer holds no other uid between calls."""
        self.select([uids[uid] for uid in self.selection if uid in uids])
        self.preview = {uids[uid]: shape for uid, shape in self.preview.items() if uid in uids}
        self.focus = uids.get(self.focus) if self.focus is not None else None
        drag = self.host.drag
        if drag and drag.get('owner') is self:
            if 'originals' in drag:
                drag['originals'] = {uids[uid]: shape for uid, shape in drag['originals'].items() if uid in uids}
            for key in ('uid', 'clicked', 'toggle_off'):
                if drag.get(key) is not None:
                    drag[key] = uids.get(drag[key])

    def prune(self):
        """Forget selected items that are gone or hidden."""
        shown = {item.uid for item in self.visible()}
        self.select([uid for uid in self.selection if uid in shown])

    @contextlib.contextmanager
    def action(self, label):
        """One finished action: one Blender undo step under the product's name for it, and (with a history of its own) one
        checkpoint of the board's history."""
        board = self.board
        label = board.action_label(label)
        own = board.own_history
        if own:
            if self.history is None:
                self.history = History(self.store(), push=lambda name, **before: self.board.push_undo(name, **before))
            self.history.catch_up()
        before = self.store().snapshot() if board.undo_before else None
        yield self.store()
        if own:
            self.history.checkpoint(label, before)
        elif not self.quiet_depth:
            board.push_undo(label) if before is None else board.push_undo(label, before=before)
        self.host.touch()

    @contextlib.contextmanager
    def quiet(self):
        """Actions inside push no undo step: for a product's own operator that Blender already makes one undo step."""
        self.quiet_depth += 1
        try:
            yield self
        finally:
            self.quiet_depth -= 1

    def carried_by(self, chosen):
        """{uid: [uids it carries]} for these items (Board.carries), leaving out locked and hidden ones."""
        shown = {item.uid: item for item in self.store().items() if not item.hidden}
        out = {}
        for item in chosen:
            held = self.host.call(lambda i=item: self.board.carries(i), failed=(), touch=False) or ()
            out[item.uid] = [uid for uid in held if uid in shown and (not shown[uid].locked) and (uid != item.uid)]
        return out

    def leaders(self, chosen):
        """These items less those another of them carries: what align and distribute line up (the rest go along)."""
        if not self.board.carries_on_place:
            return list(chosen)
        carried = {uid for uids in self.carried_by(chosen).values() for uid in uids}
        return [item for item in chosen if item.uid not in carried]

    def commit(self, label, kind=None):
        """Write the preview into the store. `kind` ('items', 'scale', 'turn'): the product's
        after_transform sees the shapes once more, final, before they are written."""
        if kind is not None and self.preview:
            self.host.call(self.board.after_transform, self.preview, TRANSFORM_NAMES.get(kind, kind), True, touch=False)
        unchanged = self.live and all((uid in self.live and all((abs(a - b) < 1e-09 for a, b in zip(shape, self.live[uid]))) for uid, shape in self.preview.items()))
        self.undo_live()
        if unchanged:
            self.preview, self.guides = ({}, (None, None))
            return
        changes, self.preview = (self.preview, {})
        self.guides = (None, None)
        if not changes:
            return
        with self.action(label) as store:
            for uid, shape in changes.items():
                item = store.get(uid)
                if item is not None:
                    store.set_shape(item, *shape)

    def nudge(self, dx, dy):
        """The arrow keys: the movable selection and what it carries."""
        moving = self.moving()
        if not moving:
            return
        with self.action('Nudge') as store:
            for uid, (x, y) in zip(list(moving), geometry.nudge([s[:2] for s in moving.values()], dx, dy)):
                item = store.get(uid)
                item.x, item.y = (x, y)

    def delete(self):
        uids = [item.uid for item in self.selected_items()]
        if not uids:
            return
        with self.action('Delete') as store:
            for uid in uids:
                store.remove(uid)
        self.prune()

    def undo(self):
        if self.history is not None and self.history.undo() is not None:
            self.preview = {}
            self.prune()
            self.host.touch()

    def redo(self):
        if self.history is not None and self.history.redo() is not None:
            self.preview = {}
            self.prune()
            self.host.touch()

    def reread(self):
        """Blender's undo or redo changed the scene: drop what the board remembered of the store."""
        if self.history is not None:
            self.history.reread()
        self.preview = {}
        self.marquee = None
        self.prune()
        self.host.touch()

    def handle_sets(self):
        """[(uids, frame, [corner points], turn point or None)] in canvas pixels: one set round the selected items that may
        move, or (Board.handles 'each') one round each of them."""
        movable = self.movable()
        if self.board.handles == 'each':
            groups = [movable] if len(movable) == 1 and len(self.selection) == 1 else []
        else:
            groups = [movable] if movable else []
        out = []
        for group in groups:
            frame, corners, turn = self.frame_handles(geometry.selection_frame([self.handle_shape(item) for item in group]))
            out.append((tuple((item.uid for item in group)), frame, corners, turn))
        return out

    def handle_shape(self, item):
        """The shape an item's handles sit on: its shape as shown now, or the product's handle_box round it (upright)."""
        shape = self.shape(item)
        shown = Shown(item, shape) if item.uid in self.preview else item
        box = self.host.call(lambda: self.board.handle_box(shown), failed=None, touch=False)
        if box is None:
            return shape
        x0, y0, x1, y1 = box
        return ((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0, 0.0)

    def handles(self):
        """(frame, [corner points], turn point or None) of the selected items that may move, in canvas pixels (with
        Board.handles 'each', the first item's); None when none."""
        sets = self.handle_sets()
        return sets[0][1:] if sets else None

    def frame_handles(self, frame):
        host = self.host
        corners = [host.canvas_point(c) for c in geometry.frame_corners(frame)]
        top_mid = ((corners[2][0] + corners[3][0]) / 2, (corners[2][1] + corners[3][1]) / 2)
        centre = host.canvas_point(frame[:2])
        dx, dy = (top_mid[0] - centre[0], top_mid[1] - centre[1])
        length = math.hypot(dx, dy)
        if length < 1e-06:
            dx, dy, length = (-math.sin(frame[4]), math.cos(frame[4]), 1.0)
        reach = self.px(style.TURN_REACH)
        turn = (top_mid[0] + dx / length * reach, top_mid[1] + dy / length * reach) if self.board.turn_handle else None
        return (frame, corners, turn)

    def handle_at(self, x, y):
        """('turn',) or ('scale', corner index) under canvas (x, y), or None. With Board.handles 'each', the handle's set
        is in self.grabbed (uids, frame) after a hit."""
        grab = self.host.call(self.board.handle_reach, failed=self.px(style.GRAB), touch=False)
        square = self.board.handle_grab == 'square'

        def near(hx, hy):
            return abs(x - hx) <= grab and abs(y - hy) <= grab if square else math.hypot(x - hx, y - hy) <= grab
        for uids, frame, corners, turn in self.handle_sets():
            self.grabbed = (uids, frame)
            if turn is not None and near(*turn):
                return ('turn',)
            for i, (cx, cy) in enumerate(corners):
                if near(cx, cy):
                    return ('scale', i)
        self.grabbed = None
        return None

    def handle(self, session, context, area, event, x, y):
        host = self.host
        kind, value = (event.type, event.value)
        if value != 'PRESS':
            return None
        inside = view.inside(host.board_rect, x, y)
        if self.view_only:
            if kind == 'LEFTMOUSE' and inside and (not host.on_controls(x, y)):
                return self.press_to_view(event, x, y)
            return None
        if kind == 'LEFTMOUSE':
            if host.on_controls(x, y) or not (inside or self.handle_at(x, y)):
                return None
            return self.press(session, context, event, x, y)
        if not inside:
            return None
        plain = not (event.ctrl or event.shift or event.alt or event.oskey)
        if kind in KEY_TRANSFORMS and plain:
            self.begin_key_transform(session, context, event, kind, x, y)
            return {'RUNNING_MODAL'}
        if kind in ARROWS and (not (event.ctrl or event.alt)):
            step = 10 if event.shift else 1
            self.nudge(ARROWS[kind][0] * step, ARROWS[kind][1] * step)
            return {'RUNNING_MODAL'}
        if kind == 'A' and (not (event.ctrl or event.shift)):
            self.select([] if event.alt else [item.uid for item in self.visible()])
            return {'RUNNING_MODAL'}
        if kind in {'DEL', 'X'} and plain:
            self.delete()
            return {'RUNNING_MODAL'}
        if kind == 'Z' and event.ctrl and (not event.alt) and self.board.own_history:
            self.host.call(self.board.redo if event.shift else self.board.undo)
            return {'RUNNING_MODAL'}
        return None

    @property
    def view_only(self):
        """A view-only edition of the board (written into a .blend by share.py): nothing on it edits, all of it shows."""
        return bool(getattr(self.board, 'view_only', False))

    def press_to_view(self, event, x, y):
        """A click on a view-only board: the product still hears it (and a double-click), but nothing is selected or moved."""
        host, board = (self.host, self.board)
        point = host.board_point(x, y)
        item = self.item_at(point)
        if not (event.shift or event.ctrl or event.alt) and host.double_click_at(x, y):
            if host.call(lambda: board.on_double_click(point, event, item=item), failed=True):
                return {'RUNNING_MODAL'}
        host.call(lambda: board.on_click(point, event, item=item))
        return {'RUNNING_MODAL'}

    def begin(self, session, context, event, kind, x, y, cursor=None, **extra):
        drag = session.begin_drag(context, self.host, event, kind, x, y, cursor)
        drag['owner'] = self
        drag['board_start'] = self.host.board_point(x, y)
        drag.update(extra)
        return drag

    def press(self, session, context, event, x, y, double=True):
        """A left press on the board: a handle, an item or the empty board. `double`: tell a double-click apart here (a
        product that does it itself, before handing the press on, passes False)."""
        host, board = (self.host, self.board)
        point = host.board_point(x, y)
        if host.call(lambda: board.press_first(point, event), failed=False):
            return {'RUNNING_MODAL'}
        item = self.item_at(point)
        if double and (not (event.shift or event.ctrl or event.alt)) and host.double_click_at(x, y):
            if host.call(lambda: board.on_double_click(point, event, item=item), failed=True):
                return {'RUNNING_MODAL'}
        grab = self.handle_at(x, y)
        if grab is not None:
            uids, frame = self.grabbed
            corners = geometry.frame_corners(frame)
            chosen = [item for item in self.movable() if item.uid in uids]
            originals = {i.uid: self.shape(i) for i in chosen}
            each = self.board.handles == 'each'
            if grab[0] == 'scale':
                self.begin(session, context, event, 'scale', x, y, 'SCROLL_XY', originals=originals, frame=frame, grip=corners[grab[1]], anchor=corners[(grab[1] + 2) % 4], uniform=self.uniform(chosen), each=each)
            else:
                about, individual = self.pivot(chosen, 'turn', frame)
                self.begin(session, context, event, 'turn', x, y, 'CROSSHAIR', originals=originals, frame=frame, fixed=self.fixed(chosen), about=about, individual=individual)
            return {'RUNNING_MODAL'}
        if item is not None:
            uid = item.uid
            toggle_off = None
            toggles = event.shift or (event.ctrl and board.toggle_on != 'press')
            if toggles:
                if board.toggle_on == 'press':
                    self.select([u for u in self.selection if u != uid] if uid in self.selection else self.selection + [uid])
                    host.call(lambda: board.on_click(point, event, item=item))
                    return {'RUNNING_MODAL'}
                if uid in self.selection:
                    toggle_off = uid
                else:
                    self.select(self.selection + [uid])
            elif uid not in self.selection:
                self.select([uid])
            self.begin(session, context, event, 'items', x, y, None, clicked=uid, toggle_off=toggle_off, originals=self.moving())
        else:
            mode = 'add' if event.shift else 'remove' if event.ctrl and board.box_ctrl == 'remove' else 'set'
            self.begin(session, context, event, 'box', x, y, None, mode=mode)
        host.call(lambda: board.on_click(point, event, item=item))
        return {'RUNNING_MODAL'}

    def drag(self, session, context, area, event, x, y, drag):
        if self.view_only:
            return None
        kind = drag['kind']
        if kind.startswith('key_'):
            return self.key_drag(session, context, event, x, y, drag)
        if event.type == 'ESC' and event.value == 'PRESS' or (event.type == 'RIGHTMOUSE' and event.value == 'PRESS'):
            self.cancel(session, context)
        elif event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self.release(drag, event, x, y)
            session.end_drag(context, self.host)
        elif event.type in MOVES:
            mx, my = runtime.window_mouse(event, x, y)
            dx, dy = (mx - drag['mouse'][0], my - drag['mouse'][1])
            if kind in {'items', 'box'} and (not drag['moved']):
                gap = math.hypot(dx, dy)
                if kind == 'box' and self.board.box_start is not None:
                    waiting = gap <= self.board.box_start
                else:
                    start = self.board.drag_start
                    waiting = gap < (start if start is not None else self.px(style.DRAG_START))
                if waiting:
                    return {'RUNNING_MODAL'}
                drag['moved'] = True
            self.follow(drag, event, x, y)
        return {'RUNNING_MODAL'}

    def cancel(self, session, context):
        """Esc or a right-click: the drag is dropped and everything shows as it was."""
        self.preview, self.marquee, self.guides = ({}, None, (None, None))
        self.undo_live()
        session.end_drag(context, self.host)

    def uniform(self, chosen):
        """Do these items scale only in proportion (one of them is a picture shown at a scale)?"""
        store = self.store()
        return any((store.keeps_aspect(item) for item in chosen))

    def fixed(self, chosen):
        """The uids among these that may not turn (Board.can_turn)."""
        return {item.uid for item in chosen if not self.host.call(lambda i=item: self.board.can_turn(i), failed=True, touch=False)}

    def follow(self, drag, event, x, y):
        """Show what the drag would do now (a preview; nothing is written until it ends)."""
        host = self.host
        kind = drag.get('shape', drag['kind'])
        if kind == 'box':
            self.marquee = (drag['start'], (x, y))
            return
        now = host.board_point(x, y)
        start = drag['board_start']
        originals = drag['originals']
        uids, shapes = (list(originals), list(originals.values()))
        if not uids:
            return
        if kind == 'items':
            dx, dy = (now[0] - start[0], now[1] - start[1])
            axis = drag.get('axis')
            dx, dy = (dx, 0.0) if axis == 'X' else (0.0, dy) if axis == 'Y' else (dx, dy)
            self.guides = (None, None)
            snap = host.call(self.board.snap_mode, failed=None, touch=False)
            if snap == 'grid' and event.ctrl:
                step = view.grid_spacing(host.view[2], runtime.board_scale())
                dx, dy = (geometry.snap(dx, step), geometry.snap(dy, step))
            elif snap == 'edges' and (not event.ctrl):
                others = [geometry.items_bounds([self.stored_shape(item)]) for item in self.visible() if item.uid not in originals]
                reach = host.call(self.board.snap_reach, failed=style.SNAP_PX, touch=False) / (host.view[2] * runtime.board_scale())
                lines_x, lines_y = host.call(self.board.snap_lines, failed=((), ()), touch=False) or ((), ())
                lead = drag.get('clicked') if drag.get('clicked') in originals else self.selection[-1] if self.selection and self.selection[-1] in originals else None
                if self.board.snap_to == 'centres' and lead is not None:
                    sx, sy, lines = geometry.snap_centre(originals[lead][:2], dx, dy, others, reach, lines_x, lines_y, ties=self.board.snap_ties)
                else:
                    sx, sy, lines = geometry.snap_move(geometry.items_bounds(shapes), dx, dy, others, reach, lines_x, lines_y, ties=self.board.snap_ties)
                if axis != 'Y':
                    dx, self.guides = (sx, (lines[0], self.guides[1]))
                if axis != 'X':
                    dy, self.guides = (sy, (self.guides[0], lines[1]))
            self.show('items', {uid: (s[0] + dx, s[1] + dy, s[2], s[3], s[4]) for uid, s in zip(uids, shapes)})
        elif kind == 'scale':
            self.show('scale', dict(zip(uids, self.scaled(drag, event, shapes, now))))
        elif kind == 'turn':
            cx, cy = drag.get('about') or drag['frame'][:2]
            angle = geometry.wrap_angle(math.atan2(now[1] - cy, now[0] - cx) - math.atan2(start[1] - cy, start[0] - cx))
            fixed = drag.get('fixed', ())
            self.show('turn', {uid: shape for uid, shape in zip(uids, self.turned(drag, event, shapes, angle)) if uid not in fixed})

    def show(self, kind, shapes):
        """Show a move ('items'), scale or turn as `shapes`, after the product's after_transform has seen (and perhaps added
        to) them."""
        self.host.call(self.board.after_transform, shapes, TRANSFORM_NAMES.get(kind, kind), False, touch=False)
        self.preview = shapes
        if self.board.live_drag:
            self.write_live(shapes)

    def write_live(self, shapes):
        """Board.live_drag: write the drag's shapes into the store now (no undo step yet); an item the drag let go of goes
        back to how it was."""
        store = self.store()
        if not self.live:
            try:
                self.live_snapshot = store.snapshot()
            except NotImplementedError:
                self.live_snapshot = None
        for uid in [uid for uid in self.live if uid not in shapes]:
            item = store.get(uid)
            if item is not None:
                store.set_shape(item, *self.live.pop(uid))
        for uid, shape in shapes.items():
            item = store.get(uid)
            if item is None:
                continue
            if uid not in self.live:
                self.live[uid] = tuple(item.shape)
            store.set_shape(item, *shape)
        self.host.touch()

    def undo_live(self):
        """Put back what a live drag wrote (before the finished action writes it again, or when the drag is dropped)."""
        if not self.live:
            return
        store = self.store()
        if self.live_snapshot is not None:
            store.restore(self.live_snapshot)
        else:
            for uid, shape in self.live.items():
                item = store.get(uid)
                if item is not None:
                    store.set_shape(item, *shape)
        self.live, self.live_snapshot = ({}, None)
        self.host.touch()

    def stored_shape(self, item):
        """An item's shape as it was before a live drag wrote into it (others snap to where items were, not to themselves)."""
        return self.live.get(item.uid, item.shape)

    def scaled(self, drag, event, shapes, now):
        """The shapes scaled by a corner handle (in the frame's own axes; Shift keeps the proportion) or by S (about the
        middle, Ctrl in steps of a tenth)."""
        frame = drag['frame']
        rotation = frame[4]
        local = lambda p: geometry.to_local((frame[0], frame[1], 0, 0, rotation), p)

        def board(p):
            c, s = (math.cos(rotation), math.sin(rotation))
            return (frame[0] + p[0] * c - p[1] * s, frame[1] + p[0] * s + p[1] * c)
        if drag.get('each'):
            return [self.scaled_each(drag, event, shape, now) for shape in shapes]
        held = frame[:2] if event.alt and drag['kind'] == 'scale' else drag['anchor']
        anchor, grip, at = (local(held), local(drag['grip']), local(now))
        if drag['kind'] == 'key_scale' or not self.free(drag, event):
            factor = geometry.scale_factor(anchor, grip, at)
            if drag['kind'] == 'key_scale' and drag.get('typed'):
                factor = drag['typed_value']
            elif drag['kind'] == 'key_scale' and event.ctrl:
                factor = max(SCALE_SNAP, geometry.snap(factor, SCALE_SNAP))
            fx = fy = factor
        else:
            fx, fy = geometry.scale_factors(anchor, grip, at)
        if drag.get('individual'):
            return [(s[0], s[1], s[2] * fx, s[3] * fy, s[4]) for s in shapes]
        in_frame = [(*local((s[0], s[1])), s[2], s[3], s[4] - rotation) for s in shapes]
        out = []
        for (lx, ly, w, h), s in zip(geometry.scaled_about(in_frame, anchor, fx, fy), shapes):
            bx, by = board((lx, ly))
            out.append((bx, by, w, h, s[4]))
        return out

    def free(self, drag, event):
        """Does a corner drag change width and height apart? Shift keeps the proportion; or (Board.shift_frees) Shift frees
        it, even for an item that keeps its aspect."""
        if self.board.shift_frees:
            return event.shift
        return not (event.shift or drag.get('uniform'))

    def scaled_each(self, drag, event, shape, now):
        """Board.handles 'each': the item scaled about its own middle by how far the mouse is from it against how far the
        press was (never nearer than style.SCALE_REACH pixels); across and up apart when free."""
        least = self.px(style.SCALE_REACH) / (self.host.view[2] * runtime.board_scale())
        middle = (shape[0], shape[1], 0.0, 0.0, shape[4])
        px, py = geometry.to_local(middle, drag['board_start'])
        mx, my = geometry.to_local(middle, now)
        if self.free(drag, event):
            fx, fy = (abs(mx) / max(least, abs(px)), abs(my) / max(least, abs(py)))
        else:
            fx = fy = math.hypot(mx, my) / max(least, math.hypot(px, py))
        return (shape[0], shape[1], shape[2] * fx, shape[3] * fy, shape[4])

    def turned(self, drag, event, shapes, angle):
        if drag.get('typed'):
            angle = math.radians(drag['typed_value'])
        elif event.ctrl:
            if len(shapes) == 1:
                angle = geometry.snap(shapes[0][4] + angle, style.TURN_STEP) - shapes[0][4]
            else:
                angle = geometry.snap(angle, style.TURN_STEP)
        if drag.get('individual'):
            return [(s[0], s[1], s[2], s[3], s[4] + angle) for s in shapes]
        about = drag.get('about') or drag['frame'][:2]
        return [(tx, ty, s[2], s[3], tr) for s, (tx, ty, tr) in zip(shapes, geometry.turned_about(shapes, about, angle))]

    def release(self, drag, event, x, y):
        kind = drag['kind']
        if kind == 'items':
            if drag['moved']:
                self.commit('Move', 'items')
            elif drag.get('toggle_off') is not None:
                self.select([uid for uid in self.selection if uid != drag['toggle_off']])
            elif not (event.shift or event.ctrl) and len(self.selection) > 1 and (self.board.toggle_on != 'press'):
                self.select([drag['clicked']])
        elif kind == 'scale':
            self.commit('Scale', 'scale')
        elif kind == 'turn':
            self.commit('Rotate', 'turn')
        elif kind == 'box':
            self.marquee = None
            mode = drag['mode']
            if not drag['moved']:
                if mode == 'set':
                    self.select([])
                return
            a, b = (self.host.board_point(*drag['start']), self.host.board_point(x, y))
            box = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
            if self.board.box_select == 'centre':
                hits = [item.uid for item in self.visible() if box[0] <= item.shape[0] <= box[2] and box[1] <= item.shape[1] <= box[3]]
            else:
                hits = [item.uid for item in self.visible() if overlap(geometry.items_bounds([item.shape]), box)]
            if mode == 'add':
                self.select(self.selection + hits)
            elif mode == 'remove':
                self.select([uid for uid in self.selection if uid not in hits])
            else:
                self.select(hits)

    def begin_key_transform(self, session, context, event, key, x, y):
        """G / S / R: the movable selection follows the mouse until a click or Return (keep) or a right-click or Esc
        (drop). X / Y lock the move to an axis; typed numbers give the value; Shift makes the mouse precise."""
        chosen = self.movable()
        frame = geometry.selection_frame([self.shape(item) for item in chosen])
        if frame is None:
            return
        kind, label = KEY_TRANSFORMS[key]
        start = self.host.board_point(x, y)
        originals = self.moving() if kind == 'key_move' else {i.uid: self.shape(i) for i in chosen}
        shape = {'key_move': 'items', 'key_scale': 'scale', 'key_turn': 'turn'}[kind]
        about, individual = self.pivot(chosen, shape, frame) if kind != 'key_move' else (frame[:2], False)
        self.begin(session, context, event, kind, x, y, 'NONE', label=label, frame=frame, axis=None, typed='', uniform=self.uniform(chosen), fixed=self.fixed(chosen), originals=originals, virtual=(x, y), last=runtime.window_mouse(event, x, y), mouse_canvas=(x, y), shape=shape, anchor=about, about=about, individual=individual, grip=start, moved=True)

    def pivot(self, chosen, kind, frame):
        """(the point S / R work about, each item about its own middle?) from the product's pivot_point."""
        point = self.host.call(lambda: self.board.pivot_point(chosen, kind), failed=frame[:2], touch=False)
        return (frame[:2], True) if point is None else (tuple(point), False)

    def key_drag(self, session, context, event, x, y, drag):
        kind, value = (event.type, event.value)
        if kind == 'ESC' and value == 'PRESS' or (kind == 'RIGHTMOUSE' and value == 'PRESS'):
            self.cancel(session, context)
        elif kind in CONFIRM and value == 'PRESS':
            self.commit(drag['label'], drag['shape'])
            session.end_drag(context, self.host)
        elif kind in MOVES:
            step = PRECISE if event.shift else 1.0
            mx, my = runtime.window_mouse(event, x, y)
            dx, dy = (mx - drag['last'][0], my - drag['last'][1])
            drag['last'] = (mx, my)
            drag['mouse_canvas'] = (x, y)
            vx, vy = drag['virtual']
            drag['virtual'] = (vx + dx * step, vy + dy * step)
            if not drag['typed']:
                self.follow(drag, event, *drag['virtual'])
        elif value == 'PRESS' and kind in {'X', 'Y'} and (drag['kind'] == 'key_move'):
            drag['axis'] = None if drag['axis'] == kind else kind
            self.reshape(drag, event)
        elif value == 'PRESS' and (kind in DIGITS or kind == 'BACK_SPACE'):
            drag['typed'] = drag['typed'][:-1] if kind == 'BACK_SPACE' else drag['typed'] + DIGITS[kind]
            self.reshape(drag, event)
        return {'RUNNING_MODAL'}

    def reshape(self, drag, event):
        """Show the transform again after an axis or a typed number changed."""
        typed = drag['typed']
        try:
            drag['typed_value'] = float(typed) if typed not in ('', '-', '.', '-.') else None
        except ValueError:
            drag['typed_value'] = None
        if drag['typed_value'] is None:
            drag['typed'] = '' if not typed else typed
            self.follow(drag, event, *drag['virtual'])
            return
        originals = drag['originals']
        if drag['kind'] == 'key_move':
            amount = drag['typed_value']
            dx, dy = (0.0, amount) if drag['axis'] == 'Y' else (amount, 0.0)
            self.show('items', {uid: (s[0] + dx, s[1] + dy, s[2], s[3], s[4]) for uid, s in originals.items()})
        else:
            self.follow(drag, event, *drag['virtual'])

    def menu_sections(self):
        return []

    def paint_board(self, painter, x0, y0, k):
        """The items (the product's draw_item, in z order, hidden ones left out), then the selection, the handles, the
        box being dragged and the snap guides; inside the board area's clip."""
        host, board = (self.host, self.board)
        for item in self.visible():
            shape = self.preview.get(item.uid)
            shown = Shown(item, shape) if shape is not None else item
            with painter.turned((shown.x, shown.y), shown.rotation):
                host.call(board.draw_item, shown, painter, touch=False)
        accent = host.colours['accent']
        chosen = self.selected_items()
        for item in chosen:
            corners = tuple(((x0 + cx, y0 + cy) for cx, cy in (host.canvas_point(c) for c in geometry.item_corners(self.shape(item)))))
            draw.polygon_outline(corners, accent if not item.locked else style.LOCKED_EDGE, 1.5)
        drag = host.drag
        hidden = drag and (drag['kind'].startswith('key_') or self.board.handles == 'each')
        for uids, frame, corners, turn in [] if hidden else self.handle_sets():
            screen = tuple(((x0 + cx, y0 + cy) for cx, cy in corners))
            if len(uids) > 1:
                draw.polygon_outline(screen, (*accent[:3], 0.6), 1.0)
            r = self.px(style.HANDLE_SIZE)
            for cx, cy in screen:
                draw.box(cx - r, cy - r, 2 * r, 2 * r, style.HANDLE_FILL, 1.5)
                draw.outline(cx - r, cy - r, 2 * r, 2 * r, style.HANDLE_EDGE, 1.5)
            if turn is not None:
                top_mid = ((screen[2][0] + screen[3][0]) / 2, (screen[2][1] + screen[3][1]) / 2)
                tx, ty = (x0 + turn[0], y0 + turn[1])
                draw.lines((top_mid, (tx, ty)), accent, 1.0)
                draw.box(tx - r, ty - r, 2 * r, 2 * r, style.HANDLE_FILL, r)
                draw.outline(tx - r, ty - r, 2 * r, 2 * r, style.HANDLE_EDGE, r)
        if self.marquee:
            (ax, ay), (bx, by) = self.marquee
            left, bottom = (min(ax, bx), min(ay, by))
            draw.box(x0 + left, y0 + bottom, abs(bx - ax), abs(by - ay), style.MARQUEE_FILL)
            draw.outline(x0 + left, y0 + bottom, abs(bx - ax), abs(by - ay), accent, 0, 1.0)
        bx, by, bw, bh = host.board_rect
        gx, gy = self.guides
        if gx is not None:
            sx = host.canvas_point((gx, 0))[0]
            draw.lines(((x0 + sx, y0 + by), (x0 + sx, y0 + by + bh)), style.GUIDE, 1.0)
        if gy is not None:
            sy = host.canvas_point((0, gy))[1]
            draw.lines(((x0 + bx, y0 + sy), (x0 + bx + bw, y0 + sy)), style.GUIDE, 1.0)

    def paint(self, x0, y0, w, h, k):
        """While G / S / R runs: Blender's arrows at the mouse (the cursor itself is hidden), the dashed line from the pivot
        for S / R, the locked axis, and what the keys do along the board's foot."""
        host = self.host
        drag = host.drag
        if not drag or drag.get('owner') is not self or (not drag['kind'].startswith('key_')):
            return
        ui = self.px(1.0)
        mouse = drag.get('mouse_canvas')
        mode = {'key_move': 'G', 'key_scale': 'S', 'key_turn': 'R'}[drag['kind']]
        if mouse is not None:
            pivot = host.canvas_point(drag.get('about') or drag['frame'][:2])
            shapes = transform_icon(mode, (x0 + pivot[0], y0 + pivot[1]), (x0 + mouse[0], y0 + mouse[1]), ui)
            draw.polygons(shapes['dark'], (0.0, 0.0, 0.0, 0.8))
            draw.polygons(shapes['light'], (1.0, 1.0, 1.0, 1.0))
        bx, by, bw, bh = host.board_rect
        axis = drag.get('axis')
        if axis:
            cx, cy = host.canvas_point(drag['frame'][:2])
            if axis == 'X':
                draw.lines(((x0 + bx, y0 + cy), (x0 + bx + bw, y0 + cy)), style.AXIS_X, 1.5)
            else:
                draw.lines(((x0 + cx, y0 + by), (x0 + cx, y0 + by + bh)), style.AXIS_Y, 1.5)
        name = drag['label'] + (f" along {axis}" if axis else '') + (f":  {drag['typed']}" if drag['typed'] else '')
        hint = {'G': 'X / Y: axis   ·   type a distance', 'S': 'Ctrl: steps   ·   type a factor', 'R': 'Ctrl: 15° steps   ·   type degrees'}[mode] + '   ·   Shift: precise   ·   Click / Return: done   ·   Esc: cancel'
        line = 24 * k
        draw.box(x0 + bx, y0 + by, bw, line + 14 * k, style.KEY_HINT_BACK)
        draw.text(x0 + bx + 10 * k, y0 + by + line - 2 * k, name, 11 * k, host.colours['text'], bw - 20 * k)
        draw.text(x0 + bx + 10 * k, y0 + by + 6 * k, hint, 9 * k, host.colours['muted'], bw - 20 * k)

class SelectionView(collections.abc.MutableSet):
    """The layer's selection as a live set: what changes here changes the selection (and is heard by the product)."""

    def __init__(self, layer):
        self._layer = layer

    @classmethod
    def _from_iterable(cls, items):
        return set(items)

    def __contains__(self, uid):
        return uid in self._layer.selection

    def __iter__(self):
        return iter(list(self._layer.selection))

    def __len__(self):
        return len(self._layer.selection)

    def add(self, uid):
        if uid not in self._layer.selection:
            self._layer.select(self._layer.selection + [uid])

    def discard(self, uid):
        if uid in self._layer.selection:
            self._layer.select([u for u in self._layer.selection if u != uid])

    def clear(self):
        self._layer.select([])

    def __repr__(self):
        return f"SelectionView({set(self._layer.selection)!r})"

class PreviewView(collections.abc.MutableMapping):
    """The layer's preview (uid -> shape) in a product's own form: `to_view(uid, shape)` and `from_view(uid, value)` convert."""

    def __init__(self, layer, to_view, from_view):
        self._layer, self._to, self._from = (layer, to_view, from_view)

    def __getitem__(self, uid):
        return self._to(uid, self._layer.preview[uid])

    def __setitem__(self, uid, value):
        self._layer.preview[uid] = tuple(self._from(uid, value))

    def __delitem__(self, uid):
        del self._layer.preview[uid]

    def __iter__(self):
        return iter(list(self._layer.preview))

    def __len__(self):
        return len(self._layer.preview)

    def __repr__(self):
        return f"PreviewView({dict(self)!r})"

def overlap(a, b):
    """Do two upright boxes touch?"""
    return a[0] <= b[2] and b[0] <= a[2] and (a[1] <= b[3]) and (b[1] <= a[3])

def reread_all():
    """After Blender's undo or redo: every open board's layer reads its store again."""
    for host in list(runtime.sessions.values()):
        if getattr(host, 'layer', None) is not None:
            host.layer.reread()
