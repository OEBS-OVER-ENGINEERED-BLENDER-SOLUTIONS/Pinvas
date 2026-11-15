"""The board's session (a modal operator, one per 3D View), Dock It Here, the sidebar panel, and the timer that opens a
session wherever the product's tab shows. The classes are built per product (`make_classes`), named by its prefix, so two
products on OEBS Canvas never register the same names.

The session keeps only the events that land on the board; everything else (keys the board does not use, clicks elsewhere,
the sidebar's resize edge and tab column) passes through to Blender untouched."""
import time
from types import FunctionType

import bpy

from . import compat, popout, roam, runtime, style, view
from .host import Host
from .runtime import area_by_pointer, region_of, sessions, ui_scale

MOUSE_BUTTONS = {'LEFTMOUSE', 'RIGHTMOUSE', 'MIDDLEMOUSE', 'BUTTON4MOUSE', 'BUTTON5MOUSE',
                 'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM'}
MOVES = {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}
WHEELS = {'WHEELUPMOUSE', 'WHEELDOWNMOUSE', 'WHEELINMOUSE', 'WHEELOUTMOUSE'}
FRAME_COMMANDS = {('fit',), ('zoom100',), ('undock',), ('float',), ('dock',), ('close',)}
HANDLE_CURSORS = {'resize': 'SCROLL_XY', 'side': 'MOVE_X'}


class Session:
    """The session operator's behaviour; make_classes() mixes it into a bpy Operator named for the product."""
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        area_id = context.area.as_pointer()
        runtime.dismissed.pop(area_id, None)            # opened on purpose: no longer closed
        if area_id in sessions:
            return {'CANCELLED'}           # this view already has one
        self.host = sessions[area_id] = new_host(context.area)
        previous = runtime.replaced.pop(area_id, None)
        self.host.call(runtime.board_class.after_open, self.host, previous, touch=False)     # Board.after_open
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        with runtime.scale_held():                    # the interface scale is read once for the event, not for every point
            return self.modal_step(context, event)

    def modal_step(self, context, event):
        host = self.host
        if not host.alive:
            return {'CANCELLED'}
        area = area_by_pointer(context.window.screen, host.area_id)
        if area is None or area.type != 'VIEW_3D':
            host.close()
            return {'CANCELLED'}
        crossing = host.crossing
        host.area = area
        placed = host.host(area)
        if host.crossing != crossing:
            roam.sync()                           # it started or stopped hanging over other editors: their handlers follow now
        if placed is None:
            if host.drag:
                self.end_drag(context, host)
            return {'PASS_THROUGH'}
        region, ox, oy, w, h = placed
        x, y = event.mouse_x - region.x - ox, event.mouse_y - region.y - oy
        over = 0 <= x < w and 0 <= y < h and not host.resize_edge(region, x)
        host.mouse = ((event.mouse_x, event.mouse_y), over and view.inside(host.board_rect, x, y))
        host.pointer = (x, y) if host.mouse[1] else None
        hovered = host.hover
        try:
            if host.drag:
                result = self.dragging(context, area, host, event, x, y)
            elif over:
                result = self.on_board(context, area, host, event, x, y)
            else:
                # Off the board: Blender's, unless a handler captures every event (an open menu, a focused text field).
                result = self.route(context, area, host, event, x, y) if host.capture else None
                if result is None:
                    result = {'PASS_THROUGH'}
                    if host.hover_to(None):
                        self.hover_cursor(context, host)
                        area.tag_redraw()
        except (ValueError, RuntimeError, ReferenceError) as exc:
            self.report({'WARNING'}, str(exc))
            host.error = str(exc)
            host.drag = None
            area.tag_redraw()
            result = {'RUNNING_MODAL'}
        if host.reach is not None and (result == {'RUNNING_MODAL'} or host.hover != hovered):
            host.redraw_reached(area)             # a board crossing editors: they show its change too
        return result

    def route(self, context, area, host, event, x, y):
        """Ask the host's input handlers (C-route), in order: the first that takes the event decides the result."""
        for handler in host.inputs:
            result = host.call(handler.handle, self, context, area, event, x, y, touch=False)
            if result is not None:
                if 'RUNNING_MODAL' in result:
                    host.touch()
                    area.tag_redraw()
                return result
        return None

    def on_board(self, context, area, host, event, x, y):
        routed = self.route(context, area, host, event, x, y)
        if routed is not None:
            return routed
        kind, value = event.type, event.value
        plain = not (event.ctrl or event.alt or event.shift or event.oskey)
        if kind in MOVES:
            if host.hover_to(host.hit(x, y)):
                self.hover_cursor(context, host)
                area.tag_redraw()
                if host.tip_wanted():
                    bpy.app.timers.register(lambda: runtime.redraw_area(host.area_id), first_interval=style.TIP_DELAY + 0.05)
            return {'PASS_THROUGH'}
        if kind == 'MIDDLEMOUSE' and value == 'PRESS':
            self.begin_drag(context, host, event, 'zoom' if event.ctrl else 'pan', x, y, cursor='ZOOM_IN' if event.ctrl else 'HAND')
        elif kind in WHEELS:
            direction = 1 if kind in {'WHEELUPMOUSE', 'WHEELINMOUSE'} else -1
            if event.ctrl:
                host.pan(direction * style.WHEEL_PAN * ui_scale(), 0)       # as in Blender's 2D editors
            elif event.shift:
                host.pan(0, direction * style.WHEEL_PAN * ui_scale())
            else:
                host.zoom_steps_at(x, y, direction)
        elif kind == 'TRACKPADPAN':
            host.pan(event.mouse_x - event.mouse_prev_x, event.mouse_y - event.mouse_prev_y)
        elif kind == 'TRACKPADZOOM':
            host.zoom_at(x, y, host.view[2] * (1.0 + (event.mouse_x - event.mouse_prev_x) * 0.01))
        elif kind == 'LEFTMOUSE' and value in {'PRESS', 'DOUBLE_CLICK'}:
            if value == 'DOUBLE_CLICK' or (not (event.shift or event.ctrl or event.alt) and host.double_click_at(x, y)):
                self.double_click(context, area, host, event, x, y)     # Shift / Ctrl clicks twice stay clicks
            else:
                self.press(context, area, host, event, x, y)
        elif kind == 'HOME' and value == 'PRESS' and plain:
            host.fit()
        elif kind in {'ONE', 'NUMPAD_1'} and value == 'PRESS' and plain:
            host.zoom_100()
        elif kind == 'S' and value == 'PRESS' and event.ctrl and not event.alt and host.popout:
            # Saving from a window of its own: dock first, so the saved file never holds that window, then save from the main one.
            host.close()
            bpy.app.timers.register(lambda as_new=event.shift: popout.resave_from_main(as_new), first_interval=0.15)
            return {'CANCELLED'}
        elif kind in MOUSE_BUTTONS:
            pass        # every other click on the board is the board's: it never reaches the hidden panel or the viewport under it
        else:
            return {'PASS_THROUGH'}
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def press(self, context, area, host, event, x, y):
        key = host.hit(x, y)
        if key is None:
            return
        kind = key[0]
        if host.popout and (key in {('move',), ('resize',)} or kind == 'edge'):
            moving = key == ('move',)
            edge = 'BR' if key == ('resize',) else key[1] if kind == 'edge' else 'BR'
            carry = popout.begin_carry(host, 'pop_move' if moving else 'pop_resize', edge)
            if carry is not None:
                if moving and popout.native_carry(host, carry):
                    return                                    # Windows carried it (and docked it, if let go on a target)
                drag = self.begin_drag(context, host, event, carry['kind'], x, y,
                                       cursor='SCROLL_XY' if moving else popout.EDGE_CURSORS.get(edge, 'SCROLL_XY'))
                drag['carry'] = carry
        elif key == ('move',):
            self.begin_drag(context, host, event, 'move', x, y, cursor='SCROLL_XY')
        elif key == ('resize',):
            self.begin_drag(context, host, event, 'resize', x, y, cursor='SCROLL_XY')
        elif key == ('error',):
            host.error = ''
        elif key == ('side',):
            drag = self.begin_drag(context, host, event, 'side', x, y, cursor='MOVE_X')
            drag['side_px'], drag['side_width'] = host.side_rect[2], host.side_width
        elif kind == 'ui':
            host.run_ui_event(key)
        elif key in FRAME_COMMANDS:
            host.command(key, area)
        elif key == ('board',):
            host.call(host.board.on_click, host.board_point(x, y), event)

    def double_click(self, context, area, host, event, x, y):
        if host.hit(x, y) == ('board',) and host.call(host.board.on_double_click, host.board_point(x, y), event, failed=True):
            return
        self.press(context, area, host, event, x, y)

    def hover_cursor(self, context, host):
        hover = host.hover
        wanted = (popout.EDGE_CURSORS.get(hover[1]) if hover[0] == 'edge' else HANDLE_CURSORS.get(hover[0])) if hover else None
        if wanted != host.cursor:
            context.window.cursor_set(wanted or 'DEFAULT')
            host.cursor = wanted

    def begin_drag(self, context, host, event, kind, x, y, cursor):
        # The mouse is kept in window pixels: a floating board moves under it, so its own pixels shift during the drag.
        host.drag = dict(kind=kind, start=(x, y), view=host.view, rect=host.float_rect, mouse=runtime.window_mouse(event, x, y),
                         moved=False, cursor=bool(cursor),
                         button=event.type if event.type in {'MIDDLEMOUSE', 'RIGHTMOUSE'} else 'LEFTMOUSE')
        if cursor:
            context.window.cursor_modal_set(cursor)
        return host.drag

    def end_drag(self, context, host):
        drag, host.drag = host.drag, None
        if drag is None or drag.get('cursor', True):
            try:
                context.window.cursor_modal_restore()
            except (RuntimeError, AttributeError):
                pass

    def dragging(self, context, area, host, event, x, y):
        drag = host.drag
        owner = drag.get('owner')
        if owner is not None:
            result = host.call(owner.drag, self, context, area, event, x, y, drag)
            if result is None:                     # the handler failed or let it go: the release still ends the drag
                if event.value == 'RELEASE' or event.type == 'ESC':
                    self.end_drag(context, host)
                result = {'RUNNING_MODAL'}
            area.tag_redraw()
            return result
        kind = drag['kind']
        if kind in {'pop_move', 'pop_resize'}:
            return self.carrying(context, area, host, event, drag)
        if event.type == 'ESC' and event.value == 'PRESS' or event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            host.set_view(drag['view'])                 # the drag is dropped: everything goes back
            if drag['rect'] is not None:
                host.float_rect = drag['rect']
            if kind == 'side':
                host.side_width = drag['side_width']
            self.end_drag(context, host)
        elif event.value == 'RELEASE' and event.type == drag['button']:
            self.end_drag(context, host)
        elif event.type in MOVES:
            mx, my = runtime.window_mouse(event, x, y)
            dx, dy = mx - drag['mouse'][0], my - drag['mouse'][1]
            if kind == 'pan':
                host.set_view(view.pan_by(drag['view'], dx, dy))
            elif kind == 'zoom':
                factor = view.ZOOM_STEP ** ((dx + dy) / (style.ZOOM_DRAG * ui_scale()))     # up or right zooms in
                host.set_view(drag['view'])
                host.zoom_at(*drag['start'], drag['view'][2] * factor)
            elif kind == 'move':
                rx, ry, rw, rh = drag['rect']
                host.float_rect = (rx + dx, ry + dy, rw, rh)
            elif kind == 'side':
                host.drag_side(drag, dx)
            elif kind == 'resize':
                window = region_of(area, 'WINDOW')
                host.float_rect = view.resize_from_corner(drag['rect'], dx, dy, host.float_bounds(area, window),
                                                          host.float_min(), host.float_margin())
        else:
            return {'RUNNING_MODAL'}
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def carrying(self, context, area, host, event, drag):
        """A window of its own being moved or resized: it follows the cursor; let go on a dock target, it docks there."""
        carry = drag['carry']
        if event.type == 'ESC' and event.value == 'PRESS' or event.type == 'RIGHTMOUSE' and event.value == 'PRESS':
            popout.cancel_carry(host, carry)
            self.end_drag(context, host)
        elif event.value == 'RELEASE' and event.type == 'LEFTMOUSE':
            self.end_drag(context, host)
            if popout.finish_carry(host, carry):
                return {'CANCELLED'}                  # docked: this window's session is over
        elif event.type in MOVES:
            popout.follow(host, carry)
            if carry['kind'] == 'pop_move':
                popout.update_target(host)
            return {'RUNNING_MODAL'}                  # the window moves; what it shows does not change
        area.tag_redraw()
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        host = getattr(self, 'host', None)
        if host is not None:
            host.close()


class Dock:
    bl_label = "Dock It Here"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.area is not None and context.area.as_pointer() in sessions

    def execute(self, context):
        host = sessions[context.area.as_pointer()]
        shown = host.elsewhere()
        if shown is not None and shown.popout:
            popout.dock_into(shown, context.area, context.window)
        elif shown is not None:
            host.take_board(context.area)
            runtime.redraw_views()
        else:
            host.set_floating(context.area, False)
        context.area.tag_redraw()
        return {'FINISHED'}


class Panel:
    """The product's sidebar tab. Its panel only holds the room the board is drawn over, or says where the board is."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        runtime.tab_seen[context.area.as_pointer()] = time.monotonic()
        layout = self.layout
        host = sessions.get(context.area.as_pointer())
        shown = host.elsewhere() if host is not None else None
        dock_op = f"{runtime.prefix()}.dock"
        if shown is not None:
            layout.label(text="The board is in a window of its own" if shown.popout else "The board is in another view", icon='WINDOW')
            layout.operator(dock_op, icon='BACK')
            return
        if host is None and dismissed_here(context.area):
            layout.operator(f"{runtime.prefix()}.{runtime.board_class.session_name}", text=f"Open {runtime.title()}", icon='WINDOW')
            return
        if host is not None and host.floating:
            layout.label(text="Floating over the viewport", icon='WINDOW')
            layout.operator(dock_op, icon='BACK')
            return
        # The board covers the whole sidebar, so the panel claims the whole height: the sidebar then has nothing to scroll,
        # and with Region Overlap on its resize edge answers all the way down.
        spacer = layout.column()
        scale = context.preferences.system.ui_scale or 1.0
        spacer.ui_units_y = max(1.0, context.region.height / (20.0 * scale) - 1.0)
        spacer.label(text="Opening the board…" if host is None else "")


def new_host(area):
    """A new open board in `area`: the product's Host subclass (Board.host_class) or the framework's Host."""
    return (getattr(runtime.board_class, 'host_class', None) or Host)(area)


def functions_of(cls):
    """The functions of a class and its bases (the framework's mixins, a product's own Session), nearest last, so a class
    built from them carries every one in its own dict: a stand-in that copies only a class's own functions has them all."""
    out = {}
    for klass in reversed(cls.__mro__):
        if klass is not object:
            out.update({name: value for name, value in vars(klass).items()
                        if isinstance(value, (FunctionType, classmethod, staticmethod)) and not name.startswith('__')})
    return out


def make_classes():
    """The product's Session, Dock and Panel classes, named for its prefix and Board.session_name / panel_name
    (bpy.ops.<prefix>.session, <PREFIX>_OT_dock, <PREFIX>_PT_board by default), each built on the product's own subclass
    when it gives one (Board.session_class, dock_class, panel_class)."""
    board = runtime.board_class
    prefix = runtime.prefix()
    upper = prefix.upper()
    name, panel_name = getattr(board, 'session_name', 'session'), getattr(board, 'panel_name', 'board')
    base = getattr(board, 'session_class', None) or Session
    session = type(f"{upper}_OT_{name}", (base, bpy.types.Operator), {**functions_of(base),
        '__doc__': own_doc(base, Session) or f"Show the {runtime.title()} board in this 3D View",
        'bl_idname': f"{prefix}.{name}", 'bl_label': f"Open {runtime.title()}"})
    dock_base = getattr(board, 'dock_class', None) or Dock
    dock = type(f"{upper}_OT_dock", (dock_base, bpy.types.Operator), {**functions_of(dock_base),
        '__doc__': own_doc(dock_base, Dock) or "Put the board back in this sidebar", 'bl_idname': f"{prefix}.dock"})
    panel_base = getattr(board, 'panel_class', None) or Panel      # any class with draw (and poll); its bl_options win
    panel = type(f"{upper}_PT_{panel_name}", (panel_base, bpy.types.Panel), {**functions_of(panel_base),
        'bl_idname': f"{upper}_PT_{panel_name}", 'bl_label': getattr(panel_base, 'bl_label', None) or runtime.title(),
        'bl_category': runtime.tab(), '__doc__': own_doc(panel_base, Panel)})
    return (session, dock, panel)


def own_doc(cls, framework):
    """A product's own class's docstring (Blender's tooltip for its operator), not the framework class's."""
    return None if cls is framework else cls.__dict__.get('__doc__')


def dismissed_here(area):
    """Was the board dismissed in this view for what it would show now (Board.dismiss_token)?"""
    pointer = area.as_pointer()
    return pointer in runtime.dismissed and runtime.dismissed[pointer] == runtime.board_class.dismiss_token(area)


def should_open(area):
    """Should the timer open a board in this 3D View now? The product's Board.should_open (by default, the rule below)."""
    return runtime.board_class.should_open(area)


def should_open_default(area):
    """Its tab shows (or its board floats), and the user has not dismissed it there for what it would show now. Hiding the
    tab forgets that it was dismissed."""
    pointer = area.as_pointer()
    ui = region_of(area, 'UI')
    tab = compat.active_tab(ui) if ui is not None else None
    tab_shown = ui is not None and area.spaces.active.show_region_ui and (
        tab == runtime.tab() if tab is not None else pointer in runtime.tab_seen)
    if not tab_shown:
        runtime.dismissed.pop(pointer, None)
        return bool(runtime.places.get(pointer, {}).get('floating', False))
    return pointer not in runtime.dismissed or runtime.dismissed[pointer] != runtime.board_class.dismiss_token(area)


def open_where_shown():
    """Timer: open a session in every 3D View whose sidebar shows the product's tab, outside any draw callback."""
    if bpy.app.background:
        return None
    wm = bpy.context.window_manager
    if wm is None:
        return 0.5
    tick_board(wm)
    present = set()
    for window in list(wm.windows):
        for area in list(window.screen.areas):
            pointer = area.as_pointer()
            present.add(pointer)
            shown = sessions.get(pointer)
            if shown is not None and shown.popout:
                popout.hide_chrome(area)
            if area.type == 'VIEW_3D' and shown is not None and not shown.popout and shown.call(
                    runtime.board_class.should_replace, shown, area, failed=False, touch=False):
                shown.close()                            # what it shows changed: a fresh board, told what it replaced
                runtime.replaced[pointer] = shown
                shown = None
            if area.type != 'VIEW_3D' or shown is not None or not should_open(area):
                continue
            ui = region_of(area, 'UI')
            try:
                with bpy.context.temp_override(window=window, area=area, region=ui or region_of(area, 'WINDOW')):
                    runtime.session_op()('INVOKE_DEFAULT')
            except (RuntimeError, TypeError) as exc:
                runtime.replaced.pop(pointer, None)
                message = str(exc)
                if runtime.open_errors.get(pointer) != message:
                    runtime.open_errors[pointer] = message
                    print(f"{runtime.title()}: could not open the board ({message})")
            else:
                runtime.open_errors.pop(pointer, None)
    for pointer in [p for p in sessions if p not in present]:
        sessions[pointer].close()
    for pointer in [p for p in runtime.dismissed if p not in present]:
        runtime.dismissed.pop(pointer, None)
    for pointer in [p for p in runtime.replaced if p not in present]:
        runtime.replaced.pop(pointer, None)
    roam.sync()
    return 0.25


def tick_board(wm):
    """The product's own work on every tick of the timer (Board.on_tick), given the first window. A failure is printed once,
    and the timer goes on."""
    hook = getattr(runtime.board_class, 'on_tick', None)
    if hook is None or not wm.windows:
        return
    try:
        hook(wm.windows[0])
    except Exception as exc:                       # noqa: BLE001 - another add-on's data can be anything; the timer must live
        message = f"{type(exc).__name__}: {exc}"
        if runtime.open_errors.get('tick') != message:
            runtime.open_errors['tick'] = message
            print(f"{runtime.title()}: {message}")


def close_all():
    for host in list(sessions.values()):
        host.close()
    sessions.clear()
