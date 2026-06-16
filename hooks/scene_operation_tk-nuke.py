# Copyright (c) Studio. All Rights Reserved.
"""
scene_operation hook for tk-multi-workfiles2 / tk-nuke (episodic context).

Auto-versioning: on 'new' and 'version_up', scans the work area for existing
v### files and saves to the next available version.
File naming: 301_001_010_temp_v001.nk

Color pipeline (first open only):
  Builds a working node graph with:
    - Read node for camera plates (raw, LogC4 input)
    - OCIOColorSpace: LogC4 -> ACEScg
    - Dot (labelled "ACEScg working")
    - Merge2 <- Read node for VFX pull placeholder (raw, ACEScg)
    - Dot (labelled "to Write")
    - Write node (EXR, raw/ACEScg, no bake)
    - Viewer (OCIO display transform handles LogC4->CDL->LUT->Rec.709)

  Artists work in ACEScg. The viewer LUT handles display.
  EXR renders are output in ACEScg linear - no color baked in.
  Review QTs are generated externally from the EXR renders.

OCIO config: ACES 1.3
Update OCIO_CAMERA_INPUT to match your camera:
  ARRI LogC4  : "Input - ARRI - Curve - LogC4 - EI800"
  ARRI LogC3  : "Input - ARRI - Curve - LogC3 - EI800"
  RED Log3G10 : "Input - RED - Curve - Log3G10"
  Sony SLog3  : "Input - Sony - Curve - SLog3 - SGamut3"
"""

import os
import re
import glob

import nuke
import sgtk

# ---------------------------------------------------------------------------
# Render-complete dialog wiring
#
# render_complete_callback.py's register() call (nuke.addAfterRender) only
# fires for plain Write nodes. Our pipeline uses TK Write nodes
# (tk-nuke-writenode "WriteTank" gizmos), whose internal Write1 node has its
# own afterRender knob wired to tk_nuke_writenode's gizmo callback chain,
# which only runs whatever Python is in the gizmo's own tk_after_render
# knob - it does NOT trigger nuke.addAfterRender() callbacks registered at
# the module level. So instead we hook onUserCreate for WriteTank nodes and
# populate tk_after_render directly with a call into this module.
# ---------------------------------------------------------------------------
try:
    import os as _os, sys as _sys
    _hooks_dir = _os.path.dirname(_os.path.abspath(__file__))
    if _hooks_dir not in _sys.path:
        _sys.path.insert(0, _hooks_dir)
    import render_complete_callback
    render_complete_callback.register()

    _AFTER_RENDER_CMD = (
        "import sys; "
        "sys.path.insert(0, r'%s') if r'%s' not in sys.path else None; "
        "import render_complete_callback as _rcc; "
        "_rcc.run_render_complete(nuke.thisGroup())"
    ) % (_hooks_dir, _hooks_dir)

    def _wire_tk_after_render(*args, **kwargs):
        node = nuke.thisNode()
        if not node:
            return
        knob = node.knob("tk_after_render")
        if knob is not None:
            knob.setValue(_AFTER_RENDER_CMD)

    nuke.addOnUserCreate(_wire_tk_after_render, nodeClass="WriteTank")
    nuke.addOnScriptLoad(
        lambda: [
            n.knob("tk_after_render").setValue(_AFTER_RENDER_CMD)
            for n in nuke.allNodes("WriteTank")
            if n.knob("tk_after_render") is not None
        ]
    )
except Exception as _exc:
    nuke.warning("[render_complete] Could not register callback: %s" % _exc)

HookClass = sgtk.get_hook_baseclass()

OCIO_CAMERA_INPUT = "Input - ARRI - Curve - LogC4 - EI800"
OCIO_ACES_WORKING = "ACES - ACEScg"


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

        elif operation == "save":
            nuke.scriptSave()

        elif operation == "save_as":
            old = nuke.root().name()
            nuke.scriptSaveAs(file_path, overwrite=1)
            if old != file_path:
                if hasattr(engine, "save_context_to_script"):
                    engine.save_context_to_script()

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
            if self._is_first_version(context):
                self._build_color_template(context)
                nuke.scriptSave()
            return resolved

        elif operation == "version_up":
            resolved = self._resolve_next_version(context)
            self._ensure_dir(resolved)
            nuke.scriptSaveAs(resolved, overwrite=0)
            if hasattr(engine, "save_context_to_script"):
                engine.save_context_to_script()
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

    # -------------------------------------------------------------------------
    # Color template builder
    # -------------------------------------------------------------------------

    def _build_color_template(self, context):
        tk     = self.parent.sgtk
        fields = self._fields(context)

        shot = fields.get("Shot", "SHOT")

        plate_path = self._safe_resolve(
            tk, "ep_shot_plates", fields,
            "PLACEHOLDER/plates/%s.####.exr" % shot)

        render_template = tk.templates.get("ep_nuke_shot_render_work")
        if render_template:
            render_fields = dict(fields)
            render_fields["nuke.output"] = "beauty"
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
        x_main  = 0
        y_step  = 130

        y = y0

        # ── Read: Camera plates (raw, LogC4) ──────────────────────────────
        read_plates = nuke.createNode("Read", inpanel=False)
        read_plates["file"].setValue(self._p(plate_path))
        read_plates["raw"].setValue(True)
        read_plates["colorspace"].setValue("raw")
        read_plates["label"].setValue("CAMERA PLATES\n(raw LogC4)\n[value file]")
        read_plates.setXYpos(x_main, y)

        # ── OCIOColorSpace: LogC4 -> ACEScg ───────────────────────────────
        y += y_step
        cs_in = nuke.createNode("OCIOColorSpace", inpanel=False)
        cs_in.setInput(0, read_plates)
        cs_in["in_colorspace"].setValue(OCIO_CAMERA_INPUT)
        cs_in["out_colorspace"].setValue(OCIO_ACES_WORKING)
        cs_in["label"].setValue("LogC4 → ACEScg")
        cs_in.setXYpos(x_main, y)

        # ── Dot: ACEScg working ───────────────────────────────────────────
        y += y_step
        dot_working = nuke.createNode("Dot", inpanel=False)
        dot_working.setInput(0, cs_in)
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

        # ── Write: EXR in ACEScg (no color bake) ─────────────────────────
        y += y_step
        write = nuke.createNode("Write", inpanel=False)
        write.setInput(0, dot_write)
        write["file"].setValue(self._p(render_path))
        write["file_type"].setValue("exr")
        write["raw"].setValue(True)
        write["colorspace"].setValue("raw")
        write["label"].setValue("EXR OUTPUT\n(ACEScg linear — no bake)\n[value file]")
        write.setXYpos(x_main, y)

        # ── Viewer ────────────────────────────────────────────────────────
        y += y_step
        viewer = nuke.createNode("Viewer", inpanel=False)
        viewer.setInput(0, dot_write)
        viewer["label"].setValue("Display via OCIO viewer LUT")
        viewer.setXYpos(x_main, y)

        nuke.message(
            "Color pipeline loaded for %s.\n\n"
            "Working space : ACEScg\n\n"
            "Camera Plates : %s\n"
            "  → raw read, LogC4 converted to ACEScg on input\n\n"
            "VFX Pulls : Replace path in VFX PULL Read node\n"
            "  → raw read, already ACEScg — no conversion needed\n\n"
            "Write node : EXR in ACEScg linear — no color baked in\n"
            "  → %s\n\n"
            "Review QTs are generated externally from EXR renders.\n"
            "Viewer display is handled by the OCIO viewer LUT."
            % (shot, plate_path, render_path)
        )

