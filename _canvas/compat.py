"""Blender-version differences, each wrapped once (see docs/findings/2026-10-18-api-probe.md)."""
import bpy


def active_tab(region):
    """The sidebar tab on show in `region`, or None when this Blender cannot tell (Region.active_panel_category missing)."""
    return getattr(region, 'active_panel_category', None)


def set_active_tab(region, tab):
    """Show `tab` in the sidebar `region`. False when this Blender cannot (read-only or missing) or the sidebar has not drawn
    its tabs yet: then the tab stays one click away."""
    try:
        region.active_panel_category = tab
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def double_click_ms():
    """Blender's double-click time (Preferences > Input), in milliseconds."""
    inputs = getattr(bpy.context.preferences, 'inputs', None)
    return getattr(inputs, 'mouse_double_click_time', 350) or 350
