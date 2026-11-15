"""The one line the free edition has about Pro: "Get Pinvas Pro", last in the board's right-click menu."""
import bpy

URL = "https://docs.oebs-studios.com/pinvas"      # the Pinvas Pro guide, until Pro has a page of its own to sell from


class PINVAS_OT_get_pro(bpy.types.Operator):
    """Open the Pinvas Pro page in your web browser"""
    bl_idname = "pinvas.get_pro"
    bl_label = "Get Pinvas Pro"
    bl_options = {'INTERNAL'}

    def execute(self, _context):
        bpy.ops.wm.url_open(url=URL)
        return {'FINISHED'}


classes = (PINVAS_OT_get_pro,)


def draw(layout):
    """Added at the end of the board's right-click menu."""
    layout.separator()
    layout.operator("pinvas.get_pro", icon='URL')
