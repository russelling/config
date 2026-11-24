"""
BUF Comp: Nuke-style tools for Blender's compositor.

Repo:   config/blender  (repo/pipeline/config/blender/addons/)
Loads:  automatically on Toolkit-launched Blender, through BLENDER_SYSTEM_SCRIPTS;
        enable once per machine in Preferences > Add-ons ("BUF Comp").

What it adds, in the compositor only:

  * Nuke hotkeys. A new node is wired in after the selected node, as Nuke
    does (into its first input -- B, for BUF_Merge), and spliced into any
    links that already left it.
  * Shift+A > BUF: the node-group library, by catalog.
  * The library registered as an asset library ("BUF Comp"), so the groups
    are also on the node editor's asset shelf.

The library is assets/buf_comp/buf_comp_library.blend beside this add-on's
folder, built by tools/build_buf_comp_library.py. Hotkeys and Shift+A
APPEND a group (reusing it when it is already in the file); the asset shelf
uses the library's import method (Pack under Blender 5.1's defaults). Either
way the group travels inside the .blend, so files work on the farm and off
the share.

Hotkeys that collide with Blender's own (G, M, B, C, T, R, W, F, Tab, 1) are
on by default and only act in the compositor: Blender adds add-on keymap
items ahead of its own, and these operators' poll fails in any other node
editor, so G still moves nodes in the shader editor. Switch them off in the
add-on's preferences to keep Blender's keys in the compositor too.
"""

bl_info = {
    "name": "BUF Comp: Nuke-style tools",
    "author": "Buffalo VFX",
    "version": (1, 0, 0),
    "blender": (5, 1, 0),
    "location": "Compositor: Nuke hotkeys, Shift+A > BUF, asset shelf",
    "description": "Nuke-style hotkeys and the BUF node-group library for the compositor",
    "category": "Compositing",
}

import json
import os

import bpy
from bpy.props import BoolProperty, StringProperty

ADDON_ID = __name__
ASSET_LIBRARY_NAME = "BUF Comp"
DEFAULT_LIBRARY = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "assets", "buf_comp", "buf_comp_library.blend"))
INDEX_FILE = "buf_comp_index.json"


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

def prefs():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def library_path():
    p = prefs()
    custom = bpy.path.abspath(p.library_path) if p and p.library_path else ""
    return os.path.normpath(custom) if custom else DEFAULT_LIBRARY


_index_cache = {"path": None, "mtime": None, "groups": []}


def library_index():
    """[{name, catalog, description}] from the library's index file."""
    path = os.path.join(os.path.dirname(library_path()), INDEX_FILE)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _index_cache["path"] != path or _index_cache["mtime"] != mtime:
        try:
            with open(path) as fh:
                groups = json.load(fh).get("groups", [])
        except (OSError, ValueError):
            groups = []
        _index_cache.update(path=path, mtime=mtime, groups=groups)
    return _index_cache["groups"]


def get_group(name):
    """The node group `name`: the one already in this file, else appended.

    "Already in this file" includes a copy dragged in from the asset shelf
    (packed, under 5.1's defaults), so hotkeys and shelf share one group.
    """
    for ng in bpy.data.node_groups:
        if ng.name == name and ng.bl_idname == "CompositorNodeTree":
            return ng
    path = library_path()
    if not os.path.isfile(path):
        raise RuntimeError(f"BUF library not found: {path}")
    with bpy.data.libraries.load(path, link=False, reuse_local_id=True,
                                 clear_asset_data=True) as (src, dst):
        if name not in src.node_groups:
            raise RuntimeError(f"{name} is not in {path}")
        dst.node_groups = [name]
    ng = dst.node_groups[0]
    ng.use_fake_user = False      # the library keeps one; a shot file shouldn't
    return ng


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def in_compositor(context):
    space = context.space_data
    return (space is not None and space.type == "NODE_EDITOR"
            and space.tree_type == "CompositorNodeTree"
            and space.edit_tree is not None)


def _first(sockets):
    for s in sockets:
        if s.enabled and not s.hide:
            return s
    return None


def insert_after(tree, prev, node):
    """Wire `node` in after `prev`, Nuke-style. Returns True if wired.

    prev's first output feeds node's first input; links that already left that
    output are moved to node's first output, so node is spliced into the
    stream rather than hung off the side.
    """
    out = _first(prev.outputs)
    inp = _first(node.inputs)
    if out is None or inp is None:
        return False
    new_out = _first(node.outputs)
    downstream = [l.to_socket for l in tree.links if l.from_socket == out]
    tree.links.new(out, inp)
    if new_out is not None:
        for to_sock in downstream:
            tree.links.new(new_out, to_sock)
    node.location = (prev.location.x + prev.width + 80, prev.location.y)
    return True


def add_node(tree, node_type="", group="", cursor=None):
    """Create a node (or BUF group) in `tree`. Returns (node, wired)."""
    prev = tree.nodes.active
    prev = prev if prev is not None and prev.select else None

    if group:
        ng = get_group(group)
        node = tree.nodes.new("CompositorNodeGroup")
        node.node_tree = ng
        node.width = ng.default_group_node_width
    else:
        node = tree.nodes.new(node_type)

    for n in tree.nodes:
        n.select = False
    node.select = True
    tree.nodes.active = node

    wired = prev is not None and insert_after(tree, prev, node)
    if not wired and cursor is not None:
        node.location = cursor
    return node, wired


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class BUF_OT_add_node(bpy.types.Operator):
    """Add a node, wired in after the selected node (Nuke-style)"""
    bl_idname = "buf.add_node"
    bl_label = "Add Node (Nuke-style)"
    bl_options = {"REGISTER", "UNDO"}

    node_type: StringProperty(options={"SKIP_SAVE"})
    group: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return in_compositor(context)

    @classmethod
    def description(cls, context, props):
        if props.group:
            for g in library_index():
                if g["name"] == props.group:
                    return g["description"]
        return cls.__doc__

    def invoke(self, context, event):
        self._cursor = context.space_data.cursor_location_from_region(
            event.mouse_region_x, event.mouse_region_y)
        return self._run(context, grab=True)

    def execute(self, context):
        self._cursor = tuple(context.space_data.cursor_location)
        return self._run(context, grab=False)

    def _run(self, context, grab):
        if not (self.node_type or self.group):
            self.report({"ERROR"}, "Nothing to add")
            return {"CANCELLED"}
        try:
            _, wired = add_node(context.space_data.edit_tree, self.node_type,
                                self.group, self._cursor)
        except (RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if grab and not wired:
            # Same as Blender's own Add menu: the node follows the mouse.
            bpy.ops.node.translate_attach_remove_on_cancel("INVOKE_DEFAULT")
        return {"FINISHED"}


class BUF_OT_node_search(bpy.types.Operator):
    """Search for a node to add (Nuke's Tab)"""
    bl_idname = "buf.node_search"
    bl_label = "Add Node Search"

    @classmethod
    def poll(cls, context):
        return in_compositor(context)

    def invoke(self, context, event):
        bpy.ops.wm.search_single_menu("INVOKE_DEFAULT", menu_idname="NODE_MT_add")
        return {"FINISHED"}


class BUF_OT_swap_inputs(bpy.types.Operator):
    """Swap the first two inputs of the active node (Nuke's Shift+X)"""
    bl_idname = "buf.swap_inputs"
    bl_label = "Swap Inputs"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return in_compositor(context) and context.space_data.edit_tree.nodes.active is not None

    def execute(self, context):
        tree = context.space_data.edit_tree
        swap_inputs(tree, tree.nodes.active)
        return {"FINISHED"}


def swap_inputs(tree, node):
    """Swap what feeds the node's first two visible inputs. False if < 2."""
    inputs = [s for s in node.inputs if s.enabled and not s.hide][:2]
    if len(inputs) < 2:
        return False
    src = []
    for s in inputs:
        link = next((l for l in tree.links if l.to_socket == s), None)
        src.append(link.from_socket if link else None)
        if link:
            tree.links.remove(link)
    for sock, frm in zip(inputs, reversed(src)):
        if frm is not None:
            tree.links.new(frm, sock)
    return True


class BUF_OT_view_fit(bpy.types.Operator):
    """Frame the selected nodes, or all of them if none are selected (Nuke's F)"""
    bl_idname = "buf.view_fit"
    bl_label = "Fit View"

    @classmethod
    def poll(cls, context):
        return in_compositor(context)

    def execute(self, context):
        if any(n.select for n in context.space_data.edit_tree.nodes):
            bpy.ops.node.view_selected()
        else:
            bpy.ops.node.view_all()
        return {"FINISHED"}


class BUF_OT_view_node(bpy.types.Operator):
    """Connect the active node to the Viewer (Nuke's 1)"""
    bl_idname = "buf.view_node"
    bl_label = "View Node"

    @classmethod
    def poll(cls, context):
        return in_compositor(context) and context.space_data.edit_tree.nodes.active is not None

    def execute(self, context):
        return bpy.ops.node.link_viewer()


# ---------------------------------------------------------------------------
# Shift+A > BUF menu
# ---------------------------------------------------------------------------

def _catalog_menus():
    cats = {}
    for g in library_index():
        cats.setdefault(g["catalog"].split("/")[-1], []).append(g["name"])
    return cats


class NODE_MT_buf_comp(bpy.types.Menu):
    bl_idname = "NODE_MT_buf_comp"
    bl_label = "BUF"

    def draw(self, context):
        layout = self.layout
        cats = _catalog_menus()
        if not cats:
            layout.label(text="BUF library not found", icon="ERROR")
            layout.label(text=library_path())
            return
        for cat, names in cats.items():
            layout.label(text=cat)
            for name in names:
                op = layout.operator(BUF_OT_add_node.bl_idname,
                                     text=name.removeprefix("BUF_").replace("_", " "))
                op.group = name
            layout.separator()


def draw_add_menu(self, context):
    if context.space_data and context.space_data.tree_type == "CompositorNodeTree":
        self.layout.separator()
        self.layout.menu(NODE_MT_buf_comp.bl_idname, icon="NODETREE")


# ---------------------------------------------------------------------------
# Hotkeys
# ---------------------------------------------------------------------------

# (key, modifiers, operator, properties, label). OVERRIDES collide with a
# Blender key; FREE ones don't. Only the compositor is affected by OVERRIDES
# (each operator's poll checks for it).
OVERRIDES = [
    ("TAB", {}, "buf.node_search", {}, "Node search (Blender: group edit -> Ctrl+Return)"),
    ("G", {}, "buf.add_node", {"group": "BUF_Grade"}, "Grade (Blender: move; drag instead)"),
    ("M", {}, "buf.add_node", {"group": "BUF_Merge"}, "Merge (Blender: mute -> D)"),
    ("B", {}, "buf.add_node", {"node_type": "CompositorNodeBlur"}, "Blur (Blender: box select; drag instead)"),
    ("C", {}, "buf.add_node", {"node_type": "CompositorNodeColorCorrection"}, "ColorCorrect (Blender: circle select)"),
    ("T", {}, "buf.add_node", {"node_type": "CompositorNodeTransform"}, "Transform (Blender: toolbar)"),
    ("R", {}, "buf.add_node", {"node_type": "CompositorNodeImage"}, "Read / Image (Blender: rotate)"),
    ("W", {}, "buf.add_node", {"node_type": "CompositorNodeOutputFile"}, "Write / File Output (Blender: context menu; right-click instead)"),
    ("F", {}, "buf.view_fit", {}, "Fit selected / all (Blender: join in frame -> Ctrl+J)"),
    ("ONE", {}, "buf.view_node", {}, "View active node (Blender: viewer slot 1)"),
]

FREE = [
    ("D", {}, "node.mute_toggle", {}, "Disable"),
    ("K", {}, "buf.add_node", {"group": "BUF_Copy"}, "Copy (alpha)"),
    ("O", {}, "buf.add_node", {"node_type": "CompositorNodeMask"}, "Roto / Mask"),
    ("PERIOD", {}, "buf.add_node", {"node_type": "NodeReroute"}, "Dot / Reroute"),
    ("BACK_SPACE", {}, "node.delete", {}, "Delete"),
    ("X", {"shift": True}, "buf.swap_inputs", {}, "Swap A/B inputs"),
    ("RET", {"ctrl": True}, "node.group_edit", {"exit": False}, "Enter group"),
]

_keymap_items = []


def register_keymaps():
    unregister_keymaps()
    p = prefs()
    if p is not None and not p.use_nuke_keys:
        return
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:          # background without a window manager keyconfig
        return
    km = kc.keymaps.new(name="Node Editor", space_type="NODE_EDITOR")
    entries = list(FREE)
    if p is None or p.override_blender_keys:
        entries = OVERRIDES + entries
    for key, mods, op, props, _label in entries:
        kmi = km.keymap_items.new(op, key, "PRESS", **mods)
        for k, v in props.items():
            setattr(kmi.properties, k, v)
        _keymap_items.append((km, kmi))


def unregister_keymaps():
    for km, kmi in _keymap_items:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymap_items.clear()


def _keys_changed(self, context):
    register_keymaps()


# ---------------------------------------------------------------------------
# Asset library
# ---------------------------------------------------------------------------

def register_asset_library():
    libs = bpy.context.preferences.filepaths.asset_libraries
    folder = os.path.dirname(library_path())
    for lib in libs:
        if os.path.normpath(bpy.path.abspath(lib.path)) == folder:
            return lib
    lib = libs.new(name=ASSET_LIBRARY_NAME, directory=folder)
    # One copy per file, carried inside it. Blender 5.1 offers "Append (Reuse
    # Data)" only with an experimental option on, and "Pack" (link + pack into
    # the file) otherwise; whichever this Blender accepts.
    for method in ("APPEND_REUSE", "PACK"):
        try:
            lib.import_method = method
            break
        except TypeError:
            continue
    return lib


def _library_changed(self, context):
    _index_cache["path"] = None
    if self.register_library:
        register_asset_library()


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class BUF_AP_comp_prefs(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    library_path: StringProperty(
        name="Library", subtype="FILE_PATH", default="",
        description="buf_comp_library.blend. Empty = the one shipped beside this add-on",
        update=_library_changed)
    use_nuke_keys: BoolProperty(
        name="Nuke hotkeys", default=True, update=_keys_changed)
    override_blender_keys: BoolProperty(
        name="Override Blender keys in the compositor", default=True,
        description="G, M, B, C, T, R, W, F, Tab and 1 act as in Nuke (compositor only)",
        update=_keys_changed)
    register_library: BoolProperty(
        name="Register as asset library", default=True,
        description=f'Adds "{ASSET_LIBRARY_NAME}" to File Paths > Asset Libraries',
        update=_library_changed)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "library_path")
        path = library_path()
        ok = os.path.isfile(path)
        col.label(text=("Using " if ok else "MISSING: ") + path,
                  icon="CHECKMARK" if ok else "ERROR")
        col.prop(self, "register_library")
        col.separator()
        col.prop(self, "use_nuke_keys")
        sub = col.column()
        sub.active = self.use_nuke_keys
        sub.prop(self, "override_blender_keys")
        box = sub.box()
        rows = (OVERRIDES if self.override_blender_keys else []) + FREE
        for key, mods, _op, _props, label in rows:
            keyname = "+".join([m.capitalize() for m in mods] + [key.replace("_", " ").title()])
            r = box.row()
            r.label(text=keyname)
            r.label(text=label)


# ---------------------------------------------------------------------------

CLASSES = (
    BUF_AP_comp_prefs,
    BUF_OT_add_node,
    BUF_OT_node_search,
    BUF_OT_swap_inputs,
    BUF_OT_view_fit,
    BUF_OT_view_node,
    NODE_MT_buf_comp,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.NODE_MT_add.append(draw_add_menu)
    register_keymaps()
    p = prefs()
    if p is None or p.register_library:
        try:
            register_asset_library()
        except Exception as exc:       # never block the add-on over this
            print(f"[BUF Comp] could not register the asset library: {exc}")


def unregister():
    unregister_keymaps()
    bpy.types.NODE_MT_add.remove(draw_add_menu)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
