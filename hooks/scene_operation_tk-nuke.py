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
    - Merge2 <- Read node for VFX pull placeholder (raw, ACEScg)
    - Dot (labelled "to Write")
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

First-launch reliability:
  Historically, the color template would sometimes fail to build (or
  silently fall back to a plain Write node instead of the TK Write node)
  on the very first "New File" of a session, requiring a second attempt.
  The best-understood contributor was `tk-multi-workfiles2`'s
  `launch_at_startup: true` setting auto-opening the New/Open dialog
  during Nuke startup - before every app instance (tk-nuke-writenode is
  registered after tk-multi-workfiles2 in env/includes/settings/
  tk-nuke-episodic.yml) is guaranteed to have finished initializing. That
  setting has been switched to `false` (see that file) so the artist's
  first real "New File" click always happens after Nuke/the engine has
  settled. On top of that, this hook now (a) retries briefly for the
  tk-nuke-writenode app instance instead of failing immediately, and (b)
  wraps the whole template build in a try/except that logs a full
  traceback to scene_op_debug.log and tells the artist in-app if it still
  fails, instead of leaving them looking at a blank script with no
  explanation.

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
except Exception as _exc:
    nuke.warning("[render_complete] Could not register Send to Review button: %s" % _exc)

HookClass = sgtk.get_hook_baseclass()

OCIO_CAMERA_INPUT = "Input - ARRI - Curve - LogC4 - EI800"
OCIO_ACES_WORKING = "ACES - ACEScg"

# Matches an EXR frame like "301_001_010_takeA.1001.exr" or
# "301_001_010_takeA_1001.exr" -> base="301_001_010_takeA", frame="1001".
# Anything under plates/ that doesn't match this is treated as a single,
# non-sequence frame instead of being skipped.
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
            if self._is_first_version(context):
                self._safe_build_color_template(context, engine)
                nuke.scriptSave()
            return resolved

        elif operation == "prepare_new":
            # tk-multi-workfiles2 v0.16+ "New File" action calls this op
            # (after issuing a separate 'reset' first). At this point the
            # script is empty and the context is the new shot/task.
            #
            # IMPORTANT: tk-nuke-writenode.create_new_write_node() needs a
            # saved script to resolve its render template fields. So save
            # the script to its versioned path BEFORE building the graph,
            # mirroring what the legacy 'new' branch does.
            if self._is_first_version(context):
                resolved = self._resolve_new_path(context)
                self._ensure_dir(resolved)
                nuke.scriptSaveAs(resolved, overwrite=0)
                if hasattr(engine, "save_context_to_script"):
                    engine.save_context_to_script()
                self._set_project_directory(context)
                self._safe_build_color_template(context, engine)
                nuke.scriptSave()
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
            log_dir = "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config/logs"
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
        """Point Nuke's root 'project_directory' knob at the shot's folder so
        that Read/Write file browsers (and any relative paths) for new
        elements default there instead of wherever was last browsed."""
        try:
            shot_dir = self._shot_base_dir(context)
            if shot_dir and os.path.isdir(shot_dir):
                nuke.root()["project_directory"].setValue(self._p(shot_dir))
        except Exception as exc:
            self._debug_log("Could not set project_directory: %r" % exc)

    # -------------------------------------------------------------------------
    # Plates scanning
    # -------------------------------------------------------------------------

    def _scan_plate_sequences(self, plates_dir):
        """Scan a shot's plates/ folder on disk and group the EXRs in it into
        sequences (or single frames where no frame number is found).

        Assumes every EXR under plates/ is already scene-linear ACEScg - no
        color transform is applied to any of them (see module docstring).

        Returns a list of dicts, sorted with the largest sequence first:
            {"nuke_path": <Read-ready path, '#'-padded frame token for
                            sequences>,
             "first": int, "last": int, "label": <name for the node label>,
             "count": int}
        """
        if not plates_dir or not os.path.isdir(plates_dir):
            return []

        sequences = {}
        singles = []
        for fname in sorted(os.listdir(plates_dir)):
            if not fname.lower().endswith(".exr"):
                continue
            m = PLATE_SEQ_RE.match(fname)
            if m:
                base = m.group("base")
                pad = len(m.group("frame"))
                sequences.setdefault((base, pad), []).append(int(m.group("frame")))
            else:
                singles.append(fname)

        results = []
        for (base, pad), frames in sequences.items():
            frames.sort()
            hashes = "#" * pad
            nuke_path = os.path.join(plates_dir, "%s.%s.exr" % (base, hashes))
            results.append({
                "nuke_path": self._p(nuke_path),
                "first": frames[0],
                "last": frames[-1],
                "label": base,
                "count": len(frames),
            })
        for fname in singles:
            results.append({
                "nuke_path": self._p(os.path.join(plates_dir, fname)),
                "first": 1,
                "last": 1,
                "label": os.path.splitext(fname)[0],
                "count": 1,
            })

        results.sort(key=lambda r: r["count"], reverse=True)
        return results

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

        shot_dir = self._shot_base_dir(context)
        plates_dir = os.path.join(shot_dir, "plates") if shot_dir else None
        plate_sequences = self._scan_plate_sequences(plates_dir)

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
        x_vfx   = 300
        x_extra = -300
        x_main  = 0
        y_step  = 130

        y = y0

        # ── Read: plate(s) found on disk in plates/ (raw, ACEScg linear) ──
        # Every EXR in plates/ is assumed to already be scene-linear ACEScg
        # (see module docstring) - no OCIO conversion is applied here. The
        # largest sequence found becomes the primary plate wired into the
        # graph; any others found are added alongside, unwired, for the
        # artist to hook up manually.
        if plate_sequences:
            primary = plate_sequences[0]
            read_plates = nuke.createNode("Read", inpanel=False)
            read_plates["file"].setValue(primary["nuke_path"])
            read_plates["first"].setValue(primary["first"])
            read_plates["last"].setValue(primary["last"])
            read_plates["origfirst"].setValue(primary["first"])
            read_plates["origlast"].setValue(primary["last"])
            read_plates["raw"].setValue(True)
            read_plates["colorspace"].setValue("raw")
            read_plates["label"].setValue(
                "PLATE: %s\n(ACEScg linear, raw — %d-%d)"
                % (primary["label"], primary["first"], primary["last"])
            )
            read_plates.setXYpos(x_main, y)

            for i, seq in enumerate(plate_sequences[1:], start=1):
                extra = nuke.createNode("Read", inpanel=False)
                extra["file"].setValue(seq["nuke_path"])
                extra["first"].setValue(seq["first"])
                extra["last"].setValue(seq["last"])
                extra["origfirst"].setValue(seq["first"])
                extra["origlast"].setValue(seq["last"])
                extra["raw"].setValue(True)
                extra["colorspace"].setValue("raw")
                extra["label"].setValue(
                    "PLATE (extra): %s\n(ACEScg linear, raw — %d-%d)\nNot wired — connect manually"
                    % (seq["label"], seq["first"], seq["last"])
                )
                extra.setXYpos(x_extra, y0 + (i - 1) * y_step)
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

        # ── Dot: ACEScg working ───────────────────────────────────────────
        y += y_step
        dot_working = nuke.createNode("Dot", inpanel=False)
        dot_working.setInput(0, read_plates)
        dot_working["label"].setValue("ACEScg working")
        dot_working.setXYpos(x_main + 34, y)

        # ── Read: VFX pull placeholder (raw, already ACEScg) ─────────────
        read_vfx = nuke.createNode("Read", inpanel=False)
        read_vfx["file"].setValue("REPLACE_WITH_VFX_PULL_PATH.####.exr")
        read_vfx["raw"].setValue(True)
        read_vfx["colorspace"].setValue("raw")
        read_vfx["label"].setValue("VFX PULL\n(raw ACEScg — no conversion)\n[value file]")
        read_vfx.setXYpos(x_vfx, y - y_step)

        # ── Merge: plates + VFX in ACEScg ────────────────────────────────
        y += y_step
        merge = nuke.createNode("Merge2", inpanel=False)
        merge.setInput(0, dot_working)
        merge.setInput(1, read_vfx)
        merge["label"].setValue("Working: ACEScg")
        merge.setXYpos(x_main, y)

        # ── Dot: to Write ─────────────────────────────────────────────────
        y += y_step
        dot_write = nuke.createNode("Dot", inpanel=False)
        dot_write.setInput(0, merge)
        dot_write["label"].setValue("to Write")
        dot_write.setXYpos(x_main + 34, y)

        # ── Write: TK Write Node (Primary EXR 32-bit) ────────────────────
        # We use tk-nuke-writenode's WriteTank gizmo so the file path is
        # driven by the ep_nuke_shot_render_work template. The "Send to
        # Review" button knob is added to this node by the wiring near the
        # top of this module (via onUserCreate / onScriptLoad).
        y += y_step
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
            wn_app.create_new_write_node("Primary EXR (32-bit)")
            _new_nodes = [n for n in nuke.allNodes("WriteTank") if n not in _existing]
            if not _new_nodes:
                raise RuntimeError("create_new_write_node returned no new WriteTank node")
            write = _new_nodes[0]
            write["label"].setValue("EXR OUTPUT\n(ACEScg linear — no bake)\n[render template-driven]")
            write.setXYpos(x_main, y)
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
            write["label"].setValue("EXR OUTPUT (FALLBACK)\n(ACEScg linear — no bake)")
            write.setXYpos(x_main, y)

        # ── Viewer ────────────────────────────────────────────────────────
        y += y_step
        viewer = nuke.createNode("Viewer", inpanel=False)
        viewer.setInput(0, dot_write)
        viewer["label"].setValue("Display via OCIO viewer LUT")
        viewer.setXYpos(x_main, y)

        plate_summary = (
            "\n".join(
                "  - %s (%d-%d)%s"
                % (s["label"], s["first"], s["last"], "" if i == 0 else "  [not wired]")
                for i, s in enumerate(plate_sequences)
            )
            if plate_sequences
            else "  (none found in plates/ yet — placeholder Read added)"
        )

        nuke.message(
            "Color pipeline loaded for %s.\n\n"
            "Working space : ACEScg\n\n"
            "Plates (plates/, assumed ACEScg linear, raw — no conversion):\n%s\n\n"
            "VFX Pulls : Replace path in VFX PULL Read node\n"
            "  → raw read, already ACEScg — no conversion needed\n\n"
            "Write node : EXR in ACEScg linear — no color baked in\n"
            "  → %s\n\n"
            "Nuke's project directory has been set to this shot's folder.\n\n"
            "Review QTs are generated externally from EXR renders.\n"
            "Viewer display is handled by the OCIO viewer LUT."
            % (shot, plate_summary, render_path)
        )
