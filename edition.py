"""Which edition this is, and the one rule between the two: only one of them may be on at a time.

Pinvas Pro and Pinvas (free) keep their boards in the same place (`scene.pinvas`), so a file made in the free one opens in Pro with
every picture where it was. For the same reason they cannot both run: the second one to start refuses and says why.
"""
import bpy

EDITION = 'pro'
EDITION = 'free'
NAMES = {'pro': "Pinvas Pro", 'free': "Pinvas"}
IDS = {'pro': 'pinvas', 'free': 'pinvas_free'}
OTHER = 'free' if EDITION == 'pro' else 'pro'


def other_edition_on(keys=None):
    """The module name of the other edition when it is enabled, else an empty string. `keys`: the enabled add-ons' names, when the caller
    already has them (a test gives its own)."""
    wanted = IDS[OTHER]
    for key in (bpy.context.preferences.addons.keys() if keys is None else keys):
        if key == wanted or key.endswith('.' + wanted):
            return key
    return ''


def refuse_if_other_is_on(keys=None):
    """Called when this edition registers: stop it, in plain words, when the other one is on."""
    if other_edition_on(keys):
        raise RuntimeError(f"{NAMES[OTHER]} is on, and {NAMES[EDITION]} cannot run beside it (they share their boards). "
                           f"Turn {NAMES[OTHER]} off first.")
