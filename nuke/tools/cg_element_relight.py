"""
CG_Element_Relight
==================

Builds a single Nuke Group node ("CG_Element_Relight") with one slider per
lighting contribution pulled out of a Blender multi-layer EXR written by
the "Flow - Set Element Render Output" add-on (Normal/Depth + Cryptomatte
Object/Material + Diffuse/Glossy/Emission passes).

Wire your Read node (reading the multi-layer EXR) into this Group's single
input. Its output is the recombined image:

    Diffuse Color x (Diffuse Direct * k1 + Diffuse Indirect * k2)
  + Glossy  Color x (Glossy  Direct * k3 + Glossy  Indirect * k4)
  + Emission * k5

Each ki is one of the five sliders on the Group's "Relight" tab, letting a
compositor push/pull direct key light, bounced light, reflections, and
emissive glow independently, all without a re-render.

A "Map Depth to depth Channel" checkbox (also on the Relight tab, on by
default) additionally passes the source Depth/Z pass straight through onto
the output as Nuke's standard "depth.Z" channel, so ZDefocus/Position/other
depth-aware downstream nodes see it without a separate Shuffle. Unchecking
it removes the depth channel from the output again (nothing else changes).
Implemented as a single two-input Shuffle2 (see _depth_passthrough()) that
passes rgba through from the recombined image untouched and injects depth
from the original input -- an earlier version used a second Shuffle2 into
a Merge2(operation="copy"), which had a real bug: see _depth_passthrough()'s
docstring for why that let depth override the whole image.

NOT included in the reconstruction: transmission, volume, environment/AO
passes weren't part of the pass set enabled in the Blender add-on, so this
won't sum back to a pixel-perfect match of the Combined/beauty pass -- it's
built for relighting adjustments, not a lossless recombination. Normal
isn't part of this either (only Depth, via the checkbox above) -- it's a
utility pass some other tool downstream may want raw, not something this
tool merges in. Use Nuke's own Cryptomatte node directly on the Read's
CryptoObject/CryptoMaterial layers for object/material isolation -- that's
a separate, already-turnkey workflow this tool doesn't duplicate.

CHANNEL NAMES: the LAYER_DEFAULTS dict below is pinned to this studio's
actual View Layer naming, confirmed 2026-09-13 from a real Read node's
channel list: every pass is prefixed "ViewLayer_" and spelled out in full
(ViewLayer_Diffuse_Direct, ViewLayer_Glossy_Indirect, ViewLayer_Depth,
etc) -- NOT Blender's generic short-form abbreviations (DiffDir, GlossInd)
this file originally assumed, which never matched anything here and was
the real reason every Shuffle2 came up empty regardless of the connection-
order fix below. If a future render uses different View Layer naming (a
different show, a renamed View Layer, a Blender-version difference), check
the upstream Read node's Channels tab and, if a Shuffle node inside the
group shows only "black" for its input layer, either update LAYER_DEFAULTS
here or repoint that one Shuffle2's "in1" dropdown by hand (double-click
into the group first) to the correct layer name. No other node needs to
change.

HOW TO RUN THIS
---------------
1. Open Nuke, open the Script Editor (Windows menu > Script Editor, or the
   tab at the bottom of the interface).
2. Paste this entire file's contents in, then press Ctrl+Return (Cmd+Return
   on Mac) to execute it. This creates one "CG_Element_Relight" node in
   your current Node Graph.
3. Connect it downstream of your multi-layer EXR Read node.
4. Adjust the five sliders (and the depth checkbox) on its "Relight" tab.

To make this a permanent, menu-accessible tool instead of a one-off paste:
add a line calling build_cg_element_relight() from a toolbar menu command
in your Nuke preferences' menu.py (or drop this file itself into a
toolsets/ or plugin path folder and import it from menu.py) -- ask if you
want that wired up as a saved gizmo/toolset instead of a paste-and-run
script.
"""

import nuke


LAYER_DEFAULTS = {
    "diffuse_direct": "ViewLayer_Diffuse_Direct",
    "diffuse_indirect": "ViewLayer_Diffuse_Indirect",
    "diffuse_color": "ViewLayer_Diffuse_Color",
    "glossy_direct": "ViewLayer_Glossy_Direct",
    "glossy_indirect": "ViewLayer_Glossy_Indirect",
    "glossy_color": "ViewLayer_Glossy_Color",
    "emission": "ViewLayer_Emission",
    "depth": "ViewLayer_Depth",
}


def _quiet_autolabel(node):
    """Point this node's autolabel at its own `label` knob instead of
    Nuke's broken default script for this class.

    Every Nuke node carries a hidden "autolabel" knob holding a small
    python snippet Nuke evaluates to compute the node's on-DAG text. Per
    Foundry's docs: if that knob is blank (returns None), Nuke falls back
    to whatever's registered globally via nuke.addAutolabel() for that
    node's class -- it does NOT mean "no label". For some classes
    (Shuffle2, and apparently bare scripted Group nodes too) that global
    default references knobs (`this.output`/`this.channels`) that don't
    exist in the context these nodes are built in, and raises
    `ValueError: expecting a knob`, which Nuke then displays AS the node's
    label text.

    Our first attempt here set the knob to "" (blank) expecting that to
    disable autolabel -- it doesn't; blank means "defer to the global
    default", so it kept hitting the exact same crash. This version sets
    autolabel to a python expression that evaluates to a real string (the
    node's own `label` knob value), so Nuke always gets a valid non-None
    result straight from us and never falls through to the buggy global
    script. Set the `label` knob (via callers below) to whatever text you
    want shown, or "" for no text -- either way this expression returns a
    proper string, not None.
    """
    try:
        node["autolabel"].setValue("nuke.thisNode()['label'].value()")
    except Exception:
        pass


def _shuffle_layer(layer_name, node_name, input_node, out_layer="rgba", label=None):
    """Create a Shuffle2 pulling one multi-layer EXR layer into `out_layer`.

    `input_node` MUST be connected before `in1`/`out1` are set. Shuffle2's
    in1/out1 knobs are Channel_Knobs whose dropdown is populated from
    whatever's actually arriving on input 0 -- setting them on an
    unconnected node (as this used to do, connecting the Input afterward
    in a separate loop) let the string value take on an unconnected node,
    but once the real input arrived and Nuke rebuilt the channel menu, the
    knob silently fell back to its default instead of keeping our value.
    That's why manually repointing each Shuffle2's in1 dropdown by hand
    (done AFTER the graph -- and its connections -- already existed) always
    worked, while the identical value set by this script did not. Connect
    first, THEN set the channel knobs.

    Returns (node, ok) -- ok is False when the requested layer_name did not
    actually stick (either setValue raised, e.g. no such channel exists at
    all, or it silently accepted something other than what we asked for --
    Channel_Knob.setValue does not reliably raise just because a layer is
    absent, which is exactly how the 2026-09-13 LAYER_DEFAULTS mismatch
    went unnoticed the first time around). Callers should surface `ok`
    somewhere visible on the node graph, not just to the console -- a
    console warning alone is easy to miss.
    """
    sh = nuke.nodes.Shuffle2(name=node_name)
    sh.setInput(0, input_node)
    ok = True
    try:
        sh["in1"].setValue(layer_name)
        if sh["in1"].value() != layer_name:
            ok = False
    except Exception:
        ok = False
    if not ok:
        nuke.tprint(
            "CG_Element_Relight: layer '{}' not found on input - repoint "
            "{} manually from the Read node's channels.".format(layer_name, node_name)
        )
    # Explicitly pin the output layer rather than leaving it at whatever
    # default Shuffle2 picks. NOTE: Shuffle2's output-layer knob is named
    # "out1", NOT "output" -- an earlier pass here set "output" (silently
    # swallowed by a try/except, since that knob doesn't exist on
    # Shuffle2), which meant every one of these shuffles kept routing to
    # whatever it defaulted to instead of the intended output layer.
    try:
        sh["out1"].setValue(out_layer)
    except Exception:
        pass
    try:
        sh["label"].setValue(label if label is not None else layer_name)
    except Exception:
        pass
    # Must come after the label is set -- see _quiet_autolabel()'s docstring.
    _quiet_autolabel(sh)
    return sh, ok


def _depth_passthrough(base_node, source_node, depth_layer_name, node_name="Shuffle_DepthPassthrough"):
    """Single Shuffle2 that passes `base_node`'s rgba straight through
    (from input 0) and additionally injects `depth_layer_name` from
    `source_node` (input 1) as the standard "depth" layer -- everything
    else about `base_node` is untouched.

    2026-09-13: the first version of this feature used a second Shuffle2
    (just pulling the depth layer, feeding a Merge2(operation="copy")
    to merge it onto the recombined image) and Mark reported "the depth
    layer seemed to be overriding all the other channels." Root cause:
    Merge2 has no "channels" knob at all -- only Achannels/Bchannels/
    output -- so `depth_copy["channels"].setValue("depth")` silently did
    nothing (same class of bug as the earlier Shuffle2 "output" vs "out1"
    mistake), leaving Achannels/Bchannels/output at their "rgba" defaults.
    Per Nuke's own docs the "copy" operation's formula is literally "A"
    for every channel in "output" -- so with input 1 (the depth-only
    Shuffle2, whose own rgba had been zeroed out by remapping it to the
    "depth" layer) sitting in the "A" slot, the ENTIRE result collapsed to
    that near-black rgba: exactly "depth overriding everything."

    Rather than get Merge2's/the Copy node's channel-routing knobs exactly
    right (both have real but easy-to-misuse conventions here), this uses
    Shuffle2's own two-input design instead, which needs none of that:
    row 1 (in1/out1) is a plain identity passthrough of `base_node`'s rgba
    (input 0), row 2 (in2/out2) independently pulls `depth_layer_name`
    from `source_node` (input 1) into the "depth" layer. Disabling this
    node (the "Map Depth to depth Channel" checkbox drives its `disable`
    knob) falls back to Nuke's ordinary disabled-node behavior -- pass
    input 0 straight through -- so unchecking it is a clean no-op on
    `base_node`, nothing else can go wrong there.

    Both inputs MUST be connected before in1/out1/in2/out2 are touched --
    same Channel_Knob rule as _shuffle_layer().

    Returns (node, ok) -- ok False if the depth layer didn't stick, same
    meaning as _shuffle_layer()'s return.
    """
    sh = nuke.nodes.Shuffle2(name=node_name)
    sh.setInput(0, base_node)
    sh.setInput(1, source_node)
    ok = True
    try:
        sh["in1"].setValue("rgba")
        sh["out1"].setValue("rgba")
    except Exception:
        ok = False
    try:
        sh["in2"].setValue(depth_layer_name)
        sh["out2"].setValue("depth")
        if sh["in2"].value() != depth_layer_name:
            ok = False
    except Exception:
        ok = False
    if not ok:
        nuke.tprint(
            "CG_Element_Relight: depth layer '{}' not found on input - "
            "repoint {}'s \"in2\" manually from the Read node's "
            "channels.".format(depth_layer_name, node_name)
        )
    try:
        sh["label"].setValue("Depth Passthrough")
    except Exception:
        pass
    _quiet_autolabel(sh)
    return sh, ok


def _mult(src, knob_name, node_name):
    """Multiply node whose RGB channels are driven by a parent-group knob."""
    m = nuke.nodes.Multiply(name=node_name)
    m.setInput(0, src)
    for channel_index in range(3):  # R, G, B - leave alpha at its default
        m["value"].setExpression("parent.{}".format(knob_name), channel_index)
    return m


def build_cg_element_relight():
    group = nuke.nodes.Group(name="CG_Element_Relight")
    try:
        group["label"].setValue("")
    except Exception:
        pass
    # Same autolabel crash as the Shuffle2 nodes below, but on the Group
    # itself -- reported 2026-09-13 as the node showing up correctly named
    # but with "expecting a knob" as its on-DAG label, even after the first
    # (blank-knob) fix attempt. See _quiet_autolabel()'s docstring for why
    # that attempt didn't actually work and what this one does differently.
    _quiet_autolabel(group)

    _mismatched = []

    with group:
        inp = nuke.nodes.Input(name="input")

        # --- pull each pass out to its own RGB stream -----------------------
        # (each _shuffle_layer() call connects `inp` itself before touching
        # in1/out1 -- see that function's docstring for why the order matters)
        diff_dir, ok = _shuffle_layer(LAYER_DEFAULTS["diffuse_direct"], "Shuffle_DiffDir", inp)
        _mismatched.append("Diffuse Direct") if not ok else None
        diff_ind, ok = _shuffle_layer(LAYER_DEFAULTS["diffuse_indirect"], "Shuffle_DiffInd", inp)
        _mismatched.append("Diffuse Indirect") if not ok else None
        diff_col, ok = _shuffle_layer(LAYER_DEFAULTS["diffuse_color"], "Shuffle_DiffCol", inp)
        _mismatched.append("Diffuse Color") if not ok else None
        gloss_dir, ok = _shuffle_layer(LAYER_DEFAULTS["glossy_direct"], "Shuffle_GlossDir", inp)
        _mismatched.append("Glossy Direct") if not ok else None
        gloss_ind, ok = _shuffle_layer(LAYER_DEFAULTS["glossy_indirect"], "Shuffle_GlossInd", inp)
        _mismatched.append("Glossy Indirect") if not ok else None
        gloss_col, ok = _shuffle_layer(LAYER_DEFAULTS["glossy_color"], "Shuffle_GlossCol", inp)
        _mismatched.append("Glossy Color") if not ok else None
        emit, ok = _shuffle_layer(LAYER_DEFAULTS["emission"], "Shuffle_Emit", inp)
        _mismatched.append("Emission") if not ok else None

        # --- per-pass intensity multipliers, driven by the group's own
        #     user knobs (added to `group` below) -----------------------------
        diff_dir_m = _mult(diff_dir, "diffuse_direct", "Mult_DiffDir")
        diff_ind_m = _mult(diff_ind, "diffuse_indirect", "Mult_DiffInd")
        gloss_dir_m = _mult(gloss_dir, "glossy_direct", "Mult_GlossDir")
        gloss_ind_m = _mult(gloss_ind, "glossy_indirect", "Mult_GlossInd")
        emit_m = _mult(emit, "emission", "Mult_Emit")

        # --- diffuse_direct + diffuse_indirect, x diffuse_color -------------
        diff_sum = nuke.nodes.Merge2(name="Merge_DiffSum", operation="plus")
        diff_sum.setInput(0, diff_dir_m)
        diff_sum.setInput(1, diff_ind_m)

        diff_final = nuke.nodes.Merge2(name="Merge_DiffFinal", operation="multiply")
        diff_final.setInput(0, diff_sum)
        diff_final.setInput(1, diff_col)

        # --- glossy_direct + glossy_indirect, x glossy_color ----------------
        gloss_sum = nuke.nodes.Merge2(name="Merge_GlossSum", operation="plus")
        gloss_sum.setInput(0, gloss_dir_m)
        gloss_sum.setInput(1, gloss_ind_m)

        gloss_final = nuke.nodes.Merge2(name="Merge_GlossFinal", operation="multiply")
        gloss_final.setInput(0, gloss_sum)
        gloss_final.setInput(1, gloss_col)

        # --- diffuse + glossy + emission -------------------------------------
        dg_sum = nuke.nodes.Merge2(name="Merge_DiffGloss", operation="plus")
        dg_sum.setInput(0, diff_final)
        dg_sum.setInput(1, gloss_final)

        final_sum = nuke.nodes.Merge2(name="Merge_Final", operation="plus")
        final_sum.setInput(0, dg_sum)
        final_sum.setInput(1, emit_m)

        # --- optional depth passthrough --------------------------------------
        # One Shuffle2 (see _depth_passthrough()'s docstring for why this
        # replaced an earlier Shuffle2+Merge2(copy) combo that let depth
        # override the whole image) passes final_sum's rgba straight
        # through and independently injects the source Depth/Z layer as
        # Nuke's standard "depth" layer. The "Map Depth to depth Channel"
        # checkbox (added to the group below) drives this node's own
        # `disable` knob via expression, so it's a live, always-toggleable
        # option rather than a build-time choice: check it and depth.Z
        # rides along on the output; uncheck it and this node just passes
        # final_sum through unchanged (Nuke's ordinary disabled-node
        # behavior).
        depth_pass, ok = _depth_passthrough(final_sum, inp, LAYER_DEFAULTS["depth"])
        _mismatched.append("Depth") if not ok else None
        try:
            depth_pass["disable"].setExpression("!parent.map_depth")
        except Exception:
            pass

        out = nuke.nodes.Output(name="output")
        out.setInput(0, depth_pass)

    # --- user-facing sliders, added to the Group itself ---------------------
    group.addKnob(nuke.Tab_Knob("relight", "Relight"))

    for knob_name, label in (
        ("diffuse_direct", "Diffuse Direct"),
        ("diffuse_indirect", "Diffuse Indirect"),
        ("glossy_direct", "Glossy Direct"),
        ("glossy_indirect", "Glossy Indirect"),
        ("emission", "Emission"),
    ):
        k = nuke.Double_Knob(knob_name, label)
        k.setRange(0, 4)
        k.setValue(1.0)
        group.addKnob(k)

    group.addKnob(nuke.Text_Knob("divider1", ""))

    depth_knob = nuke.Boolean_Knob("map_depth", "Map Depth to depth Channel")
    depth_knob.setValue(True)
    depth_knob.setFlag(nuke.STARTLINE)
    group.addKnob(depth_knob)

    group.addKnob(nuke.Text_Knob("divider2", ""))
    group.addKnob(
        nuke.Text_Knob(
            "note",
            "",
            "Diffuse/Glossy Color passes multiply the direct+indirect sum\n"
            "but aren't separately adjustable here - only the light\n"
            "contributions are. Ask if you want albedo sliders added too.",
        )
    )

    # --- surface any layer-name mismatch ON THE NODE ITSELF, not just to the
    #     console -- a console-only nuke.tprint() warning is exactly how the
    #     2026-09-13 LAYER_DEFAULTS-vs-real-View-Layer-names mismatch went
    #     unnoticed. Any pass that didn't find its channel turns the node
    #     tile red/orange and adds a Text_Knob spelling out which passes need
    #     manual repointing (see the module docstring's CHANNEL NAMES section).
    if _mismatched:
        try:
            group["tile_color"].setValue(0xD04020FF)
        except Exception:
            pass
        try:
            group["label"].setValue(
                "⚠ layer mismatch: " + ", ".join(_mismatched)
            )
        except Exception:
            pass
        group.addKnob(nuke.Text_Knob("divider3", ""))
        group.addKnob(
            nuke.Text_Knob(
                "mismatch_warning",
                "",
                "⚠ These passes did not match any channel on the input "
                "and are black:\n" + ", ".join(_mismatched) + "\n"
                "Double-click in, select the matching Shuffle_* node, and "
                "repoint its \"in1\" dropdown from the Read node's real "
                "channel list.",
            )
        )
        nuke.tprint(
            "CG_Element_Relight: layer mismatch on {} - see the node's "
            "Relight tab.".format(", ".join(_mismatched))
        )

    return group


if __name__ == "__main__":
    build_cg_element_relight()
