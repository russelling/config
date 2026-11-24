# Copyright (c) Studio. All Rights Reserved.
"""
write_node_rebuild.py -- "Rebuild Write Node" (Flow Production Tracking >
Buffalo VFX).

STUDIO_REBUILD_WRITE_NODE_2026_09_29 (Mark's request 2026-09-29): a menu
item that throws away a misbehaving Write node and puts a fresh
tk-nuke-writenode WriteTank in its place, wired exactly where the old one
was.

WHAT IT REBUILDS, in order of preference:
  1. The WriteTank / Write nodes that are SELECTED.
  2. Otherwise every WriteTank in the script, plus any plain Write the new-
     script builder left behind as its fallback (label "EXR OUTPUT
     (FALLBACK)" -- scene_operation_tk-nuke.py falls back to a plain Write
     when tk-nuke-writenode is not loaded, which also loses the Send to
     Review button).
  3. If there is none at all, it creates one, fed by the selected node or
     the builder's "to Write" Dot.

For each node it keeps: name, position, input, every downstream
connection, the disable state, and -- for a WriteTank -- its profile
("Primary EXR (32-bit)" etc.) and output name. A plain Write, or a WriteTank
whose profile no longer exists in the config, gets the Step default
(temp -> 16-bit DWAA, everything else -> 32-bit), the same table the
new-script builder uses. The whole rebuild is one undo step.

Verified against tk-nuke-writenode v1.7.2 (app.py / handler.py):
create_new_write_node(profile) creates via nuke.createNode and returns
nothing, and refuses unless the script is saved as a valid work file;
get_setting("write_nodes") lists the profiles; the output knob is
"tank_channel" / "tk_use_name_as_channel"; reset_node_render_path() is the
public way to recompute the path after changing them.

Registered from core/hooks/engine_init.py and core/hooks/context_change.py
(_COMMAND_MODULES), like plate_reads.py -- a command registered in only one
of them vanishes on the first shot open.
"""

import os
import sys

import nuke

COMMAND_NAME = "Rebuild Write Node"

# Keep in step with WRITE_PRESET_BY_STEP / DEFAULT_WRITE_PRESET in
# hooks/scene_operation_tk-nuke.py (that file cannot be imported outside the
# hook loader, so the table is repeated rather than shared). Names must match
# a write_nodes `name:` in env/includes/settings/tk-nuke-episodic.yml -- this
# module checks that at run time and says so if one has drifted.
DEFAULT_WRITE_PRESET = "Primary EXR (32-bit)"
WRITE_PRESET_BY_STEP = {
    "temp": "Primary EXR (16-bit DWAA)",
    "comp": "Primary EXR (32-bit)",
}

# Same knob name and command scene_operation_tk-nuke.py adds to every
# WriteTank. Added here too so the button is there even if that module's
# onUserCreate callback was never registered in this session.
_REVIEW_KNOB_NAME = "send_to_review"
_SEND_TO_REVIEW_CMD = "__import__('render_complete_callback').send_to_review()"

_WRITETANK = "WriteTank"
_FALLBACK_LABEL_MARK = "EXR OUTPUT (FALLBACK)"
_TO_WRITE_DOT_LABEL = "to Write"
_LABEL = "EXR OUTPUT\n(ACEScg linear — no bake)\n[%s]"

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))


class _Abort(Exception):
    """A reason to stop that the artist should read, not a bug."""


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def _engine():
    import sgtk
    engine = sgtk.platform.current_engine()
    if engine is None:
        raise _Abort("Flow Production Tracking is not running in this Nuke.")
    return engine


def _step_code(context):
    try:
        step = context.step
        if step and step.get("name"):
            return str(step["name"]).strip().lower()
    except Exception:
        pass
    return None


def _profile_names(wn_app):
    names = []
    for entry in wn_app.get_setting("write_nodes") or []:
        name = entry.get("name")
        if name:
            names.append(name)
    return names


def _step_preset(context, profiles):
    preset = WRITE_PRESET_BY_STEP.get(_step_code(context), DEFAULT_WRITE_PRESET)
    if preset not in profiles:
        raise _Abort(
            "The Step default Write preset %r is not one of this config's "
            "tk-nuke-writenode presets:\n  %s\n\nWRITE_PRESET_BY_STEP in "
            "hooks/write_node_rebuild.py and scene_operation_tk-nuke.py has "
            "drifted from env/includes/settings/tk-nuke-episodic.yml."
            % (preset, "\n  ".join(profiles) or "(none)")
        )
    return preset


def _targets():
    """(nodes to rebuild, how they were chosen)."""
    selected = [n for n in nuke.selectedNodes() if n.Class() in (_WRITETANK, "Write")]
    if selected:
        return selected, "selected"
    found = list(nuke.allNodes(_WRITETANK))
    found += [
        n for n in nuke.allNodes("Write")
        if _FALLBACK_LABEL_MARK in (n["label"].value() or "")
    ]
    return found, "found in script"


def _feed_for_new_node():
    """What a brand-new Write should hang off when the script has none."""
    selected = [n for n in nuke.selectedNodes() if n.Class() not in (_WRITETANK, "Write")]
    if len(selected) == 1:
        return selected[0]
    dots = [
        n for n in nuke.allNodes("Dot")
        if (n["label"].value() or "").strip() == _TO_WRITE_DOT_LABEL
    ]
    return dots[0] if len(dots) == 1 else None


def _downstream(node):
    """[(dependent node, input index)] for every pipe leaving `node`."""
    links = []
    for dep in node.dependent(nuke.INPUTS | nuke.HIDDEN_INPUTS, False):
        for index in range(dep.inputs()):
            if dep.input(index) is node:
                links.append((dep, index))
    return links


def _knob_value(node, name, default=None):
    knob = node.knob(name)
    return knob.value() if knob is not None else default


def _add_review_button(node):
    if _HOOKS_DIR not in sys.path:
        sys.path.insert(0, _HOOKS_DIR)  # so the button's __import__ resolves
    knob = node.knob(_REVIEW_KNOB_NAME)
    if knob is not None:
        knob.setCommand(_SEND_TO_REVIEW_CMD)
        return
    button = nuke.PyScript_Knob(_REVIEW_KNOB_NAME, "Send to Review", _SEND_TO_REVIEW_CMD)
    button.setFlag(nuke.STARTLINE)
    node.addKnob(button)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _create_writetank(wn_app, profile):
    for node in nuke.selectedNodes():
        node.setSelected(False)  # nothing selected -> createNode connects nothing
    before = set(nuke.allNodes(_WRITETANK))
    wn_app.create_new_write_node(profile)
    new = [n for n in nuke.allNodes(_WRITETANK) if n not in before]
    if not new:
        # create_new_node() has already told the artist why (unsaved script
        # / not a work file) with its own nuke.message.
        raise _Abort("tk-nuke-writenode did not create a node (see its message).")
    return new[0]


def _finish(new, wn_app, name, xy, feed, downstream, profile,
            channel=None, use_name=None, disabled=False):
    new.setInput(0, feed)
    for dep, index in downstream:
        dep.setInput(index, new)
    if name:
        new.setName(name)
    new.setXYpos(int(xy[0]), int(xy[1]))
    if channel is not None and new.knob("tank_channel") is not None:
        new["tank_channel"].setValue(channel)
    if use_name is not None and new.knob("tk_use_name_as_channel") is not None:
        new["tk_use_name_as_channel"].setValue(use_name)
    wn_app.reset_node_render_path(new)
    new["disable"].setValue(bool(disabled))
    new["label"].setValue(_LABEL % profile)
    _add_review_button(new)
    try:
        return wn_app.get_node_render_path(new)
    except Exception as exc:
        return "(could not resolve render path: %s)" % exc


def _rebuild_one(old, wn_app, profiles, step_preset):
    is_tank = old.Class() == _WRITETANK
    profile = _knob_value(old, "profile_name") if is_tank else None
    kept_profile = profile in profiles
    if not kept_profile:
        profile = step_preset

    record = dict(
        name=old.name(),
        xy=(old.xpos(), old.ypos()),
        feed=old.input(0),
        downstream=_downstream(old),
        disabled=_knob_value(old, "disable", False),
        channel=_knob_value(old, "tank_channel") if is_tank else None,
        use_name=_knob_value(old, "tk_use_name_as_channel") if is_tank else None,
    )

    # Create first, delete second: if creation fails the old node is untouched.
    new = _create_writetank(wn_app, profile)
    nuke.delete(old)
    path = _finish(new, wn_app, profile=profile, **record)

    note = "kept its preset" if kept_profile else "Step default preset"
    if not is_tank:
        note = "was a plain Write -> WriteTank, Step default preset"
    return "%s  [%s, %s]\n    -> %s" % (record["name"], profile, note, path)


def rebuild_write_node():
    try:
        engine = _engine()
        context = engine.context
        if not (context.entity and context.entity.get("type") == "Shot" and context.step):
            raise _Abort(
                "Rebuild Write Node works in a shot script (Shot + Step). "
                "The current context is: %s" % context
            )
        wn_app = engine.apps.get("tk-nuke-writenode")
        if wn_app is None:
            raise _Abort(
                "tk-nuke-writenode is not loaded in this environment, so a "
                "Flow Production Tracking Write node cannot be made here."
            )
        if nuke.root().name() in ("", "Root"):
            raise _Abort("Save the script (File Save As via Flow) first.")

        profiles = _profile_names(wn_app)
        step_preset = _step_preset(context, profiles)
        targets, how = _targets()

        if targets:
            question = (
                "Rebuild %d Write node%s (%s)?\n\n  %s\n\nEach is replaced by "
                "a fresh Flow Production Tracking Write node in the same "
                "place, with the same connections, name and preset. "
                "Ctrl/Cmd+Z undoes it."
                % (len(targets), "" if len(targets) == 1 else "s", how,
                   "\n  ".join(n.name() for n in targets))
            )
        else:
            feed = _feed_for_new_node()
            question = (
                "No Write node found. Create a new Flow Production Tracking "
                "Write node (%s) fed by %s?"
                % (step_preset, feed.name() if feed else "nothing - connect it yourself")
            )
        if not nuke.ask(question):
            return

        undo = nuke.Undo()
        undo.begin(COMMAND_NAME)
        try:
            if targets:
                lines = [_rebuild_one(n, wn_app, profiles, step_preset) for n in targets]
            else:
                new = _create_writetank(wn_app, step_preset)
                xy = (feed.xpos(), feed.ypos() + 130) if feed else (new.xpos(), new.ypos())
                path = _finish(new, wn_app, name=None, xy=xy, feed=feed,
                               downstream=[], profile=step_preset)
                lines = ["%s  [%s, new]\n    -> %s" % (new.name(), step_preset, path)]
        finally:
            undo.end()

        nuke.message("Write node rebuilt:\n\n%s" % "\n\n".join(lines))
    except _Abort as reason:
        nuke.message("Rebuild Write Node: %s" % reason)
    except Exception as exc:
        import traceback
        trace = traceback.format_exc()
        nuke.tprint("[write_node_rebuild] failed:\n%s" % trace)
        nuke.message(
            "Rebuild Write Node failed: %s\n\nThe full traceback is in the "
            "Script Editor / terminal. Ctrl/Cmd+Z undoes any partial change."
            % exc
        )


def register(engine):
    """Register the menu command. No app -> buffalo_menu files it under
    Flow Production Tracking > Buffalo VFX."""
    engine.register_command(
        COMMAND_NAME,
        rebuild_write_node,
        {"short_name": "rebuild_write_node",
         "description": "Replace this script's Write node with a fresh Flow "
                        "Production Tracking Write node, keeping its "
                        "connections, name and preset."},
    )
