"""What is on the system clipboard, for Paste: a picture (PNG data or a bitmap, as screenshots and
image editors put it there), files copied in the file manager, or text naming picture files.

Blender's own clipboard paste only works in the Image Editor, so on Windows the clipboard is read
directly. Elsewhere only text is read (Blender's own clipboard), and file paths in it are used.
"""
import os
import struct
import sys
import time

CF_DIB = 8
CF_DIBV5 = 17
CF_HDROP = 15
BI_RGB = 0
BI_BITFIELDS = 3


def read():
    """(kind, value): ('png', bytes), ('dib', bytes), ('files', [paths]) or (None, None)."""
    if sys.platform != 'win32':
        return (None, None)
    try:
        return _read_windows()
    except OSError:
        return (None, None)


def _read_windows():
    import ctypes
    from ctypes import wintypes
    user32, kernel32, shell32 = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.shell32
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT

    png = user32.RegisterClipboardFormatW("PNG")
    for _attempt in range(5):                       # another program may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return (None, None)
    try:
        def block(fmt):
            handle = user32.GetClipboardData(fmt)
            if not handle:
                return None
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.string_at(pointer, kernel32.GlobalSize(handle))
            finally:
                kernel32.GlobalUnlock(handle)

        if user32.IsClipboardFormatAvailable(CF_HDROP):
            handle = user32.GetClipboardData(CF_HDROP)
            if handle:
                count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                paths = []
                for i in range(count):
                    length = shell32.DragQueryFileW(handle, i, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(handle, i, buffer, length + 1)
                    paths.append(buffer.value)
                if paths:
                    return ('files', paths)
        if png and user32.IsClipboardFormatAvailable(png):
            data = block(png)
            if data:
                return ('png', data)
        for fmt in (CF_DIBV5, CF_DIB):
            if user32.IsClipboardFormatAvailable(fmt):
                data = block(fmt)
                if data:
                    return ('dib', data)
        return (None, None)
    finally:
        user32.CloseClipboard()


def _shift(mask):
    if not mask:
        return 0, 0
    shift = 0
    while not (mask >> shift) & 1:
        shift += 1
    bits = 0
    while (mask >> (shift + bits)) & 1:
        bits += 1
    return shift, bits


def dib_pixels(data):
    """A Windows bitmap (BITMAPINFOHEADER or BITMAPV5HEADER followed by its pixels) as
    (width, height, rgba): rgba is a bytes object, 4 bytes per pixel, bottom row first (Blender's order).
    Raises ValueError for anything it cannot read (palette bitmaps, compressed bitmaps, short data)."""
    if len(data) < 40:
        raise ValueError("Too short to be a bitmap")
    size, width, height, _planes, bits, compression = struct.unpack_from('<IiiHHI', data, 0)
    if size < 40 or width <= 0 or height == 0:
        raise ValueError("Not a bitmap header")
    if size > len(data):
        raise ValueError("The bitmap's header is cut short")
    if bits not in (24, 32):
        raise ValueError(f"{bits}-bit bitmaps are not supported")
    if compression not in (BI_RGB, BI_BITFIELDS):
        raise ValueError("Compressed bitmaps are not supported")
    masks = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000 if bits == 32 else 0)
    offset = size
    if compression == BI_BITFIELDS:
        if size >= 52:
            r, g, b = struct.unpack_from('<III', data, 40)
            a = struct.unpack_from('<I', data, 52)[0] if size >= 56 else 0
            masks = (r, g, b, a)
        else:
            if len(data) < size + 12:
                raise ValueError("The bitmap's colour masks are cut short")
            r, g, b = struct.unpack_from('<III', data, size)
            masks = (r, g, b, 0)
            offset = size + 12                         # the three masks follow a plain header
    clr_used = struct.unpack_from('<I', data, 32)[0] if size >= 36 else 0
    offset += clr_used * 4
    rows = abs(height)
    stride = ((width * bits + 31) // 32) * 4
    if len(data) < offset + stride * rows:
        raise ValueError("The bitmap's pixels are cut short")
    import numpy as np
    raw = np.frombuffer(data, dtype=np.uint8, count=stride * rows, offset=offset).reshape(rows, stride)
    if bits == 24:
        px = raw[:, :width * 3].reshape(rows, width, 3)
        rgba = np.empty((rows, width, 4), dtype=np.uint8)
        rgba[..., 0], rgba[..., 1], rgba[..., 2], rgba[..., 3] = px[..., 2], px[..., 1], px[..., 0], 255
    else:
        words = raw[:, :width * 4].copy().view('<u4').reshape(rows, width)
        rgba = np.empty((rows, width, 4), dtype=np.uint8)
        for channel, mask in enumerate(masks):
            shift, length = _shift(mask)
            if not length:
                rgba[..., channel] = 255
                continue
            values = (words >> shift) & ((1 << length) - 1)
            rgba[..., channel] = (values * 255 // ((1 << length) - 1)).astype(np.uint8)
        if masks[3] and not rgba[..., 3].any():
            rgba[..., 3] = 255                       # many programs leave the alpha byte at 0 for an opaque picture
    if height < 0:
        rgba = rgba[::-1]                            # stored top row first: turn it to Blender's bottom-up order
    return width, rows, rgba.tobytes()


def picture_paths(text):
    """Existing picture files named in some text, one per line (quotes and file:/// allowed)."""
    out = []
    for line in (text or "").splitlines():
        path = line.strip().strip('"').strip("'")
        if path.lower().startswith('file:///'):
            from urllib.parse import unquote
            path = unquote(path[7:])                     # file:///C:/a.png -> /C:/a.png ; file:///home/a.png -> /home/a.png
            if len(path) > 2 and path[0] == '/' and path[2] == ':':
                path = path[1:]
            path = path.replace('/', os.sep)
        if path and os.path.isfile(path):
            out.append(path)
    return out
