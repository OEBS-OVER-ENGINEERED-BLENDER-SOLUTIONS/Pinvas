"""The colours of the frame round a board: fixed (the framework's own, from style.py) or taken from Blender's theme, so the
board looks like the rest of the interface in any theme. A product chooses with Board.palette ('fixed' or 'theme')."""
import time

from . import style

REFRESH = 1.0             # seconds a theme read is kept (a theme change shows within a second)
BLACK = (0.0, 0.0, 0.0, 1.0)
WHITE = (1.0, 1.0, 1.0, 1.0)

FIXED = dict(board=style.BOARD, bar=style.BAR, button=style.BUTTON, button_hover=style.BUTTON_HOVER, text=style.TEXT,
             muted=style.MUTED, faint=style.FAINT, accent=style.ACCENT, tip_back=style.TIP_BACK, chip=style.CHIP,
             chip_hover=style.CHIP_HOVER)

_cache = {'at': None, 'value': None}


def mix(a, b, t):
    """Straight RGB between a (t=0) and b (t=1), opaque."""
    return tuple(a[i] * (1 - t) + b[i] * t for i in range(3)) + (1.0,)


def rgba(value):
    return tuple(value[:3]) + (1.0,)


def from_theme(ui, grey_accent=style.ACCENT):
    """The frame's colours from a theme's user_interface (Preferences > Themes > User Interface). Pure: `ui` may be a stand-in
    with wcol_regular.inner / text / inner_sel (/ outline_sel) and wcol_box.inner. `grey_accent`: the accent kept where the
    theme's own is grey."""
    regular = ui.wcol_regular
    surface = rgba(regular.inner)
    back = rgba(ui.wcol_box.inner)
    text = rgba(regular.text)
    # A theme shows "switched on" as a coloured fill or as a coloured outline: take the more colourful of the two.
    accent = max((rgba(regular.inner_sel), rgba(getattr(regular, 'outline_sel', regular.inner_sel))),
                 key=lambda c: max(c[:3]) - min(c[:3]))
    if max(accent[:3]) - min(accent[:3]) < 0.08:
        accent = grey_accent                     # a grey theme: keep a colour that reads as "on"
    else:
        accent = tuple(v * 0.7 + 0.3 for v in accent[:3]) + (1.0,)
    # The bar and tips are set off from the board away from the text colour: darker in a dark theme, lighter in a light one,
    # so the theme's text stays readable on them.
    shade = BLACK if sum(text[:3]) > sum(back[:3]) else WHITE
    return dict(board=back, bar=mix(back, shade, 0.35), button=surface, button_hover=mix(surface, text, 0.12), text=text,
                muted=mix(text, back, 0.45), faint=mix(text, back, 0.7), accent=accent,
                tip_back=mix(back, shade, 0.6)[:3] + (0.95,), chip=style.CHIP, chip_hover=style.CHIP_HOVER)


def colours(kind, now=None):
    """The palette for Board.palette `kind`: 'fixed' (FIXED), 'theme' (the current theme's, read at most once every REFRESH
    seconds), a product's own palette (a dict, as it is), or a callable that returns one (asked every time: the product
    decides how often it reads anything)."""
    if isinstance(kind, dict):
        return kind
    if callable(kind):
        return kind()
    if kind != 'theme':
        return FIXED
    now = time.monotonic() if now is None else now
    if _cache['value'] is not None and now - _cache['at'] < REFRESH:
        return _cache['value']
    try:
        import bpy
        value = from_theme(bpy.context.preferences.themes[0].user_interface)
    except (AttributeError, IndexError, TypeError):
        value = FIXED
    _cache.update(at=now, value=value)
    return value


def forget():
    _cache.update(at=None, value=None)
