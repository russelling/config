"""
test_buf_comp_library.py -- checks every BUF group's maths against NumPy.

Runs each group in the real compositor (a headless render) on test images that
include the awkward cases (zero alpha, half alpha, negatives, values over 1)
and compares every pixel with a NumPy statement of Nuke's formula.

    /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup \
        --python test_buf_comp_library.py

Exit code 0 = all passed. Reads ../assets/buf_comp/buf_comp_library.blend.
"""

import os
import sys
import tempfile

import bpy
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LIB = os.path.normpath(os.path.join(HERE, "..", "assets", "buf_comp", "buf_comp_library.blend"))
OUT = os.path.join(tempfile.mkdtemp(prefix="buf_comp_test_"), "out.exr")
W = H = 8
TOL = 1e-4

# One RGBA value per pixel row (bottom row first). Premultiplied-plausible
# values plus the edge cases each formula has to survive.
ROWS_A = [(0.2, 0.4, 0.6, 0.5), (0.0, 0.0, 0.0, 0.0), (1.5, 0.5, 0.25, 1.0),
          (-0.2, 0.3, 0.9, 0.8), (0.8, 0.8, 0.8, 1.0), (0.1, 0.2, 0.3, 0.25),
          (2.0, 3.0, 0.5, 1.0), (0.5, 0.5, 0.5, 0.5)]
ROWS_B = [(0.5, 0.5, 0.5, 1.0), (0.3, 0.6, 0.9, 1.0), (0.25, 2.0, 0.5, 0.5),
          (0.1, 0.1, 0.1, 0.0), (1.2, 0.4, 0.0, 1.0), (0.0, 0.0, 0.0, 0.0),
          (1.5, 0.5, 4.0, 1.0), (0.4, 0.2, 0.1, 0.6)]
A = np.array(ROWS_A, np.float64)
B = np.array(ROWS_B, np.float64)
REC709 = np.array([0.2126, 0.7152, 0.0722])

failures = []


# --- plumbing ---------------------------------------------------------------

def setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    s.render.engine = "BLENDER_WORKBENCH"
    s.render.resolution_x, s.render.resolution_y = W, H
    s.render.resolution_percentage = 100
    s.render.image_settings.file_format = "OPEN_EXR"
    s.render.image_settings.color_depth = "32"
    s.render.filepath = OUT
    with bpy.data.libraries.load(LIB, link=False) as (src, dst):
        dst.node_groups = [n for n in src.node_groups if n.startswith("BUF_")]
    return s


def make_image(name, rows):
    img = bpy.data.images.new(name, W, H, alpha=True, float_buffer=True)
    try:
        img.colorspace_settings.is_data = True   # no colour conversion
    except AttributeError:
        pass
    px = np.zeros((H, W, 4), np.float32)
    for y in range(H):
        px[y, :] = rows[y]
    img.pixels.foreach_set(px.ravel())
    img.update()
    return img


def run(group, inputs, output="Image"):
    """inputs: socket name -> image name or value. Returns (H, 4) or (H,)."""
    s = bpy.context.scene
    tree = bpy.data.node_groups.new("__comp", "CompositorNodeTree")
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    s.compositing_node_group = tree
    gn = tree.nodes.new("CompositorNodeGroup")
    gn.node_tree = bpy.data.node_groups[group]
    for sock, val in inputs.items():
        if isinstance(val, str) and val in bpy.data.images:
            n = tree.nodes.new("CompositorNodeImage")
            n.image = bpy.data.images[val]
            tree.links.new(n.outputs["Image"], gn.inputs[sock])
        else:
            gn.inputs[sock].default_value = val
    src = gn.outputs[output]
    if src.type == "VALUE":   # float output: show it in R
        cc = tree.nodes.new("CompositorNodeCombineColor")
        tree.links.new(src, cc.inputs[0])
        src = cc.outputs[0]
    out = tree.nodes.new("NodeGroupOutput")
    tree.links.new(src, out.inputs[0])
    if os.path.exists(OUT):
        os.remove(OUT)
    bpy.ops.render.render(write_still=True)
    r = bpy.data.images.load(OUT, check_existing=False)
    a = np.array(r.pixels[:], np.float64).reshape(r.size[1], r.size[0], 4)
    bpy.data.images.remove(r)
    bpy.data.node_groups.remove(tree)
    a = a[:, 0, :]
    return a[:, 0] if output != "Image" and gn.outputs[output].type == "VALUE" else a


def check(label, got, want):
    want = np.asarray(want, np.float64)
    err = np.max(np.abs(got - want))
    ok = err <= TOL
    print(f"  {'ok  ' if ok else 'FAIL'} {label:44} max err {err:.2e}")
    if not ok:
        failures.append(label)
        for y in range(len(got)):
            print(f"        row {y}: got {np.round(got[y], 4)} want {np.round(want[y], 4)}")


def lerp(x, y, t):
    return x + (y - x) * t


def safe_div(x, y):
    return np.where(y == 0, 0.0, x / np.where(y == 0, 1.0, y))


# --- references ---------------------------------------------------------------

def nuke_merge(op, A, B):
    a, b = A[:, 3:4], B[:, 3:4]
    if op == "Over": return A + B * (1 - a)
    if op == "Under": return A * (1 - b) + B
    if op == "Plus": return A + B
    if op == "Minus": return A - B
    if op == "From": return B - A
    if op == "Multiply": return A * B
    if op == "Screen":
        return np.where(np.minimum(A, B) <= 1, A + B - A * B, np.maximum(A, B))
    if op == "Max": return np.maximum(A, B)
    if op == "Min": return np.minimum(A, B)
    if op == "Difference": return np.abs(A - B)
    if op == "Divide": return safe_div(A, B)
    if op == "In": return A * b
    if op == "Out": return A * (1 - b)
    if op == "Mask": return B * a
    if op == "Stencil": return B * (1 - a)
    raise KeyError(op)


def nuke_grade(img, bp, wp, lift, gain, mult, off, gamma, bclamp, wclamp,
               unpremult=False, mix=1.0):
    rgb, a = img[:, :3], img[:, 3:4]
    x = np.where((a > 0) & unpremult, safe_div(rgb, a), rgb)
    Ak = mult * (gain - lift) / (wp - bp)
    Bk = off + lift - Ak * bp
    v = Ak * x + Bk
    v = np.where(v > 0, np.power(np.maximum(v, 0), 1.0 / gamma), v)
    if bclamp: v = np.maximum(v, 0)
    if wclamp: v = np.minimum(v, 1)
    v = np.where((a > 0) & unpremult, v * a, v)
    return np.hstack([lerp(rgb, v, mix), a])


# --- tests --------------------------------------------------------------------

def main():
    if not os.path.isfile(LIB):
        sys.exit(f"Library not found: {LIB}")
    setup()
    make_image("A", ROWS_A)
    make_image("B", ROWS_B)
    print(f"Blender {bpy.app.version_string}, library {LIB}")

    print("passthrough")
    check("Image node in == out (no conversion)", run("BUF_Premult", {"Image": "A"})[:, 3], A[:, 3])

    print("BUF_Merge")
    ops = ["Over", "Under", "Plus", "Minus", "From", "Multiply", "Screen", "Max",
           "Min", "Difference", "Divide", "In", "Out", "Mask", "Stencil"]
    for op in ops:
        check(f"Merge {op}", run("BUF_Merge", {"B": "B", "A": "A", "Operation": op}),
              nuke_merge(op, A, B))
    check("Merge default operation is Over", run("BUF_Merge", {"B": "B", "A": "A"}),
          nuke_merge("Over", A, B))
    check("Merge Over, Mix 0.5 x Mask 0.5",
          run("BUF_Merge", {"B": "B", "A": "A", "Operation": "Over", "Mix": 0.5, "Mask": 0.5}),
          lerp(B, nuke_merge("Over", A, B), 0.25))
    check("KeyMix Mask 0.5", run("BUF_KeyMix", {"B": "B", "A": "A", "Mask": 0.5}),
          lerp(B, A, 0.5))

    print("BUF_Grade")
    knobs = dict(Blackpoint=0.1, Whitepoint=0.9, Lift=0.05, Gain=1.2, Multiply=1.1,
                 Offset=0.02, Gamma=1.5)
    ref = lambda **kw: nuke_grade(A, 0.1, 0.9, 0.05, 1.2, 1.1, 0.02, 1.5, **kw)
    check("Grade defaults are identity (black clamp on)",
          run("BUF_Grade", {"Image": "A"}), nuke_grade(A, 0, 1, 0, 1, 1, 0, 1, True, False))
    check("Grade all knobs", run("BUF_Grade", {"Image": "A", **knobs}),
          ref(bclamp=True, wclamp=False))
    check("Grade no black clamp (negatives stay linear)",
          run("BUF_Grade", {"Image": "A", **knobs, "Black Clamp": False}),
          ref(bclamp=False, wclamp=False))
    check("Grade white clamp", run("BUF_Grade", {"Image": "A", **knobs, "White Clamp": True}),
          ref(bclamp=True, wclamp=True))
    check("Grade unpremult", run("BUF_Grade", {"Image": "A", **knobs, "Unpremult": True}),
          ref(bclamp=True, wclamp=False, unpremult=True))
    check("Grade Mix 0.5", run("BUF_Grade", {"Image": "A", **knobs, "Mix": 0.5}),
          ref(bclamp=True, wclamp=False, mix=0.5))
    rgbk = {"Gain": (1.2, 0.8, 1.0, 1.0), "Lift": (0.0, 0.05, 0.1, 1.0),
            "Gamma": (1.0, 1.4, 0.7, 1.0)}
    want = nuke_grade(A, 0, 1, np.array([0.0, 0.05, 0.1]), np.array([1.2, 0.8, 1.0]), 1, 0,
                      np.array([1.0, 1.4, 0.7]), True, False)
    check("Grade_RGB per-channel gain/lift/gamma", run("BUF_Grade_RGB", {"Image": "A", **rgbk}), want)

    print("Colour")
    luma = (A[:, :3] @ REC709)[:, None]
    sat = luma + 0.3 * (A[:, :3] - luma)
    check("Saturation 0.3", run("BUF_Saturation", {"Image": "A", "Saturation": 0.3}),
          np.hstack([sat, A[:, 3:]]))
    check("Clamp 0.1..0.9 incl. alpha",
          run("BUF_Clamp", {"Image": "A", "Minimum": 0.1, "Maximum": 0.9}), np.clip(A, 0.1, 0.9))
    check("Clamp 0.1..0.9 RGB only",
          run("BUF_Clamp", {"Image": "A", "Minimum": 0.1, "Maximum": 0.9, "Clamp Alpha": False}),
          np.hstack([np.clip(A[:, :3], 0.1, 0.9), A[:, 3:]]))

    print("Channel")
    a = A[:, 3:]
    check("Premult", run("BUF_Premult", {"Image": "A"}), np.hstack([A[:, :3] * a, a]))
    check("Unpremult (alpha 0 left alone)", run("BUF_Unpremult", {"Image": "A"}),
          np.hstack([np.where(a > 0, safe_div(A[:, :3], a), A[:, :3]), a]))
    check("Copy alpha A -> B", run("BUF_Copy", {"B": "B", "A": "A"}), np.hstack([B[:, :3], a]))
    check("Copy alpha + Premult", run("BUF_Copy", {"B": "B", "A": "A", "Premult": True}),
          np.hstack([B[:, :3] * a, a]))
    check("Shuffle alpha to RGB", run("BUF_Shuffle_AlphaToRGB", {"Image": "A"}),
          np.hstack([a, a, a, a]))
    check("Shuffle solid alpha", run("BUF_Shuffle_SolidAlpha", {"Image": "A"}),
          np.hstack([A[:, :3], np.ones_like(a)]))

    print("Keyer")
    m = np.clip(((A[:, :3] @ REC709) - 0.2) / 0.6, 0, 1)[:, None]
    check("Keyer_Luma 0.2..0.8", run("BUF_Keyer_Luma", {"Image": "A", "Low": 0.2, "High": 0.8}),
          np.hstack([A[:, :3], m]))
    check("Keyer_Luma inverted Matte output",
          run("BUF_Keyer_Luma", {"Image": "A", "Low": 0.2, "High": 0.8, "Invert": True}, "Matte"),
          1 - m[:, 0])

    print("Format")
    y = (np.arange(H) + 0.5) / H
    bar = (np.abs(y - 0.5) > (W / 2.39) / H * 0.5).astype(float)[:, None]
    check("AspectMask 2.39, opacity 0.5",
          run("BUF_AspectMask", {"Image": "A", "Opacity": 0.5}),
          np.hstack([A[:, :3] * (1 - 0.5 * bar), A[:, 3:]]))

    print("CG")
    beauty = (A[:, :3] + B[:, :3]) * A[:, :3] * np.array([1.0, 0.5, 2.0]) + B[:, :3]
    check("AOV_Rebuild_Cycles diffuse x gain + emission",
          run("BUF_AOV_Rebuild_Cycles", {"Diffuse Direct": "A", "Diffuse Indirect": "B",
                                         "Diffuse Color": "A", "Emission": "B",
                                         "Diffuse Gain": (1.0, 0.5, 2.0, 1.0)}),
          np.hstack([beauty, np.ones_like(a)]))

    print("QC")
    d = np.abs(A[:, :3] - B[:, :3])
    check("QC_Difference image", run("BUF_QC_Difference", {"A": "A", "B": "B", "Gain": 10.0}),
          np.hstack([d * 10, np.ones_like(a)]))
    check("QC_Difference Diff output", run("BUF_QC_Difference", {"A": "A", "B": "B"}, "Diff"),
          d.max(axis=1))
    neg = (A[:, :3].min(axis=1) < 0)[:, None]
    over = (A[:, :3].max(axis=1) > 1)[:, None] & ~neg
    want = np.where(neg, [1.0, 0.0, 1.0], np.where(over, [0.0, 1.0, 1.0], A[:, :3]))
    check("QC_Range negatives magenta, >1 cyan", run("BUF_QC_Range", {"Image": "A"}),
          np.hstack([want, a]))

    print()
    if failures:
        print(f"{len(failures)} FAILED: {', '.join(failures)}")
        sys.exit(1)
    print("All passed.")


main()
