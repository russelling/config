# -*- coding: utf-8 -*-
"""
Render Review Read - after a local render, a Read of the new EXRs appears in
the script so they can be reviewed straight away. Mark's request 2026-10-28.

AUTOMATIC (register(), called from menu.py, so GUI sessions only):
  nuke.addAfterRender fires for the Write that just rendered. For a Flow
  Write node (WriteTank) that is the Write INSIDE the gizmo; the Read is
  placed next to the root-level node that contains it.

  * One review Read per Write node (keyed on its full name). Rendering again (e.g. after version-up)
    repoints the same Read at the new frames and reloads it, so the graph
    does not fill up with stale Reads. The Read carries a hidden-ish
    "Render Review" tab naming the Write it follows; delete the Read and the
    next render makes a fresh one.
  * Range = the frames actually on disk for that path, not the Root range,
    so a partial/cancelled render shows what exists.
  * Colour: copies the Write's raw / colorspace onto the Read, so the frames
    come back in exactly as they were written (ACEScg linear for our EXRs).
  * Nothing is connected and the Viewer is not touched.
  * EXR only - QuickReview's mov/jpeg renders and other formats are ignored.

MANUAL ("Read From Selected Write" in the Buffalo VFX menu):
  Same thing for the selected Write / Flow Write node(s). Use it after a
  FARM render, or a background (frame server) render - neither fires
  afterRender in the artist's session.

Never raises into Nuke's render: every failure is printed with
"[render-review-read]" and the render result is left alone.
"""

import os
import re

import nuke

MENU_LABEL = "Read From Selected Write"
TAG_KNOB = "buf_review_read_for"
LOG = "[render-review-read]"
TILE_COLOR = 0x4B8F5AFF   # muted green - easy to spot, not a plate colour

_PCT = re.compile(r"%0?(\d*)d")
_HASH = re.compile(r"#+")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _log(msg):
    nuke.tprint("%s %s" % (LOG, msg))


def _root_level_node(node):
    """The node at root level that contains node (itself if already there)."""
    top = node.fullName().split(".")[0]
    with nuke.root():
        return nuke.toNode(top) or node


def _inner_writes(node):
    """Write nodes to read back for a selected node: itself if it is a Write,
    else the Writes inside it (a WriteTank / group)."""
    if node.Class() == "Write":
        return [node]
    try:
        return nuke.allNodes("Write", group=node, recurseGroups=True)
    except Exception:
        return []


def _frames_on_disk(pattern):
    """(first, last, count) of frames on disk for a %04d / #### pattern,
    or None if the pattern has no frame token or nothing is there."""
    folder, base = os.path.split(pattern)
    m = _PCT.search(base) or _HASH.search(base)
    if not m or not os.path.isdir(folder):
        return None
    rx = re.compile(
        "^" + re.escape(base[:m.start()]) + r"(-?\d+)"
        + re.escape(base[m.end():]) + "$")
    frames = []
    for name in os.listdir(folder):
        hit = rx.match(name)
        if hit:
            frames.append(int(hit.group(1)))
    if not frames:
        return None
    return min(frames), max(frames), len(frames)


def _is_exr(write, path):
    try:
        if write["file_type"].value() == "exr":
            return True
    except Exception:
        pass
    return path.lower().endswith(".exr")


def _find_review_read(owner_name):
    with nuke.root():
        for read in nuke.allNodes("Read"):
            knob = read.knob(TAG_KNOB)
            if knob is not None and knob.value() == owner_name:
                return read
    return None


def _match_colour(write, read):
    try:
        if write["raw"].value():
            read["raw"].setValue(True)
            return "raw"
        read["raw"].setValue(False)
        cs = write["colorspace"].value()
        read["colorspace"].setValue(cs)
        return cs
    except Exception as exc:
        _log("could not copy colour settings from %s: %s"
             % (write.fullName(), exc))
        return "default"


# --------------------------------------------------------------------------
# core
# --------------------------------------------------------------------------

def make_review_read(write, quiet=True):
    """Create or update the review Read for this Write. Returns the Read or
    None. quiet=False pops a message when there is nothing to read."""
    def _skip(msg):
        _log(msg)
        if not quiet:
            nuke.message("Read From Write: %s" % msg)
        return None

    try:
        path = nuke.filename(write)
    except Exception as exc:
        return _skip("could not resolve the file path of %s: %s"
                     % (write.fullName(), exc))
    if not path:
        return _skip("%s has no file path." % write.fullName())
    path = path.replace("\\", "/")
    if not _is_exr(write, path):
        _log("%s is not an EXR write - skipped" % write.fullName())
        return None
    found = _frames_on_disk(path)
    if found is None:
        return _skip("no rendered frames on disk for %s\n%s"
                     % (write.fullName(), path))
    first, last, count = found

    owner = _root_level_node(write)
    with nuke.root():
        read = _find_review_read(write.fullName())
        made = read is None
        if made:
            read = nuke.nodes.Read()
            tab = nuke.Tab_Knob("buf_review_tab", "Render Review")
            tag = nuke.String_Knob(TAG_KNOB, "follows Write")
            read.addKnob(tab)
            read.addKnob(tag)
            tag.setValue(write.fullName())
            read.setXYpos(owner.xpos() + 150, owner.ypos())
            read["tile_color"].setValue(TILE_COLOR)
            read["label"].setValue(
                "RENDER REVIEW\n[value %s]\n[file tail [value file]]" % TAG_KNOB)
        read["file"].fromUserText("%s %d-%d" % (path, first, last))
        colour = _match_colour(write, read)
        try:
            read["reload"].execute()
        except Exception:
            pass

    gaps = (last - first + 1) - count
    _log("%s %s -> %s  %d-%d%s  colour=%s" % (
        "created" if made else "updated", read.name(), path, first, last,
        "  (%d frames MISSING in range)" % gaps if gaps else "", colour))
    return read


def _after_render():
    try:
        if not nuke.env.get("gui"):
            return
        make_review_read(nuke.thisNode(), quiet=True)
    except Exception as exc:
        _log("afterRender failed (render itself is unaffected): %s" % exc)


def read_from_selected():
    """Menu command: review Read(s) for the selected Write / Flow Write."""
    with nuke.root():
        selected = nuke.selectedNodes()
    writes = []
    for node in selected:
        writes.extend(_inner_writes(node))
    if not writes:
        nuke.message("Read From Write: select a Write or Flow Write node first.")
        return
    for write in writes:
        make_review_read(write, quiet=False)


_REGISTERED = []


def register():
    """Install the afterRender callback once per session."""
    if _REGISTERED:
        return
    nuke.addAfterRender(_after_render)
    _REGISTERED.append(True)
    _log("afterRender callback registered")
