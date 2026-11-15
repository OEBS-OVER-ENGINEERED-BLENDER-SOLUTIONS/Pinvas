"""Pinvas's board in a window of its own is the framework's: this module is the framework's popout itself, under the name
the rest of Pinvas (and its tests) use."""
import sys

from ._canvas import popout as _popout

sys.modules[__name__] = _popout
