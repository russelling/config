"""
render_complete_callback.py

Registers an `afterRender` callback on Write nodes. When a render completes:

  1. Prompts the artist with a small dialog:
       - "Submitted For" dropdown (populated from the SG Version.sg_submitted_for
         list field's valid values)
       - "Description" text box (free text)

  2. Gathers context (shot/episode/scene/step/version/artist/date/frame range)

  3. Writes a JSON "render complete" flag file next to the EXR output, using
     the `ep_nuke_shot_render_flag` template.

  4. The Mac Studio watcher (qt_watcher.py) polls for these flag files and
     does the actual bake/slate/burn-in/upload.

NOTE: Currently fires automatically on every render. To switch to a manual
"push button" trigger later, remove the nuke.addAfterRender() registration
in `register()` below and instead call `run_render_complete(write_node)`
from a custom menu command.

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

try:
    from PySide2 import QtWidgets
except ImportError:
    from PySide import QtGui as QtWidgets  # Nuke 10 fallback

import sgtk


FALLBACK_SUBMITTED_FOR = ["Internal Review", "Supervisor", "Editorial", "Client"]


# ---------------------------------------------------------------------------
# Dialog
# ---------------------------------------------------------------------------

class RenderCompleteDialog(QtWidgets.QDialog):
    def __init__(self, submitted_for_options, parent=None):
        super(RenderCompleteDialog, self).__init__(parent)
        self.setWindowTitle("Render Complete")

        layout = QtWidgets.QVBoxLayout(self)

        layout.addWidget(QtWidgets.QLabel("Submitted For:"))
        self.submitted_for = QtWidgets.QComboBox()
        self.submitted_for.addItems(submitted_for_options)
        layout.addWidget(self.submitted_for)

        layout.addWidget(QtWidgets.QLabel("Description:"))
        self.description = QtWidgets.QTextEdit()
        self.description.setFixedHeight(80)
        layout.addWidget(self.description)

        btn_row = QtWidgets.QHBoxLayout()
        ok_btn = QtWidgets.QPushButton("Submit")
        cancel_btn = QtWidgets.QPushButton("Cancel")
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


def _frame_range():
    """Full render range including handles, as set on the Root node."""
    root = nuke.root()
    return int(root["first_frame"].value()), int(root["last_frame"].value())


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

    # --- Resolve template fields from the Write node's output path ---
    out_path = write_node["file"].value()
    if not out_path:
        return

    work_template = tk.templates["ep_nuke_shot_work"]
    flag_template = tk.templates["ep_nuke_shot_render_flag"]
    render_work_template = tk.templates["ep_nuke_shot_render_work"]

    fields = ctx.as_template_fields(work_template)

    # output - use the write node's name, lowercased, as the output identifier
    output_name = write_node.name().lower()
    fields["output"] = output_name

    # version - pull from current script's version via context fields if present,
    # otherwise try to parse from the write path
    fields.setdefault("version", fields.get("version", 1))

    # Build the EXR pattern from the template/fields rather than trusting the
    # raw Write node file knob, which can be stale or hand-edited and drift
    # from the actual version/output values used elsewhere in this flag.
    render_work_fields = dict(fields)
    render_work_fields["SEQ"] = "%04d"
    # The watcher daemon that consumes this flag always runs on the Mac
    # Studio, regardless of which OS this hook fires from. Always resolve
    # the Mac-style path here so the flag is portable across platforms.
    resolved_exr_pattern = render_work_template.apply_fields(
        render_work_fields, platform="mac"
    )

    # --- Prompt artist ---
    submitted_for_options = _get_submitted_for_options(sg)
    dialog = RenderCompleteDialog(submitted_for_options)
    if not dialog.exec_():
        # Artist cancelled - do not write flag
        return
    values = dialog.values()

    # --- Gather metadata ---
    first_frame, last_frame = _frame_range()
    cut_in, cut_out = _cut_range(sg, ctx.entity["id"]) if ctx.entity else (None, None)

    flag_data = {
        "project_id": ctx.project["id"] if ctx.project else None,
        "project_name": ctx.project["name"] if ctx.project else None,
        "entity_type": ctx.entity["type"] if ctx.entity else None,
        "entity_id": ctx.entity["id"] if ctx.entity else None,
        "shot_code": fields.get("Shot"),
        "episode": fields.get("Episode"),
        "scene": fields.get("Scene"),
        "step": fields.get("Step"),
        "version": fields.get("version"),
        "output": output_name,
        "artist": getpass.getuser(),
        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
        "frame_first": first_frame,
        "frame_last": last_frame,
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
        nuke.warning("[render_complete] Could not resolve flag path: %s" % exc)
        return

    flag_dir = os.path.dirname(flag_path)
    if not os.path.exists(flag_dir):
        os.makedirs(flag_dir)

    with open(flag_path, "w") as f:
        json.dump(flag_data, f, indent=2)

    nuke.tprint("[render_complete] Flag written: %s" % flag_path)


def _after_render():
    write_node = nuke.thisNode()
    try:
        run_render_complete(write_node)
    except Exception as exc:
        nuke.warning("[render_complete] Error in afterRender callback: %s" % exc)


# ---------------------------------------------------------------------------
# Registration - call this from init.py / menu.py
# ---------------------------------------------------------------------------

def register():
    nuke.addAfterRender(_after_render)


