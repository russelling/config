"""
farm_render.py -- let the artist choose where a Flow Write node renders.

Adds a **Render** tab to every Flow Production Tracking Write node (WriteTank):

    Render on:  [ Local | Farm (Deadline) ]
    [ Render ]
    [ Send to Review ]          <- the existing button, moved onto this tab

The stock Render button on the main tab is hidden, so there is one Render
button and it follows the dropdown.

    Local            exactly what the stock Render button did: the internal
                     Write's own Render button, which opens Nuke's usual
                     render dialog and runs Toolkit's before/after-render
                     callbacks.
    Farm (Deadline)  saves the script, writes a FARM COPY of it to a temp
                     folder, and submits that copy to Deadline 10 with
                     deadlinecommand (group "nuke").

WHY A FARM COPY
---------------
The farm's Linux workers run Nuke without Toolkit. The WriteTank gizmo is
written to survive that -- every callback checks for Toolkit, and its file
path falls back to the node's `cached_path` -- but the internal Write node's
FORMAT settings (bit depth, compression, DWA level...) are NOT saved in the
.nk. Toolkit keeps them in a hidden knob (`tk_write_node_settings`) and
re-applies them when the script opens. Without Toolkit a farm render would go
to the right path at Nuke's default EXR settings -- silently wrong. The
WriteTank.gizmo file also isn't on the workers' plugin path.

So the farm copy has every WriteTank replaced by a plain Write carrying the
live internal Write's non-default knobs (the same data Toolkit itself
stores), the cached render path, and the node's name/position/inputs. Every
WriteTank is converted, not just the one being rendered, so the copy loads on
a machine that has never heard of the gizmo; Deadline renders only the chosen
node (plugin info `WriteNode`).

THE ARTIST'S SCRIPT IS ONLY READ
--------------------------------
The copy is built from the saved .nk text plus values read off the live
nodes. Nothing in the open session is converted, deleted or recreated, so a
failure part-way through cannot leave the work file with plain Writes in it.
If the saved file and the open session disagree about which WriteTanks exist,
the submit refuses rather than guess.

Review stays manual: when the farm job finishes, the artist presses Send to
Review, exactly as after a local render.

Wired from scene_operation_tk-nuke.py (marker STUDIO_FARM_RENDER_2026_10_03),
next to the Send to Review button wiring. 2026-10-03.
v2 (same day): WriteTanks are looked up from the root -- see _all_writetanks().
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

import nuke

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

FARM_GROUP = "nuke"          # Spot Event Plugin fleets are keyed by group
DEFAULT_CHUNK = 10           # frames per Deadline task
DEFAULT_PRIORITY = 50

WRITETANK = "WriteTank"
INTERNAL_WRITE = "Write1"    # name of the Write inside WriteTank.gizmo

TAB_KNOB = "buf_render_tab"
TAB_LABEL = "Render"
LOCATION_KNOB = "buf_render_location"
LOCATIONS = ["Local", "Farm (Deadline)"]
RENDER_KNOB = "buf_render"
RENDER_CMD = "__import__('farm_render').render()"
REVIEW_KNOB = "send_to_review"
STOCK_RENDER_KNOB = "Render"

_LOG_PREFIX = "[farm_render]"

# Internal-Write knobs NOT copied to the farm Write: the path and callbacks
# are Toolkit's (replaced or meaningless on the farm), the rest are identity
# and layout, which come from the WriteTank itself. file_type is written
# first, separately, because the format knobs only exist once it is set.
_SKIP_INTERNAL = {
    "file", "proxy", "beforeRender", "afterRender", "name", "xpos", "ypos",
    "selected", "file_type", "help", "onCreate", "knobChanged",
}

# Top-level WriteTank knobs carried onto the farm Write, as written in the
# saved .nk. `inputs` matters: it is how .nk stack order says "no input".
_CARRY_TOP = {
    "inputs", "disable", "label", "note_font", "note_font_size",
    "note_font_color", "tile_color", "gl_color", "postage_stamp",
    "hide_input", "name", "xpos", "ypos",
}


class FarmError(RuntimeError):
    """A farm submission was refused; the message is shown to the artist."""


def _log(message):
    try:
        nuke.tprint("%s %s" % (_LOG_PREFIX, message))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Controls on the node
# ---------------------------------------------------------------------------

def add_controls(node):
    """Idempotently give a WriteTank its Render tab. Safe on every load.

    First time on a node: the existing Send to Review button is moved onto
    the new tab (removed and re-created with the same command), and the
    auto-made "User" tab it lived on is dropped if that leaves it empty.
    """
    if node is None or node.Class() != WRITETANK:
        return

    if node.knob(TAB_KNOB) is None:
        review = node.knob(REVIEW_KNOB)
        review_cmd = None
        if review is not None:
            review_cmd = review.value()
            node.removeKnob(review)
            _drop_trailing_empty_tab(node)

        node.addKnob(nuke.Tab_Knob(TAB_KNOB, TAB_LABEL))

        location = nuke.Enumeration_Knob(LOCATION_KNOB, "Render on", LOCATIONS)
        location.setTooltip(
            "Local renders on this machine, as before.\n"
            "Farm saves the script and submits it to Deadline "
            "(group '%s'). Press Send to Review when the farm job is done."
            % FARM_GROUP)
        node.addKnob(location)

        button = nuke.PyScript_Knob(RENDER_KNOB, "Render", RENDER_CMD)
        button.setFlag(nuke.STARTLINE)
        node.addKnob(button)

        if review_cmd is not None:
            moved = nuke.PyScript_Knob(REVIEW_KNOB, "Send to Review", review_cmd)
            moved.setFlag(nuke.STARTLINE)
            node.addKnob(moved)
    else:
        # Repair a partly-present set (hand edits, older versions).
        if node.knob(LOCATION_KNOB) is None:
            node.addKnob(nuke.Enumeration_Knob(LOCATION_KNOB, "Render on", LOCATIONS))
        button = node.knob(RENDER_KNOB)
        if button is None:
            button = nuke.PyScript_Knob(RENDER_KNOB, "Render", RENDER_CMD)
            button.setFlag(nuke.STARTLINE)
            node.addKnob(button)
        else:
            button.setCommand(RENDER_CMD)

    stock = node.knob(STOCK_RENDER_KNOB)
    if stock is not None:
        stock.setVisible(False)   # not saved in the .nk; redone on every load


def _drop_trailing_empty_tab(node):
    """Remove the last knob if it is a Tab with nothing after it."""
    knobs = node.allKnobs()
    if knobs and knobs[-1].Class() == "Tab_Knob":
        node.removeKnob(knobs[-1])


def _all_writetanks():
    """Every WriteTank in the script, looked up from the ROOT.

    nuke.allNodes() searches the *current group*, and a button on a gizmo runs
    with that gizmo as the current group -- so from the Render button a bare
    allNodes() looks inside the Flow Write node itself and finds nothing.
    (That was the bug in the first version: every farm submit was refused with
    "...is not in the open script".)
    """
    return nuke.allNodes(WRITETANK, group=nuke.root(), recurseGroups=True)


def _on_user_create():
    try:
        add_controls(nuke.thisNode())
    except Exception as exc:  # never break node creation
        _log("could not add render controls: %s" % exc)


def _on_script_load():
    for node in _all_writetanks():
        try:
            add_controls(node)
        except Exception as exc:
            _log("could not add render controls to %s: %s" % (node.fullName(), exc))


def install():
    """Register the callbacks once per Nuke session, and wire open nodes."""
    if getattr(nuke, "_buf_farm_render_installed", False):
        return
    nuke.addOnUserCreate(_on_user_create, nodeClass=WRITETANK)
    nuke.addOnScriptLoad(_on_script_load)
    nuke._buf_farm_render_installed = True
    _on_script_load()


# ---------------------------------------------------------------------------
# The Render button
# ---------------------------------------------------------------------------

def render():
    """Render button: follow the node's Render on setting."""
    node = nuke.thisNode()
    if node is None or node.Class() != WRITETANK:
        nuke.message("Render: run this from a Flow Write node.")
        return
    knob = node.knob(LOCATION_KNOB)
    where = knob.value() if knob is not None else LOCATIONS[0]
    if where == LOCATIONS[1]:
        try:
            with nuke.root():
                submit_to_farm(node)
        except FarmError as exc:
            nuke.message("Farm submit refused:\n\n%s" % exc)
        except Exception as exc:
            _log("farm submit failed: %r" % exc)
            nuke.message("Farm submit failed:\n\n%s" % exc)
    else:
        render_local(node)


def render_local(node):
    """Exactly what the stock (now hidden) Render button did."""
    internal = node.node(INTERNAL_WRITE)
    if internal is None:
        nuke.message("Render: %s has no internal Write node." % node.name())
        return
    stock = internal.knob(STOCK_RENDER_KNOB)
    if stock is not None and hasattr(stock, "execute"):
        stock.execute()
        return
    import nukescripts
    nukescripts.render_panel((internal,), False)


# ---------------------------------------------------------------------------
# Farm copy
# ---------------------------------------------------------------------------

_BLOCK_START = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*) \{(.*)$", re.S)
_KNOB_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)(?:\s|$)")

# Node classes whose block is followed by child nodes up to `end_group`.
_CONTAINERS = {"Group", "LiveGroup", "Gizmo"}


def _tcl(value):
    """Quote a string the way a .nk knob value needs it."""
    value = str(value)
    if value and not re.search(r'[\s"\\\[\]{}$;]', value):
        return value
    escaped = (value.replace("\\", "\\\\").replace('"', '\\"')
               .replace("[", "\\[").replace("]", "\\]").replace("$", "\\$"))
    return '"%s"' % escaped


def _unevaluated_path(node, knob_name):
    """The render path with its frame padding intact. STUDIO_FARM_RENDER_V3_2026_10_13

    NOT knob.evaluate(): on a file knob that substitutes the current frame,
    turning name.%04d.exr into name.1001.exr -- every farm frame then
    overwrites one file. Toolkit stores cached_path as a plain resolved path,
    so value() is the real thing.
    """
    knob = node.knob(knob_name)
    path = (knob.value() if knob is not None else "") or ""
    if not path:
        return ""
    if "[" in path:
        raise FarmError(
            "%s.%s is an expression (%s), not a plain path. Not guessing how "
            "it resolves on the farm." % (node.fullName(), knob_name, path))
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".mov", ".mp4", ".mxf") and not re.search(r"%0?\d*d|#", path):
        raise FarmError(
            "%s.%s has no frame number token (%%04d or ####): %s\n"
            "Every frame would overwrite one file." % (
                node.fullName(), knob_name, path))
    return path


def farm_write_lines(writetank):
    """Knob lines (no indent) for the plain Write that replaces `writetank`.

    Read off the live node: the internal Write's non-default knobs (the same
    serialisation Toolkit stores in tk_write_node_settings), its file type,
    and the cached render path.
    """
    internal = writetank.node(INTERNAL_WRITE)
    if internal is None:
        raise FarmError("%s has no internal Write node." % writetank.fullName())

    path = _unevaluated_path(writetank, "cached_path")
    if not path:
        raise FarmError(
            "%s has no render path yet. Open the node once so Flow can "
            "resolve it, then try again." % writetank.fullName())

    lines = ["file_type %s" % _tcl(internal["file_type"].value())]
    serialised = internal.writeKnobs(
        nuke.WRITE_NON_DEFAULT_ONLY | nuke.TO_SCRIPT | nuke.TO_VALUE)
    for statement in _statements(serialised):
        match = _KNOB_LINE.match(statement)
        if match and match.group(1) in _SKIP_INTERNAL:
            continue
        lines.append(statement.strip())

    lines.append("file %s" % _tcl(path))
    proxy_template = writetank.knob("proxy_render_template")
    if proxy_template is not None and proxy_template.value():
        proxy = _unevaluated_path(writetank, "tk_cached_proxy_path")
        if proxy:
            lines.append("proxy %s" % _tcl(proxy))
    lines.append("create_directories true")
    return lines


class _Scanner(object):
    """Brace/quote depth across .nk lines, the way Nuke's reader sees them.

    feed(line) returns the depth the line ENDS at. Backslash escapes the next
    character; braces inside a quoted string don't count; a quoted string may
    run across lines (Nuke writes literal newlines inside some values).
    """

    def __init__(self):
        self.depth = 0
        self.quoted = False

    def feed(self, line):
        i, n = 0, len(line)
        while i < n:
            c = line[i]
            if c == "\\":
                i += 2
                continue
            if self.quoted:
                if c == '"':
                    self.quoted = False
            elif c == '"':
                self.quoted = True
            elif c == "{":
                self.depth += 1
            elif c == "}":
                self.depth -= 1
            i += 1
        return self.depth


def _statements(text):
    """Split knob text (one knob per statement) honouring multi-line values."""
    scanner = _Scanner()
    out, current = [], []
    for line in text.splitlines(True):
        at_start = scanner.depth == 0 and not scanner.quoted
        if at_start and current:
            out.append("".join(current).rstrip("\r\n"))
            current = []
        if at_start and not line.strip():
            scanner.feed(line)
            continue
        current.append(line)
        scanner.feed(line)
    if current:
        out.append("".join(current).rstrip("\r\n"))
    return [s for s in out if s.strip()]


def _parse_blocks(lines):
    """Yield ('line', i) or ('block', start, end, cls, rest) over .nk lines.

    A block is any top-level statement that opens a brace -- `Blur {`,
    `clone $C8a306c00 {`, `define_window_layout_xml {<?xml...` -- and runs to
    the line where the brace depth returns to zero. `cls` is its first word;
    `rest` is whatever follows the opening brace on the first line. Comment
    lines (#...) at top level are not scanned: Nuke's #write_info header
    carries quotes and braces.
    """
    scanner = _Scanner()
    i, total = 0, len(lines)
    while i < total:
        content = lines[i].rstrip("\r\n")
        if content.lstrip().startswith("#"):
            yield ("line", i)
            i += 1
            continue
        scanner.feed(content + "\n")
        if scanner.depth == 0 and not scanner.quoted:
            yield ("line", i)
            i += 1
            continue
        if scanner.depth < 0:
            raise FarmError("The saved script could not be read (line %d)." % (i + 1))
        start, end = i, i
        while scanner.depth > 0 or scanner.quoted:
            end += 1
            if end >= total:
                raise FarmError("The saved script could not be read: a block "
                                "starting on line %d never closes." % (start + 1))
            scanner.feed(lines[end].rstrip("\r\n") + "\n")
        if scanner.depth < 0:
            raise FarmError("The saved script could not be read (line %d)." % (end + 1))
        match = _BLOCK_START.match(content)
        if match:
            cls, rest = match.group(2), match.group(3)
        else:
            words = content.split()
            cls, rest = (words[0] if words else ""), ""
        yield ("block", start, end, cls, rest)
        i = end + 1


def make_farm_script(text, replacements):
    """Return `text` with each WriteTank block replaced by a plain Write.

    `replacements` maps a WriteTank's full name ("Group1.WriteTank1" inside a
    group) to the knob lines from farm_write_lines(). Every WriteTank in the
    file must have an entry and every entry must be used, or FarmError --
    the saved file and the session disagreeing is not something to guess at.

    Everything outside the replaced blocks is copied byte for byte, line
    endings included.
    """
    lines = text.splitlines(True)
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    out, groups, used = [], [], set()

    for item in _parse_blocks(lines):
        if item[0] == "line":
            line = lines[item[1]]
            if line.strip() == "end_group" and groups:
                groups.pop()
            out.append(line)
            continue

        _, start, end, cls, rest = item
        block = lines[start:end + 1]
        first = rest
        inner = "".join(block[1:])
        # Drop the block's own closing brace: the last "}" in the text.
        cut = inner.rfind("}")
        knob_text = (first + "\n" if first.strip() else "") + inner[:cut]
        statements = _statements(knob_text)

        name = None
        for statement in statements:
            km = re.match(r"^\s*name\s+(\S+)\s*$", statement)
            if km:
                name = km.group(1).strip('"')
                break

        if cls == WRITETANK:
            if name is None:
                raise FarmError("A Flow Write node in the saved script has no name.")
            if lines[end].strip() != "}":
                raise FarmError("A Flow Write node (%s) ends in an unexpected way; "
                                "not submitting." % name)
            full = ".".join(groups + [name])
            if full not in replacements:
                raise FarmError(
                    "The saved script has a Flow Write node '%s' that is not in "
                    "the open script. Save again and retry." % full)
            used.add(full)
            indent = re.match(r"^(\s*)", block[0]).group(1).rstrip("\r\n")
            knob_indent = indent + " "
            carried = [s for s in statements
                       if (_KNOB_LINE.match(s) and _KNOB_LINE.match(s).group(1)
                           in _CARRY_TOP)]
            out.append(indent + "Write {" + newline)
            for statement in carried + list(replacements[full]):
                out.append(knob_indent + statement.strip() + newline)
            out.append(indent + "}" + newline)
        else:
            out.extend(block)
            if cls in _CONTAINERS:
                groups.append(name or cls)

    missing = sorted(set(replacements) - used)
    if missing:
        raise FarmError(
            "These Flow Write nodes are in the open script but not in the saved "
            "file: %s. Save the script and try again." % ", ".join(missing))

    result = "".join(out)
    if re.search(r"^\s*%s \{" % WRITETANK, result, re.M):
        raise FarmError("A Flow Write node survived the conversion; not submitting.")
    return result


# ---------------------------------------------------------------------------
# Deadline
# ---------------------------------------------------------------------------

def find_deadlinecommand():
    """deadlinecommand, the way Deadline's own submitters look for it."""
    exe = "deadlinecommand.exe" if sys.platform.startswith("win") else "deadlinecommand"
    candidates = []
    root = os.environ.get("DEADLINE_PATH")
    if root:
        candidates.append(os.path.join(root, exe))
    if sys.platform == "darwin":
        try:
            with open("/Users/Shared/Thinkbox/DEADLINE_PATH") as fh:
                candidates.append(os.path.join(fh.read().strip(), exe))
        except OSError:
            pass
        candidates.append("/Applications/Thinkbox/Deadline10/Resources/" + exe)
    elif sys.platform.startswith("win"):
        candidates.append(r"C:\Program Files\Thinkbox\Deadline10\bin\%s" % exe)
    else:
        candidates.append("/opt/Thinkbox/Deadline10/bin/" + exe)
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return shutil.which("deadlinecommand")


def _default_frames(node):
    internal = node.node(INTERNAL_WRITE)
    if internal is not None and internal["use_limit"].value():
        return "%d-%d" % (int(internal["first"].value()), int(internal["last"].value()))
    root = nuke.root()
    return "%d-%d" % (int(root["first_frame"].value()), int(root["last_frame"].value()))


_FRAMES_RE = re.compile(r"^\d+(-\d+)?(x\d+)?(,\d+(-\d+)?(x\d+)?)*$")


def _ask_options(node):
    panel = nuke.Panel("Render on Farm (Deadline) - %s" % node.name())
    panel.addSingleLineInput("Frames", _default_frames(node))
    panel.addSingleLineInput("Frames per task", str(DEFAULT_CHUNK))
    panel.addSingleLineInput("Priority (0-100)", str(DEFAULT_PRIORITY))
    if not panel.show():
        return None

    frames = (panel.value("Frames") or "").replace(" ", "")
    if not _FRAMES_RE.match(frames):
        raise FarmError("Frames must look like 1001-1100 (or 1001-1100x2, or "
                        "a comma list). Got: %r" % frames)
    try:
        chunk = int(panel.value("Frames per task"))
        priority = int(panel.value("Priority (0-100)"))
    except (TypeError, ValueError):
        raise FarmError("Frames per task and Priority must be whole numbers.")
    if chunk < 1:
        raise FarmError("Frames per task must be at least 1.")
    if not 0 <= priority <= 100:
        raise FarmError("Priority must be between 0 and 100.")
    return frames, chunk, priority


def _deadline_output_name(path):
    """%04d -> #### so Monitor's 'view output' finds the frames."""
    return re.sub(r"%0?(\d*)d", lambda m: "#" * int(m.group(1) or 1), path)


def _write_info(path, pairs):
    with open(path, "w", encoding="utf-8") as fh:
        for key, value in pairs:
            fh.write("%s=%s\n" % (key, value))


def _run_deadlinecommand(args):
    env = dict(os.environ)
    # Nuke's Python environment must not leak into deadlinecommand's own.
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(key, None)
    kwargs = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = 0x08000000   # CREATE_NO_WINDOW
    result = subprocess.run(args, capture_output=True, text=True,
                            timeout=180, env=env, **kwargs)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def submit_to_farm(node):
    script = nuke.root().name()
    if not script or script == "Root":
        raise FarmError("Save the script before rendering on the farm.")

    deadlinecommand = find_deadlinecommand()
    if not deadlinecommand:
        raise FarmError("The Deadline client (deadlinecommand) was not found on "
                        "this machine.")

    options = _ask_options(node)
    if options is None:
        return
    frames, chunk, priority = options

    # The farm copy is built from the file on disk, so it must match the
    # session. Saving here is what Deadline's own submitter does too.
    if nuke.root().modified():
        nuke.scriptSave()

    replacements = {}
    for writetank in _all_writetanks():
        replacements[writetank.fullName()] = farm_write_lines(writetank)

    with open(script, "r", encoding="utf-8", errors="surrogateescape") as fh:
        text = fh.read()
    farm_text = make_farm_script(text, replacements)

    base = os.path.splitext(os.path.basename(script))[0]
    folder = tempfile.mkdtemp(prefix="buf_farm_")
    farm_script = os.path.join(folder, base + "_farm.nk")
    with open(farm_script, "w", encoding="utf-8", errors="surrogateescape",
              newline="") as fh:
        fh.write(farm_text)

    output = _unevaluated_path(node, "cached_path")
    write_node = node.fullName()
    version = "%d.%d" % (nuke.NUKE_VERSION_MAJOR, nuke.NUKE_VERSION_MINOR)

    job_info = os.path.join(folder, "nuke_job_info.job")
    plugin_info = os.path.join(folder, "nuke_plugin_info.job")
    _write_info(job_info, [
        ("Plugin", "Nuke"),
        ("Name", "%s | %s" % (base, node.name())),
        ("BatchName", base),
        ("Comment", "Flow Write %s, from %s" % (node.name(), os.path.basename(script))),
        ("Frames", frames),
        ("ChunkSize", chunk),
        ("Priority", priority),
        ("Group", FARM_GROUP),
        ("MachineLimit", 0),
        ("OutputDirectory0", os.path.dirname(output)),
        ("OutputFilename0", _deadline_output_name(os.path.basename(output))),
        ("ExtraInfoKeyValue0", "BufSourceScript=%s" % script),
        ("ExtraInfoKeyValue1", "BufWriteNode=%s" % write_node),
        ("ExtraInfoKeyValue2", "BufOutput=%s" % output),
    ])
    # No SceneFile: the farm copy goes as an auxiliary file, so it is stored
    # with the job in the repository and path-mapped there.
    _write_info(plugin_info, [
        ("Version", version),
        ("WriteNode", write_node),
        ("BatchMode", "True"),
        ("BatchModeIsMovie", "False"),
        ("NukeX", "False"),
        ("Threads", 0),
        ("RamUse", 0),
        ("RenderMode", "Use Scene Settings"),
        ("EnablePathMapping", "True"),
        ("ContinueOnError", "False"),
        ("UseGpu", "False"),
        ("EnforceRenderOrder", "False"),
        ("StackSize", 0),
        ("PerformanceProfiler", "False"),
        ("Views", ""),
    ])

    _log("submitting %s (%s) frames %s via %s" % (write_node, farm_script,
                                                  frames, deadlinecommand))
    code, out = _run_deadlinecommand([deadlinecommand, job_info, plugin_info,
                                      farm_script])
    job = re.search(r"JobID=(\S+)", out)
    if code != 0 or not job:
        _log("deadlinecommand output:\n%s" % out)
        raise FarmError("Deadline did not accept the job.\n\n%s"
                        % out.strip()[-1500:])

    _log("submitted job %s" % job.group(1))
    nuke.message(
        "Submitted to the farm.\n\n"
        "Job ID: %s\nFrames: %s  (%d per task, priority %d, group %s)\n"
        "Output: %s\n\n"
        "When the job finishes in Deadline Monitor, press Send to Review on "
        "this node as usual.\n\nFarm copy: %s"
        % (job.group(1), frames, chunk, priority, FARM_GROUP, output, farm_script))
