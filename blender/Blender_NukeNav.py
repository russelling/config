# Blender_NukeNav keymap preset
# Stock Blender keymap (built from this Blender version's own defaults) plus
# Nuke-style Alt+drag viewport navigation. Every other shortcut is default Blender.
#
#   3D Viewport:                    Alt+LMB drag = Pan   Alt+MMB drag = Zoom   Alt+RMB drag = Orbit
#   Image / Clip / Node & 2D views: Alt+LMB drag = Pan   Alt+MMB drag = Zoom
#
# Bindings use "Click Drag", so plain Alt+clicks (e.g. edge-loop select) still work.
# The stock Alt+MMB-drag "snap view to axis" is removed so it doesn't fight the zoom.
#
# Install on another machine: Preferences > Keymap > Import..., pick this file,
# then choose "Blender NukeNav" in the keymap dropdown.
import os
import bpy

PRESET_FILE = __file__
IDNAME = os.path.splitext(os.path.basename(PRESET_FILE))[0]

NAV = {
    "3D View": [
        ("view3d.move",   "LEFTMOUSE"),
        ("view3d.zoom",   "MIDDLEMOUSE"),
        ("view3d.rotate", "RIGHTMOUSE"),
    ],
    "Image":       [("image.view_pan", "LEFTMOUSE"), ("image.view_zoom", "MIDDLEMOUSE")],
    "Clip Editor": [("clip.view_pan",  "LEFTMOUSE"), ("clip.view_zoom",  "MIDDLEMOUSE")],
    "View2D":      [("view2d.pan",     "LEFTMOUSE"), ("view2d.zoom",     "MIDDLEMOUSE")],
}

def _patch(keyconfig_data):
    for km_name, _km_args, km_content in keyconfig_data:
        if km_name not in NAV:
            continue
        items = km_content["items"]
        if km_name == "3D View":
            # drop stock Alt+MMB drag view-axis snapping (conflicts with Alt+MMB zoom)
            items[:] = [it for it in items
                        if not (it[0] == "view3d.view_axis"
                                and it[1].get("type") == "MIDDLEMOUSE"
                                and it[1].get("alt"))]
        new = [(op, {"type": btn, "value": "CLICK_DRAG", "alt": True}, None)
               for op, btn in NAV[km_name]]
        items[:0] = new          # highest priority within the keymap
    return keyconfig_data

def _load_stock_with_patch():
    stock_path = bpy.utils.preset_find("Blender", "keyconfig")
    ns = {"__file__": stock_path, "__name__": "nukenav_stock_blender"}
    with open(stock_path, encoding="utf-8") as fh:
        exec(compile(fh.read(), stock_path, "exec"), ns)
    # rename the stock preset's identity to ours
    ns["IDNAME"] = IDNAME
    Prefs = ns["Prefs"]
    Prefs.bl_idname = IDNAME
    Prefs.__name__ = IDNAME + "_Prefs"
    try:
        bpy.utils.register_class(Prefs)
    except Exception:
        pass
    import bl_keymap_utils.io as kio
    stock_load = ns["load"]
    def load():
        orig = kio.keyconfig_init_from_data
        def patched(kc, data):
            return orig(kc, _patch(data))
        kio.keyconfig_init_from_data = patched
        try:
            stock_load()
        finally:
            kio.keyconfig_init_from_data = orig
    # the preset's option toggles call ns["load"]; route them through the patch too
    ns["load"] = load
    load()

if __name__ == "__main__":
    _load_stock_with_patch()
