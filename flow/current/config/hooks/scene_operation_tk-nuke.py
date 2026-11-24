# Copyright (c) Studio. All Rights Reserved.
"""
scene_operation hook for tk-multi-workfiles2 / tk-nuke (episodic context).

Auto-versioning: on 'new' and 'version_up', scans the work area for existing
v### files and saves to the next available version.
File naming: 301_001_010_temp_v001.nk

Color pipeline (first open only):
  Builds a working node graph with:
    - One raw Read node per EXR sequence found on disk in the shot's
      plates/ folder (scanned at build time - see _scan_plate_sequences).
      Plates are assumed to already be scene-linear ACEScg (per the
      ingest/plates contract - see CLAUDE_INSTRUCTIONS.md), so no color
      transform is applied to them; the largest sequence found is wired
      in as the primary plate, any others are added alongside, unwired,
      for the artist to hook up manually.
    - Dot (labelled "ACEScg working") off the primary plate Read
    - Dot (labelled "to Write")
    (STUDIO_NO_VFX_PULL_READ_2026_10_28: the VFX PULL placeholder Read and its
    Merge2 were removed at Mark's request.)
    - Write node (EXR, raw/ACEScg, no bake)
    - Viewer (OCIO display transform handles LogC4->CDL->LUT->Rec.709)

  Artists work in ACEScg. The viewer LUT handles display.
  EXR renders are output in ACEScg linear - no color baked in.
  Review QTs are generated externally from the EXR renders.

  NOTE (2026-08-13): this used to also build a single hardcoded "CAMERA
  PLATES" Read node (from the ep_shot_plates template) feeding an
  OCIOColorSpace LogC4->ACEScg conversion. That's gone - plates are now
  assumed to land already-converted to ACEScg linear EXR, and the plates/
  folder is scanned directly instead of assuming one fixed filename
  pattern, since a shot's plates/ folder can hold more than one sequence
  (multiple takes/passes). If that assumption ever stops being true,
  the color-convert step needs to come back here (or upstream of it).

Working-directory behavior:
  Nuke's root 'project_directory' knob is pointed at the shot's folder
  (shots/{Episode}/{Scene}/{Shot} - the `shot_base` template) any time a
  script is opened, created, or saved-as for a shot context. This makes
  Read/Write file browsers (and any relative paths) default into the
  shot's own folders (plates/, render/, review/, reference/...) instead
  of wherever the artist last happened to browse.

First-launch reliability (ROOT CAUSE FOUND 2026-09-26):
  "New File had to be clicked twice" was not a timing race. In
  tk-multi-workfiles2 v0.16.2, NewFileAction.execute() runs
  reset -> prepare_new -> change_context, in that order, through the
  Workfiles2 instance of the environment Nuke is CURRENTLY in. Nuke from
  Desktop starts in the project environment, whose Workfiles2 had no
  hook_scene_operation, so the app's stock hook ran prepare_new (a no-op) and
  the script stayed blank; the second New File worked only because the engine
  was by then in the shot context. Now every Nuke Workfiles2 instance uses
  this hook, and prepare_new switches the engine into the target shot
  context itself before saving and building (_ensure_engine_context), so
  tk-nuke-writenode is loaded when the WriteTank is made. Workfiles2 then
  sees the contexts already match and skips its own switch.
  The writenode retry and the traceback-to-scene_op_debug.log wrapper stay
  as belt and braces.

OCIO config: ACES 1.3
Update OCIO_CAMERA_INPUT to match your camera:
  ARRI LogC4  : "Input - ARRI - Curve - LogC4 - EI800"
  ARRI LogC3  : "Input - ARRI - Curve - LogC3 - EI800"
  RED Log3G10 : "Input - RED - Curve - Log3G10"
  Sony SLog3  : "Input - Sony - Curve - SLog3 - SGamut3"
  (Only relevant if a camera-log color-convert step is ever reinstated
  above the plates Read nodes - see the NOTE above.)
"""

import os
import re
import glob
import time

import nuke
import sgtk

# ---------------------------------------------------------------------------
# Send to Review button wiring
#
# Adds a manual "Send to Review" button knob to each WriteTank node. The
# artist renders EXRs, reviews them, and clicks the button on the node that
# produced them to write a review flag JSON for the Mac Studio watcher.
#
# HISTORY (2026-06-17): we first tried to trigger the review flag
# automatically after render via the WriteTank gizmo's tk_after_render knob
# (read by tk-nuke-writenode v1.7.2 handler.py on_after_render_gizmo_callback,
# which runs the knob value through exec()). That path proved unreliable in
# practice and was abandoned in favor of this explicit button, which is
# strictly better here: it only runs when the artist intends it, runs in the
# node's own context (so nuke.thisNode() is the right node), and a human is
# present when it executes. A PyScript_Knob button is the correct use of a
# custom knob on this node - unlike the afterRender field, which fought us.
#
# The button is added by config hook code (here), NOT by editing the gizmo
# definition in install/, so it is tracked in git and survives
# `tank cache_apps` without a manual reapply step.
# ---------------------------------------------------------------------------
try:
    import os as _os, sys as _sys
    _hooks_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _hooks_dir not in _sys.path:
        _sys.path.insert(0, _hooks_dir)

    # Primary review trigger: a "Send to Review" button knob on each
    # WriteTank node. The afterRender knob approach was abandoned as
    # unreliable (see PIPELINE_REFERENCE.md / 2026-06-17). The artist
    # renders, reviews the EXRs, then clicks this button on the node that
    # produced them, which writes the review flag JSON for the Mac Studio
    # watcher. Single-expression call keeps the knob value robust.
    _SEND_TO_REVIEW_CMD = "__import__('render_complete_callback').send_to_review()"
    _REVIEW_KNOB_NAME = "send_to_review"

    def _add_review_button(node):
        """Idempotently add the Send to Review button knob to a node."""
        if node is None:
            return
        if node.knob(_REVIEW_KNOB_NAME) is not None:
            # Already present - just make sure the command is current.
            node.knob(_REVIEW_KNOB_NAME).setCommand(_SEND_TO_REVIEW_CMD)
            return
        btn = nuke.PyScript_Knob(_REVIEW_KNOB_NAME, "Send to Review", _SEND_TO_REVIEW_CMD)
        btn.setFlag(nuke.STARTLINE)
        node.addKnob(btn)

    def _wire_review_button(*args, **kwargs):
        _add_review_button(nuke.thisNode())

    nuke.addOnUserCreate(_wire_review_button, nodeClass="WriteTank")

    def _wire_all_on_script_load():
        for n in nuke.allNodes("WriteTank"):
            _add_review_button(n)

    nuke.addOnScriptLoad(_wire_all_on_script_load)

    # STUDIO_FARM_RENDER_2026_10_03 -- 'Render on: Local / Farm (Deadline)'
    # on every WriteTank. See hooks/farm_render.py.
    try:
        import farm_render as _farm_render
        _farm_render.install()
    except Exception as _farm_exc:
        nuke.warning("[farm_render] Could not add render controls: %s" % _farm_exc)
except Exception as _exc:
    nuke.warning("[render_complete] Could not register Send to Review button: %s" % _exc)

HookClass = sgtk.get_hook_baseclass()

OCIO_CAMERA_INPUT = "Input - ARRI - Curve - LogC4 - EI800"
OCIO_ACES_WORKING = "ACES - ACEScg"

# STUDIO_STEP_WRITE_DEFAULTS_APPLIED_2026_09_16
# Mark's request 2026-09-16: the auto-built Write node's render preset
# depends on the Step. "temp" renders default to 16-bit DWAA (fast,
# lossy, fine for test renders); "comp" (and any other Step, unchanged)
# stays on the existing 32-bit float default. Preset names must match
# settings.tk-nuke.episodic.shot_step.apps.tk-nuke-writenode.write_nodes
# in env/includes/settings/tk-nuke-episodic.yml -- flag a mismatch there,
# don't rename one side to fix the other (CLAUDE_INSTRUCTIONS.md rule 7,
# "Step codes are sacred").
DEFAULT_WRITE_PRESET = "Primary EXR (32-bit)"
WRITE_PRESET_BY_STEP = {
    "temp": "Primary EXR (16-bit DWAA)",
    "comp": "Primary EXR (32-bit)",
}

# STUDIO_NEWFILE_FRAME_RANGE_2026_10_27
# New scripts get their root frame range from Flow first (the Shot's cut
# in/out - the same fields tk-multi-setframerange uses by default), then
# from the BG01 plate on disk. Change the field names here, not the code.
SG_FRAME_IN_FIELD = "sg_cut_in"
SG_FRAME_OUT_FIELD = "sg_cut_out"
# "bg01" anywhere in the element / file / folder name, not part of a
# longer word or number: matches bg01, BG01, BG1, bg001; not bg010, bg11.
FRAME_RANGE_PLATE_RE = re.compile(r"(?<![a-z0-9])bg0*1(?![0-9])", re.IGNORECASE)

# STUDIO_NEWFILE_RANGE_FROM_PLATE_2026_11_16 (Mark, 2026-11-16)
# Ahead of the cut in/out above: the PRIMARY plate's Flow lineup
# (sg_plt_lineup_frame_start / _end) is the shot length. Primary = BG,
# then FG, then EL; lowest number; latest version. The rule lives in
# hooks/plate_sg.py (PRIMARY_KINDS, primary_plate_range) so Plate Lineup
# uses the same range.
#
# STUDIO_NEWFILE_AUTO_LINEUP_2026_11_16
# A first-version New File for a shot with at least this many distinct
# plate elements on Flow (latest version of each counted once) runs
# Plate Lineup: Build Lineup Nodes, then opens the pane.
AUTO_LINEUP_MIN_ELEMENTS = 2

# STUDIO_NEWFILE_CUT_MARKS_2026_10_28
# sg_cut_in/out include the handles (per Mark), so the script range above
# is the full plate length. The cut = range minus these handle lengths.
# Fields are found by DISPLAY NAME in the Flow Shot schema - add a name
# here if the field is ever renamed. See _handle_fields().
SG_HANDLES_HEAD_NAMES = ("Handles Head", "Head Handles")
SG_HANDLES_TAIL_NAMES = ("Handles Tail", "Tail Handles")
CUT_NOTE_NAME = "CutInfo"

# STUDIO_NEWFILE_REGRAIN_QC_2026_10_28
# DasGrain is pasted from the shared tools folder (repo/installers/nuke,
# overridable with BUF_NUKE_SHARED_TOOLS like the Shared Tools menu). The
# file also holds a DegrainHelper group, which is removed after pasting.
DASGRAIN_FILE_NAME = "DasGrain.nk"
DASGRAIN_GROUP_PREFIX = "DasGrain"

# STUDIO_NEWFILE_LAYOUT_2026_11_03
# New-script layout, copied from the graph Mark laid out by hand
# (302_038_0040, 2026-11-03). (x, y) offsets from the primary plate Read;
# Dots are offset +34 in x so they sit centred under an 80px-wide node.
# Move things here, not in _build_color_template.
NEWFILE_LAYOUT = {
    "branch":        (34, 213),    # Dot off the Read: comp line + degrain line
    "working":       (34, 286),    # "ACEScg working"
    "dasgrain":      (0, 922),
    "to_write":      (34, 1080),   # "to Write"
    "write":         (0, 1210),
    "viewer":        (0, 1340),
    # regrain box, right of the main line
    "backdrop":      (390, 96),
    "mask_top":      (432, 422),   # "Mask" (unconnected)
    "mask_in":       (432, 888),
    "degrain_top":   (507, 213),   # "DeGrained PLATE" (from the branch Dot)
    "degrain_in":    (507, 914),
    "plate_top":     (563, 172),   # "Plate" (from the Read)
    "plate_in":      (563, 954),
    "degrain_note":  (522, 416),
    # QC, off the render path
    "qc_tap":        (641, 291),   # "plate (QC)"
    "qc_diff":       (607, 1059),
}
NEWFILE_BACKDROP_SIZE = (310, 968)
NEWFILE_BACKDROP_LABEL = "REGRAIN - DasGrain (dis)\nDiff Matte"

# STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21 (Mark, 2026-11-21)
# New scripts take their regrain / degrain layout from Mark's template,
# pasted at build time - edit the .nk, not this hook. The NEWFILE_LAYOUT
# main line above is only the fallback when the file is missing or can't
# be used; the build message says which one ran.
# Bump NEWFILE_TEMPLATE_VERSION / _DATE whenever the new-file graph
# changes: both go on the TemplateInfo sticky note of every new script.
NEWFILE_TEMPLATE_VERSION = "1.0"
NEWFILE_TEMPLATE_DATE = "2026-11-21"
DASGRAIN_TEMPLATE_REL = ("pipeline", "config", "nuke", "DansTools", "dasgraintemplate.nk")
DASGRAIN_TEMPLATE_ENV = "BUF_NEWFILE_DASGRAIN_TEMPLATE"
TEMPLATE_PLATE_LABEL = "MAIN PLATE HERE"   # this Dot's input is replaced by the plate Read
TEMPLATE_WRITE_CLASS = "WriteTank"         # rebuilt for this shot by tk-nuke-writenode
TEMPLATE_WRITE_MARKER = "BUF_template_write"
TEMPLATE_DROP_CLASSES = ("Viewer",)        # rebuilt for this shot
TEMPLATE_VIEWER_BELOW_WRITE = 170
TEMPLATE_NANFIX_NODE = "Fix_nan_pixels"
# The template's NaN fix reads a root knob that doesn't exist; it becomes
# a "fix NaN pixels" checkbox (default on) on the node itself.
TEMPLATE_NANFIX_BAD_EXPR = "{{!parent.nanfix}}"
NEATVIDEO_CLASS = "OFXcom.absoft.NeatVideo5_v5"
# Sticky notes above the plate Read.
EFFECT_INSTRUCTIONS_FIELD = "sg_plt_effect_instructions"   # on the BG01 plate
TEMPLATE_NOTE_NAME = "TemplateInfo"
EFFECT_NOTE_NAME = "BG01_EffectInstructions"
INFO_NOTE_FONT_SIZE = 20
INFO_NOTE_GAP = 120
EFFECT_NOTE_X = 520

# Matches an EXR frame like "301_001_010_takeA.1001.exr" or
# "301_001_010_takeA_1001.exr" -> base="301_001_010_takeA", frame="1001".
# Anything under plates/ that doesn't match this is treated as a single,
# non-sequence frame instead of being skipped.
# Plate scanning itself now lives in plate_reads.py, shared with the
# "Load Shot Plates" menu command. Kept here only because older copies of
# this hook referenced it; nothing in this file uses it any more.
PLATE_SEQ_RE = re.compile(r"^(?P<base>.+?)[._](?P<frame>\d{3,8})\.(?P<ext>exr)$", re.IGNORECASE)


class SceneOperation(HookClass):

    def execute(self, operation, file_path, context, parent_action,
                file_version, read_only, **kwargs):

        engine = self.parent.engine

        if operation == "current_path":
            root_name = nuke.root().name()
            return root_name if root_name != "Root" else ""

        elif operation == "open":
            nuke.scriptOpen(file_path)
            if read_only:
                nuke.root()["lock_range"].setValue(True)
            self._set_project_directory(context)

        elif operation == "save":
            nuke.scriptSave()

        elif operation == "save_as":
            old = nuke.root().name()
            nuke.scriptSaveAs(file_path, overwrite=1)
            if old != file_path:
                if hasattr(engine, "save_context_to_script"):
                    engine.save_context_to_script()
            self._set_project_directory(context)

        elif operation == "reset":
            # Ask before discarding unsaved work, as the stock hook does -
            # this hook now also serves the project/shot/asset environments,
            # which used the stock hook until 2026-09-26.
            from sgtk.platform.qt import QtGui
            while nuke.root().modified():
                answer = QtGui.QMessageBox.question(
                    None,
                    "Save your script?",
                    "Your script has unsaved changes. Save before proceeding?",
                    QtGui.QMessageBox.Yes
                    | QtGui.QMessageBox.No
                    | QtGui.QMessageBox.Cancel,
                )
                if answer == QtGui.QMessageBox.Cancel:
                    return False
                if answer == QtGui.QMessageBox.No:
                    break
                nuke.scriptSave()
            nuke.scriptClear()
            return True

        elif operation == "new":
            nuke.scriptClear()
            resolved = self._resolve_new_path(context)
            self._ensure_dir(resolved)
            nuke.scriptSaveAs(resolved, overwrite=0)
            if hasattr(engine, "save_context_to_script"):
                engine.save_context_to_script()
            self._set_project_directory(context)
            self._safe_apply_frame_range(context)
            if self._is_first_version(context):
                self._safe_build_color_template(context, engine)
            nuke.scriptSave()
            return resolved

        elif operation == "prepare_new":
            # STUDIO_NEWFILE_CONTEXT_FIX_2026_09_26
            # Workfiles2 v0.16 "New File" calls reset, then this, and only
            # THEN changes context - through the app instance of whatever
            # environment Nuke is currently in (see the module docstring).
            # So move the engine into the new shot context first, or the
            # shot's apps (tk-nuke-writenode) are not loaded yet.
            if not self._is_shot_context(context):
                # Project / asset / sequence: nothing to build - same as the
                # stock hook.
                return True
            engine = self._ensure_engine_context(context, engine)
            # tk-nuke-writenode.create_new_write_node() needs a saved script
            # to resolve its render template fields, so save to the
            # versioned path BEFORE building the graph.
            if self._is_first_version(context):
                resolved = self._resolve_new_path(context)
                self._ensure_dir(resolved)
                nuke.scriptSaveAs(resolved, overwrite=0)
                if hasattr(engine, "save_context_to_script"):
                    engine.save_context_to_script()
                self._set_project_directory(context)
                self._safe_apply_frame_range(context)
                self._safe_build_color_template(context, engine)
                nuke.scriptSave()
            else:
                self._set_project_directory(context)
                self._safe_apply_frame_range(context)
            return True

        elif operation == "version_up":
            resolved = self._resolve_next_version(context)
            self._ensure_dir(resolved)
            nuke.scriptSaveAs(resolved, overwrite=0)
            if hasattr(engine, "save_context_to_script"):
                engine.save_context_to_script()
            self._set_project_directory(context)
            return resolved

    # -------------------------------------------------------------------------
    # Template resolution helpers
    # -------------------------------------------------------------------------

    def _work_template(self, context):
        return self.parent.sgtk.templates["ep_nuke_shot_work"]

    def _fields(self, context):
        fields = context.as_template_fields(self._work_template(context))
        fields.setdefault("nuke_extension", "nk")
        return fields

    def _resolve_new_path(self, context):
        return self._resolve_next_version(context, start_at=1)

    def _resolve_next_version(self, context, start_at=None):
        template = self._work_template(context)
        fields   = self._fields(context)
        existing = self._existing_versions(template, fields)
        if start_at is not None:
            next_v = max(start_at, (max(existing) + 1) if existing else start_at)
        else:
            next_v = (max(existing) + 1) if existing else 1
        fields["version"] = next_v
        return template.apply_fields(fields)

    def _existing_versions(self, template, fields):
        search = dict(fields)
        search["version"] = 0
        try:
            glob_path = template.apply_fields(search)
        except Exception:
            return []
        glob_path = re.sub(r"v\d{3,}", "v*", glob_path)
        versions = []
        for path in glob.glob(glob_path):
            m = re.search(r"v(\d{3,})", os.path.basename(path))
            if m:
                versions.append(int(m.group(1)))
        return sorted(versions)

    def _is_first_version(self, context):
        return len(self._existing_versions(
            self._work_template(context), self._fields(context))) <= 1

    @staticmethod
    def _ensure_dir(path):
        folder = os.path.dirname(path)
        if folder and not os.path.exists(folder):
            os.makedirs(folder)

    @staticmethod
    def _safe_resolve(tk, template_name, fields, fallback):
        try:
            tmpl   = tk.templates[template_name]
            needed = {k: fields[k] for k in tmpl.keys if k in fields}
            return tmpl.apply_fields(needed)
        except Exception as exc:
            nuke.warning("[color] Could not resolve '%s': %s" % (template_name, exc))
            return fallback

    @staticmethod
    def _p(path):
        """Normalise path separators to forward slashes for Nuke."""
        return path.replace("\\", "/")

    @staticmethod
    def _debug_log(message):
        """
        Write a line to a plain text file on the network share, bypassing
        Nuke's logging APIs entirely. Use this for diagnosing failures that
        don't appear in the Script Editor or tk-nuke.log (e.g. when
        workfiles2 callbacks run before/without a visible Nuke GUI panel).

        Safe to leave in place permanently - it's a no-op cost-wise and a
        cheap insurance policy against another silent-failure debugging
        session like 2026-06-16.
        """
        try:
            log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "logs")  # <config>/flow/current/logs
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            log_path = os.path.join(log_dir, "scene_op_debug.log")
            import datetime
            stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a") as f:
                f.write("[%s] %s\n" % (stamp, message))
        except Exception:
            # Never let logging itself break the calling code.
            pass

    # -------------------------------------------------------------------------
    # Step-based Write defaults
    # -------------------------------------------------------------------------

    def _step_code(self, context, fields=None):
        """Return the lowercase Step short name for this context (e.g.
        "temp", "comp"), or None if the context has no Step.

        Reads context.step directly rather than the template fields dict:
        the exact field key a given templates.yml uses for the Step short
        name isn't guaranteed (not verified against the live
        core/templates.yml when this was written -- see
        CLAUDE_INSTRUCTIONS.md rule 2, "verify before writing"), whereas
        context.step is sgtk's own Context API and is stable regardless of
        template field naming. fields is accepted only as a secondary
        fallback if context.step is ever unavailable.
        """
        try:
            step = context.step
            if step and step.get("name"):
                return str(step["name"]).strip().lower()
        except Exception as exc:
            self._debug_log("_step_code: context.step lookup failed: %r" % exc)
        if fields:
            for key in ("Step", "step_name", "step"):
                val = fields.get(key)
                if val:
                    return str(val).strip().lower()
        return None

    def _write_preset_for_step(self, context, fields=None):
        """The tk-nuke-writenode preset name to use for this context's Step.

        Must match a `name:` entry under settings.tk-nuke.episodic.
        shot_step.apps.tk-nuke-writenode.write_nodes in
        env/includes/settings/tk-nuke-episodic.yml. An unrecognised Step
        falls back to DEFAULT_WRITE_PRESET and is logged rather than
        guessed at silently.
        """
        step_code = self._step_code(context, fields)
        if step_code is not None and step_code not in WRITE_PRESET_BY_STEP:
            self._debug_log(
                "_write_preset_for_step: Step %r has no entry in "
                "WRITE_PRESET_BY_STEP, using DEFAULT_WRITE_PRESET %r"
                % (step_code, DEFAULT_WRITE_PRESET)
            )
        return WRITE_PRESET_BY_STEP.get(step_code, DEFAULT_WRITE_PRESET)

    def _fallback_write_knobs_for_step(self, context, fields=None):
        """Knob values for the plain-Write fallback node (only used if
        tk-nuke-writenode isn't available -- see _get_write_node_app) so it
        still matches the Step-based bit-depth default the WriteTank preset
        would have applied. Kept in sync with WRITE_PRESET_BY_STEP by hand;
        there are only two presets in play here so a lookup table would be
        more indirection, not less.
        """
        step_code = self._step_code(context, fields)
        if step_code == "temp":
            return {
                "datatype": "16 bit half",
                "compression": "DWAA",
                "dw_compression_level": 45,
            }
        # comp, and any unrecognised Step, matches DEFAULT_WRITE_PRESET.
        return {"datatype": "32 bit float"}

    # -------------------------------------------------------------------------
    # Working-directory helper
    # -------------------------------------------------------------------------

    def _shot_base_dir(self, context):
        """Resolve the shot's own folder (shots/{Episode}/{Scene}/{Shot}) via
        the `shot_base` template. Returns None if it can't be resolved (e.g.
        wrong context type, template missing needed fields)."""
        tk = self.parent.sgtk
        try:
            fields = self._fields(context)
            template = tk.templates["shot_base"]
            needed = {k: fields[k] for k in template.keys if k in fields}
            return template.apply_fields(needed)
        except Exception as exc:
            nuke.warning("[scene_op] Could not resolve shot_base directory: %s" % exc)
            return None

    def _set_project_directory(self, context):
        """Make the shot folder Nuke's working location: project_directory
        knob, cwd, and file-browser favourites (Shot, Shot plates, Shot
        renders, ...). See hooks/shot_workdir.py. No-op for non-shot
        contexts; never raises."""
        try:
            import shot_workdir
            shot_workdir.apply(self.parent.sgtk, context, set_script_knob=True)
        except Exception as exc:
            self._debug_log("Could not set shot working directory: %r" % exc)

    # -------------------------------------------------------------------------
    # Context helpers (STUDIO_NEWFILE_CONTEXT_FIX_2026_09_26)
    # -------------------------------------------------------------------------

    def _is_shot_context(self, context):
        """True for a Shot + Step context the ep_* templates can resolve."""
        try:
            entity = context.entity or {}
            if entity.get("type") != "Shot" or not context.step:
                return False
            self._fields(context)
            return True
        except Exception as exc:
            self._debug_log("_is_shot_context: %r not buildable: %r" % (context, exc))
            return False

    def _ensure_engine_context(self, context, engine):
        """Move the engine to `context` if it is not there yet; return the
        current engine. Raises if the context change fails - Workfiles2
        shows that to the artist, which beats a silently blank script."""
        if engine is not None and engine.context == context:
            return engine
        self._debug_log(
            "prepare_new: changing context %s -> %s before building"
            % (getattr(engine, "context", None), context)
        )
        sgtk.platform.change_context(context)
        return sgtk.platform.current_engine() or engine

    # -------------------------------------------------------------------------
    # Plates scanning
    # -------------------------------------------------------------------------

    def _plates_dir(self, context):
        """The shot's plates folder, from the config's own template.

        Resolved through plate_reads so the menu command and this hook look
        in exactly the same place; falls back to shot_base/plates if the
        template lookup fails for any reason.
        """
        try:
            import plate_reads
            resolved = plate_reads.plates_dir_for_context(self.parent.sgtk, context)
            if resolved:
                return resolved
        except Exception as exc:
            self._debug_log("plates_dir_for_context failed: %r" % exc)
        shot_dir = self._shot_base_dir(context)
        return os.path.join(shot_dir, "plates") if shot_dir else None

    def _scan_plate_sequences(self, plates_dir, shot_code=None):
        """Find this shot's delivered plate elements.

        Delegates to plate_reads.scan_plates() so this and the "Load Shot
        Plates" menu command can never disagree about what the plates are.
        That module knows the real delivery layout - one folder per element,
        holding a resolution folder, holding the frames:

            plates/301_001_0050_bg01_v02/4608x2592/..._1001.exr

        NOTE (2026-09-10): this used to list plates/ itself and look only at
        EXRs sitting directly in it, which is not where deliveries land - so
        it found nothing and every new script got the placeholder Read. Loose
        EXRs in plates/ are still picked up, for older shots that have them.

        Returns one record per element, best delivery first (highest version,
        bg before el before ref). See plate_reads.scan_plates for the keys.
        """
        try:
            import plate_reads
        except Exception as exc:
            self._debug_log("plate_reads unavailable: %r" % exc)
            nuke.warning("[scene_op] Could not import plate_reads: %s" % exc)
            return []
        records = plate_reads.scan_plates(plates_dir, shot_code=shot_code)
        for record in records:
            record["nuke_path"] = self._p(record["nuke_path"])
        return records

    # -------------------------------------------------------------------------
    # Frame range for new scripts (STUDIO_NEWFILE_FRAME_RANGE_2026_10_27)
    # -------------------------------------------------------------------------

    def _shotgrid_frame_range(self, context):
        """(first, last) from the Shot's cut in/out on Flow, or None if the
        context is not a Shot, the query fails, or either value is empty."""
        entity = context.entity or {}
        if entity.get("type") != "Shot" or not entity.get("id"):
            return None
        try:
            shot = self._sg().find_one(
                "Shot", [["id", "is", entity["id"]]],
                [SG_FRAME_IN_FIELD, SG_FRAME_OUT_FIELD],
            )
        except Exception as exc:
            self._debug_log("frame range: Flow query failed for %r: %r" % (entity, exc))
            return None
        if not shot:
            return None
        first, last = shot.get(SG_FRAME_IN_FIELD), shot.get(SG_FRAME_OUT_FIELD)
        if first is None or last is None:
            return None
        try:
            first, last = int(first), int(last)
        except (TypeError, ValueError):
            return None
        if last < first:
            self._debug_log(
                "frame range: ignoring Flow range %d-%d on %s (out before in)"
                % (first, last, entity.get("name"))
            )
            return None
        return first, last

    def _bg01_plate_range(self, context):
        """((first, last), description) from the newest BG01 plate delivery
        on disk, or (None, None). Uses the same scan as the plate Reads, so
        the range always matches the Read the template builds."""
        plates_dir = self._plates_dir(context)
        if not plates_dir:
            return None, None
        shot = (context.entity or {}).get("name")
        for record in self._scan_plate_sequences(plates_dir, shot_code=shot):
            if record.get("older") or record.get("count", 0) < 2:
                continue
            names = " ".join(
                str(record.get(k) or "") for k in ("element", "label", "relpath")
            )
            if FRAME_RANGE_PLATE_RE.search(names):
                return (record["first"], record["last"]), record["relpath"]
        return None, None

    # STUDIO_SCENE_OP_REAL_SG_2026_11_16
    def _sg(self):
        """The real Flow connection. Workfiles2's app.shotgun is a logging
        wrapper (DebugWrapperShotgun) that only has find / find_one /
        create / update / insert, so schema reads through it fail."""
        wrapped = self.parent.shotgun
        real = getattr(wrapped, "_sg", None)
        if real is not None:
            return real
        try:
            return self.parent.sgtk.shotgun
        except Exception:
            return wrapped

    # STUDIO_NEWFILE_RANGE_FROM_PLATE_2026_11_16
    def _load_hook_module(self, name):
        """hooks/<name>.py, loaded fresh by path under a private module
        name (same pattern as dailies_viewer / plate_lineup)."""
        import importlib.util
        import sys as _sys_mod
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            name + ".py")
        module_name = "_buf_scene_op_" + name
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        _sys_mod.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _flow_plates(self, context):
        """(plate_sg module, plate_sg.fetch_shot_plates() dict) for the
        context's Shot. Read once per New File (the cache is cleared by
        _safe_apply_frame_range). Raises on failure."""
        entity = context.entity or {}
        cached = getattr(self, "_flow_plates_cache", None)
        if cached and cached[0] == entity.get("id"):
            return cached[1], cached[2]
        ps = self._load_hook_module("plate_sg")
        data = ps.fetch_shot_plates(
            self._sg(), {"type": "Shot", "id": entity["id"]})
        self._flow_plates_cache = (entity.get("id"), ps, data)
        return ps, data

    def _primary_plate_range(self, context):
        """((first, last), how) from the primary plate's Flow lineup
        (plate_sg.primary_plate_range), or (None, reason). Never raises."""
        entity = context.entity or {}
        if entity.get("type") != "Shot" or not entity.get("id"):
            return None, "not a Shot context"
        try:
            ps, data = self._flow_plates(context)
        except Exception as exc:
            self._debug_log("frame range: Flow plate query failed for %r: %r"
                            % (entity, exc))
            return None, "Flow plate query failed (%s)" % exc
        first, last, how = ps.primary_plate_range(data["plates"], data["fields"])
        if first is None:
            return None, how
        return (first, last), how

    def _safe_apply_frame_range(self, context):
        """Set the root first/last frame on a new script, from the first of
          1. the primary plate's Flow lineup start/end (BG, then FG, then
             EL - STUDIO_NEWFILE_RANGE_FROM_PLATE_2026_11_16),
          2. the Shot's cut in/out on Flow,
          3. the BG01 plate on disk,
        else leave Nuke's default. Never raises; records what it did for
        the build message (_frame_range_summary)."""
        self._frame_range_note = "unchanged (Nuke default)"
        self._cut = None
        self._cut_note = "not marked - script range not set"
        self._flow_plates_cache = None
        try:
            frame_range, plate_how = self._primary_plate_range(context)
            source = "Flow %s" % plate_how
            if frame_range is None:
                self._debug_log("frame range: primary plate lineup not used - %s"
                                % plate_how)
                frame_range = self._shotgrid_frame_range(context)
                source = "Flow %s / %s - no plate lineup: %s" % (
                    SG_FRAME_IN_FIELD, SG_FRAME_OUT_FIELD, plate_how)
            if frame_range is None:
                frame_range, plate = self._bg01_plate_range(context)
                source = "BG01 plate %s on disk" % plate
            if frame_range is None:
                self._frame_range_note = (
                    "NOT SET - no plate lineup on Flow (%s), no cut in/out "
                    "on Flow and no BG01 plate on disk" % plate_how
                )
                self._debug_log("frame range: %s for %s" % (self._frame_range_note, context))
                return
            first, last = frame_range
            root = nuke.root()
            root["last_frame"].setValue(last)
            root["first_frame"].setValue(first)
            root["last_frame"].setValue(last)
            self._frame_range_note = "%d-%d  (from %s)" % (first, last, source)
            self._debug_log("frame range: %s for %s" % (self._frame_range_note, context))
            self._cut = self._cut_from_handles(context, first, last)
            self._apply_cut_marks()
        except Exception as exc:
            self._frame_range_note = "NOT SET - error: %s" % exc
            self._debug_log("frame range failed for %s: %r" % (context, exc))

    # STUDIO_NEWFILE_CUT_MARKS_2026_10_28
    _HANDLE_FIELD_CACHE = {}

    def _handle_fields(self):
        """(head_code, tail_code) of the Shot fields whose display names are in
        SG_HANDLES_HEAD_NAMES / SG_HANDLES_TAIL_NAMES. Read once per session
        from the Flow schema; a failed read is not cached."""
        cache = self.__class__._HANDLE_FIELD_CACHE
        if "fields" in cache:
            return cache["fields"]
        try:
            schema = self._sg().schema_field_read("Shot")
        except Exception as exc:
            self._debug_log("handles: Shot schema read failed: %r" % exc)
            return None, None
        want_head = set(n.lower() for n in SG_HANDLES_HEAD_NAMES)
        want_tail = set(n.lower() for n in SG_HANDLES_TAIL_NAMES)
        head = tail = None
        for code, info in sorted(schema.items()):
            name = ((info.get("name") or {}).get("value") or "").strip().lower()
            if name in want_head and head is None:
                head = code
            elif name in want_tail and tail is None:
                tail = code
        self._debug_log("handles: Shot fields head=%r tail=%r" % (head, tail))
        cache["fields"] = (head, tail)
        return head, tail

    def _cut_from_handles(self, context, first, last):
        """(cut_in, cut_out, head, tail): the script range minus the Shot's
        Handles Head / Handles Tail. None (reason in _cut_note) if unknown.
        Never raises."""
        try:
            entity = context.entity or {}
            if entity.get("type") != "Shot" or not entity.get("id"):
                self._cut_note = "not marked - not a Shot context"
                return None
            head_f, tail_f = self._handle_fields()
            if not head_f or not tail_f:
                self._cut_note = (
                    "not marked - no %s / %s field found on Shot"
                    % (SG_HANDLES_HEAD_NAMES[0], SG_HANDLES_TAIL_NAMES[0]))
                return None
            shot = self._sg().find_one(
                "Shot", [["id", "is", entity["id"]]], [head_f, tail_f]) or {}
            head, tail = shot.get(head_f), shot.get(tail_f)
            if head in (None, "") or tail in (None, ""):
                self._cut_note = "not marked - Handles Head/Tail empty on Flow"
                return None
            head, tail = int(float(head)), int(float(tail))
            cut_in, cut_out = first + head, last - tail
            if head < 0 or tail < 0 or cut_out < cut_in:
                self._cut_note = (
                    "not marked - handles %d/%d do not fit range %d-%d"
                    % (head, tail, first, last))
                return None
            self._cut_note = "%d-%d  (handles %d head / %d tail)" % (
                cut_in, cut_out, head, tail)
            return cut_in, cut_out, head, tail
        except Exception as exc:
            self._cut_note = "not marked - error: %s" % exc
            return None
        finally:
            self._debug_log("cut: %s for %s" % (self._cut_note, context))

    def _apply_cut_marks(self, near=None):
        """Lock every Viewer's in/out to the cut and write/update the
        CutInfo sticky note (moved beside `near` when given). Never raises."""
        cut = getattr(self, "_cut", None)
        if not cut:
            return
        cut_in, cut_out, head, tail = cut
        try:
            with nuke.root():
                for viewer in nuke.allNodes("Viewer"):
                    try:
                        viewer["frame_range"].setValue("%d-%d" % (cut_in, cut_out))
                        viewer["frame_range_lock"].setValue(True)
                    except Exception as exc:
                        self._debug_log("cut: Viewer in/out failed on %s: %r"
                                        % (viewer.name(), exc))
                note = nuke.toNode(CUT_NOTE_NAME)
                if note is None:
                    note = nuke.nodes.StickyNote(name=CUT_NOTE_NAME)
                root = nuke.root()
                note["label"].setValue(
                    "CUT  %d - %d\nhandles  %d head / %d tail\nscript  %d - %d"
                    % (cut_in, cut_out, head, tail,
                       int(root["first_frame"].value()),
                       int(root["last_frame"].value())))
                try:
                    note["note_font_size"].setValue(24)
                except Exception:
                    pass
                if near is not None:
                    note.setXYpos(near.xpos() + 200, near.ypos())
        except Exception as exc:
            self._debug_log("cut: marking failed: %r" % exc)

    # STUDIO_NEWFILE_REGRAIN_QC_2026_10_28
    @staticmethod
    def _dasgrain_path():
        """repo/installers/nuke/DasGrain.nk, found relative to this hook
        (<repo>/pipeline/config/flow/current/config/hooks)."""
        root = os.environ.get("BUF_NUKE_SHARED_TOOLS")
        if not root:
            repo = os.path.dirname(os.path.abspath(__file__))
            for _ in range(6):
                repo = os.path.dirname(repo)
            root = os.path.join(repo, "installers", "nuke")
        return os.path.normpath(os.path.join(root, DASGRAIN_FILE_NAME))

    def _paste_dasgrain(self):
        """Paste DasGrain.nk, keep only the DasGrain group. None if missing."""
        path = self._dasgrain_path()
        if not os.path.isfile(path):
            self._regrain_note = "NOT ADDED - %s not found" % path
            return None
        for node in nuke.selectedNodes():
            node.setSelected(False)
        before = set(n.name() for n in nuke.allNodes())
        nuke.nodePaste(path.replace("\\", "/"))
        pasted = [n for n in nuke.allNodes() if n.name() not in before]
        dasgrain = None
        for node in pasted:
            if (dasgrain is None and node.Class() == "Group"
                    and node.name().startswith(DASGRAIN_GROUP_PREFIX)):
                dasgrain = node
        for node in pasted:
            if node is not dasgrain:
                nuke.delete(node)
        for node in nuke.allNodes():
            node.setSelected(False)
        if dasgrain is None:
            self._regrain_note = "NOT ADDED - no DasGrain group in %s" % path
        return dasgrain

    def _place(self, node, key, origin):
        """Put node at NEWFILE_LAYOUT[key] relative to origin (x, y). STUDIO_NEWFILE_LAYOUT_2026_11_03"""
        dx, dy = NEWFILE_LAYOUT[key]
        node.setXYpos(origin[0] + dx, origin[1] + dy)
        return node

    def _build_regrain(self, read_plates, comp_in, branch, origin):
        """DasGrain on the main line, disabled, with its inputs piped in from
        the regrain backdrop to the right (NEWFILE_LAYOUT):
            0 COMP             <- comp_in ("ACEScg working")
            1 PLATE            <- "Plate" Dot <- the plate Read
            2 DEGRAINED_PLATE  <- "DeGrained PLATE" Dot <- branch Dot
                                  (put the degrain on this line)
            3 mask             <- "Mask" Dot, unconnected
        Returns the node the "to Write" Dot should take - DasGrain, or
        comp_in if it could not be added. Never raises."""
        self._regrain_note = "not added"
        try:
            dasgrain = self._paste_dasgrain()
            if dasgrain is None:
                self._debug_log("regrain: %s" % self._regrain_note)
                return comp_in
            dasgrain.setInput(0, comp_in)
            dasgrain["disable"].setValue(True)
            dasgrain["label"].setValue(
                "DISABLED until set up:\n"
                "1. connect degrained plate (input 2)\n"
                "2. Analyze   3. enable")
            self._place(dasgrain, "dasgrain", origin)

            def _pipe(label, source, top_key, in_key, input_index):
                top = nuke.nodes.Dot()
                if label:
                    top["label"].setValue(label)
                if source is not None:
                    top.setInput(0, source)
                self._place(top, top_key, origin)
                bottom = nuke.nodes.Dot()
                bottom.setInput(0, top)
                self._place(bottom, in_key, origin)
                dasgrain.setInput(input_index, bottom)

            _pipe("Plate", read_plates, "plate_top", "plate_in", 1)
            _pipe("DeGrained PLATE", branch, "degrain_top", "degrain_in", 2)
            _pipe("Mask", None, "mask_top", "mask_in", 3)

            note = nuke.nodes.StickyNote(
                label="DEGRAINED PLATE\nput it here and connect to\n"
                      "DasGrain input 2 (DEGRAINED_PLATE)")
            self._place(note, "degrain_note", origin)

            backdrop = nuke.nodes.BackdropNode(
                label=NEWFILE_BACKDROP_LABEL,
                note_font_size=24,
                tile_color=0x3D4A5AFF,
                bdwidth=NEWFILE_BACKDROP_SIZE[0],
                bdheight=NEWFILE_BACKDROP_SIZE[1],
            )
            self._place(backdrop, "backdrop", origin)

            self._regrain_note = (
                "%s added DISABLED - PLATE and DEGRAINED PLATE lines wired "
                "from the plate; put the degrain on the DeGrained PLATE line, "
                "Analyze, then enable" % dasgrain.name())
            self._debug_log("regrain: %s" % self._regrain_note)
            return dasgrain
        except Exception as exc:
            self._regrain_note = "NOT ADDED - error: %s" % exc
            self._debug_log("regrain failed: %r" % exc)
            return comp_in

    def _build_difference_qc(self, plate_src, comp_src, origin):
        """Difference of comp vs plate, off the render path. Never raises."""
        self._qc_note = "not added"
        try:
            tap = nuke.nodes.Dot(label="plate (QC)")
            tap.setInput(0, plate_src)
            self._place(tap, "qc_tap", origin)
            diff = nuke.nodes.Difference(
                label="QC: comp vs plate\n(not rendered)")
            diff.setInput(0, comp_src)
            diff.setInput(1, tap)
            self._place(diff, "qc_diff", origin)
            self._qc_note = "%s: comp vs plate (alpha), not in the render path" % diff.name()
        except Exception as exc:
            self._qc_note = "NOT ADDED - error: %s" % exc
            self._debug_log("difference QC failed: %r" % exc)

    # STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21
    @staticmethod
    def _dasgrain_template_path():
        """repo/pipeline/config/nuke/DansTools/dasgraintemplate.nk, found
        relative to this hook; DASGRAIN_TEMPLATE_ENV overrides."""
        override = os.environ.get(DASGRAIN_TEMPLATE_ENV)
        if override:
            return os.path.normpath(override)
        repo = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            repo = os.path.dirname(repo)
        return os.path.normpath(os.path.join(repo, *DASGRAIN_TEMPLATE_REL))

    @staticmethod
    def _node_class_available(class_name):
        """True if this Nuke can create class_name (e.g. an OFX plug-in)."""
        for node in nuke.selectedNodes():
            node.setSelected(False)
        try:
            node = nuke.createNode(class_name, inpanel=False)
        except Exception:
            return False
        try:
            nuke.delete(node)
        except Exception:
            pass
        return True

    @staticmethod
    def _template_paste_text(text, drop_classes, substitute, quiet=()):
        """The template as text nodePaste can take without side effects:
        the header, the window layout and the Root block are removed (Root
        would overwrite this shot's format, range and OCIO settings), and so
        is every top-level node whose class is in drop_classes. A top-level
        node whose class is a key of `substitute` becomes a NoOp at the same
        place, named substitute[class][0] and labelled substitute[class][1],
        so the node stack - and every connection - is unchanged.
        Returns (text, dropped names, substituted names not in `quiet`)."""
        def _depth(line):
            # Braces in .nk values are written escaped (\{ \}), so a plain
            # count that skips escaped characters finds where a block ends.
            # (A closing brace at column 0 does not: Root's gsv {} has one.)
            d, k = 0, 0
            while k < len(line):
                c = line[k]
                if c == "\\":
                    k += 2
                    continue
                if c == "{":
                    d += 1
                elif c == "}":
                    d -= 1
                k += 1
            return d

        def _block_end(i):
            depth, j = _depth(lines[i]), i
            while depth > 0 and j + 1 < n:
                j += 1
                depth += _depth(lines[j])
            if depth > 0:
                raise RuntimeError("unbalanced braces from template line %d" % (i + 1))
            return j

        lines = text.splitlines()
        out, dropped, swapped = [], [], []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if line.startswith("define_window_layout_xml"):
                i = _block_end(i) + 1
                continue
            if line.startswith("#") or line.startswith("version "):
                i += 1
                continue
            match = re.match(r"^([A-Za-z_][\w.]*) \{$", line)
            if not match:
                out.append(line)
                i += 1
                continue
            j = _block_end(i)
            block = lines[i:j + 1]
            i = j + 1
            cls = match.group(1)
            name = ""
            for b in block:
                if b.startswith(" name "):
                    name = b[len(" name "):].strip()
                    break
            if cls == "Root" or cls in drop_classes:
                dropped.append(name or cls)
                continue
            if cls in substitute:
                new_name, label = substitute[cls]
                keep = [b for b in block
                        if b.startswith((" inputs ", " xpos ", " ypos "))]
                block = (["NoOp {", " name %s" % new_name] + keep
                         + [" label \"%s\"" % label, " tile_color 0xff0000ff", "}"])
                if cls not in quiet:
                    swapped.append("%s (%s)" % (name or cls, cls))
            out.extend(block)
        return "\n".join(out) + "\n", dropped, swapped

    def _build_dasgrain_template(self, read_plates):
        """Paste dasgraintemplate.nk around the primary plate Read.

        The "MAIN PLATE HERE" placeholder (a Constant) is replaced by the
        Read, and the whole template is moved so the Read sits where the
        placeholder was. "DEGRAIN PLATE HERE" stays a black Constant (Mark,
        2026-11-21) until the artist reads in the degrain render. The
        template's Viewer is left out and its Flow Write is replaced by a
        NoOp marker; _build_color_template builds both for this shot.

        Returns {"write_in", "write_pos", "viewer_pos"}, or None - with the
        reason in the build message - so the caller uses the previous
        built-in layout instead. Never raises."""
        import tempfile
        self._template_note = "not used"
        path = self._dasgrain_template_path()
        tmp = None
        pasted = []
        try:
            if not os.path.isfile(path):
                self._template_note = "NOT USED - %s not found" % path
                return None
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
            substitute = {TEMPLATE_WRITE_CLASS: (TEMPLATE_WRITE_MARKER, "Flow Write goes here")}
            if NEATVIDEO_CLASS in raw and not self._node_class_available(NEATVIDEO_CLASS):
                substitute[NEATVIDEO_CLASS] = (
                    "NeatVideo_not_installed",
                    "Neat Video is not installed on this machine -\\n"
                    "replace with Reduce Noise v5")
            text, dropped, swapped = self._template_paste_text(
                raw, TEMPLATE_DROP_CLASSES, substitute, quiet=(TEMPLATE_WRITE_CLASS,))
            nanfix_fixed = text.count(TEMPLATE_NANFIX_BAD_EXPR)
            text = text.replace(TEMPLATE_NANFIX_BAD_EXPR, "false")

            fd, tmp = tempfile.mkstemp(prefix="buf_newfile_", suffix=".nk")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            for node in nuke.selectedNodes():
                node.setSelected(False)
            before = set(n.name() for n in nuke.allNodes())
            nuke.nodePaste(tmp.replace("\\", "/"))
            pasted = [n for n in nuke.allNodes() if n.name() not in before]
            for node in nuke.allNodes():
                node.setSelected(False)

            def _find(pred):
                for node in pasted:
                    try:
                        if pred(node):
                            return node
                    except Exception:
                        pass
                return None

            plate_dot = _find(lambda n: n.Class() == "Dot" and
                              n["label"].value().strip() == TEMPLATE_PLATE_LABEL)
            marker = _find(lambda n: n.Class() == "NoOp" and
                           n.name().startswith(TEMPLATE_WRITE_MARKER))
            if plate_dot is None or plate_dot.input(0) is None:
                raise RuntimeError("no Dot labelled %r with a placeholder "
                                   "input in the template" % TEMPLATE_PLATE_LABEL)
            if marker is None or marker.input(0) is None:
                raise RuntimeError("no connected %s in the template"
                                   % TEMPLATE_WRITE_CLASS)

            placeholder = plate_dot.input(0)
            dx = read_plates.xpos() - placeholder.xpos()
            dy = read_plates.ypos() - placeholder.ypos()
            for node in pasted:
                node.setXYpos(node.xpos() + dx, node.ypos() + dy)
            plate_dot.setInput(0, read_plates)
            nuke.delete(placeholder)

            write_in = marker.input(0)
            write_pos = (marker.xpos(), marker.ypos())
            nuke.delete(marker)
            pasted = []  # kept from here on

            notes = []
            nanfix = nuke.toNode(TEMPLATE_NANFIX_NODE)
            if nanfix is not None:
                if "nanfix" not in nanfix.knobs():
                    nanfix.addKnob(nuke.Tab_Knob("buf_tab", "NaN fix"))
                    nanfix.addKnob(nuke.Boolean_Knob("nanfix", "fix NaN pixels"))
                    nanfix["nanfix"].setValue(True)
                nanfix["disable"].setExpression("!nanfix")
            else:
                notes.append("no %s node" % TEMPLATE_NANFIX_NODE)
            if not nanfix_fixed:
                notes.append("%r not found - NaN fix expression left as is"
                             % TEMPLATE_NANFIX_BAD_EXPR)

            dasgrain = None
            diff = None
            for node in nuke.allNodes():
                if (dasgrain is None and node.Class() == "Group"
                        and node.name().startswith(DASGRAIN_GROUP_PREFIX)):
                    dasgrain = node
                if diff is None and node.Class() == "Difference":
                    diff = node
            self._regrain_note = (
                "%s from the template, %s" % (
                    dasgrain.name(),
                    "DISABLED - read in the degrain render at DEGRAIN PLATE "
                    "HERE, Analyze, then enable"
                    if dasgrain["disable"].value() else "enabled")
                if dasgrain is not None else "NOT IN TEMPLATE")
            self._qc_note = ("%s from the template (not rendered)" % diff.name()
                             if diff is not None else "not in the template")
            if swapped:
                notes.append("replaced: %s" % ", ".join(swapped))
            mtime = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(path)))
            self._template_file_note = "%s (modified %s)" % (
                os.path.basename(path), mtime)
            self._template_note = "%s%s" % (
                self._template_file_note,
                ("  [%s]" % "; ".join(notes)) if notes else "")
            self._debug_log("dasgrain template: %s; dropped %s"
                            % (self._template_note, dropped))
            return {
                "write_in": write_in,
                "write_pos": write_pos,
                "viewer_pos": (write_pos[0], write_pos[1] + TEMPLATE_VIEWER_BELOW_WRITE),
            }
        except Exception as exc:
            import traceback as _tb
            for node in pasted:
                try:
                    nuke.delete(node)
                except Exception:
                    pass
            self._template_note = "NOT USED - error: %s" % exc
            self._debug_log("dasgrain template failed: %r\n%s"
                            % (exc, _tb.format_exc()))
            return None
        finally:
            if tmp:
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def _bg01_effect_instructions(self, context):
        """(BG01 plate code or None, instructions text or None, note).
        Raises if Flow can't be read."""
        ps, data = self._flow_plates(context)
        plate, how = ps.pick_bg01(data["plates"])
        if plate is None:
            return None, None, how
        code = ps.plate_code(plate)
        plate_type = data["entity_type"]
        field = ps.resolve_field(ps._field_schema(self._sg(), plate_type),
                                 EFFECT_INSTRUCTIONS_FIELD)
        if field is None:
            return code, None, "no %s field on Flow %s" % (
                EFFECT_INSTRUCTIONS_FIELD, plate_type)
        if field in plate:
            value = plate.get(field)
        else:
            row = self._sg().find_one(plate_type, [["id", "is", plate["id"]]],
                                      [field]) or {}
            value = row.get(field)
        value = str(value).strip() if value is not None else ""
        if not value:
            return code, None, "%s is empty on %s" % (field, code)
        return code, value, "from %s.%s" % (code, field)

    @staticmethod
    def _note_text(text, width=70):
        """Wrap free text and escape it for a StickyNote label, which is
        TCL-evaluated: [...] would run as a command and $ as a variable."""
        import textwrap
        lines = []
        for para in str(text).replace("\r\n", "\n").split("\n"):
            lines.extend(textwrap.wrap(para, width) or [""])
        out = "\n".join(lines)
        for old, new in (("\\", "\\\\"), ("[", "\\["), ("$", "\\$")):
            out = out.replace(old, new)
        return out

    def _add_info_notes(self, context, origin, tpl):
        """Two StickyNotes above the plate Read: which template built this
        script, and BG01's effect instructions from Flow. Never raises."""
        import datetime
        import getpass

        def _sticky(name, label, x_offset, tile):
            note = nuke.toNode(name)
            if note is None:
                note = nuke.nodes.StickyNote(name=name)
            note["label"].setValue(label)
            try:
                note["note_font_size"].setValue(INFO_NOTE_FONT_SIZE)
                note["tile_color"].setValue(tile)
            except Exception:
                pass
            # Notes grow downwards, so lift each one by its own height.
            est = 30 + int((label.count("\n") + 1) * INFO_NOTE_FONT_SIZE * 1.4)
            note.setXYpos(origin[0] + x_offset, origin[1] - INFO_NOTE_GAP - est)
            return note

        try:
            try:
                user = getpass.getuser()
            except Exception:
                user = "unknown"
            layout = (getattr(self, "_template_file_note", "dasgraintemplate.nk")
                      if tpl is not None else
                      "previous built-in layout - template %s" % self._template_note)
            _sticky(TEMPLATE_NOTE_NAME,
                    "NEW FILE TEMPLATE  v%s  (%s)\nregrain layout: %s\nbuilt %s by %s"
                    % (NEWFILE_TEMPLATE_VERSION, NEWFILE_TEMPLATE_DATE,
                       self._note_text(layout),
                       datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                       self._note_text(user)),
                    0, 0x5A5A5AFF)
            self._template_version_note = "v%s (%s)" % (
                NEWFILE_TEMPLATE_VERSION, NEWFILE_TEMPLATE_DATE)
        except Exception as exc:
            self._template_version_note = "note NOT ADDED - error: %s" % exc
            self._debug_log("template note failed: %r" % exc)

        try:
            try:
                code, text, how = self._bg01_effect_instructions(context)
            except Exception as exc:
                code, text, how = None, None, "Flow read failed (%s)" % exc
            title = "BG01 EFFECT INSTRUCTIONS%s" % (
                ("  -  %s" % code) if code else "")
            body = self._note_text(text) if text else "(none - %s)" % self._note_text(how)
            _sticky(EFFECT_NOTE_NAME, "%s\n\n%s" % (title, body),
                    EFFECT_NOTE_X, 0x7A6A2AFF)
            self._effect_note = ("added %s" % how) if text else ("added, empty - %s" % how)
        except Exception as exc:
            self._effect_note = "NOT ADDED - error: %s" % exc
            self._debug_log("effect instructions note failed: %r" % exc)
        self._debug_log("info notes: template %s; BG01 instructions %s"
                        % (getattr(self, "_template_version_note", "?"),
                           self._effect_note))

    def _template_summary(self):
        return "%s - %s" % (getattr(self, "_template_version_note", "no note"),
                            getattr(self, "_template_note", "not used"))

    def _effect_summary(self):
        return getattr(self, "_effect_note", "not added")

    def _regrain_summary(self):
        return getattr(self, "_regrain_note", "not added")

    def _qc_summary(self):
        return getattr(self, "_qc_note", "not added")

    def _cut_summary(self):
        return getattr(self, "_cut_note", "not marked")

    def _frame_range_summary(self):
        return getattr(self, "_frame_range_note", "unchanged (Nuke default)")

    # STUDIO_NEWFILE_AUTO_LINEUP_2026_11_16
    def _lineup_summary(self):
        return getattr(self, "_lineup_note", "not run")

    def _auto_plate_lineup(self, context, engine):
        """Shot with >= AUTO_LINEUP_MIN_ELEMENTS plate elements on Flow
        (latest version of each counted once): do what the artist would
        do in Plate Lineup - Build Lineup Nodes now (one undo step, so
        it is in the script that gets saved), then open the pane once
        New File has finished. Never raises; the outcome goes in the
        build message and scene_op_debug.log."""
        self._lineup_note = "not run"
        try:
            entity = context.entity or {}
            if entity.get("type") != "Shot" or not entity.get("id"):
                self._lineup_note = "not run - not a Shot context"
                return
            engine_entity = (getattr(engine, "context", None) and
                             engine.context.entity) or {}
            if engine_entity.get("id") != entity.get("id"):
                self._lineup_note = ("not run - Nuke is not in this shot's "
                                     "context yet; open Plate Lineup from "
                                     "the Buffalo VFX menu")
                return
            ps, data = self._flow_plates(context)
            elements = sorted(ps.latest_by_element(data["plates"]))
            names = ", ".join(ps.element_label(k) for k in elements) or "none"
            if len(elements) < AUTO_LINEUP_MIN_ELEMENTS:
                self._lineup_note = ("not run - %d plate element%s on Flow (%s)"
                                     % (len(elements),
                                        "" if len(elements) == 1 else "s",
                                        names))
                return
            pl = self._load_hook_module("plate_lineup")
            g = pl.gather(engine)
            result = pl._do_build(g)
            self._lineup_note = "%d elements (%s) - built, pane opened:\n    %s" % (
                len(elements), names, "\n    ".join(result))
            self._open_lineup_pane_later(pl)
        except Exception as exc:
            import traceback as _tb
            self._lineup_note = ("FAILED - %s (open Plate Lineup from the "
                                 "Buffalo VFX menu)" % exc)
            self._debug_log("auto lineup failed: %r\n%s" % (exc, _tb.format_exc()))
        finally:
            self._debug_log("auto lineup: %s" % self._lineup_note)

    def _open_lineup_pane_later(self, pl):
        """Open the Plate Lineup pane after New File returns (Qt timer),
        so it is not created inside the Workfiles2 action."""
        def _open():
            try:
                pl._open_pane()
            except Exception as exc:
                self._debug_log("auto lineup: pane could not open: %r" % exc)
                nuke.tprint("[scene_op] Plate Lineup pane could not open: %s"
                            % exc)
        try:
            from sgtk.platform.qt import QtCore
            QtCore.QTimer.singleShot(0, _open)
        except Exception:
            _open()

    # -------------------------------------------------------------------------
    # tk-nuke-writenode app lookup (with retry)
    # -------------------------------------------------------------------------

    def _get_write_node_app(self, engine, retries=6, delay=0.5):
        """Return the tk-nuke-writenode app instance, retrying briefly if it
        isn't registered in engine.apps yet.

        This is the documented failure mode behind the old "have to click
        New File twice" symptom: prepare_new firing before every app
        instance has finished initializing. Disabling
        tk-multi-workfiles2's launch_at_startup (see tk-nuke-episodic.yml)
        removes the main trigger for that race, but this retry keeps the
        build robust rather than falling back to a plain Write node (and
        silently losing the Send to Review button) the instant it happens.
        """
        for _attempt in range(retries):
            wn_app = engine.apps.get("tk-nuke-writenode")
            if wn_app is not None:
                return wn_app
            time.sleep(delay)
        return None

    # -------------------------------------------------------------------------
    # Color template builder
    # -------------------------------------------------------------------------

    def _safe_build_color_template(self, context, engine):
        """Wrapper around _build_color_template that never leaves the artist
        looking at a blank script with no explanation. Any exception is
        logged (with traceback) to scene_op_debug.log and surfaced via
        nuke.message, instead of silently aborting prepare_new/new."""
        try:
            self._build_color_template(context, engine)
        except Exception as exc:
            import traceback as _tb
            trace = _tb.format_exc()
            nuke.tprint("[scene_op] _build_color_template failed:\n%s" % trace)
            self._debug_log("_build_color_template failed: %r\n%s" % (exc, trace))
            try:
                nuke.message(
                    "The color-pipeline template failed to build automatically.\n\n"
                    "Error: %s\n\n"
                    "A blank versioned script was still saved for you - build the "
                    "graph manually, or try File > New again. See "
                    "scene_op_debug.log for the full traceback."
                    % exc
                )
            except Exception:
                pass

    def _build_color_template(self, context, engine):
        tk     = self.parent.sgtk
        fields = self._fields(context)

        shot = fields.get("Shot", "SHOT")

        plates_dir = self._plates_dir(context)
        plate_sequences = self._scan_plate_sequences(plates_dir, shot_code=shot)

        render_template = tk.templates.get("ep_nuke_shot_render_work")
        if render_template:
            render_fields = dict(fields)
            render_fields["output"] = "beauty"
            render_fields["version"] = 1
            try:
                render_path = render_template.apply_fields(render_fields)
            except Exception:
                render_path = "PLACEHOLDER/render/%s_beauty_v001.####.exr" % shot
        else:
            render_path = "PLACEHOLDER/render/%s_beauty_v001.####.exr" % shot

        # ── Layout constants ──────────────────────────────────────────────
        x0, y0  = 0, 0
        x_extra = -300
        x_main  = 0
        y_step  = 130

        y = y0

        # ── Read: one per plate element delivered for this shot ───────────
        # Built by plate_reads.make_read so these are identical to the nodes
        # the "Load Shot Plates" menu command makes - same colorspace
        # handling, same labels. The first element (bg, by the ordering
        # plate_reads applies) is wired into the graph as the primary plate;
        # the rest are placed alongside, unwired, for the artist.
        if plate_sequences:
            import plate_reads
            primary = plate_sequences[0]
            read_plates = plate_reads.make_read(primary, primary=True)
            read_plates.setXYpos(x_main, y)

            for i, seq in enumerate(plate_sequences[1:], start=1):
                extra = plate_reads.make_read(
                    seq, primary=False, note="Not wired - connect manually"
                )
                # Every sequence is loaded now, so there can be a dozen:
                # a row to the left of the primary, 8 per row, rows
                # stacking upward.
                row, col = divmod(i - 1, 8)
                extra.setXYpos(x_main - 150 * (col + 1), y0 - row * y_step)
        else:
            # No EXRs found in plates/ yet (e.g. before plates are
            # delivered). Keep the graph structurally valid with a
            # placeholder the artist can repoint once plates land.
            fallback_path = self._p(
                os.path.join(plates_dir or "PLACEHOLDER/plates", "%s.####.exr" % shot)
            )
            read_plates = nuke.createNode("Read", inpanel=False)
            read_plates["file"].setValue(fallback_path)
            read_plates["raw"].setValue(True)
            read_plates["colorspace"].setValue("raw")
            read_plates["label"].setValue(
                "PLATE PLACEHOLDER\n(no EXRs found in plates/ yet — browse manually)\n[value file]"
            )
            read_plates.setXYpos(x_main, y)

        # STUDIO_NEWFILE_LAYOUT_2026_11_03: every position below comes from NEWFILE_LAYOUT,
        # relative to the primary Read.
        origin = (read_plates.xpos(), read_plates.ypos())

        # STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21: the regrain / degrain
        # layout comes from dasgraintemplate.nk. The NEWFILE_LAYOUT main line
        # below is only the fallback when the template can't be used.
        tpl = self._build_dasgrain_template(read_plates)
        if tpl is not None:
            dot_working = None
            dot_write = tpl["write_in"]
        else:
            # ── Dot: branch (comp line + DeGrained PLATE line) ────────────────
            dot_branch = nuke.createNode("Dot", inpanel=False)
            dot_branch.setInput(0, read_plates)
            self._place(dot_branch, "branch", origin)

            # ── Dot: ACEScg working ───────────────────────────────────────────
            dot_working = nuke.createNode("Dot", inpanel=False)
            dot_working.setInput(0, dot_branch)
            dot_working["label"].setValue("ACEScg working")
            self._place(dot_working, "working", origin)

            # STUDIO_NO_VFX_PULL_READ_2026_10_28: no VFX PULL placeholder Read / Merge any more.

            # ── Regrain: DasGrain after the comp area (STUDIO_NEWFILE_REGRAIN_QC_2026_10_28) ──
            # Two steps of empty space below "ACEScg working" for the comp.
            comp_out = self._build_regrain(read_plates, dot_working, dot_branch, origin)

            # ── Dot: to Write ─────────────────────────────────────────────────
            dot_write = nuke.createNode("Dot", inpanel=False)
            dot_write.setInput(0, comp_out)
            dot_write["label"].setValue("to Write")
            self._place(dot_write, "to_write", origin)

        # ── Write: TK Write Node (Step-based EXR preset) ────────────
        # We use tk-nuke-writenode's WriteTank gizmo so the file path is
        # driven by the ep_nuke_shot_render_work template. The "Send to
        # Review" button knob is added to this node by the wiring near the
        # top of this module (via onUserCreate / onScriptLoad).
        #
        # STUDIO_STEP_WRITE_DEFAULTS_APPLIED_2026_09_16: preset now depends on the Step
        # (temp -> 16-bit DWAA, comp -> 32-bit) -- see WRITE_PRESET_BY_STEP
        # near the top of this file.
        write_preset = self._write_preset_for_step(context, fields)
        try:
            wn_app = self._get_write_node_app(engine)
            if wn_app is None:
                raise RuntimeError(
                    "tk-nuke-writenode app not available in engine.apps after retrying"
                )
            # Select dot_write first so the new node connects to it
            for sel in nuke.selectedNodes():
                sel.setSelected(False)
            dot_write.setSelected(True)
            # create_new_write_node creates the node but doesn't return it.
            # Snapshot the WriteTank nodes before/after to grab the new one.
            _existing = set(nuke.allNodes("WriteTank"))
            wn_app.create_new_write_node(write_preset)
            _new_nodes = [n for n in nuke.allNodes("WriteTank") if n not in _existing]
            if not _new_nodes:
                raise RuntimeError("create_new_write_node returned no new WriteTank node")
            write = _new_nodes[0]
            write["label"].setValue(
                "EXR OUTPUT\n(ACEScg linear — no bake)\n[%s]" % write_preset
            )
            self._place(write, "write", origin)
        except Exception as _wn_exc:
            # Fallback: if tk-nuke-writenode isn't loaded for some reason,
            # fall back to a plain Write node so the script still works.
            # Note: render-complete callback will NOT fire on this path.
            import traceback as _tb
            _trace = _tb.format_exc()
            # Use nuke.tprint (always goes to stdout, never silenced by Nuke
            # warning filters or Script Editor visibility timing) and write
            # to root-level error log too for guaranteed capture.
            nuke.tprint("[scene_op] tk-nuke-writenode fallback triggered. Exception was:\n%s" % _trace)
            self._debug_log(
                "tk-nuke-writenode fallback triggered for shot=%s. Exception: %r\n%s"
                % (shot, _wn_exc, _trace)
            )
            try:
                nuke.warning("[scene_op] tk-nuke-writenode fallback. See stdout for traceback. Error: %r" % _wn_exc)
            except Exception:
                pass
            write = nuke.createNode("Write", inpanel=False)
            write.setInput(0, dot_write)
            write["file"].setValue(self._p(render_path))
            write["file_type"].setValue("exr")
            write["raw"].setValue(True)
            write["colorspace"].setValue("raw")
            for _fb_knob, _fb_value in self._fallback_write_knobs_for_step(
                context, fields
            ).items():
                try:
                    write[_fb_knob].setValue(_fb_value)
                except Exception as _fb_exc:
                    self._debug_log(
                        "Fallback Write: could not set %s = %r: %r"
                        % (_fb_knob, _fb_value, _fb_exc)
                    )
            write["label"].setValue(
                "EXR OUTPUT (FALLBACK)\n(ACEScg linear — no bake)\n[%s]" % write_preset
            )
            self._place(write, "write", origin)

        if tpl is not None:  # STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21
            write.setXYpos(*tpl["write_pos"])

        # ── Viewer ────────────────────────────────────────────────────────
        viewer = nuke.createNode("Viewer", inpanel=False)
        viewer.setInput(0, dot_write)
        viewer["label"].setValue("Display via OCIO viewer LUT")
        self._place(viewer, "viewer", origin)
        if tpl is not None:  # STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21
            viewer.setXYpos(*tpl["viewer_pos"])
        self._apply_cut_marks(near=write)  # STUDIO_NEWFILE_CUT_MARKS_2026_10_28
        if tpl is None:  # template has its own Difference
            self._build_difference_qc(dot_working, dot_write, origin)  # STUDIO_NEWFILE_LAYOUT_2026_11_03
        self._auto_plate_lineup(context, engine)  # STUDIO_NEWFILE_AUTO_LINEUP_2026_11_16
        self._add_info_notes(context, origin, tpl)  # STUDIO_NEWFILE_DASGRAIN_TEMPLATE_2026_11_21

        plate_summary = (
            "\n".join(
                "  - %s (%d-%d)%s"
                % (s["label"], s["first"], s["last"],
                   ("" if i == 0 else "  [not wired]")
                   + ("  [older delivery]" if s.get("older") else ""))
                for i, s in enumerate(plate_sequences)
            )
            if plate_sequences
            else "  (none found in plates/ yet — placeholder Read added)"
        )

        nuke.message(
            "Color pipeline loaded for %s.\n\n"
            "Working space : ACEScg\n\n"
            "Frame range   : %s\n"
            "Cut (Viewer in/out) : %s\n\n"
            "Plates (plates/, assumed ACEScg linear, raw — no conversion):\n%s\n\n"
            "Write node : EXR in ACEScg linear — no color baked in\n"
            "  → %s\n"
            "  → preset: %s (Step-based default)\n\n"
            "Regrain (DasGrain) : %s\n"
            "QC (Difference)    : %s\n"
            "Plate Lineup       : %s\n"
            "Template           : %s\n"
            "BG01 instructions  : %s\n\n"
            "Nuke's project directory has been set to this shot's folder.\n\n"
            "Review QTs are generated externally from EXR renders.\n"
            "Viewer display is handled by the OCIO viewer LUT."
            % (shot, self._frame_range_summary(), self._cut_summary(),
               plate_summary, render_path,
               write_preset, self._regrain_summary(), self._qc_summary(),
               self._lineup_summary(), self._template_summary(),
               self._effect_summary())
        )
