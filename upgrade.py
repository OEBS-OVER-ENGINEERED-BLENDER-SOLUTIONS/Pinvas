"""Where the free edition offers Pro: "Get Pinvas Pro", last in the board's right-click menu and in the add-on's
Preferences. Both open Pinvas Pro on Superhive."""
import bpy

URL = "https://superhivemarket.com/products/pinvas-pro"


class PINVAS_OT_get_pro(bpy.types.Operator):
    """Open Pinvas Pro on Superhive in your web browser"""
    bl_idname = "pinvas.get_pro"
    bl_label = "Get Pinvas Pro"
    bl_options = {'INTERNAL'}

    def execute(self, _context):
        bpy.ops.wm.url_open(url=URL)
        return {'FINISHED'}


class PinvasPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, _context):
        self.layout.operator("pinvas.get_pro", icon='URL')


classes = (PINVAS_OT_get_pro, PinvasPreferences)


def draw(layout):
    """Added at the end of the board's right-click menu."""
    layout.separator()
    layout.operator("pinvas.get_pro", icon='URL')
