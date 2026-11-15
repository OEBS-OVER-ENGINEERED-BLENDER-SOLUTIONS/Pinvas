"""Operators: the session that takes the mouse while the board is on screen (in a sidebar, over the
viewport, or in a window of its own), and Dock.

The session is a modal operator, one per 3D View. It only keeps the events that land on the
board; everything else (keys Pinvas does not use, clicks elsewhere, the sidebar's resize edge
and tab column) passes through to Blender untouched.
"""
import math

import bpy

from . import board as board_module
from . import canvas as canvas_module
from . import geometry, images, media, popout
from ._canvas.runtime import area_by_pointer, redraw_area  # noqa: F401
from ._canvas.session import Session, close_all, open_where_shown  # noqa: F401 (the framework's timer and Dock)
from .canvas import ui_scale

MOUSE_BUTTONS = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE',
                 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE',
                 'TRACKPADPAN', 'TRACKPADZOOM'}
MOVES = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
WHEEL_PAN = 40            # pixels a Ctrl/Shift + wheel notch pans
HANDLE_CURSORS = {'scale': 'SCROLL_XY', 'turn': 'NONE', 'resize': 'SCROLL_XY'}       # 'NONE' over the turn handle: Pinvas draws the rotate cursor itself
KEY_TRANSFORMS = {'G', 'S', 'R'}       # move, scale, turn: the board layer's


def open_menu():
    """The board's right-click menu (a hook of its own so tests can stand in for it)."""
    if bpy.app.background or bpy.context.window is None:
        return                      # Blender crashes building a popup with no window to show it in
    bpy.ops.wm.call_menu(name="PINVAS_MT_board")


def open_add_menu():
    """Shift+A: what can be added to the board (a hook of its own so tests can stand in for it)."""
    if bpy.app.background or bpy.context.window is None:
        return
    bpy.ops.wm.call_menu(name="PINVAS_MT_add")


def push_undo(message):
    try:
        bpy.ops.ed.undo_push(message=message)
    except RuntimeError:
        pass


PINVAS_OT_session = None   # the registered session operator (register_board builds it from PinvasSession), for the tests


class PinvasSession(Session):
    """Pinvas's session: the framework's, with Pinvas's own routing of the board's events. It keeps the open board as
    `canvas`, as Pinvas has always called it."""

    def invoke(self, context, event):
        result = Session.invoke(self, context, event)
        self.canvas = getattr(self, 'host', None)
        return result

    def modal(self, context, event):
        canvas = self.canvas
        if not canvas.alive:
            return {'CANCELLED'}
        area = area_by_pointer(context.window.screen, canvas.area_id)
        if area is None or area.type != 'VIEW_3D':
            canvas.close()
            return {'CANCELLED'}
        host = canvas.host(area)
        if host is None:
            if canvas.drag:
                self.end_drag(context, canvas)
            return {'PASS_THROUGH'}
        region, ox, oy, w, h = host
        x, y = event.mouse_x - region.x - ox, event.mouse_y - region.y - oy
        over = 0 <= x < w and 0 <= y < h and not canvas.resize_edge(region, x)
        # Kept for drops: a file dropped on a floating board is only the board's where the mouse was over it.
        canvas.mouse = ((event.mouse_x, event.mouse_y), over and geometry.inside(canvas.board_rect, x, y))
        canvas.drop_at = (x, y)
        try:
            if canvas.drag:
                return self.dragging(context, area, canvas, event, x, y)
            if not over:
                if canvas.hover_to(None):
                    self.hover_cursor(context, canvas)
                    area.tag_redraw()
                return {'PASS_THROUGH'}
            return self.on_board(context, area, region, canvas, event, x, y)
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            canvas.drag = None
            area.tag_redraw()
            return {'RUNNING_MODAL'}

    def on_board(self, context, area, region, canvas, event, x, y):
        kind, value = event.type, event.value
        plain = not (event.ctrl or event.alt or event.shift or event.oskey)
        if kind in MOVES:
            changed = canvas.hover_to(canvas.hit(x, y))
            if canvas.hover == ('turn',):
                area.tag_redraw()                          # the rotate cursor is drawn: it follows the mouse
            moved = changed
            if moved:
                self.hover_cursor(context, canvas)
                area.tag_redraw()
                if changed and canvas.tip_wanted():
                    bpy.app.timers.register(lambda: redraw_area(canvas.area_id), first_interval=canvas_module.TIP_DELAY + 0.05)
            return {'PASS_THROUGH'}
        if kind == 'MIDDLEMOUSE' and value == 'PRESS':
            if event.ctrl:
                self.begin_drag(context, canvas, event, 'zoom', x, y, cursor='ZOOM_IN')
            else:
                self.begin_drag(context, canvas, event, 'pan', x, y, cursor='HAND')
        elif kind in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE'}:
            direction = 1 if kind in {'WHEELUPMOUSE', 'WHEELINMOUSE'} else -1
            if event.ctrl:
                canvas.pan(direction * WHEEL_PAN * ui_scale(), 0)        # as in Blender's 2D editors
            elif event.shift:
                canvas.pan(0, direction * WHEEL_PAN * ui_scale())
            else:
                canvas.zoom_steps_at(x, y, direction)
        elif kind == 'TRACKPADPAN':
            canvas.pan(event.mouse_x - event.mouse_prev_x, event.mouse_y - event.mouse_prev_y)
        elif kind == 'TRACKPADZOOM':
            delta = event.mouse_x - event.mouse_prev_x
            canvas.zoom_at(x, y, canvas.view[2] * (1.0 + delta * 0.01))
        elif kind == 'LEFTMOUSE' and value in {'PRESS', 'DOUBLE_CLICK'}:
            self.press(context, area, canvas, event, x, y)
        elif kind == 'RIGHTMOUSE' and value == 'PRESS':
            key = canvas.hit(x, y)
            if key and key[0] == 'item' and key[1] not in canvas.selection:
                canvas.selection = {key[1]}           # the menu acts on what was clicked, as everywhere in Blender
            canvas.menu_at = (x, y)
            area.tag_redraw()
            open_menu()
            return {'RUNNING_MODAL'}
        elif kind == 'HOME' and value == 'PRESS' and plain:
            canvas.fit()
        elif kind in {'ONE', 'NUMPAD_1'} and value == 'PRESS' and plain:
            canvas.zoom_100()
        elif kind in {'PERIOD', 'NUMPAD_PERIOD'} and value == 'PRESS' and plain:
            canvas.fit_selected()
        elif kind == 'A' and value == 'PRESS' and event.shift and not (event.ctrl or event.alt):
            canvas.menu_at = (x, y)
            open_add_menu()
            return {'RUNNING_MODAL'}
        elif kind == 'P' and value == 'PRESS' and event.ctrl and not (event.alt or event.shift):
            board = canvas.board()                    # PureRef's Arrange > Optimal: the selection, or the whole board
            if board is not None and not isinstance(media.arrange(canvas, board, 'PACK'), str):
                push_undo("Arrange Pictures")
        elif kind == 'E' and value == 'PRESS' and plain:
            self.begin_drag(context, canvas, event, 'pick', x, y, cursor='EYEDROPPER')
        elif kind == 'A' and value == 'PRESS' and (plain or event.alt and not (event.ctrl or event.shift)):
            if event.alt:
                canvas.selection.clear()
            else:
                canvas.select_all()
        elif kind in {'X', 'DEL'} and value == 'PRESS' and plain:
            # A key Pinvas uses stays on the board even with nothing selected: passed on over a floating board it would
            # reach the viewport under it (X there deletes objects).
            if canvas.selection:
                canvas.layer.delete()                 # one undo step, "Remove Pictures"
        elif kind in KEY_TRANSFORMS and value == 'PRESS' and plain:
            canvas.layer.begin_key_transform(self, context, event, kind, x, y)     # G / S / R: the board layer's
        elif kind == 'V' and value == 'PRESS' and event.ctrl and not (event.alt or event.shift):
            canvas.menu_at = None                     # a menu closed earlier says nothing about this: it lands at the mouse
            bpy.ops.pinvas.paste('INVOKE_DEFAULT')
        elif kind == 'S' and value == 'PRESS' and event.ctrl and not event.alt and canvas.popout:
            # Saving from a window of its own: dock first, so the saved file never holds that window, then save from the main one.
            canvas.close()
            bpy.app.timers.register(lambda as_new=event.shift: popout.resave_from_main(as_new), first_interval=0.15)
            return {'CANCELLED'}
        elif kind in MOUSE_BUTTONS:
            pass        # every other click on the board is the board's: it never reaches the hidden panel or the viewport under it
        else:
            return {'PASS_THROUGH'}
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def press(self, context, area, canvas, event, x, y):
        """A left click on the canvas: a control, a handle, a picture or the empty board."""
        key = canvas.hit(x, y)
        if key is None:
            return
        kind = key[0]
        if canvas.popout and (key in {('move',), ('resize',)} or kind == 'edge'):
            # A window of its own is carried in screen pixels; a move offers the dock targets of the views it passes over.
            moving = key == ('move',)
            edge = 'BR' if key == ('resize',) else key[1] if kind == 'edge' else 'BR'
            carry = popout.begin_carry(canvas, 'pop_move' if moving else 'pop_resize', edge)
            if carry is not None:
                drag = self.begin_drag(context, canvas, event, carry['kind'], x, y,
                                       cursor='SCROLL_XY' if moving else popout.EDGE_CURSORS.get(edge, 'SCROLL_XY'))
                drag['carry'] = carry
        elif key == ('move',):
            self.begin_drag(context, canvas, event, 'move', x, y, cursor='SCROLL_XY')
        elif key == ('resize',):
            self.begin_drag(context, canvas, event, 'resize', x, y, cursor='SCROLL_XY')
        elif kind in {'scale', 'turn', 'item', 'board'}:
            canvas.layer.press(self, context, event, x, y, double=False)      # handles, items and box select: the board layer's
        else:
            canvas.command(key, area)

    def hover_cursor(self, context, canvas):
        """Over a handle the cursor says what dragging it does; elsewhere it is Blender's again."""
        hover = canvas.hover
        wanted = (popout.EDGE_CURSORS.get(hover[1]) if hover[0] == 'edge' else HANDLE_CURSORS.get(hover[0])) if hover else None
        if wanted != getattr(canvas, 'cursor', None):
            context.window.cursor_set(wanted or 'DEFAULT')
            canvas.cursor = wanted

    def begin_drag(self, context, canvas, event, kind, x, y, cursor):
        # The mouse is kept in window pixels: a floating board moves under it, so its own pixels shift during the drag.
        canvas.drag = dict(kind=kind, start=(x, y), view=canvas.view, rect=canvas.float_rect,
                           mouse=(event.mouse_x, event.mouse_y), moved=False, cursor=bool(cursor),
                           button=event.type if event.type in {'MIDDLEMOUSE', 'RIGHTMOUSE'} else 'LEFTMOUSE',
                           board_start=canvas.board_point(x, y))
        if cursor:
            context.window.cursor_modal_set(cursor)
        return canvas.drag

    def end_drag(self, context, canvas):
        drag, canvas.drag = canvas.drag, None
        canvas.marquee = None
        if drag is None or drag.get('cursor', True):
            try:
                context.window.cursor_modal_restore()
            except (RuntimeError, AttributeError):
                pass

    def dragging(self, context, area, canvas, event, x, y):
        drag = canvas.drag
        kind = drag['kind']
        if drag.get('owner') is not None:                 # a drag of the board layer's: items, handles, a box, G / S / R
            return Session.dragging(self, context, area, canvas, event, x, y)    # by name: the tests drive these methods on a stand-in
        if kind in {'pop_move', 'pop_resize'}:
            return self.carrying(context, area, canvas, event, drag)
        if kind == 'pick':
            return self.picking(context, area, canvas, event, x, y)
        if event.type == 'ESC' and event.value == 'PRESS' or event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            canvas.set_view(drag['view'])                 # the drag is dropped: everything goes back
            if drag['rect'] is not None:
                canvas.float_rect = drag['rect']
            self.end_drag(context, canvas)
        elif event.value == 'RELEASE' and event.type == drag.get('button', event.type):
            self.end_drag(context, canvas)
        elif event.type in MOVES:
            dx, dy = event.mouse_x - drag['mouse'][0], event.mouse_y - drag['mouse'][1]
            if kind == 'pan':
                canvas.set_view(geometry.pan_by(drag['view'], dx, dy))
            elif kind == 'zoom':
                # Up or right zooms in; the point the drag started on stays put.
                factor = geometry.ZOOM_STEP ** ((dx + dy) / (40.0 * ui_scale()))
                canvas.set_view(drag['view'])
                canvas.zoom_at(*drag['start'], drag['view'][2] * factor)
            elif kind == 'move':
                if math.hypot(dx, dy) >= canvas_module.DRAG_START * ui_scale():
                    drag['moved'] = True
                rx, ry, rw, rh = drag['rect']
                canvas.float_rect = (rx + dx, ry + dy, rw, rh)
            elif kind == 'resize':
                window = canvas_module.region_of(area, 'WINDOW')
                k = ui_scale()
                bounds = canvas.float_bounds(area, window)
                canvas.float_rect = geometry.resize_from_corner(drag['rect'], dx, dy, bounds,
                                                                (canvas_module.FLOAT_MIN[0] * k, canvas_module.FLOAT_MIN[1] * k),
                                                                canvas_module.FLOAT_MARGIN * k)
        else:
            return {'RUNNING_MODAL'}
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def carrying(self, context, area, canvas, event, drag):
        """A window of its own being moved or resized: it follows the cursor; let go on a dock target, it docks there."""
        carry = drag['carry']
        if event.type == 'ESC' and event.value == 'PRESS' or event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            popout.cancel_carry(canvas, carry)
            self.end_drag(context, canvas)
        elif event.value == 'RELEASE' and event.type == 'LEFTMOUSE':
            self.end_drag(context, canvas)
            if popout.finish_carry(canvas, carry):
                return {'CANCELLED'}                  # docked: this window's session is over
        elif event.type in MOVES:
            if math.hypot(event.mouse_x - drag['mouse'][0], event.mouse_y - drag['mouse'][1]) >= canvas_module.DRAG_START * ui_scale():
                drag['moved'] = True
            popout.follow(canvas, carry)
            if carry['kind'] == 'pop_move':
                popout.update_target(canvas)
            return {'RUNNING_MODAL'}                  # the window moves; what it shows does not change
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    # The eyedropper (E)

    def picking(self, context, area, canvas, event, x, y):
        kind, value = event.type, event.value
        if kind == 'LEFTMOUSE' and value == 'PRESS':
            self.end_drag(context, canvas)
            if not geometry.inside(canvas.board_rect, x, y):
                return {'PASS_THROUGH'}
            self.pick_colour(context, canvas, x, y)
        elif (kind == 'ESC' or kind == 'RIGHTMOUSE') and value == 'PRESS':
            self.end_drag(context, canvas)
        else:
            return {'RUNNING_MODAL'} if kind not in MOVES else {'PASS_THROUGH'}
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def pick_colour(self, context, canvas, x, y):
        """Copy the colour under canvas (x, y) as #RRGGBB: a picture's own pixel there."""
        board = canvas.board()
        uid = canvas.item_at(x, y)
        item = board_module.item_by_uid(board, uid) if board is not None and uid is not None else None
        rgb = None
        if item is not None and item.kind == 'IMAGE':
            uv = geometry.picture_uv(canvas.shown_placed(item), canvas.board_point(x, y))
            image = item.image
            rgb = images.pixel(image, *uv) if uv is not None and image is not None else (0.0, 0.0, 0.0) if uv is not None else None
            if rgb is not None and item.grey:
                grey = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
                rgb = (grey, grey, grey)
        if rgb is None:
            self.report({'INFO'}, "No colour to pick there: click on a picture")
            return None
        hexed = "#" + "".join(f"{round(min(1.0, max(0.0, c)) * 255):02X}" for c in rgb)
        context.window_manager.clipboard = hexed
        canvas.show_toast(f"{hexed}  copied", rgb)
        return hexed

classes = ()                # the session, Dock and the panel are built and registered by register_board
