"""Pictures on screen, under the names Pinvas has always used. OEBS Canvas's media does the work: one shader that turns
Blender's image textures into display colour with premultiplied alpha and applies the picture's looks, a safe way to get
each picture's texture, and the eyedropper's pixels."""
from ._canvas import media

_pixels = media._pixels               # the eyedropper's pixels, read once per picture


def forget():
    """Drop the shader, the list of failed pictures and the eyedropper's pixels (a new file, or the add-on going away)."""
    media.forget()


def is_missing(image):
    """Is there nothing to show for this picture (deleted datablock, file gone, unreadable)?"""
    return media.is_missing(image)


def texture(image):
    """The picture's GPU texture, or None when it cannot be had (then it is drawn as missing)."""
    return media.texture(image)


def retry(image):
    """Look at a picture again (after Pack, Reload or a new file path)."""
    media.retry(image)


def pixel(image, u, v):
    """The colour (r, g, b) in display values at (u, v) of a picture's own image (not of the screen), or None."""
    rgba = media.pixel(image, u, v)
    return tuple(rgba[:3]) if rgba is not None else None


def draw_image(tex, image, corners, opacity=1.0, display_target=True, grey=False):
    """Draw a picture (from `tex`, its texture) filling the four corners (region pixels, from its bottom-left corner round).
    `display_target`: the region takes display colour (the sidebar); False for one that encodes linear colour itself (the
    viewport's main region)."""
    media.draw_picture(image, corners, ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)), opacity, display_target,
                       grey=grey, texture=tex)
