bl_info = {
    "name": "Flow - Set Element Render Output (V-Ray)",
    "description": (
        "V-Ray for Blender version of 'Set Element Render Output'. Resolves "
        "the pipeline's ep_blender_shot_render_work template from the current "
        "Toolkit context and points V-Ray's image output at it, so CG-element "
        "pre-renders always land in the right place with the right name. "
        "Optionally adds a standard set of V-Ray render channels (Normals, "
        "Z-Depth, Cryptomatte Object/Material, Diffuse/Lighting/GI, "
        "Reflection/Specular, Self-Illumination) written into a single "
        "multi-channel EXR."
    ),
    "author": "Buffalo VFX",
    "version": (2, 0, 0),
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
#
# Same rule for V-Ray: vray_blender is imported lazily inside the helpers, so
# this add-on still loads (and reports a clear error) on a Blender without
# V-Ray installed.


TEMPLATE_NAME = "ep_blender_shot_render_work"

# Default vendor_code matches the same key's default in core/templates.yml -
# duplicated here rather than relied on, since apply_fields() does not
# automatically pull in a key's "default:" for a missing field.
DEFAULT_VENDOR_CODE = "INH"

# Must match keys.version.format_spec ("03") in core/templates.yml.
VERSION_PAD = 3

# Frame number padding in the output file name. Matches the template's
# {SEQ} (4 digits).
FRAME_PAD = 4

VRAY_ENGINE = "VRAY_RENDER_RT"

# SettingsOutput.img_format item for OpenEXR (V-Ray's own enum, from
# plugins_desc/settings/SettingsOutput.custom.json: 0 PNG, 1 JPEG, 2 TIFF,
# 3 TGA, 4 SGI, 5 OpenEXR, 6 VRayImage). The Community Edition disables 5.
VRAY_IMG_FORMAT_EXR = "5"

# SettingsEXR.compression items.
EXR_COMPRESSION = {"ZIPS": "3", "ZIP": "4", "PIZ": "5", "DWAA": "9", "DWAB": "10"}

# SettingsUnitsInfo.rgb_color_space items: 1 sRGB, 2 ACEScg.
VRAY_RGB_ACESCG = "2"


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


def _set(obj, attr, value, problems):
    """setattr that records, rather than raises, on a missing/rejected value."""
    try:
        setattr(obj, attr, value)
    except Exception as exc:
        problems.append("%s = %r not applied (%s)" % (attr, value, exc))


# ---------------------------------------------------------------------------
# Render channels (V-Ray's AOVs)
#
# V-Ray has no view-layer pass booleans: channels are nodes in the WORLD node
# tree, plugged into the "Render Channels Container", which feeds the
# "Channels" input of the V-Ray World Output. A channel node that is not
# linked into the container is NOT exported.
#
# Cycles pass -> V-Ray channel used here:
#   Normal                         -> Normals
#   Depth (Z)                      -> Z-Depth (raw distance, channel named "Z")
#   Cryptomatte Object / Material  -> Cryptomatte, id_type 0 / 1
#   Diffuse Color/Direct/Indirect  -> Diffuse (filter) / Lighting / GI
#   Glossy Color/Direct/Indirect   -> Reflection Filter / Specular / Reflection
#   Emission                       -> Self-Illumination
#
# Every node this add-on creates is named "AOV_<x>", so running it again
# reuses the same nodes instead of piling up duplicates.
# ---------------------------------------------------------------------------

# (node name, node bl_idname, channel name written to the EXR, {plugin attr: value})
COMP_CHANNELS = (
    ("AOV_Normals", "VRayNodeRenderChannelNormals", "Normals", {}),
    # V-Ray GPU ignores depth_raw and ALWAYS remaps depth through
    # depth_black/depth_white (see vray_blender/exporting/world_export.py).
    # The default white of 1000 writes distance/1000; black 0 + white 1 +
    # clamp off makes it write the true distance in scene units on GPU and CPU.
    # force_infinity_black: empty background reads 0 rather than 1.0 (which
    # with white = 1 would look like a surface 1 m from camera).
    ("AOV_ZDepth", "VRayNodeRenderChannelZDepth", "Z",
     {"depth_raw": True, "depth_black": 0.0, "depth_white": 1.0,
      "depth_clamp": False, "force_infinity_black": True,
      "depth_from_camera": False, "filtering": False}),
    ("AOV_Crypto_Object", "VRayNodeRenderChannelCryptomatte", "CryptoObject",
     {"id_type": "0"}),
    ("AOV_Crypto_Material", "VRayNodeRenderChannelCryptomatte", "CryptoMaterial",
     {"id_type": "1"}),
    ("AOV_Diffuse", "VRayNodeRenderChannelDiffuse", "Diffuse", {}),
    ("AOV_Lighting", "VRayNodeRenderChannelLighting", "Lighting", {}),
    ("AOV_GI", "VRayNodeRenderChannelGI", "GI", {}),
    ("AOV_ReflectionFilter", "VRayNodeRenderChannelReflectionFilter", "ReflectionFilter", {}),
    ("AOV_Specular", "VRayNodeRenderChannelSpecular", "Specular", {}),
    ("AOV_Reflection", "VRayNodeRenderChannelReflection", "Reflection", {}),
    ("AOV_SelfIllumination", "VRayNodeRenderChannelSelfIllumination", "SelfIllumination", {}),
)

DENOISER_NODE = "AOV_Denoiser"


def _plugin_group(node):
    """The node's plugin property group, e.g. node.RenderChannelZDepth."""
    plugin = getattr(node, "vray_plugin", None)
    if plugin and hasattr(node, plugin):
        return getattr(node, plugin)
    for attr in dir(node):
        if attr.startswith("RenderChannel"):
            group = getattr(node, attr, None)
            if hasattr(group, "bl_rna"):
                return group
    return None


def _norm(name):
    return name.replace("_", "").replace(" ", "").lower()


def _set_channel_param(node, attr, value, problems):
    """
    Set a V-Ray channel parameter on BOTH places it lives: the node's plugin
    property group and the matching input socket (e.g. "depth_white" ->
    socket "Depth White"). Channel nodes expose their parameters as sockets,
    and the exporter reads the socket value - setting only the property group
    is silently ignored at render time.
    """
    found = False
    group = _plugin_group(node)
    if group is not None and hasattr(group, attr):
        _set(group, attr, value, problems)
        found = True
    for sock in node.inputs:
        if _norm(sock.name) == _norm(attr) or _norm(sock.identifier) == _norm(attr):
            if hasattr(sock, "value"):
                _set(sock, "value", value, problems)
                found = True
            elif hasattr(sock, "default_value"):
                _set(sock, "default_value", value, problems)
                found = True
    if not found:
        problems.append("%s has no parameter %r" % (node.name, attr))


def _world_channels(scene, problems):
    """
    Return (node_tree, container_node) for the scene's V-Ray world, creating
    the V-Ray world tree and/or the Render Channels Container if missing.
    Returns (None, None) if V-Ray is not available.
    """
    try:
        from vray_blender.nodes import tree_defaults
    except ImportError:
        problems.append("V-Ray for Blender is not installed/enabled - render channels not set")
        return None, None

    world = scene.world
    if world is None:
        world = bpy.data.worlds.new("World")
        scene.world = world

    ntree = world.node_tree
    has_output = ntree is not None and any(
        n.bl_idname == "VRayNodeWorldOutput" for n in ntree.nodes)
    if not has_output:
        # Only when there is no V-Ray world yet: addWorldNodeTree() strips every
        # non-V-Ray node from the tree, and calling it on an existing V-Ray world
        # adds a second output/container.
        tree_defaults.addWorldNodeTree(world)
        ntree = world.node_tree

    outputs = [n for n in ntree.nodes if n.bl_idname == "VRayNodeWorldOutput"]
    output = next((n for n in outputs if getattr(n, "is_active_output", False)), outputs[0])

    # The container that actually reaches the output is the one that counts.
    channels_in = output.inputs["Channels"]
    container = None
    if channels_in.is_linked:
        candidate = channels_in.links[0].from_node
        if candidate.bl_idname == "VRayNodeRenderChannels":
            container = candidate
    if container is None:
        container = next((n for n in ntree.nodes if n.bl_idname == "VRayNodeRenderChannels"), None)
    if container is None:
        container = ntree.nodes.new("VRayNodeRenderChannels")
        container.location = (output.location.x - 300, output.location.y - 200)
    if not channels_in.is_linked or channels_in.links[0].from_node != container:
        ntree.links.new(container.outputs["Channels"], channels_in)
    return ntree, container


def _free_channel_socket(container):
    """A free 'Channel N' input on the container, adding one if all are used."""
    from vray_blender.nodes.sockets import addInput, moveExtendSocketToBottom

    for sock in container.inputs:
        if sock.bl_idname == "VRaySocketRenderChannel" and not sock.is_linked:
            return sock
    count = sum(1 for s in container.inputs if s.bl_idname == "VRaySocketRenderChannel")
    sock = addInput(container, "VRaySocketRenderChannel", "Channel %d" % (count + 1))
    moveExtendSocketToBottom(container)
    return sock


def _container_links(ntree, container, node):
    return [l for l in ntree.links if l.from_node == node and l.to_node == container]


def _enable_comp_channels(scene, problems):
    """Create/reuse the COMP_CHANNELS nodes and plug them into the container."""
    ntree, container = _world_channels(scene, problems)
    if container is None:
        return 0

    enabled = 0
    for i, (node_name, bl_idname, channel_name, attrs) in enumerate(COMP_CHANNELS):
        node = ntree.nodes.get(node_name)
        if node is not None and node.bl_idname != bl_idname:
            problems.append("Node %r is a %s, not a %s - left alone"
                            % (node_name, node.bl_idname, bl_idname))
            continue
        if node is None:
            try:
                node = ntree.nodes.new(bl_idname)
            except Exception as exc:
                problems.append("Could not add %s (%s)" % (channel_name, exc))
                continue
            node.name = node.label = node_name
            node.location = (container.location.x - 320, container.location.y - 140 * i)

        _set_channel_param(node, "name", channel_name, problems)
        for attr, value in attrs.items():
            _set_channel_param(node, attr, value, problems)
        node.mute = False

        if not _container_links(ntree, container, node):
            ntree.links.new(node.outputs[0], _free_channel_socket(container))
        enabled += 1
    return enabled


def _disable_comp_channels(scene, problems):
    """
    Beauty only: unplug this add-on's AOV_* channels from the container so
    they are not exported (the nodes are kept for the next comp-pass run).
    Channels added by hand are left as they are.
    """
    ntree, container = _world_channels(scene, problems)
    if container is None:
        return []
    names = {c[0] for c in COMP_CHANNELS}
    removed = []
    for link in list(ntree.links):
        if link.to_node == container and link.from_node.name in names:
            removed.append(link.from_node.name)
            ntree.links.remove(link)

    others = [l.from_node.name for l in ntree.links
              if l.to_node == container and l.from_node.name != DENOISER_NODE
              and l.to_socket.bl_idname == "VRaySocketRenderChannel"]
    if others:
        problems.append("Beauty only, but these hand-made channels are still linked "
                        "and will be written: %s" % ", ".join(sorted(others)))
    return removed


def _setup_denoiser(scene, gpu, problems):
    """
    Plug a V-Ray Denoiser channel into the container's Denoiser socket.
    NVIDIA AI denoiser on GPU renders (V-Ray falls back by itself when the
    card cannot run it), Intel Open Image Denoise on CPU renders.
    """
    ntree, container = _world_channels(scene, problems)
    if container is None:
        return None

    node = ntree.nodes.get(DENOISER_NODE)
    if node is None:
        node = next((n for n in ntree.nodes
                     if n.bl_idname == "VRayNodeRenderChannelDenoiser"), None)
    if node is None:
        node = ntree.nodes.new("VRayNodeRenderChannelDenoiser")
        node.name = node.label = DENOISER_NODE
        node.location = (container.location.x - 320, container.location.y + 200)

    engine = "1" if gpu else "2"
    _set_channel_param(node, "enabled", True, problems)
    _set_channel_param(node, "engine", engine, problems)
    _set_channel_param(node, "use_gpu", gpu, problems)
    _set_channel_param(node, "mode", "2", problems)   # show the denoised result in the VFB
    _set_channel_param(node, "denoise_alpha", True, problems)
    node.mute = False

    denoise_in = container.inputs.get("Denoiser")
    if denoise_in is None:
        problems.append("Render Channels Container has no Denoiser socket - denoiser not connected")
        return None
    if not denoise_in.is_linked or denoise_in.links[0].from_node != node:
        ntree.links.new(node.outputs[0], denoise_in)
    return "NVIDIA AI" if gpu else "Intel OIDN"


# ---------------------------------------------------------------------------
# Render quality presets  (STUDIO_RENDER_QUALITY_PRESETS_2026_09_26, V-Ray)
#
# Both presets: V-Ray GPU (CUDA) progressive, a denoiser channel, EXR written
# in ACEScg. V-Ray stops each frame at whichever comes first: the noise
# threshold or the sample cap, so the sample count is a cap, not a cost. No
# time limit (0), matching the Cycles version.
#
# Bit depth: V-Ray uses ONE bit depth for every channel in the EXR. Cryptomatte
# IDs and Z do not survive 16-bit half, so whenever comp passes are on the EXR
# is forced to 32-bit float with lossless ZIP, whatever the preset says. The
# preset's 16-bit / codec only apply to beauty-only renders.
# ---------------------------------------------------------------------------

RENDER_PRESETS = {
    "TEMP": {
        "resolution_percentage": 50,
        "noise_threshold": 0.05,
        "max_samples": 256,
        "trace_depth": 5,
        "gi_depth": 3,
        "beauty_bits": "16",
        "beauty_codec": "DWAA",
    },
    "FINAL": {
        "resolution_percentage": 100,
        "noise_threshold": 0.01,
        "max_samples": 1024,
        "trace_depth": 8,
        "gi_depth": 4,
        "beauty_bits": "16",
        "beauty_codec": "ZIPS",
    },
}

# GPU backend for V-Ray GPU. CUDA runs on every NVIDIA card V-Ray supports;
# "RTX" (OptiX) is faster on RTX cards but fails on older ones.
GPU_BACKEND = "CUDA"


def _merge_exr_attribute(existing, key, value):
    """Set key=value in V-Ray's 'a=b;c=d' EXR extra-attribute string."""
    parts = [p for p in (existing or "").split(";") if p.strip()]
    parts = [p for p in parts if p.split("=", 1)[0].strip() != key]
    parts.append("%s=%s" % (key, value))
    return ";".join(parts)


def _apply_render_quality(scene, quality, include_comp_passes, problems):
    """Apply a RENDER_PRESETS entry to the scene. Returns a one-line summary."""
    preset = RENDER_PRESETS[quality]
    render = scene.render
    vray = scene.vray

    try:
        render.engine = VRAY_ENGINE
    except Exception as exc:
        problems.append("Could not switch to V-Ray (%s)" % exc)
        return "V-Ray unavailable"

    exporter = vray.Exporter
    _set(exporter, "device_type", "GPU", problems)
    _set(exporter, "gpu_device_type", GPU_BACKEND, problems)
    # Write the image to disk at the end of the render (also needed for
    # command-line / farm renders).
    _set(exporter, "auto_save_render", True, problems)
    gpu = exporter.device_type == "GPU"

    _set(render, "resolution_percentage", preset["resolution_percentage"], problems)

    # Sampling: progressive, stop on noise threshold or sample cap.
    sampler = vray.SettingsImageSampler
    _set(sampler, "type", "3", problems)                       # Progressive
    _set(sampler, "progressive_threshold", preset["noise_threshold"], problems)
    _set(sampler, "progressive_maxTime", 0.0, problems)        # no time limit
    rt = vray.SettingsRTEngine
    _set(rt, "noise_threshold", preset["noise_threshold"], problems)
    _set(rt, "max_sample_level", preset["max_samples"], problems)
    _set(rt, "max_render_time", 0.0, problems)                 # no time limit
    _set(rt, "trace_depth", preset["trace_depth"], problems)
    _set(rt, "gi_depth", preset["gi_depth"], problems)

    denoiser = _setup_denoiser(scene, gpu, problems)

    # Output: ACEScg, EXR.
    _set(vray.SettingsUnitsInfo, "rgb_color_space", VRAY_RGB_ACESCG, problems)
    exr = vray.SettingsEXR
    if include_comp_passes:
        bits, codec = "32", "ZIPS"
    else:
        bits, codec = preset["beauty_bits"], preset["beauty_codec"]
    _set(exr, "bits_per_channel", bits, problems)
    _set(exr, "compression", EXR_COMPRESSION[codec], problems)
    _set(exr, "multipart", False, problems)
    _set(exr, "extra_attributes",
         _merge_exr_attribute(exr.extra_attributes, "colorspace", "ACEScg"), problems)

    return "%s: V-Ray %s, %d%%, max %d samples, noise %.2f, %s denoise, EXR %s-bit %s ACEScg" % (
        quality.title(), ("GPU " + GPU_BACKEND) if gpu else "CPU",
        preset["resolution_percentage"], preset["max_samples"],
        preset["noise_threshold"], denoiser or "no", bits, codec,
    )


def _set_vray_output(scene, folder, basename, problems):
    """Point V-Ray's image output at <folder>/<basename>.####.exr."""
    out = scene.vray.SettingsOutput
    _set(out, "img_format", VRAY_IMG_FORMAT_EXR, problems)
    if out.img_format != VRAY_IMG_FORMAT_EXR:
        problems.append("V-Ray would not accept EXR output (Community Edition?)")
    _set(out, "img_dir", folder, problems)
    # Explicit V-Ray frame token, giving "<basename>.1001.exr" as the
    # template's "..._v{version}.{SEQ}.exr" expects. Without a token V-Ray only
    # appends the frame number for animation renders - a single-frame render
    # (F12, or "-f 1001" on the command line) would be written with no frame
    # number at all.
    _set(out, "img_file", "%s.<frame%02d>" % (basename, FRAME_PAD), problems)
    # All channels in one EXR, not one file per channel.
    _set(out, "relements_separateFiles", False, problems)
    _set(out, "relements_separateFolders", False, problems)
    _set(out, "img_saveCryptomattesSeparately", False, problems)
    _set(out, "img_noAlpha", False, problems)
    _set(out, "img_separateAlpha", False, problems)
    # V-Ray keeps its own pixel aspect; match the scene's (2:1 for anamorphic
    # shots), so the EXR header carries the right squeeze for Nuke.
    aspect = scene.render.pixel_aspect_x / max(scene.render.pixel_aspect_y, 1e-6)
    _set(out, "img_pixelAspect", aspect, problems)

    # Keep Blender's own output path pointing at the same place, so other
    # tools (and a Cycles fallback render) agree with V-Ray.
    scene.render.filepath = os.path.join(folder, basename + ".")
    scene.render.use_file_extension = True


class FLOW_OT_set_element_render_output_vray(bpy.types.Operator):
    """Resolve the pipeline render path for a CG element and set it as this scene's V-Ray output"""

    bl_idname = "flow.set_element_render_output_vray"
    bl_label = "Set Element Render Output (V-Ray)..."
    bl_options = {"REGISTER"}

    element_name: bpy.props.StringProperty(
        name="Element Name",
        description="Descriptive name for this CG element (e.g. smoke, debris) - alphanumeric only",
    )

    include_comp_passes: bpy.props.BoolProperty(
        name="Include Compositing Passes",
        description=(
            "Add V-Ray render channels (Normals, Z-Depth, Cryptomatte "
            "Object/Material, Diffuse/Lighting/GI, Reflection/Specular, "
            "Self-Illumination) to the scene's world and write them into the "
            "same 32-bit EXR as the beauty. Turn off for a quick beauty-only "
            "test render"
        ),
        default=True,
    )

    render_quality: bpy.props.EnumProperty(
        name="Render Quality",
        description="V-Ray settings to apply along with the output path",
        items=[
            ("TEMP", "Temp",
             "Fast temp render: 50% resolution, max 256 samples, noise 0.05, "
             "denoised; 16-bit DWAA EXR when beauty only"),
            ("FINAL", "Final",
             "Full-quality element for comp: 100% resolution, max 1024 "
             "samples, noise 0.01, denoised; 16-bit ZIP EXR when beauty only"),
        ],
        default="TEMP",
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        if not hasattr(context.scene, "vray"):
            self.report({"ERROR"}, "V-Ray for Blender is not enabled in this Blender.")
            return {"CANCELLED"}

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
        # key's real internal name, so the fields dict must be keyed "output",
        # not "blender.output".
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

        scene = context.scene
        problems = []

        _set_vray_output(scene, folder, basename, problems)

        if self.include_comp_passes:
            count = _enable_comp_channels(scene, problems)
            passes = "%d render channels in one EXR" % count
        else:
            _disable_comp_channels(scene, problems)
            passes = "beauty only"

        # Engine / quality / ACEScg output. After the channels - the EXR bit
        # depth depends on whether comp passes are on.
        summary = _apply_render_quality(scene, self.render_quality,
                                        self.include_comp_passes, problems)
        for problem in problems:
            self.report({"WARNING"}, problem)

        self.report({"INFO"}, "Render output set ({}; {}): {}".format(
            passes, summary, os.path.join(folder, basename + ".####.exr")))
        return {"FINISHED"}


def _menu_draw(self, context):
    self.layout.operator(FLOW_OT_set_element_render_output_vray.bl_idname, icon="RENDER_ANIMATION")


def register():
    bpy.utils.register_class(FLOW_OT_set_element_render_output_vray)
    bpy.types.TOPBAR_MT_render.append(_menu_draw)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(_menu_draw)
    bpy.utils.unregister_class(FLOW_OT_set_element_render_output_vray)


if __name__ == "__main__":
    register()
