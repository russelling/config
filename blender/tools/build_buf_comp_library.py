"""
build_buf_comp_library.py -- builds the BUF compositor node-group library.

Repo:    config/blender (repo/pipeline/config/blender/tools/)
Output:  assets/buf_comp/buf_comp_library.blend
         assets/buf_comp/blender_assets.cats.txt
         assets/buf_comp/buf_comp_index.json   (the add-on's menu)
Blender: 5.1.x (the compositor API changed in 5.0; this will not run on 4.x)

Run it from any Mac with Blender 5.1 and the share mounted:

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python build_buf_comp_library.py

Writes beside this script by default (../assets/buf_comp/). --out DIR to send it
elsewhere, after a "--":

    ... --python build_buf_comp_library.py -- --out /some/other/folder

WHAT IT BUILDS
--------------
Nuke-style compositor node groups, marked as assets and filed in catalogs, so
they show up on the node editor's asset shelf and in the BUF add-on's menu.

  BUF/Merge    BUF_Merge (15 Nuke operations on one dropdown), BUF_KeyMix
  BUF/Color    BUF_Grade, BUF_Grade_RGB, BUF_Saturation, BUF_Clamp
  BUF/Channel  BUF_Premult, BUF_Unpremult, BUF_Copy, BUF_Shuffle_AlphaToRGB,
               BUF_Shuffle_SolidAlpha
  BUF/Keyer    BUF_Keyer_Luma
  BUF/Format   BUF_AspectMask
  BUF/CG       BUF_AOV_Rebuild_Cycles
  BUF/QC       BUF_QC_Difference, BUF_QC_Range

Maths follows Nuke's own definitions (premultiplied, operations applied to
all four channels for merges, RGB only for colour tools). Every group is
checked numerically by test_buf_comp_library.py.

Re-running is safe: it starts from an empty file every time, and catalog
UUIDs are derived from the catalog path, so they never change between builds
(files that use the assets keep finding their catalogs).
"""

import argparse
import json
import os
import sys
import uuid

import bpy

LIBRARY_FILE = "buf_comp_library.blend"
CATALOG_FILE = "blender_assets.cats.txt"
INDEX_FILE = "buf_comp_index.json"   # read by the buf_comp_nuke_tools add-on
AUTHOR = "Buffalo VFX"

# Stable namespace: catalog UUID = uuid5(namespace, catalog path).
_CATALOG_NS = uuid.UUID("5b1f0e2a-6c3d-4b8e-9f7a-2d4c6e8a0b1c")

REC709 = (0.2126, 0.7152, 0.0722)

_CATALOG_BY_NAME = {}   # group name -> catalog path, filled by Group()


# ---------------------------------------------------------------------------
# A tiny expression layer: arithmetic on these objects creates Math / Vector
# Math nodes. S is a scalar (one channel), V is RGB as a vector.
# ---------------------------------------------------------------------------

def _ident(coll, identifier):
    for s in coll:
        if s.identifier == identifier:
            return s
    raise KeyError(identifier)


class Graph:
    def __init__(self, ng):
        self.ng = ng

    def new(self, node_type, **props):
        n = self.ng.nodes.new(node_type)
        for k, v in props.items():
            setattr(n, k, v)
        return n

    def link(self, out, inp):
        self.ng.links.new(out, inp)

    def feed(self, inp, val):
        if isinstance(val, Expr):
            val = val.out
        if isinstance(val, bpy.types.NodeSocket):
            self.link(val, inp)
        elif inp.type == "VECTOR" and isinstance(val, (int, float)):
            inp.default_value = (float(val),) * 3
        else:
            inp.default_value = val


class Expr:
    def __init__(self, g, out):
        self.g = g
        self.out = out


def _is_vec(x):
    return isinstance(x, V)


class S(Expr):
    def _math(self, op, a, b):
        if _is_vec(a) or _is_vec(b):
            return V.promote(self.g, a)._vmath(op, V.promote(self.g, a), V.promote(self.g, b))
        n = self.g.new("ShaderNodeMath", operation=op)
        self.g.feed(n.inputs[0], a)
        self.g.feed(n.inputs[1], b)
        return S(self.g, n.outputs[0])

    def __add__(self, o): return self._math("ADD", self, o)
    def __radd__(self, o): return self._math("ADD", o, self)
    def __sub__(self, o): return self._math("SUBTRACT", self, o)
    def __rsub__(self, o): return self._math("SUBTRACT", o, self)
    def __truediv__(self, o): return self._math("DIVIDE", self, o)
    def __rtruediv__(self, o): return self._math("DIVIDE", o, self)

    def __mul__(self, o):
        if _is_vec(o):
            return o * self
        return self._math("MULTIPLY", self, o)

    __rmul__ = __mul__


class V(Expr):
    @staticmethod
    def promote(g, x):
        if isinstance(x, V):
            return x
        c = g.new("ShaderNodeCombineXYZ")
        for i in range(3):
            g.feed(c.inputs[i], x)
        return V(g, c.outputs[0])

    def _vmath(self, op, a, b):
        n = self.g.new("ShaderNodeVectorMath", operation=op)
        self.g.feed(_ident(n.inputs, "Vector"), a)
        self.g.feed(_ident(n.inputs, "Vector_001"), b)
        return V(self.g, _ident(n.outputs, "Vector"))

    def _scale(self, s):
        n = self.g.new("ShaderNodeVectorMath", operation="SCALE")
        self.g.feed(_ident(n.inputs, "Vector"), self)
        self.g.feed(_ident(n.inputs, "Scale"), s)
        return V(self.g, _ident(n.outputs, "Vector"))

    def __add__(self, o): return self._vmath("ADD", self, o)
    def __radd__(self, o): return self._vmath("ADD", o, self)
    def __sub__(self, o): return self._vmath("SUBTRACT", self, o)
    def __rsub__(self, o): return self._vmath("SUBTRACT", o, self)
    def __truediv__(self, o): return self._vmath("DIVIDE", self, o)
    def __rtruediv__(self, o): return self._vmath("DIVIDE", o, self)

    def __mul__(self, o):
        if isinstance(o, (S, int, float)):
            return self._scale(o)
        return self._vmath("MULTIPLY", self, o)

    __rmul__ = __mul__


def _unary(x, op):
    g = x.g
    if isinstance(x, V):
        n = g.new("ShaderNodeVectorMath", operation=op)
        g.feed(_ident(n.inputs, "Vector"), x)
        return V(g, _ident(n.outputs, "Vector"))
    n = g.new("ShaderNodeMath", operation=op)
    g.feed(n.inputs[0], x)
    return S(g, n.outputs[0])


def _binary(x, y, op):
    if isinstance(x, V) or isinstance(y, V):
        g = x.g if isinstance(x, Expr) else y.g
        return V(g, None)._vmath(op, V.promote(g, x), V.promote(g, y))
    return x._math(op, x, y) if isinstance(x, S) else y._math(op, x, y)


def absolute(x): return _unary(x, "ABSOLUTE")
def sign(x): return _unary(x, "SIGN")
def maximum(x, y): return _binary(x, y, "MAXIMUM")
def minimum(x, y): return _binary(x, y, "MINIMUM")
def power(x, y): return _binary(x, y, "POWER")
def lerp(x, y, t): return x + (y - x) * t


def step_above(x, edge):
    """1 where x > edge, else 0 (per channel)."""
    return maximum(sign(x - edge), 0.0)


# ---------------------------------------------------------------------------
# Group scaffolding
# ---------------------------------------------------------------------------

class Group:
    """One node group: interface, input/output nodes, split/join helpers."""

    def __init__(self, name, description, catalog, color_tag="NONE"):
        self.ng = bpy.data.node_groups.new(name, "CompositorNodeTree")
        self.ng.description = description
        self.ng.color_tag = color_tag
        self.ng.default_group_node_width = 160
        self.catalog = catalog
        _CATALOG_BY_NAME[name] = catalog
        self.g = Graph(self.ng)
        self._in = None
        self._out = None
        self._pending_menu_defaults = []

    def input(self, name, kind, default=None, lo=None, hi=None, desc=""):
        sock_type = {
            "color": "NodeSocketColor", "float": "NodeSocketFloat",
            "factor": "NodeSocketFloat", "bool": "NodeSocketBool",
            "int": "NodeSocketInt", "menu": "NodeSocketMenu",
        }[kind]
        item = self.ng.interface.new_socket(name, in_out="INPUT", socket_type=sock_type)
        if kind == "factor":
            item.subtype = "FACTOR"
            lo, hi = (0.0 if lo is None else lo), (1.0 if hi is None else hi)
        if default is not None and kind != "menu":
            item.default_value = default
        if lo is not None:
            item.min_value = lo
        if hi is not None:
            item.max_value = hi
        if desc:
            item.description = desc
        if kind == "menu":
            self._pending_menu_defaults.append((item, default))
        return item

    def output(self, name, kind="color", desc=""):
        sock_type = {"color": "NodeSocketColor", "float": "NodeSocketFloat"}[kind]
        item = self.ng.interface.new_socket(name, in_out="OUTPUT", socket_type=sock_type)
        if desc:
            item.description = desc
        return item

    @property
    def gin(self):
        if self._in is None:
            self._in = self.g.new("NodeGroupInput")
        return self._in

    @property
    def gout(self):
        if self._out is None:
            self._out = self.g.new("NodeGroupOutput")
        return self._out

    def s(self, name):
        return S(self.g, self.gin.outputs[name])

    def raw(self, name):
        return self.gin.outputs[name]

    def split(self, color_socket):
        """Color socket -> (V rgb, S alpha)."""
        sep = self.g.new("CompositorNodeSeparateColor")
        self.g.feed(sep.inputs[0], color_socket)
        comb = self.g.new("ShaderNodeCombineXYZ")
        for i in range(3):
            self.g.link(sep.outputs[i], comb.inputs[i])
        return V(self.g, comb.outputs[0]), S(self.g, sep.outputs[3])

    def join(self, rgb, alpha):
        """(V rgb, S alpha or number) -> Color socket."""
        sep = self.g.new("ShaderNodeSeparateXYZ")
        self.g.feed(sep.inputs[0], rgb)
        comb = self.g.new("CompositorNodeCombineColor")
        for i in range(3):
            self.g.link(sep.outputs[i], comb.inputs[i])
        self.g.feed(comb.inputs[3], alpha)
        return comb.outputs[0]

    def finish(self, **outputs):
        for name, val in outputs.items():
            self.g.feed(self.gout.inputs[name], val)
        # A group's menu default can only be set once its menu input is
        # linked to the switch that defines the items.
        for item, default in self._pending_menu_defaults:
            if default is not None:
                item.default_value = default
        _layout(self.ng)
        return self.ng


def _layout(ng):
    """Columns by link depth, so the inside of a group is readable."""
    depth = {n: 0 for n in ng.nodes}
    for _ in range(len(ng.nodes)):
        changed = False
        for l in ng.links:
            d = depth[l.from_node] + 1
            if d > depth[l.to_node]:
                depth[l.to_node] = d
                changed = True
        if not changed:
            break
    out = [n for n in ng.nodes if n.bl_idname == "NodeGroupOutput"]
    last = max(depth.values()) + 1 if depth else 0
    for n in out:
        depth[n] = last
    rows = {}
    for n in ng.nodes:
        col = depth[n]
        row = rows.get(col, 0)
        n.location = (col * 220, -row * 180)
        rows[col] = row + 1


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def _screen(A, B):
    # Nuke: A+B-AB if A or B <= 1, otherwise max(A, B)
    lin = A + B - A * B
    return lerp(lin, maximum(A, B), step_above(minimum(A, B), 1.0))


# (name, fn(A, B, a, b)). A/B are the channel being computed (V for RGB,
# S for alpha); a/b are always the two alphas. Order = dropdown order.
MERGE_OPS = [
    ("Over",       lambda A, B, a, b: A + B * (1.0 - a)),
    ("Under",      lambda A, B, a, b: A * (1.0 - b) + B),
    ("Plus",       lambda A, B, a, b: A + B),
    ("Minus",      lambda A, B, a, b: A - B),
    ("From",       lambda A, B, a, b: B - A),
    ("Multiply",   lambda A, B, a, b: A * B),
    ("Screen",     lambda A, B, a, b: _screen(A, B)),
    ("Max",        lambda A, B, a, b: maximum(A, B)),
    ("Min",        lambda A, B, a, b: minimum(A, B)),
    ("Difference", lambda A, B, a, b: absolute(A - B)),
    ("Divide",     lambda A, B, a, b: A / B),
    ("In",         lambda A, B, a, b: A * b),
    ("Out",        lambda A, B, a, b: A * (1.0 - b)),
    ("Mask",       lambda A, B, a, b: B * a),
    ("Stencil",    lambda A, B, a, b: B * (1.0 - a)),
]


def build_merge():
    grp = Group(
        "BUF_Merge",
        "Nuke Merge. B is the background (first input, like Nuke's B pipe), "
        "A the foreground. Premultiplied maths, applied to RGBA. Result = "
        "B + (operation - B) * Mix * Mask.",
        "BUF/Merge", "CONVERTER")
    grp.input("B", "color", (0, 0, 0, 0), desc="Background (Nuke's B input)")
    grp.input("A", "color", (0, 0, 0, 0), desc="Foreground (Nuke's A input)")
    grp.input("Operation", "menu", "Over", desc="Nuke merge operation")
    grp.input("Mask", "factor", 1.0, desc="Limits the effect; unconnected = everywhere")
    grp.input("Mix", "factor", 1.0)
    grp.output("Image")

    A, a = grp.split(grp.raw("A"))
    B, b = grp.split(grp.raw("B"))

    # ONE switch only: a menu input that drives two Menu Switch nodes is
    # ambiguous to Blender (even with identical items) and ends up with no
    # items at all. So each operation's RGB and alpha are joined into one
    # colour before the switch, and split again after it.
    sw = grp.g.new("GeometryNodeMenuSwitch", data_type="RGBA")
    sw.enum_items.clear()
    for name, _ in MERGE_OPS:
        sw.enum_items.new(name)
    grp.g.link(grp.raw("Operation"), sw.inputs[0])

    for i, (name, fn) in enumerate(MERGE_OPS, start=1):
        grp.g.link(grp.join(fn(A, B, a, b), fn(a, b, a, b)), sw.inputs[i])

    result, result_a = grp.split(sw.outputs[0])
    t = grp.s("Mix") * grp.s("Mask")
    rgb = lerp(B, result, t)
    alpha = lerp(b, result_a, t)
    return grp.finish(Image=grp.join(rgb, alpha))


def build_keymix():
    grp = Group(
        "BUF_KeyMix",
        "Nuke KeyMix: A over B through Mask, without using A's alpha. "
        "Result = B + (A - B) * Mask * Mix, on RGBA.",
        "BUF/Merge", "CONVERTER")
    grp.input("B", "color", (0, 0, 0, 0))
    grp.input("A", "color", (0, 0, 0, 0))
    grp.input("Mask", "factor", 1.0)
    grp.input("Mix", "factor", 1.0)
    grp.output("Image")
    A, a = grp.split(grp.raw("A"))
    B, b = grp.split(grp.raw("B"))
    t = grp.s("Mask") * grp.s("Mix")
    return grp.finish(Image=grp.join(lerp(B, A, t), lerp(b, a, t)))


# ---------------------------------------------------------------------------
# Colour
# ---------------------------------------------------------------------------

def _rgb_tool(grp, fn, unpremult_option=True):
    """Common wrapper for RGB-only colour tools, as Nuke's colour nodes:
    optional unpremult by alpha, then Mask/Mix back against the input.
    fn(rgb V) -> V. Alpha passes through untouched."""
    rgb, a = grp.split(grp.raw("Image"))
    src = rgb
    if unpremult_option:
        up = grp.s("Unpremult")
        rgb = lerp(rgb, rgb / a, up * step_above(a, 0.0))
    out = fn(rgb)
    if unpremult_option:
        out = lerp(out, out * a, up * step_above(a, 0.0))
    out = lerp(src, out, grp.s("Mix") * grp.s("Mask"))
    return grp.join(out, a)


def _std_tail(grp, unpremult=True):
    if unpremult:
        grp.input("Unpremult", "bool", False,
                  desc="Divide by alpha first and re-multiply after, like Nuke's (un)premult by alpha")
    grp.input("Mask", "factor", 1.0)
    grp.input("Mix", "factor", 1.0)
    grp.output("Image")


def build_grade(per_channel):
    name = "BUF_Grade_RGB" if per_channel else "BUF_Grade"
    kind = "color" if per_channel else "float"
    grp = Group(
        name,
        "Nuke Grade" + (" with per-channel (RGB) knobs" if per_channel else "") +
        ". out = (A*in + B) ^ (1/gamma), with A = multiply*(gain-lift)/"
        "(whitepoint-blackpoint) and B = offset + lift - A*blackpoint. "
        "Gamma leaves negatives linear. RGB only; alpha passes through.",
        "BUF/Color", "COLOR")

    def knob(n, v):
        grp.input(n, kind, (v, v, v, 1.0) if per_channel else v)

    grp.input("Image", "color", (0, 0, 0, 1))
    for n, v in (("Blackpoint", 0.0), ("Whitepoint", 1.0), ("Lift", 0.0),
                 ("Gain", 1.0), ("Multiply", 1.0), ("Offset", 0.0), ("Gamma", 1.0)):
        knob(n, v)
    grp.input("Black Clamp", "bool", True, desc="Clamp negatives to 0 (Nuke default: on)")
    grp.input("White Clamp", "bool", False)
    _std_tail(grp)

    def k(n):
        if per_channel:
            return grp.split(grp.raw(n))[0]
        return grp.s(n)

    def fn(x):
        A = k("Multiply") * (k("Gain") - k("Lift")) / (k("Whitepoint") - k("Blackpoint"))
        Bc = k("Offset") + k("Lift") - A * k("Blackpoint")
        v = x * A + Bc
        inv_gamma = 1.0 / maximum(k("Gamma"), 1e-6)
        v = power(maximum(v, 0.0), inv_gamma) + minimum(v, 0.0)
        v = lerp(v, maximum(v, 0.0), grp.s("Black Clamp"))
        v = lerp(v, minimum(v, 1.0), grp.s("White Clamp"))
        return v

    return grp.finish(Image=_rgb_tool(grp, fn))


def build_saturation():
    grp = Group(
        "BUF_Saturation",
        "Nuke Saturation (Rec.709 luminance): out = luma + saturation*(in - luma). "
        "RGB only.",
        "BUF/Color", "COLOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.input("Saturation", "float", 1.0, lo=0.0)
    _std_tail(grp)

    def fn(x):
        luma = V.promote(grp.g, _luma(grp, x))
        return luma + (x - luma) * grp.s("Saturation")

    return grp.finish(Image=_rgb_tool(grp, fn))


def _luma(grp, rgb):
    dot = grp.g.new("ShaderNodeVectorMath", operation="DOT_PRODUCT")
    grp.g.feed(_ident(dot.inputs, "Vector"), rgb)
    _ident(dot.inputs, "Vector_001").default_value = REC709
    return S(grp.g, _ident(dot.outputs, "Value"))


def build_clamp():
    grp = Group(
        "BUF_Clamp",
        "Nuke Clamp: limits RGB (and optionally alpha) to Minimum..Maximum.",
        "BUF/Color", "COLOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.input("Minimum", "float", 0.0)
    grp.input("Maximum", "float", 1.0)
    grp.input("Clamp Alpha", "bool", True)
    grp.input("Mix", "factor", 1.0)
    grp.output("Image")
    rgb, a = grp.split(grp.raw("Image"))
    lo, hi, mix = grp.s("Minimum"), grp.s("Maximum"), grp.s("Mix")
    out = minimum(maximum(rgb, lo), hi)
    a_cl = minimum(maximum(a, lo), hi)
    a_out = lerp(a, a_cl, grp.s("Clamp Alpha") * mix)
    return grp.finish(Image=grp.join(lerp(rgb, out, mix), a_out))


# ---------------------------------------------------------------------------
# Channel
# ---------------------------------------------------------------------------

def build_premult():
    grp = Group("BUF_Premult", "Nuke Premult: RGB multiplied by alpha.",
                "BUF/Channel", "VECTOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.output("Image")
    rgb, a = grp.split(grp.raw("Image"))
    return grp.finish(Image=grp.join(rgb * a, a))


def build_unpremult():
    grp = Group("BUF_Unpremult",
                "Nuke Unpremult: RGB divided by alpha; where alpha is 0 the "
                "RGB is left unchanged.",
                "BUF/Channel", "VECTOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.output("Image")
    rgb, a = grp.split(grp.raw("Image"))
    return grp.finish(Image=grp.join(lerp(rgb, rgb / a, step_above(a, 0.0)), a))


def build_copy():
    grp = Group("BUF_Copy",
                "Nuke Copy (alpha): B's RGB with A's alpha. Premult optional, "
                "for the usual Copy -> Premult pair.",
                "BUF/Channel", "VECTOR")
    grp.input("B", "color", (0, 0, 0, 1))
    grp.input("A", "color", (0, 0, 0, 1))
    grp.input("Premult", "bool", False)
    grp.output("Image")
    _, a = grp.split(grp.raw("A"))
    rgb, _ = grp.split(grp.raw("B"))
    rgb = lerp(rgb, rgb * a, grp.s("Premult"))
    return grp.finish(Image=grp.join(rgb, a))


def build_shuffle_alpha_to_rgb():
    grp = Group("BUF_Shuffle_AlphaToRGB",
                "Nuke Shuffle with alpha into R, G and B: view a matte.",
                "BUF/Channel", "VECTOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.output("Image")
    _, a = grp.split(grp.raw("Image"))
    return grp.finish(Image=grp.join(V.promote(grp.g, a), a))


def build_shuffle_solid_alpha():
    grp = Group("BUF_Shuffle_SolidAlpha",
                "Nuke Shuffle with white into alpha: alpha = 1, RGB untouched.",
                "BUF/Channel", "VECTOR")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.output("Image")
    rgb, _ = grp.split(grp.raw("Image"))
    return grp.finish(Image=grp.join(rgb, 1.0))


# ---------------------------------------------------------------------------
# Keyer
# ---------------------------------------------------------------------------

def build_keyer_luma():
    grp = Group("BUF_Keyer_Luma",
                "Nuke Keyer (luminance key): Rec.709 luma ramped from Low (0) "
                "to High (1) into alpha. RGB untouched; Matte also output alone.",
                "BUF/Keyer", "MATTE")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.input("Low", "float", 0.0)
    grp.input("High", "float", 1.0)
    grp.input("Invert", "bool", False)
    grp.output("Image")
    grp.output("Matte", "float")
    rgb, _ = grp.split(grp.raw("Image"))
    lo, hi = grp.s("Low"), grp.s("High")
    m = (_luma(grp, rgb) - lo) / maximum(hi - lo, 1e-6)
    m = minimum(maximum(m, 0.0), 1.0)
    m = lerp(m, 1.0 - m, grp.s("Invert"))
    return grp.finish(Image=grp.join(rgb, m), Matte=m)


# ---------------------------------------------------------------------------
# Format
# ---------------------------------------------------------------------------

def build_aspect_mask():
    grp = Group("BUF_AspectMask",
                "Letterbox guide for a target aspect ratio (default 2.39, as the "
                "QT review bake). Bars are darkened by Opacity; alpha untouched.",
                "BUF/Format", "DISTORT")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.input("Ratio", "float", 2.39, lo=0.1)
    grp.input("Opacity", "factor", 1.0)
    grp.output("Image")
    g = grp.g
    info = g.new("CompositorNodeImageInfo")
    g.link(grp.raw("Image"), info.inputs[0])
    res = g.new("ShaderNodeSeparateXYZ")
    g.link(_ident(info.outputs, "Resolution"), res.inputs[0])
    coords = g.new("CompositorNodeImageCoordinates")
    g.link(grp.raw("Image"), coords.inputs[0])
    xy = g.new("ShaderNodeSeparateXYZ")
    g.link(_ident(coords.outputs, "Normalized"), xy.inputs[0])
    w, h, y = S(g, res.outputs[0]), S(g, res.outputs[1]), S(g, xy.outputs[1])
    half_visible = (w / grp.s("Ratio")) / h * 0.5
    in_bar = step_above(absolute(y - 0.5), half_visible)
    rgb, a = grp.split(grp.raw("Image"))
    out = rgb * (1.0 - in_bar * grp.s("Opacity"))
    return grp.finish(Image=grp.join(out, a))


# ---------------------------------------------------------------------------
# CG
# ---------------------------------------------------------------------------

CYCLES_LOBES = [
    ("Diffuse", True), ("Glossy", True), ("Transmission", True),
]


def build_aov_rebuild_cycles():
    grp = Group(
        "BUF_AOV_Rebuild_Cycles",
        "Rebuilds a Cycles beauty from its light passes, with a gain per lobe "
        "for relighting: (Direct + Indirect) * Color for Diffuse, Glossy and "
        "Transmission, plus Volume, Emission and Environment. Unconnected "
        "passes contribute nothing.",
        "BUF/CG", "INPUT")
    for lobe, _ in CYCLES_LOBES:
        grp.input(f"{lobe} Direct", "color", (0, 0, 0, 1))
        grp.input(f"{lobe} Indirect", "color", (0, 0, 0, 1))
        grp.input(f"{lobe} Color", "color", (0, 0, 0, 1))
    grp.input("Volume Direct", "color", (0, 0, 0, 1))
    grp.input("Volume Indirect", "color", (0, 0, 0, 1))
    grp.input("Emission", "color", (0, 0, 0, 1))
    grp.input("Environment", "color", (0, 0, 0, 1))
    grp.input("Alpha", "factor", 1.0, desc="Usually the render's Alpha output")
    for lobe in ("Diffuse", "Glossy", "Transmission", "Volume", "Emission"):
        grp.input(f"{lobe} Gain", "color", (1, 1, 1, 1))
    grp.output("Image")

    def rgb(n):
        return grp.split(grp.raw(n))[0]

    total = None
    for lobe, _ in CYCLES_LOBES:
        term = (rgb(f"{lobe} Direct") + rgb(f"{lobe} Indirect")) * rgb(f"{lobe} Color")
        term = term * rgb(f"{lobe} Gain")
        total = term if total is None else total + term
    total = total + (rgb("Volume Direct") + rgb("Volume Indirect")) * rgb("Volume Gain")
    total = total + rgb("Emission") * rgb("Emission Gain")
    total = total + rgb("Environment")
    return grp.finish(Image=grp.join(total, grp.s("Alpha")))


# ---------------------------------------------------------------------------
# QC
# ---------------------------------------------------------------------------

def build_qc_difference():
    grp = Group("BUF_QC_Difference",
                "Difference QC: |A - B| * Gain in RGB, alpha 1. Diff is the "
                "largest channel difference, for thresholding.",
                "BUF/QC", "OUTPUT")
    grp.input("A", "color", (0, 0, 0, 1))
    grp.input("B", "color", (0, 0, 0, 1))
    grp.input("Gain", "float", 10.0, lo=0.0)
    grp.output("Image")
    grp.output("Diff", "float")
    A, _ = grp.split(grp.raw("A"))
    B, _ = grp.split(grp.raw("B"))
    d = absolute(A - B)
    sep = grp.g.new("ShaderNodeSeparateXYZ")
    grp.g.feed(sep.inputs[0], d)
    dmax = maximum(maximum(S(grp.g, sep.outputs[0]), S(grp.g, sep.outputs[1])),
                   S(grp.g, sep.outputs[2]))
    return grp.finish(Image=grp.join(d * grp.s("Gain"), 1.0), Diff=dmax)


def build_qc_range():
    grp = Group("BUF_QC_Range",
                "Flags out-of-range pixels: any channel below 0 shows magenta, "
                "any channel above Maximum shows cyan (when Check Maximum is on). "
                "Everything else passes through.",
                "BUF/QC", "OUTPUT")
    grp.input("Image", "color", (0, 0, 0, 1))
    grp.input("Maximum", "float", 1.0)
    grp.input("Check Maximum", "bool", True)
    grp.output("Image")
    rgb, a = grp.split(grp.raw("Image"))
    sep = grp.g.new("ShaderNodeSeparateXYZ")
    grp.g.feed(sep.inputs[0], rgb)
    r, gg, b = (S(grp.g, sep.outputs[i]) for i in range(3))
    lowest = minimum(minimum(r, gg), b)
    highest = maximum(maximum(r, gg), b)
    neg = step_above(0.0 - lowest, 0.0)
    over = step_above(highest, grp.s("Maximum")) * grp.s("Check Maximum")
    over = over * (1.0 - neg)   # negatives win
    magenta = V.promote(grp.g, 0.0)
    cyan = V.promote(grp.g, 0.0)
    _set_vec(magenta, (1.0, 0.0, 1.0))
    _set_vec(cyan, (0.0, 1.0, 1.0))
    out = lerp(lerp(rgb, magenta, neg), cyan, over)
    return grp.finish(Image=grp.join(out, a))


def _set_vec(v, rgb):
    node = v.out.node
    for i, c in enumerate(rgb):
        node.inputs[i].default_value = c


# ---------------------------------------------------------------------------

BUILDERS = [
    build_merge, build_keymix,
    lambda: build_grade(False), lambda: build_grade(True),
    build_saturation, build_clamp,
    build_premult, build_unpremult, build_copy,
    build_shuffle_alpha_to_rgb, build_shuffle_solid_alpha,
    build_keyer_luma, build_aspect_mask,
    build_aov_rebuild_cycles,
    build_qc_difference, build_qc_range,
]


def catalog_uuid(path):
    return str(uuid.uuid5(_CATALOG_NS, path))


def write_catalogs(out_dir, paths):
    all_paths = set()
    for p in paths:
        parts = p.split("/")
        for i in range(1, len(parts) + 1):
            all_paths.add("/".join(parts[:i]))
    lines = [
        "# This is an Asset Catalog Definition file for Blender.",
        "#",
        "# Written by build_buf_comp_library.py -- edit the script, not this file.",
        "",
        "VERSION 1",
        "",
    ]
    for p in sorted(all_paths):
        lines.append(f"{catalog_uuid(p)}:{p}:{p.replace('/', '-')}")
    with open(os.path.join(out_dir, CATALOG_FILE), "w") as fh:
        fh.write("\n".join(lines) + "\n")


def build(out_dir):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for ng in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(ng)

    built = []
    for fn in BUILDERS:
        grp_ng = fn()
        built.append(grp_ng)

    catalogs = []
    for ng in built:
        cat = _CATALOG_BY_NAME[ng.name]
        catalogs.append(cat)
        ng.asset_mark()
        ng.asset_data.catalog_id = catalog_uuid(cat)
        ng.asset_data.description = ng.description
        ng.asset_data.author = AUTHOR
        for tag in ("BUF", "Nuke", cat.split("/")[-1]):
            ng.asset_data.tags.new(tag, skip_if_exists=True)
        ng.use_fake_user = True

    os.makedirs(out_dir, exist_ok=True)
    write_catalogs(out_dir, catalogs)
    index = [{"name": ng.name, "catalog": cat, "description": ng.description}
             for ng, cat in zip(built, catalogs)]
    with open(os.path.join(out_dir, INDEX_FILE), "w") as fh:
        json.dump({"blender": bpy.app.version_string, "groups": index}, fh, indent=2)
    path = os.path.join(out_dir, LIBRARY_FILE)
    # No .blend1 backup beside the library: it would sit in the asset folder.
    bpy.context.preferences.filepaths.save_version = 0
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True, check_existing=False)
    print(f"Wrote {path} ({len(built)} groups)")
    for ng, cat in zip(built, catalogs):
        print(f"  {cat:12} {ng.name:26} {len(ng.nodes):4} nodes")
    return path


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser()
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--out", default=os.path.join(here, "..", "assets", "buf_comp"))
    args = ap.parse_args(argv)
    if bpy.app.version < (5, 0, 0):
        sys.exit("Needs Blender 5.0 or later (this is %s)." % bpy.app.version_string)
    build(os.path.normpath(args.out))


if __name__ == "__main__":
    main()
