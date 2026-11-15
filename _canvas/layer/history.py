"""The board's own history (Ctrl+Z / Ctrl+Shift+Z over the board) and Blender's undo steps.

Every finished action is one checkpoint: the store's snapshot after it, and one Blender undo step named after it, so
Edit > Undo outside the board steps back through the same actions (and the layer reads the store again after it)."""
import bpy

LIMIT = 64                # checkpoints kept


def push_undo(label):
    """One Blender undo step. There is none to push without a window (a background Blender)."""
    try:
        bpy.ops.ed.undo_push(message=label)
    except RuntimeError:
        pass


class History:

    def __init__(self, store, push=None):
        self.store = store
        self.push = push or push_undo           # how an undo step is pushed (the product may route it)
        self.states = [(None, store.snapshot())]     # (label, snapshot after it): the first is how the board was found
        self.at = 0

    def catch_up(self):
        """Record a change made outside the history (the product's own code, another add-on) as a step of its own, so
        undoing the next action never takes it away too."""
        now = self.store.snapshot()
        if now != self.states[self.at][1]:
            self.states = self.states[:self.at + 1] + [("Change", now)]
            self.at = len(self.states) - 1

    def checkpoint(self, label, before=None):
        """An action has finished: remember the board as it is now, and push Blender's undo step (with the snapshot from
        before it, when the product asked for that)."""
        self.states = self.states[:self.at + 1] + [(label, self.store.snapshot())]
        if len(self.states) > LIMIT:
            self.states = self.states[-LIMIT:]
        self.at = len(self.states) - 1
        self.push(label) if before is None else self.push(label, before=before)

    def can_undo(self):
        return self.at > 0

    def can_redo(self):
        return self.at < len(self.states) - 1

    def undo(self):
        """Step back one action. Returns its label, or None when there is nothing to undo."""
        if not self.can_undo():
            return None
        label = self.states[self.at][0]
        self.at -= 1
        self.store.restore(self.states[self.at][1])
        self.push(f"Undo {label}")
        return label

    def redo(self):
        if not self.can_redo():
            return None
        self.at += 1
        label = self.states[self.at][0]
        self.store.restore(self.states[self.at][1])
        self.push(label)
        return label

    def reread(self):
        """Blender's own undo (or redo) changed the scene: start again from the store as it is now."""
        self.states = [(None, self.store.snapshot())]
        self.at = 0
