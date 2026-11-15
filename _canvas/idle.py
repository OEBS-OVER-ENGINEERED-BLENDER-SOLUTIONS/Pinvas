"""Work a board does while nobody is using it (building what the next click will need), one step at a time on a timer:
never during a drag on the board or while the timeline plays, and resting four times as long as each step took, so it
takes at most a fifth of the time however big the work.

    self.start_idle(step)        # in a Board: step() does one piece and returns True while there is more

A product with more ways of being busy than a drag (an edit in progress, a dialog) passes `busy`; one with its own words
for a step that failed passes `report`."""
import time

import bpy

from . import runtime

FIRST = 1.2               # seconds before the first step
REST = 4.0                # rest this many times as long as a step took...
LEAST = 0.1               # ... and at least this long
BUSY = 0.6                # the board is being dragged: look again after this
PLAYING = 1.0             # the timeline plays: look again after this
_runs = {}                # id(host) -> the number of its current run; a new start or a stop ends the old one


def playing():
    wm = bpy.context.window_manager
    try:
        return any(window.screen.is_animation_playing for window in wm.windows) if wm else False
    except (AttributeError, RuntimeError):
        return False


def report_stopped(exc):
    print(f"{runtime.title() if runtime.board_class else 'OEBS Canvas'}: idle work stopped: {exc}")


def make_tick(host, step, run, is_playing=playing, clock=time.perf_counter, busy=None, report=report_stopped):
    """The timer function of one run. Returns the seconds until it runs again, or None when the run is over. `busy()` says
    the board is in use (default: it is being dragged); `report(exc)` tells of a step that failed (default: one line)."""
    def tick():
        if not host.alive or _runs.get(id(host)) != run:
            return None
        if (busy() if busy is not None else host.drag):
            return BUSY
        if is_playing():
            return PLAYING
        began = clock()
        try:
            more = step()
        except Exception as exc:
            report(exc)
            return None
        if not more:
            return None
        return max(LEAST, REST * (clock() - began))
    return tick


def start(host, step, first=FIRST, busy=None, report=report_stopped):
    """Start `step` for this host, replacing any run it had (`busy` and `report` as make_tick's). Returns the run number. A
    background Blender runs no timers, so there nothing is registered: tests drive make_tick() themselves."""
    run = _runs.get(id(host), 0) + 1
    _runs[id(host)] = run
    if not bpy.app.background:
        bpy.app.timers.register(make_tick(host, step, run, busy=busy, report=report), first_interval=first)
    return run


def stop(host):
    """End the host's run (the board closed, or the product stopped it)."""
    if id(host) in _runs:
        _runs[id(host)] += 1
