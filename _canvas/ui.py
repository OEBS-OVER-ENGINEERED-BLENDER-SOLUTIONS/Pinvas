"""The product's controls, immediate mode: BarUi in the board's bar (right to left) and ColumnUi in a side panel (top to
bottom).

Board.draw_bar(ui) and Board.draw_side(ui) run on every redraw ('paint': the controls are drawn and their places recorded)
and again, outside the redraw, when a click lands on one of them ('event': nothing is drawn; the control clicked answers).
Because both passes lay the controls out the same way, the answer always belongs to the control drawn under the mouse, and
the product's code reacts outside Blender's drawing, where it may change data and push undo steps.

`ui.event` holds what came with the click: {'drag': dx, 'ctrl': bool, ...} for a slider being dragged, {'text': str} for a
text field committed, {} for a plain click."""
from contextlib import contextmanager

from . import draw, ui_input

FONT = 11                 # control text size, interface units
ROW = 22                  # a control's height in a column, interface units
GAP = 4                   # space between controls
PAD = 8                   # a column's inner margin
SLIDER_WIDTH = 120        # a slider in the bar
DROP_MARK = " ▾"      # the small down-pointing triangle after a dropdown's label


def clamp(value, low, high):
    return max(low, min(high, value))


class Controls:
    """What both kinds of Ui share: the controls themselves. A subclass says where the next control goes (place)."""

    def __init__(self, host, mode, x0, y0, k, clicked=None, event=None):
        self.host, self.mode = host, mode
        self.x0, self.y0, self.k = x0, y0, k
        self.clicked = clicked
        self.event = event or {}
        self.input = ui_input.of(host)

    # Where controls go (BarUi and ColumnUi)

    def place(self, width, height=None):
        """The (x, y, w, h) rect in canvas pixels for a control `width` wide, or None when there is no room for it."""
        raise NotImplementedError

    def natural(self, label):
        return draw.text_width(label, FONT * self.k) + 18 * self.k

    # Helpers

    @property
    def colours(self):
        return self.host.colours

    def answered(self, key):
        return self.mode == 'event' and self.clicked == key

    def note(self, key, rect, tip=None, **info):
        """Record a control: its hit for the host, its tip, and (paint pass) what the input handler needs to know."""
        if tip:
            self.host.ui_tips[key] = tip
        self.host.hits.append((rect, key))
        if self.mode == 'paint' and self.input is not None and info:
            self.input.register(key, rect=rect, **info)

    def frame(self, rect, key, fill=None):
        x, y, w, h = rect
        c = self.colours
        hovered = self.host.hover == key
        draw.box(self.x0 + x, self.y0 + y, w, h, fill or (c['button_hover'] if hovered else c['button']), 4 * self.k)

    def caption(self, rect, text, colour=None, centred=True):
        x, y, w, h = rect
        k = self.k
        colour = colour or self.colours['text']
        if centred:
            draw.text_centred(self.x0 + x + w / 2, self.y0 + y + h / 2, text, FONT * k, colour, w - 6 * k)
        else:
            draw.text(self.x0 + x + 6 * k, self.y0 + y + h / 2 - 4 * k, text, FONT * k, colour, w - 12 * k)

    # The controls

    def button(self, label, key=None, width=None, on=False, tip=None):
        """A button; True in the event pass when it was the one clicked. `key` defaults to the label."""
        key = ('ui', key or label)
        rect = self.place((width * self.k) if width else self.natural(label))
        if rect is None:
            return False                          # no room left: left out, never overlapping
        if tip:
            self.host.ui_tips[key] = tip
        if self.mode == 'paint':
            self.host.button(self.x0, self.y0, *rect, label, key, self.k, on)
        else:
            self.host.hits.append((rect, key))
        return self.answered(key)

    def toggle(self, label, owner, attr, key=None, tip=None):
        """A button that flips owner.attr; drawn on while it is true. True in the event pass when it was flipped."""
        on = bool(getattr(owner, attr))
        if self.button(label, key=key or attr, on=on, tip=tip):
            setattr(owner, attr, not on)
            self.host.touch()
            return True
        return False

    def slider(self, label, owner, attr, low, high, key=None, fmt="{:.2f}", tip=None):
        """A value dragged between low and high. A press starts a drag (the input handler carries it); each move sets
        owner.attr and returns True. Esc puts the start value back; Ctrl steps by a tenth of the range."""
        key = ('ui', key or attr)
        k = self.k
        rect = self.place(SLIDER_WIDTH * k if isinstance(self, BarUi) else None)
        if rect is None:
            return False
        value = getattr(owner, attr)
        if self.mode == 'paint':
            c = self.colours
            self.frame(rect, key)
            x, y, w, h = rect
            share = 0.0 if high == low else clamp((value - low) / (high - low), 0.0, 1.0)
            if share > 0:
                draw.box(self.x0 + x, self.y0 + y, w * share, h, (*c['accent'][:3], 0.55), 4 * k)
            self.caption(rect, f"{label}: {fmt.format(value)}")
        self.note(key, rect, tip, kind='slider', owner=owner, attr=attr, low=low, high=high)
        if not self.answered(key):
            return False
        if 'value' in self.event:                 # Esc: the start value back
            setattr(owner, attr, self.event['value'])
            self.host.touch()
            return True
        if 'drag' not in self.event:
            return False                          # a plain click: the drag starts in the input handler
        width = max(1.0, rect[2])
        start = self.event.get('start', value)
        new = start + self.event['drag'] * (high - low) / width
        if self.event.get('ctrl'):
            step = (high - low) / 10.0
            if step:
                new = low + round((new - low) / step) * step
        setattr(owner, attr, type(start)(clamp(new, low, high)) if isinstance(start, int) else clamp(new, low, high))
        self.host.touch()
        return True

    def menu(self, label, build, key=None, tip=None):
        """A dropdown button: a click opens the board-drawn menu `build` fills, under the button."""
        key = ('ui', key or label)
        text = label + DROP_MARK
        rect = self.place(self.natural(text))
        if rect is None:
            return None
        if tip:
            self.host.ui_tips[key] = tip
        if self.mode == 'paint':
            self.host.button(self.x0, self.y0, *rect, text, key, self.k, False)
        else:
            self.host.hits.append((rect, key))
            if self.answered(key) and self.input is not None:
                x, y, _w, _h = rect
                self.input.open_menu(build, (x, y))
        return None

    def label(self, text, muted=False):
        rect = self.place(draw.text_width(text, FONT * self.k) + 12 * self.k)
        if rect is not None and self.mode == 'paint':
            self.caption(rect, text, self.colours['muted'] if muted else self.colours['text'], centred=False)

    def separator(self):
        raise NotImplementedError


class BarUi(Controls):
    """Controls in the bar, from the right towards the title. A control that does not fit is left out."""

    def __init__(self, host, mode, x0, y0, right, y, height, k, clicked=None, event=None):
        super().__init__(host, mode, x0, y0, k, clicked, event)
        self.right, self.y, self.height = right, y, height
        self.least = 70 * k                       # the title keeps at least this much of the bar

    def place(self, width, height=None):
        width = width if width is not None else SLIDER_WIDTH * self.k
        if self.right - width < self.least:
            return None
        self.right -= width
        rect = (self.right, self.y, width, self.height)
        self.right -= GAP * self.k
        return rect

    def separator(self):
        self.right -= 2 * GAP * self.k


class ColumnUi(Controls):
    """Controls in a side panel `rect` (x, y, w, h in canvas pixels), from the top down, one per row; row() puts several
    side by side. A control below the panel's bottom is left out."""

    def __init__(self, host, mode, x0, y0, rect, k, clicked=None, event=None):
        super().__init__(host, mode, x0, y0, k, clicked, event)
        self.rect = rect
        x, y, w, h = rect
        self.left, self.right = x + PAD * k, x + w - PAD * k
        self.top = y + h - PAD * k                # the next row's top edge
        self.bottom = y + PAD * k
        self.in_row = None                        # the next x while inside row()

    def place(self, width, height=None):
        k = self.k
        height = height or ROW * k
        if self.in_row is not None:
            width = width or (self.right - self.left) / 2
            if self.in_row + width > self.right + 0.5 or self.top - height < self.bottom:
                return None
            rect = (self.in_row, self.top - height, width, height)
            self.in_row += width + GAP * k
            self.row_height = max(self.row_height, height)
            return rect
        if self.top - height < self.bottom:
            return None
        rect = (self.left, self.top - height, self.right - self.left, height)
        self.top -= height + GAP * k
        return rect

    @contextmanager
    def row(self):
        """Lay the controls inside the `with` block side by side, each at its natural width."""
        self.in_row, self.row_height = self.left, 0
        try:
            yield self
        finally:
            self.in_row = None
            if self.row_height:
                self.top -= self.row_height + GAP * self.k

    def separator(self):
        k = self.k
        if self.mode == 'paint' and self.top - 6 * k > self.bottom:
            y = self.y0 + self.top - 3 * k
            draw.lines(((self.x0 + self.left, y), (self.x0 + self.right, y)), self.colours['faint'], 1.0)
        self.top -= 6 * k

    def label(self, text, muted=False):
        rect = self.place(None if self.in_row is None else draw.text_width(text, FONT * self.k) + 12 * self.k)
        if rect is not None and self.mode == 'paint':
            self.caption(rect, text, self.colours['muted'] if muted else self.colours['text'], centred=False)

    def list_view(self, items, label_of, selected, key, rows=6):
        """A list of `items` showing `rows` at a time (the wheel scrolls). Returns the index clicked in the event pass, else
        None. `selected` is the index drawn chosen (or None)."""
        k = self.k
        row_h = ROW * k
        rect = self.place(None, rows * row_h)
        if rect is None:
            return None
        items = list(items)
        top = self.input.scroll_of(('ui', key), len(items), rows) if self.input is not None else 0
        x, y, w, h = rect
        if self.mode == 'paint':
            c = self.colours
            draw.box(self.x0 + x, self.y0 + y, w, h, c['board'], 4 * k)
        chosen = None
        for slot in range(rows):
            index = top + slot
            if index >= len(items):
                break
            row_rect = (x, y + h - (slot + 1) * row_h, w, row_h)
            row_key = ('ui', key, index)
            if self.mode == 'paint':
                if index == selected:
                    draw.box(self.x0 + row_rect[0], self.y0 + row_rect[1], w, row_h, (*self.colours['accent'][:3], 0.45), 3 * k)
                elif self.host.hover == row_key:
                    draw.box(self.x0 + row_rect[0], self.y0 + row_rect[1], w, row_h, self.colours['button'], 3 * k)
                self.caption(row_rect, str(label_of(items[index])), centred=False)
            self.host.hits.append((row_rect, row_key))
            if self.answered(row_key):
                chosen = index
        if self.mode == 'paint' and self.input is not None:
            self.input.register(('ui', key), rect=rect, kind='list', count=len(items), rows=rows)
        return chosen

    def text_field(self, owner, attr, key=None, placeholder="", width=None):
        """A line of text for owner.attr. A click focuses it (typing goes to it); Return commits, Esc cancels, a click
        elsewhere commits. True in the event pass when the text was committed (owner.attr is set then)."""
        k = self.k
        key = ('ui', key or attr)
        rect = self.place((width * k) if width else None)
        if rect is None:
            return False
        focused = self.input is not None and self.input.focus is not None and self.input.focus['key'] == key
        if self.mode == 'paint':
            c = self.colours
            self.frame(rect, key, fill=c['board'] if not focused else c['button_hover'])
            if focused:
                draw.outline(self.x0 + rect[0], self.y0 + rect[1], rect[2], rect[3], c['accent'], 4 * k, 1.0)
                text, caret = self.input.focus['text'], self.input.focus['caret']
                self.caption(rect, text, centred=False)
                cx = self.x0 + rect[0] + 6 * k + draw.text_width(text[:caret], FONT * k)
                draw.lines(((cx, self.y0 + rect[1] + 4 * k), (cx, self.y0 + rect[1] + rect[3] - 4 * k)), c['text'], 1.0)
            else:
                value = str(getattr(owner, attr))
                self.caption(rect, value or placeholder, c['text'] if value else c['faint'], centred=False)
        self.note(key, rect, None, kind='text', owner=owner, attr=attr)
        if not self.answered(key):
            return False
        if 'text' in self.event:
            setattr(owner, attr, self.event['text'])
            self.host.touch()
            return True
        if self.input is not None:
            self.input.focus_field(key, str(getattr(owner, attr)))
        return False
