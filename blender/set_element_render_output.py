bl_info = {
    "name": "Flow - Set Element Render Output",
    "description": (
        "Resolves the pipeline's ep_blender_shot_render_work template from "
        "the current Toolkit context and points the scene's render output "
        "at it, so CG-element pre-renders (smoke, debris, etc.) always land "
        "in the right place with the right name - no manually typed paths."
    ),
    "author": "Buffalo VFX",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "category": "Pipeline",
}

import os
import re

import bpy
import sgtk


TEMPLATE_NAME = "ep_blender_shot_render_work"

# Default vendor_code matches the same key's default in core/templates.yml -
# duplicated here rather than relied on, since apply_fields() does not
# automatically pull in a key's "default:" for a missing field.
DEFAULT_VENDOR_CODE = "INH"

# Must match keys.version.format_spec ("03") in core/templates.yml.
VERSION_PAD = 3


def _next_version(folder_template, element_fields, tk):
    """
    Scan disk for existing '<Shot>_<vendor_code>_<Step>_<element>_v###'
    folders and return one past the highest version found (1 if none exist).
    """
    # Resolve the render/elements folder for this shot WITHOUT a version,
    # by asking the template's grandparent (".../render/elements") - a
    # static path once Episode/Scene/Shot are known, no version/element
    # baked in yet.
    elements_root_template = folder_template.parent
    elements_root = elements_root_template.apply_fields(element_fields)

    prefix = "{Shot}_{vendor_code}_{Step}_{output}_v".format(
        Shot=element_fields["Shot"],
        vendor_code=element_fields["vendor_code"],
        Step=element_fields["Step"],
        output=element_fields["output"],
    )

    highest = 0
    if os.path.isdir(elements_root):
        for entry in os.listdir(elements_root):
            if entry.startswith(prefix):
                match = re.match(r".*_v(\d+)$", entry)
                if match:
                    highest = max(highest, int(match.group(1)))

    return highest + 1


class FLOW_OT_set_element_render_output(bpy.types.Operator):
    """Resolve the pipeline render path for a CG element and set it as this scene's render output"""

    bl_idname = "flow.set_element_render_output"
    bl_label = "Set Element Render Output..."
    bl_options = {"REGISTER"}

    element_name: bpy.props.StringProperty(
        name="Element Name",
        description="Descriptive name for this CG element (e.g. smoke, debris) - alphanumeric only",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        engine = sgtk.platform.current_engine()
        if engine is None:
            self.report({"ERROR"}, "Flow/Toolkit engine is not running - open this file through FPTR.")
            return {"CANCELLED"}

        tk = engine.sgtk
        ctx = engine.context

        if not ctx.entity or ctx.entity.get("type") != "Shot":
            self.report({"ERROR"}, "This only works in a Shot context - open the scene from a Shot's Task.")
            return {"CANCELLED"}

        if not ctx.step:
            self.report({"ERROR"}, "No Step in the current context - open the scene from a Task, not just the Shot.")
            return {"CANCELLED"}

        element_name = (self.element_name or "").strip()
        if not element_name.isalnum():
            self.report({"ERROR"}, "Element name must be a single alphanumeric word, e.g. 'smoke' or 'debris01'.")
            return {"CANCELLED"}

        template = tk.templates[TEMPLATE_NAME]
        folder_template = template.parent

        fields = ctx.as_template_fields(template)
        fields["vendor_code"] = fields.get("vendor_code") or DEFAULT_VENDOR_CODE

        # ctx.as_template_fields() resolves "Step" from ctx.step's cached
        # dict, which doesn't always carry short_name (context objects are
        # often built with only type/id/name) - if it's missing, look it up
        # in ShotGrid directly rather than assume it's there.
        if "Step" not in fields:
            step_entity = tk.shotgun.find_one("Step", [["id", "is", ctx.step["id"]]], ["short_name"])
            if not step_entity or not step_entity.get("short_name"):
                self.report({"ERROR"}, "Could not resolve a short_name for the current Step in ShotGrid.")
                return {"CANCELLED"}
            fields["Step"] = step_entity["short_name"]

        # The "blender.output" key in core/templates.yml declares
        # "alias: output" -- Toolkit's TemplateKey treats the alias as the
        # key's real internal name (see templatekey.py: "The alias becomes
        # the key's name and is used internally by Templates as the key's
        # name"), even though the path definition spells the token
        # "{blender.output}". So the fields dict here must be keyed "output",
        # not "blender.output" -- using the yaml key name instead raises
        # "required fields were missing from the input: ['output']".
        fields["output"] = element_name

        fields["version"] = _next_version(folder_template, fields, tk)

        folder = folder_template.apply_fields(fields)
        os.makedirs(folder, exist_ok=True)

        basename = "{Shot}_{vendor_code}_{Step}_{output}_v{version:0{pad}d}".format(
            Shot=fields["Shot"],
            vendor_code=fields["vendor_code"],
            Step=fields["Step"],
            output=element_name,
            version=fields["version"],
            pad=VERSION_PAD,
        )

        # Trailing "." with no frame token - Blender appends the frame
        # number (default 4-digit padding) and extension itself, giving
        # exactly "<basename>.0001.exr" per the template's
        # "..._v{version}.{SEQ}.exr" pattern.
        render_prefix = os.path.join(folder, basename + ".")

        scene = context.scene
        scene.render.filepath = render_prefix
        scene.render.image_settings.file_format = "OPEN_EXR"
        scene.render.use_file_extension = True

        self.report({"INFO"}, "Render output set: {}".format(render_prefix))
        return {"FINISHED"}


def _menu_draw(self, context):
    self.layout.operator(FLOW_OT_set_element_render_output.bl_idname, icon="RENDER_ANIMATION")


def register():
    bpy.utils.register_class(FLOW_OT_set_element_render_output)
    bpy.types.TOPBAR_MT_render.append(_menu_draw)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(_menu_draw)
    bpy.utils.unregister_class(FLOW_OT_set_element_render_output)


if __name__ == "__main__":
    register()
