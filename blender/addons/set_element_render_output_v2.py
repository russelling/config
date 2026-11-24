bl_info = {
    "name": "Flow - Set Element Render Output",
    "description": (
        "Resolves the pipeline's ep_blender_shot_render_work template from "
        "the current Toolkit context and points the scene's render output "
        "at it, so CG-element pre-renders (smoke, debris, etc.) always land "
        "in the right place with the right name - no manually typed paths. "
        "Optionally enables a standard set of compositing passes (Normal, "
        "Depth, Cryptomatte Object/Material, Diffuse/Glossy/Emission) and "
        "writes everything to a single multi-layer EXR."
    ),
    "author": "Buffalo VFX",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "category": "Pipeline",
}

import os
import re

import bpy

# STUDIO_LAZY_SGTK_2026_11_02: sgtk is imported inside execute(), not here. Blender
# enables saved add-ons before tk-blender has put sgtk on sys.path, so a
# module-level import failed at every launch and Blender switched the
# add-on off (and saved it off).


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

    # STUDIO_TEMP_SUFFIX_2026_09_26: temp ("<element>_temp_v###") and final
    # ("<element>_v###") renders of an element share ONE version counter, so
    # a v number never means two different renders.
    prefix = "{Shot}_{vendor_code}_{Step}_{output}_".format(
        Shot=element_fields["Shot"],
        vendor_code=element_fields["vendor_code"],
        Step=element_fields["Step"],
        output=element_fields["output"],
    )

    highest = 0
    if os.path.isdir(elements_root):
        for entry in os.listdir(elements_root):
            if entry.startswith(prefix):
                match = re.match(r"^(?:temp_)?v(\d+)$", entry[len(prefix):])
                if match:
                    highest = max(highest, int(match.group(1)))

    return highest + 1


# Standard comp-pass set requested for CG elements feeding Nuke: Normal +
# Depth/Z, Cryptomatte (Object/Material), and Diffuse/Glossy/Emission
# separations (so a compositor can re-balance lighting without a re-render).
# All of these are plain ViewLayer booleans as of Blender 4.2 (EEVEE Next
# unified most pass availability with Cycles) - if a given engine doesn't
# support one, setting it is a silent no-op rather than an error, so no
# per-engine branching is needed here.
def _enable_comp_passes(view_layer):
    # Normal + Depth/Z
    view_layer.use_pass_normal = True
    view_layer.use_pass_z = True

    # Cryptomatte (Object/Material) - Asset-level crypto deliberately left
    # off; add view_layer.use_pass_cryptomatte_asset = True if Mark wants it.
    view_layer.use_pass_cryptomatte_object = True
    view_layer.use_pass_cryptomatte_material = True

    # Diffuse / Glossy / Emission separations
    view_layer.use_pass_diffuse_color = True
    view_layer.use_pass_diffuse_direct = True
    view_layer.use_pass_diffuse_indirect = True
    view_layer.use_pass_glossy_color = True
    view_layer.use_pass_glossy_direct = True
    view_layer.use_pass_glossy_indirect = True
    view_layer.use_pass_emit = True


# ---------------------------------------------------------------------------
# Render quality presets  (STUDIO_RENDER_QUALITY_PRESETS_2026_09_26)
#
# Both presets: Cycles on the best GPU backend found (OptiX first), OptiX
# denoiser when an OptiX device exists (else OpenImageDenoise - reported, not
# silent), transparent film (CG elements need alpha over the plate), EXR
# written in ACEScg, 16-bit half colour passes. Blender only halves COLOUR
# passes - Depth, Normal and Cryptomatte are always written 32-bit float
# (verified: blenkernel image_save.cc, "We only store RGBA passes as half
# float"), so half is safe with the comp passes.
#
# Caustics off + Filter Glossy 1.0 + indirect clamp 10 remove most fireflies
# for very little visible change; adaptive sampling stops clean pixels early,
# so the sample count is a cap, not a cost.
# ---------------------------------------------------------------------------

RENDER_PRESETS = {
    "TEMP": {
        "resolution_percentage": 50,
        "samples": 256,
        "adaptive_threshold": 0.05,
        "max_bounces": 8,
        "diffuse_bounces": 3,
        "glossy_bounces": 3,
        "transmission_bounces": 8,
        "volume_bounces": 1,
        "transparent_max_bounces": 16,
        "volume_step_rate": 2.0,
        "oidn_quality": "BALANCED",
        "exr_codec": "DWAA",
    },
    "FINAL": {
        "resolution_percentage": 100,
        "samples": 1024,
        "adaptive_threshold": 0.01,
        "max_bounces": 12,
        "diffuse_bounces": 4,
        "glossy_bounces": 4,
        "transmission_bounces": 12,
        "volume_bounces": 2,
        "transparent_max_bounces": 32,
        "volume_step_rate": 1.0,
        "oidn_quality": "HIGH",
        "exr_codec": "ZIP",
    },
}

# The EXR output colour space. First name the active OCIO config accepts
# wins; Blender's bundled config calls it "ACEScg", studio ACES configs often
# "ACES - ACEScg".
OUTPUT_COLORSPACE_CANDIDATES = ("ACEScg", "ACES - ACEScg", "lin_ap1_scene")

# GPU backends in order of preference. OptiX first: it is also what makes the
# OptiX denoiser available at all.
GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")


def _set(obj, attr, value, problems):
    """setattr that records, rather than raises, on a missing/rejected value."""
    try:
        setattr(obj, attr, value)
    except Exception as exc:
        problems.append("%s = %r not applied (%s)" % (attr, value, exc))


def _setup_gpu(problems):
    """Pick the best GPU backend in Preferences > System and enable its GPUs.
    Returns the backend name, or None if there is no usable GPU."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception as exc:
        problems.append("Cycles preferences unavailable (%s) - rendering on CPU" % exc)
        return None
    for backend in GPU_BACKENDS:
        try:
            devices = prefs.get_devices_for_type(backend)
        except Exception:
            continue
        gpus = [d for d in devices if d.type != "CPU"]
        if not gpus:
            continue
        try:
            prefs.compute_device_type = backend
        except Exception:
            continue
        for device in devices:
            device.use = device.type != "CPU"
        return backend
    problems.append("No GPU found for Cycles - rendering on CPU")
    return None


def _set_output_colorspace(image_settings, problems):
    """Write the EXR in ACEScg. Returns the colour space name used, or None."""
    try:
        image_settings.color_management = "OVERRIDE"
    except Exception as exc:
        problems.append("Could not override output colour management (%s)" % exc)
        return None
    for name in OUTPUT_COLORSPACE_CANDIDATES:
        try:
            image_settings.linear_colorspace_settings.name = name
        except Exception:
            continue
        if image_settings.linear_colorspace_settings.name == name:
            return name
    image_settings.color_management = "FOLLOW_SCENE"
    problems.append("This OCIO config has no ACEScg colour space - EXR left in "
                    "the scene's linear space")
    return None


def _apply_render_quality(scene, quality, problems):
    """Apply a RENDER_PRESETS entry to the scene. Returns a one-line summary."""
    preset = RENDER_PRESETS[quality]
    render = scene.render
    cycles = scene.cycles

    render.engine = "CYCLES"
    backend = _setup_gpu(problems)
    _set(cycles, "device", "GPU" if backend else "CPU", problems)

    _set(render, "resolution_percentage", preset["resolution_percentage"], problems)
    _set(render, "film_transparent", True, problems)
    _set(render, "use_persistent_data", True, problems)

    # Sampling: the sample count is a cap - adaptive sampling stops each
    # pixel once it is under the noise threshold.
    _set(cycles, "samples", preset["samples"], problems)
    _set(cycles, "use_adaptive_sampling", True, problems)
    _set(cycles, "adaptive_threshold", preset["adaptive_threshold"], problems)
    _set(cycles, "adaptive_min_samples", 0, problems)  # 0 = automatic
    _set(cycles, "time_limit", 0.0, problems)

    # Denoise. The OPTIX enum item only exists when an OptiX device is set up
    # in Preferences, so try it and fall back loudly.
    _set(cycles, "use_denoising", True, problems)
    denoiser = "OPTIX"
    try:
        cycles.denoiser = "OPTIX"
    except Exception:
        denoiser = "OPENIMAGEDENOISE"
        problems.append("OptiX denoiser unavailable on this machine (needs an "
                        "NVIDIA RTX GPU) - using OpenImageDenoise")
        _set(cycles, "denoiser", denoiser, problems)
        _set(cycles, "denoising_prefilter", "ACCURATE", problems)
        _set(cycles, "denoising_quality", preset["oidn_quality"], problems)
    _set(cycles, "denoising_input_passes", "RGB_ALBEDO_NORMAL", problems)
    _set(cycles, "denoising_use_gpu", True, problems)

    # Light paths
    for key in ("max_bounces", "diffuse_bounces", "glossy_bounces",
                "transmission_bounces", "volume_bounces",
                "transparent_max_bounces"):
        _set(cycles, key, preset[key], problems)
    _set(cycles, "sample_clamp_direct", 0.0, problems)
    _set(cycles, "sample_clamp_indirect", 10.0, problems)
    _set(cycles, "blur_glossy", 1.0, problems)
    _set(cycles, "caustics_reflective", False, problems)
    _set(cycles, "caustics_refractive", False, problems)
    _set(cycles, "use_light_tree", True, problems)
    _set(cycles, "volume_step_rate", preset["volume_step_rate"], problems)

    # EXR storage: half colour passes (data passes stay float - see above).
    image_settings = render.image_settings
    _set(image_settings, "color_depth", "16", problems)
    _set(image_settings, "exr_codec", preset["exr_codec"], problems)
    colorspace = _set_output_colorspace(image_settings, problems)

    return "%s: Cycles %s, %d%%, max %d samples, %s denoise, EXR %s" % (
        quality.title(), backend or "CPU", preset["resolution_percentage"],
        preset["samples"], "OptiX" if denoiser == "OPTIX" else "OIDN",
        colorspace or "scene linear",
    )


class FLOW_OT_set_element_render_output(bpy.types.Operator):
    """Resolve the pipeline render path for a CG element and set it as this scene's render output"""

    bl_idname = "flow.set_element_render_output"
    bl_label = "Set Element Render Output..."
    bl_options = {"REGISTER"}

    element_name: bpy.props.StringProperty(
        name="Element Name",
        description="Descriptive name for this CG element (e.g. smoke, debris) - alphanumeric only",
    )

    include_comp_passes: bpy.props.BoolProperty(
        name="Include Compositing Passes",
        description=(
            "Enable Normal, Depth (Z), Cryptomatte (Object/Material), and "
            "Diffuse/Glossy/Emission passes on the active View Layer, and "
            "write to a single multi-layer EXR instead of a beauty-only "
            "file. Turn off for a quick beauty-only test render."
        ),
        default=True,
    )

    render_quality: bpy.props.EnumProperty(
        name="Render Quality",
        description="Cycles settings to apply along with the output path",
        items=[
            ("TEMP", "Temp",
             "Fast temp render: 50% resolution, max 256 samples, OptiX "
             "denoise, lighter light paths, 16-bit DWAA EXR"),
            ("FINAL", "Final",
             "Full-quality element for comp: 100% resolution, max 1024 "
             "samples, OptiX denoise, fuller light paths, 16-bit ZIP EXR"),
        ],
        default="TEMP",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        try:
            import sgtk
        except ImportError:
            self.report({"ERROR"}, "Flow/Toolkit is not loaded in this Blender - "
                        "launch Blender from Flow Production Tracking.")
            return {"CANCELLED"}
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

        # Temp renders are tagged "_temp" (optional blender.quality key,
        # alias "quality"); Final renders leave the key out.
        if self.render_quality == "TEMP":
            fields["quality"] = "temp"
        else:
            fields.pop("quality", None)

        fields["version"] = _next_version(folder_template, fields, tk)

        folder = folder_template.apply_fields(fields)
        os.makedirs(folder, exist_ok=True)

        basename = "{Shot}_{vendor_code}_{Step}_{output}{quality}_v{version:0{pad}d}".format(
            Shot=fields["Shot"],
            vendor_code=fields["vendor_code"],
            Step=fields["Step"],
            output=element_name,
            quality="_" + fields["quality"] if fields.get("quality") else "",
            version=fields["version"],
            pad=VERSION_PAD,
        )
        # The folder came from the template; the file name is built by hand
        # above. Refuse rather than render into a mismatched name.
        if os.path.basename(folder.rstrip("/\\")) != basename:
            self.report({"ERROR"}, "Template folder %r and file name %r disagree - "
                        "check ep_blender_shot_render_work in core/templates.yml"
                        % (os.path.basename(folder), basename))
            return {"CANCELLED"}

        # Trailing "." with no frame token - Blender appends the frame
        # number (default 4-digit padding) and extension itself, giving
        # exactly "<basename>.0001.exr" per the template's
        # "..._v{version}.{SEQ}.exr" pattern.
        render_prefix = os.path.join(folder, basename + ".")

        scene = context.scene
        scene.render.filepath = render_prefix

        image_settings = scene.render.image_settings
        # Blender 4.2+ added ImageFormatSettings.media_type ('IMAGE' /
        # 'MULTI_LAYER_IMAGE' / 'VIDEO'), which restricts which
        # file_format enum values are legal - "OPEN_EXR" only under
        # 'IMAGE', "OPEN_EXR_MULTILAYER" only under 'MULTI_LAYER_IMAGE'.
        # Mismatching the two raises a TypeError (hit once already - see
        # the project doc's bug #4). Older Blender (pre-4.2) has no
        # media_type attribute at all, so guard with hasattr.
        if self.include_comp_passes:
            _enable_comp_passes(context.view_layer)
            if hasattr(image_settings, "media_type"):
                image_settings.media_type = "MULTI_LAYER_IMAGE"
            image_settings.file_format = "OPEN_EXR_MULTILAYER"
        else:
            if hasattr(image_settings, "media_type"):
                image_settings.media_type = "IMAGE"
            image_settings.file_format = "OPEN_EXR"

        scene.render.use_file_extension = True

        # Render engine / quality / ACEScg output. After the file format is
        # chosen above - exr_codec and color_depth depend on it.
        problems = []
        summary = _apply_render_quality(scene, self.render_quality, problems)
        for problem in problems:
            self.report({"WARNING"}, problem)

        passes = ("multi-layer EXR + comp passes" if self.include_comp_passes
                  else "beauty only")
        self.report({"INFO"}, "Render output set ({}; {}): {}".format(
            passes, summary, render_prefix))
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
