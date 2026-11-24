"""
test_buf_comp_addon.py -- headless checks for the buf_comp_nuke_tools add-on.

Discovers the add-on the way Toolkit-launched Blender does (BLENDER_SYSTEM_SCRIPTS
pointing at config/blender), enables it, and checks: hotkeys registered (and
only the free ones when overrides are off), asset library registered, Shift+A
index, library append/reuse, Nuke-style wiring, input swap, clean unregister.

    BLENDER_SYSTEM_SCRIPTS=<config/blender> blender -b --factory-startup \
        --python test_buf_comp_addon.py
"""
import os, sys
import bpy, addon_utils

failures = []
def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok: failures.append(label)

NAME = "buf_comp_nuke_tools"
mod = addon_utils.enable(NAME, default_set=True, handle_error=None)
check("add-on found via BLENDER_SYSTEM_SCRIPTS and enabled", mod is not None)
check("add-on file is the one under config/blender/addons",
      mod is not None and mod.__file__.endswith(os.path.join("addons", NAME + ".py")), mod and mod.__file__)

kc = bpy.context.window_manager.keyconfigs.addon
km = kc.keymaps.get("Node Editor")
keys = {(i.type, i.shift, i.ctrl): i.idname for i in km.keymap_items} if km else {}
check("G -> Grade", keys.get(("G", False, False)) == "buf.add_node")
check("M -> Merge", keys.get(("M", False, False)) == "buf.add_node")
check("Tab -> node search", keys.get(("TAB", False, False)) == "buf.node_search")
check("D -> disable", keys.get(("D", False, False)) == "node.mute_toggle")
check("Shift+X -> swap inputs", keys.get(("X", True, False)) == "buf.swap_inputs")
check("Ctrl+Return -> enter group", keys.get(("RET", False, True)) == "node.group_edit")
g_item = next(i for i in km.keymap_items if i.type == "G")
check("G carries group=BUF_Grade", g_item.properties.group == "BUF_Grade")
n_items = len(km.keymap_items)

p = mod.prefs()
p.override_blender_keys = False
keys2 = {i.type for i in kc.keymaps["Node Editor"].keymap_items}
check("overrides off: G/M/B/C/T/R/W/F/Tab/1 released", not (keys2 & {"G","M","B","C","T","R","W","F","TAB","ONE"}), keys2)
check("overrides off: free keys kept", {"D","K","O","PERIOD","BACK_SPACE"} <= keys2, keys2)
p.use_nuke_keys = False
check("Nuke keys off: nothing registered", len(kc.keymaps["Node Editor"].keymap_items) == 0)
p.use_nuke_keys = True; p.override_blender_keys = True
check("toggling back restores every key", len(kc.keymaps["Node Editor"].keymap_items) == n_items)

libs = [l for l in bpy.context.preferences.filepaths.asset_libraries if l.name == mod.ASSET_LIBRARY_NAME]
check("asset library registered once", len(libs) == 1, [l.path for l in libs])
check("asset library import keeps one copy in the file (Append Reuse or Pack)", libs and libs[0].import_method in ("APPEND_REUSE", "PACK"), libs and libs[0].import_method)
check("asset library points at the library folder",
      libs and os.path.normpath(libs[0].path) == os.path.dirname(mod.library_path()))
mod.register_asset_library()
check("re-registering does not duplicate it",
      len([l for l in bpy.context.preferences.filepaths.asset_libraries if l.name == mod.ASSET_LIBRARY_NAME]) == 1)

cats = mod._catalog_menus()
check("Shift+A menu: all catalogs", set(cats) == {"Merge","Color","Channel","Keyer","Format","CG","QC"}, cats.keys())
check("Shift+A menu: all 16 groups", sum(map(len, cats.values())) == 16)

# --- wiring, on a real compositor tree ---------------------------------------
tree = bpy.data.node_groups.new("comp", "CompositorNodeTree")
read = tree.nodes.new("CompositorNodeImage"); read.location = (0, 0)
viewer = tree.nodes.new("CompositorNodeViewer"); viewer.location = (600, 0)
tree.links.new(read.outputs["Image"], viewer.inputs["Image"])
for n in tree.nodes: n.select = False
read.select = True; tree.nodes.active = read

grade, wired = mod.add_node(tree, group="BUF_Grade")
check("Grade wired after the selected Read", wired)
check("Read -> Grade.Image", any(l.from_node == read and l.to_node == grade and l.to_socket.name == "Image" for l in tree.links))
check("Grade spliced in: Grade -> Viewer, Read no longer -> Viewer",
      any(l.from_node == grade and l.to_node == viewer for l in tree.links)
      and not any(l.from_node == read and l.to_node == viewer for l in tree.links))
check("new node is selected and active", grade.select and tree.nodes.active == grade and not read.select)
check("placed to the right of the Read", grade.location.x > read.location.x)
check("appended group is local, unmarked, no fake user",
      grade.node_tree.library is None and grade.node_tree.asset_data is None and not grade.node_tree.use_fake_user)

merge, _ = mod.add_node(tree, group="BUF_Merge")
check("Merge wired into B (its first input), like Nuke", any(l.to_node == merge and l.to_socket.name == "B" and l.from_node == grade for l in tree.links))
check("Merge default operation is Over", merge.inputs["Operation"].default_value == "Over")
read2 = tree.nodes.new("CompositorNodeImage")
tree.links.new(read2.outputs["Image"], merge.inputs["A"])
check("swap inputs swaps A and B", mod.swap_inputs(tree, merge)
      and any(l.to_node == merge and l.to_socket.name == "B" and l.from_node == read2 for l in tree.links)
      and any(l.to_node == merge and l.to_socket.name == "A" and l.from_node == grade for l in tree.links))

before = len(bpy.data.node_groups)
for n in tree.nodes: n.select = False
g2, wired2 = mod.add_node(tree, group="BUF_Grade", cursor=(1000.0, 500.0))
check("second Grade reuses the group (no BUF_Grade.001)", len(bpy.data.node_groups) == before and g2.node_tree.name == "BUF_Grade")
check("nothing selected: not wired, placed at cursor", not wired2 and tuple(g2.location) == (1000.0, 500.0))

r3 = tree.nodes.new("CompositorNodeImage"); 
for n in tree.nodes: n.select = False
g2.select = True; tree.nodes.active = g2
rd, wired3 = mod.add_node(tree, node_type="CompositorNodeImage", cursor=(5.0, 5.0))
check("a node with no inputs (Read) is never wired", not wired3)

lib_path = mod.library_path()
fresh = bpy.data.node_groups.new("fresh", "CompositorNodeTree")
for ng in [g for g in bpy.data.node_groups if g.name == "BUF_Saturation"]:
    bpy.data.node_groups.remove(ng)
with bpy.data.libraries.load(lib_path, link=True) as (src, dst):
    dst.node_groups = ["BUF_Saturation"]
linked = dst.node_groups[0]
n_before = len(bpy.data.node_groups)
for n in fresh.nodes: n.select = False
sat, _ = mod.add_node(fresh, group="BUF_Saturation")
check("a shelf (linked/packed) copy already in the file is reused, not duplicated",
      sat.node_tree == linked and len(bpy.data.node_groups) == n_before)

addon_utils.disable(NAME, default_set=True)
km = kc.keymaps.get("Node Editor")
check("disable removes every hotkey", km is None or len(km.keymap_items) == 0)
check("disable removes the operators", not hasattr(bpy.types, "BUF_OT_add_node"))

print()
if failures:
    print(f"{len(failures)} FAILED"); sys.exit(1)
print("All passed.")
