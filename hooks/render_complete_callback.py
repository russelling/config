"""
render_complete_callback.py

Implements the "Send to Review" submission step for the QT review pipeline.

A "Send to Review" button knob is added to each WriteTank node (wired in
scene_operation_tk-nuke.py). The artist renders EXRs, reviews them, and
clicks the button on the node that produced them, which:

  1. Prompts the artist with a small dialog:
       - "Submitted For" dropdown (populated from the SG Version
         .sg_submitted_for list field's valid values)
       - "Description" text box (free text)

  2. Gathers metadata, resolving everything from the node's ACTUAL render
     output rather than guessing:
       - fields parsed from the resolved render path (so the output token,
         version, etc. exactly match what was rendered)
       - frame range derived from the rendered EXR files on disk (NOT the
         project's global Root range, which reflects the edit conform)
       - embedded source timecode read from the first EXR's header (the
         render frame numbers are offset, but the burn-in TC must reflect
         the source TC); may be None if the render carries no embedded TC

  3. Writes a JSON "render complete" flag file next to the EXR output, using
     the `ep_nuke_shot_render_flag` template. The EXR path pattern inside
     the flag is resolved for macOS ('darwin'), since the watcher runs on
     the Mac Studio regardless of which OS submitted.

  4. The Mac Studio watcher (BUF_Mac_watcher) polls for these flag files and
     does the actual bake/slate/burn-in/upload to ShotGrid.

HISTORY: An earlier version tried to fire automatically via Nuke's
afterRender / the tk-nuke-writenode tk_after_render knob; that proved
unreliable and was replaced by the explicit button (see PIPELINE_REFERENCE.md
/ 2026-06-17 session notes).

SG FIELD REQUIREMENT:
  Version entity needs a list field `sg_submitted_for` with valid values
  configured by the admin (e.g. "Supervisor", "Client", "Editorial",
  "Internal Review"). This script reads those valid values to populate
  the dropdown - if the field doesn't exist yet, a fallback static list
  is used instead.
"""

import os
import json
import getpass
import datetime

import nuke
import sgtk

# Use the engine's Qt binding rather than importing PySide* directly. The
# QtImporter returns whatever binding the running engine loaded (PySide6 on
# Nuke 16+, PySide2 on Nuke 11-15) and folds all QtWidgets classes into the
# QtGui shim, so QtGui.QDialog etc. resolve on every supported Nuke version.
from sgtk.platform.qt import QtGui


FALLBACK_SUBMITTED_FOR = ["Internal Review", "Supervisor", "Editorial", "Client"]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class RenderCompleteDialog(QtGui.QDialog):
    def __init__(self, submitted_for_options, parent=None):
        super(RenderCompleteDialog, self).__init__(parent)
        self.setWindowTitle("Render Complete")

        layout = QtGui.QVBoxLayout(self)

        layout.addWidget(QtGui.QLabel("Submitted For:"))
        self.submitted_for = QtGui.QComboBox()
        self.submitted_for.addItems(submitted_for_options)
        layout.addWidget(self.submitted_for)

        layout.addWidget(QtGui.QLabel("Description:"))
        self.description = QtGui.QTextEdit()
        self.description.setFixedHeight(80)
        layout.addWidget(self.description)

        btn_row = QtGui.QHBoxLayout()
        ok_btn = QtGui.QPushButton("Submit")
        cancel_btn = QtGui.QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def values(self):
        return {
            "submitted_for": self.submitted_for.currentText(),
            "description": self.description.toPlainText().strip(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_submitted_for_options(sg):
    """Read valid values for Version.sg_submitted_for from SG schema."""
    try:
        schema = sg.schema_field_read("Version", "sg_submitted_for")
        props = schema.get("sg_submitted_for", {}).get("properties", {})
        valid_values = props.get("valid_values", {}).get("value", [])
        if valid_values:
            return valid_values
    except Exception as exc:
        nuke.warning("[render_complete] Could not read sg_submitted_for valid values: %s" % exc)
    return FALLBACK_SUBMITTED_FOR


def _frame_range_from_files(render_files, render_template):
    """
    Derive the actual rendered frame range from the list of EXR files on
    disk, using the render template's SEQ key to extract each frame number.

    This is the range of frames that were ACTUALLY rendered - not the
    project's global range on the Root node (nuke.root()['first_frame'] /
    ['last_frame']), which reflects the timeline/edit conform and can be
    wildly different from what a given Write node produced.

    Returns (first, last) ints, or (None, None) if it can't be determined.
    """
    if not render_files or render_template is None:
        return (None, None)
    frames = []
    for f in render_files:
        try:
            flds = render_template.get_fields(f)
            seq = flds.get("SEQ")
            if seq is not None:
                frames.append(int(seq))
        except Exception:
            # Skip any file that doesn't parse cleanly rather than failing
            # the whole submission.
            continue
    if not frames:
        return (None, None)
    return (min(frames), max(frames))


def _frame_range_root():
    """
    Fallback: the project's global render range on the Root node. Only used
    when the rendered frames can't be enumerated. Note this is the
    timeline/handle range, which may not match a specific Write node's
    actual output.
    """
    try:
        root = nuke.root()
        return int(root["first_frame"].value()), int(root["last_frame"].value())
    except Exception:
        return (None, None)


def _read_embedded_start_tc(exr_files, first_frame, render_template):
    """
    Read the embedded source timecode from the FIRST rendered EXR's header
    and return it as a string (HH:MM:SS:FF), or None if no timecode is
    embedded (some renders legitimately lack it).

    The artist's render numbers are offset (e.g. 1001+), but the EXRs carry
    the original source timecode in their headers. The burn-in TC must
    reflect that embedded source TC, not the offset frame numbers - so we
    capture the start TC here, at submission, and store it in the flag. The
    Mac Studio watcher then increments from this start TC per frame.

    Reads via a temporary, throwaway Read node so the artist's open script
    is never modified. EXR timecode surfaces under different metadata keys
    depending on the writer, so we try the common ones in order.
    """
    if not exr_files or first_frame is None:
        return None

    # Find the file matching first_frame (don't assume list order).
    target = None
    for f in exr_files:
        try:
            seq = render_template.get_fields(f).get("SEQ")
            if seq is not None and int(seq) == int(first_frame):
                target = f
                break
        except Exception:
            continue
    if target is None:
        # Fall back to the first file we can parse.
        target = exr_files[0]

    read = None
    try:
        # forward slashes for Nuke regardless of OS
        read = nuke.createNode("Read", inpanel=False)
        read["file"].fromUserText(target.replace("\\", "/"))
        # Metadata is frame-dependent; read at the first rendered frame.
        meta_keys = ("input/timecode", "exr/timeCode", "exr/timecode")
        tc = None
        for key in meta_keys:
            try:
                val = read.metadata(key, int(first_frame))
            except Exception:
                val = None
            if val:
                tc = str(val).strip()
                break
        return tc or None
    except Exception as exc:
        nuke.warning("[render_complete] Could not read embedded timecode: %s" % exc)
        return None
    finally:
        if read is not None:
            try:
                nuke.delete(read)
            except Exception:
                pass


def _cut_range(sg, shot_entity_id):
    """Cut in/out (edit range) from the Shot entity in SG."""
    try:
        shot = sg.find_one(
            "Shot",
            [["id", "is", shot_entity_id]],
            ["sg_cut_in", "sg_cut_out", "cut_in", "cut_out"],
        )
        cut_in = shot.get("sg_cut_in") or shot.get("cut_in")
        cut_out = shot.get("sg_cut_out") or shot.get("cut_out")
        return cut_in, cut_out
    except Exception as exc:
        nuke.warning("[render_complete] Could not read cut in/out: %s" % exc)
        return None, None


# ---------------------------------------------------------------------------
# Main callback
# ---------------------------------------------------------------------------

def run_render_complete(write_node):
    engine = sgtk.platform.current_engine()
    if engine is None:
        nuke.warning("[render_complete] No SGTK engine running, skipping flag write.")
        return

    ctx = engine.context
    tk = engine.sgtk
    sg = engine.shotgun

    # --- Resolve the render output path ---
    # Prefer the supported tk-nuke-writenode handler API, which computes the
    # path correctly for the WriteTank gizmo. Only fall back to the raw
    # `file` knob if the handler is unavailable, since on the WriteTank
    # group node that knob is unreliable.
    out_path = None
    wn_app = _get_writenode_app(engine)
    if wn_app is not None:
        try:
            out_path = wn_app.get_node_render_path(write_node)
        except Exception as exc:
            nuke.warning(
                "[render_complete] get_node_render_path failed, falling back "
                "to file knob: %s" % exc
            )
    if not out_path:
        try:
            out_path = write_node["file"].value()
        except Exception:
            out_path = None
    if not out_path:
        _debug_log("run_render_complete: EXIT - no render path")
        nuke.warning("[render_complete] Could not resolve render path; aborting.")
        nuke.message("Send to Review: could not resolve the render path for this node.")
        return

    flag_template = tk.templates["ep_nuke_shot_render_flag"]
    render_work_template = tk.templates["ep_nuke_shot_render_work"]

    # Derive the template fields from the ACTUAL resolved render path the
    # node renders to, rather than rebuilding them from context and guessing
    # the output token. out_path came from the writenode app's
    # get_node_render_path(), so it already carries the correct values
    # (including whatever the 'output' token resolves to - or none, when the
    # optional [_{nuke.output}] segment is empty). Skip SEQ so a concrete
    # frame number in the path doesn't prevent the parse.
    try:
        fields = render_work_template.get_fields(out_path, skip_keys=["SEQ"])
    except Exception as exc:
        _debug_log("run_render_complete: EXIT - get_fields raised: %r" % exc)
        nuke.warning("[render_complete] Could not parse render path fields: %s" % exc)
        nuke.message(
            "Send to Review: could not parse fields from the render path:\n%s" % exc
        )
        return

    # The output token (if any) is whatever the path resolved to - we do NOT
    # override it with the node name. It may legitimately be absent when the
    # optional {nuke.output} template segment is empty.
    output_name = fields.get("output")

    # Build the EXR pattern from the template/fields rather than trusting the
    # raw Write node file knob, which can be stale or hand-edited and drift
    # from the actual version/output values used elsewhere in this flag.
    render_work_fields = dict(fields)
    render_work_fields["SEQ"] = "%04d"
    # The watcher daemon that consumes this flag always runs on the Mac
    # Studio, regardless of which OS this hook fires from. Always resolve
    # the Mac-style path here so the flag is portable across platforms.
    # NOTE: Toolkit uses Python sys.platform names for the platform arg -
    # 'darwin' for macOS (NOT 'mac'), 'win32' for Windows, 'linux' for Linux.
    try:
        resolved_exr_pattern = render_work_template.apply_fields(
            render_work_fields, platform="darwin"
        )
    except Exception as exc:
        _debug_log("run_render_complete: EXIT - render_work apply_fields raised: %r" % exc)
        nuke.warning("[render_complete] EXR pattern resolution failed: %s" % exc)
        nuke.message("Send to Review: could not resolve render path template:\n%s" % exc)
        return

    # --- Prompt artist ---
    submitted_for_options = _get_submitted_for_options(sg)
    dialog = RenderCompleteDialog(submitted_for_options)
    if not dialog.exec_():
        # Artist cancelled - do not write flag
        _debug_log("run_render_complete: EXIT - dialog cancelled/closed")
        return
    values = dialog.values()

    # --- Gather metadata ---
    # Prefer the ACTUAL rendered frame range (parsed from the EXR files on
    # disk) over the project's global Root range, which reflects the
    # timeline/edit conform and can differ wildly from a given Write node's
    # real output.
    render_files = []
    if wn_app is not None:
        try:
            render_files = wn_app.get_node_render_files(write_node) or []
        except Exception as exc:
            nuke.warning("[render_complete] get_node_render_files failed: %s" % exc)
            render_files = []
    first_frame, last_frame = _frame_range_from_files(render_files, render_work_template)
    if first_frame is None:
        # Fall back to the Root range only if we couldn't enumerate frames.
        first_frame, last_frame = _frame_range_root()
    cut_in, cut_out = _cut_range(sg, ctx.entity["id"]) if ctx.entity else (None, None)

    # Embedded source timecode at the first rendered frame. The render frame
    # numbers are offset (clean 1001+), but the QT burn-in TC must reflect
    # the EXR's embedded source TC. Captured here at submission; the watcher
    # increments from this start TC. May be None if the render carries no
    # embedded timecode (handled gracefully downstream).
    start_tc = _read_embedded_start_tc(render_files, first_frame, render_work_template)

    flag_data = {
        "project_id": ctx.project["id"] if ctx.project else None,
        "project_name": ctx.project["name"] if ctx.project else None,
        "entity_type": ctx.entity["type"] if ctx.entity else None,
        "entity_id": ctx.entity["id"] if ctx.entity else None,
        "shot_code": fields.get("Shot"),
        "episode": fields.get("Episode"),
        "sequence": fields.get("Sequence"),
        "step": fields.get("Step"),
        "version": fields.get("version"),
        "output": output_name,
        "artist": getpass.getuser(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "frame_first": first_frame,
        "frame_last": last_frame,
        "start_timecode": start_tc,
        "cut_in": cut_in,
        "cut_out": cut_out,
        "exr_path_pattern": resolved_exr_pattern,
        "submitted_for": values["submitted_for"],
        "description": values["description"],
        "task_id": ctx.task["id"] if ctx.task else None,
    }

    # --- Write flag file ---
    try:
        flag_fields = dict(fields)
        flag_path = flag_template.apply_fields(flag_fields)
    except Exception as exc:
        _debug_log("run_render_complete: EXIT - flag_template apply_fields raised: %r" % exc)
        nuke.warning("[render_complete] Could not resolve flag path: %s" % exc)
        nuke.message("Send to Review: could not resolve flag path:\n%s" % exc)
        return

    flag_dir = os.path.dirname(flag_path)
    if not os.path.exists(flag_dir):
        os.makedirs(flag_dir)

    with open(flag_path, "w") as f:
        json.dump(flag_data, f, indent=2)

    nuke.tprint("[render_complete] Flag written: %s" % flag_path)
    nuke.message("Sent to Review.\n\nFlag written:\n%s" % flag_path)


# ---------------------------------------------------------------------------
# Manual "Send to Review" button entry point
#
# This is the PRIMARY trigger (the afterRender knob approach proved
# unreliable - see PIPELINE_REFERENCE.md / 2026-06-17 session notes). A
# PyScript button knob is added to each WriteTank node by the wiring in
# scene_operation_tk-nuke.py; the button calls:
#
#   __import__('render_complete_callback').send_to_review()
#
# The artist renders EXRs, reviews them, then clicks the button ON the
# WriteTank node that produced them. Because the button lives on that node,
# nuke.thisNode() resolves to it directly - no scanning or guessing which
# render to flag.
#
# Unlike the afterRender path, this resolves the render path via the
# supported tk-nuke-writenode handler API (compute_render_path /
# get_files_on_disk) rather than reading the WriteTank group's `file`
# knob (which is unreliable on the gizmo wrapper), and it verifies frames
# actually exist on disk before writing a flag.
# ---------------------------------------------------------------------------

def _get_writenode_app(engine):
    """Return the tk-nuke-writenode app instance, or None."""
    try:
        return engine.apps.get("tk-nuke-writenode")
    except Exception as exc:
        nuke.warning("[render_complete] Could not get writenode app: %s" % exc)
        return None


def _debug_log(message):
    """
    Append a line to a plain text log on disk, bypassing Nuke's logging
    APIs entirely. For diagnosing button-click behavior that doesn't surface
    in the Script Editor. Safe to leave in place - cheap and self-contained.
    """
    try:
        log_dir = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config/logs"
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        with open(os.path.join(log_dir, "send_to_review_debug.log"), "a") as f:
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write("[%s] %s\n" % (stamp, message))
    except Exception:
        pass


def send_to_review():
    """
    Invoked from the 'Send to Review' button on a WriteTank node.
    Validates the node's frames exist, prompts the artist, and writes the
    review flag JSON for the Mac Studio watcher to pick up.
    """
    try:
        node = nuke.thisNode()
        if node is None:
            nuke.message("Send to Review: could not determine the node.")
            return

        engine = sgtk.platform.current_engine()
        if engine is None:
            nuke.message("Send to Review: no Flow (SGTK) engine running.")
            return

        wn_app = _get_writenode_app(engine)

        # Verify frames actually exist on disk before flagging. If we can't
        # get the app for some reason, fall back to a soft check so the
        # artist isn't hard-blocked, but warn.
        files_on_disk = []
        if wn_app is not None:
            try:
                files_on_disk = wn_app.get_node_render_files(node) or []
            except Exception as exc:
                nuke.warning(
                    "[render_complete] get_node_render_files failed, proceeding "
                    "without disk check: %s" % exc
                )
                files_on_disk = None  # unknown, not "empty"
        else:
            files_on_disk = None  # unknown

        if files_on_disk == []:
            _debug_log("send_to_review: EXIT - no frames on disk")
            nuke.message(
                "Send to Review: no rendered frames found for this Write "
                "node yet.\n\nRender the node first, then click Send to "
                "Review."
            )
            return

        # Hand off to the shared flag-writing routine (prompts the artist
        # and writes the JSON). run_render_complete reads the node's render
        # path; pass the WriteTank node directly.
        run_render_complete(node)

    except Exception as exc:
        _debug_log("send_to_review: EXCEPTION %r" % exc)
        nuke.warning("[render_complete] send_to_review failed: %s" % exc)
        try:
            nuke.message("Send to Review failed: %s" % exc)
        except Exception:
            pass

