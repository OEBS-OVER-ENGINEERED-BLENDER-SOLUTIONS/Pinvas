"""Menus the board draws itself: a dropdown under a bar button, the right-click menu and the Shift+A menu.

Immediate mode, like the controls: `builds` are callables that add entries (`build(menu)`). They run to lay the menu out
and draw it, and run again when an entry is chosen, with that entry's `item(...)` returning True, so the product's code
reacts in its own `if menu.item("..."):` branch. An entry that cannot apply (enabled=False) is left out."""
from . import draw

FONT = 11
ITEM = 22                 # an entry's height, interface units
HEADER = 20
SEPARATOR = 7
PAD = 6
MIN_WIDTH = 120
TICK = "✓"            # a check mark
MORE = "▸"            # a small right-pointing triangle: the entry opens a submenu


class Menu:

    def __init__(self, builds, anchor, title=None):
        self.builds = list(builds) if isinstance(builds, (list, tuple)) else [builds]
        self.anchor = anchor              # (x, y) canvas pixels: the menu's top-left corner, opening downwards
        self.title = title
        self.rows = []                    # laid out: dicts with kind, label, key, shortcut, checked, build, rect
        self.rect = (0, 0, 0, 0)
        self.highlight = None             # the key of the highlighted entry
        self.child = None                 # an open submenu (a Menu)
        self.child_key = None
        self.mode, self.chosen, self.answered = 'paint', None, False

    # What builds call

    def item(self, label, key=None, shortcut=None, checked=None, enabled=True):
        """An entry. True (once) in the event pass when it is the one chosen."""
        if not enabled:
            return False
        key = key or label
        self.rows.append(dict(kind='item', label=label, key=key, shortcut=shortcut, checked=checked))
        if self.mode == 'event' and self.chosen == key and not self.answered:
            self.answered = True
            return True
        return False

    def separator(self):
        if self.rows and self.rows[-1]['kind'] != 'separator':
            self.rows.append(dict(kind='separator', key=None))

    def header(self, text):
        self.rows.append(dict(kind='header', label=text, key=None))

    def submenu(self, label, build):
        """An entry that opens `build`'s menu beside this one when the mouse rests on it."""
        self.rows.append(dict(kind='submenu', label=label, key=('submenu', label), build=build))

    # Running the builds

    def collect(self, mode='paint', chosen=None):
        """Run the builds; in the event pass, `chosen` answers True. Returns whether some entry answered."""
        self.rows, self.mode, self.chosen, self.answered = [], mode, chosen, False
        if self.title:
            self.header(self.title)
        for build in self.builds:
            if self.rows and self.rows[-1]['kind'] != 'separator' and self.rows[-1]['kind'] != 'header':
                self.separator()
            build(self)
        while self.rows and self.rows[-1]['kind'] == 'separator':
            self.rows.pop()
        return self.answered

    def layout(self, k, size):
        """Place the rows: the menu opens down from its anchor and is kept inside the canvas (size = width, height)."""
        width = MIN_WIDTH * k
        for row in self.rows:
            if row['kind'] in {'item', 'submenu', 'header'}:
                text = draw.text_width(row['label'], FONT * k)
                extra = draw.text_width(row.get('shortcut') or '', FONT * k) + (24 if row.get('shortcut') else 0) * k
                width = max(width, text + extra + (34 + (14 if row['kind'] == 'submenu' else 0)) * k)
        heights = [{'separator': SEPARATOR, 'header': HEADER}.get(row['kind'], ITEM) * k for row in self.rows]
        height = sum(heights) + 2 * PAD * k
        cw, ch = size
        x = min(max(0.0, self.anchor[0]), max(0.0, cw - width))
        top = min(max(height, self.anchor[1]), ch)
        y = top - height
        self.rect = (x, y, width, height)
        at = top - PAD * k
        for row, h in zip(self.rows, heights):
            at -= h
            row['rect'] = (x, at, width, h)
        return self.rect

    def open_child(self, row, k, size):
        """Open the submenu of `row` beside it (on the left when there is no room on the right)."""
        if self.child_key == row['key'] and self.child is not None:
            return
        x, y, w, h = row['rect']
        child = Menu(row['build'], (x + w - 2 * k, y + h + PAD * k))
        child.collect()
        cx, cy, cw, ch = child.layout(k, size)
        if cx + cw > size[0] - 1 or cx < x + w - 4 * k:
            child.anchor = (max(0.0, x - cw + 2 * k), y + h + PAD * k)
            child.layout(k, size)
        self.child, self.child_key = child, row['key']

    # Asking where things are

    def menus(self):
        """This menu and its open submenus, innermost last."""
        found, menu = [], self
        while menu is not None:
            found.append(menu)
            menu = menu.child
        return found

    def contains(self, x, y):
        return any(_inside(menu.rect, x, y) for menu in self.menus())

    def row_at(self, x, y):
        """(menu, row) under canvas (x, y), searching the innermost submenu first."""
        for menu in reversed(self.menus()):
            for row in menu.rows:
                if row['kind'] in {'item', 'submenu'} and _inside(row['rect'], x, y):
                    return menu, row
            if _inside(menu.rect, x, y):
                return menu, None
        return None, None

    def selectable(self):
        return [row for row in self.rows if row['kind'] in {'item', 'submenu'}]

    def step(self, direction):
        """Up/Down: move the highlight in the innermost menu."""
        menu = self.menus()[-1]
        rows = menu.selectable()
        if not rows:
            return
        keys = [row['key'] for row in rows]
        at = keys.index(menu.highlight) if menu.highlight in keys else (-1 if direction > 0 else 0)
        menu.highlight = keys[(at + direction) % len(keys)]

    # Drawing

    def paint(self, x0, y0, k, colours):
        for menu in self.menus():
            menu.paint_one(x0, y0, k, colours)

    def paint_one(self, x0, y0, k, colours):
        x, y, w, h = self.rect
        draw.box(x0 + x - 1, y0 + y - 1, w + 2, h + 2, (0.0, 0.0, 0.0, 0.35), 5 * k)            # a soft edge
        draw.box(x0 + x, y0 + y, w, h, colours['tip_back'][:3] + (0.98,), 4 * k)
        for row in self.rows:
            rx, ry, rw, rh = row['rect']
            if row['kind'] == 'separator':
                draw.lines(((x0 + rx + 8 * k, y0 + ry + rh / 2), (x0 + rx + rw - 8 * k, y0 + ry + rh / 2)), colours['faint'], 1.0)
                continue
            if row['kind'] == 'header':
                draw.text(x0 + rx + 10 * k, y0 + ry + rh / 2 - 4 * k, row['label'], (FONT - 1) * k, colours['muted'], rw - 20 * k)
                continue
            lit = self.highlight == row['key'] or (row['kind'] == 'submenu' and self.child_key == row['key'])
            if lit:
                draw.box(x0 + rx + 3 * k, y0 + ry + 1 * k, rw - 6 * k, rh - 2 * k, (*colours['accent'][:3], 0.55), 3 * k)
            if row.get('checked'):
                draw.text(x0 + rx + 8 * k, y0 + ry + rh / 2 - 4 * k, TICK, FONT * k, colours['text'])
            draw.text(x0 + rx + 24 * k, y0 + ry + rh / 2 - 4 * k, row['label'], FONT * k, colours['text'], rw - 34 * k)
            if row.get('shortcut'):
                sw = draw.text_width(row['shortcut'], FONT * k)
                draw.text(x0 + rx + rw - sw - 10 * k, y0 + ry + rh / 2 - 4 * k, row['shortcut'], FONT * k, colours['muted'])
            if row['kind'] == 'submenu':
                draw.text(x0 + rx + rw - 16 * k, y0 + ry + rh / 2 - 4 * k, MORE, FONT * k, colours['muted'])


def _inside(rect, x, y):
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh
