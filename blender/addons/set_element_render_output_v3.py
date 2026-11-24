bl_info = {
    "name": "Flow - Set Element Render Output (Cycles / V-Ray)",
    "description": (
        "Resolves the pipeline's ep_blender_shot_render_work template from "
        "the current Toolkit context and points the scene's render output "
        "at it, so CG-element pre-renders (smoke, debris, etc.) always land "
        "in the right place with the right name - no manually typed paths. "
        "Asks which renderer to set up: Cycles or V-Ray. Optionally enables "
        "a standard set of compositing passes (Normal, Depth, Cryptomatte "
        "Object/Material, Diffuse/Glossy/Emission separations) and writes "
        "everything to a single multi-channel EXR."
    ),
    "author": "Buffalo VFX",
    "version": (3, 1, 0),
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
# Same rule for V-Ray: vray_blender is only imported inside the V-Ray helpers,
# so this add-on loads (and Cycles works) on a Blender without V-Ray.


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


def _vray_available():
    return "VRAY_RENDER_RT" in {getattr(e, "bl_idname", None)
                                for e in bpy.types.RenderEngine.__subclasses__()}


# ===========================================================================
# CYCLES
# ===========================================================================

# Standard comp-pass set requested for CG elements feeding Nuke: Normal +
# Depth/Z, Cryptomatte (Object/Material), and Diffuse/Glossy/Emission
# separations (so a compositor can re-balance lighting without a re-render).
# All of these are plain ViewLayer booleans as of Blender 4.2 (EEVEE Next
# unified most pass availability with Cycles) - if a given engine doesn't
# support one, setting it is a silent no-op rather than an error, so no
# per-engine branching is needed here.
def _cycles_enable_comp_passes(view_layer):
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
# Cycles quality presets  (STUDIO_RENDER_QUALITY_PRESETS_2026_09_26)
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

CYCLES_PRESETS = {
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
CYCLES_GPU_BACKENDS = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")


def _cycles_setup_gpu(problems):
    """Pick the best GPU backend in Preferences > System and enable its GPUs.
    Returns the backend name, or None if there is no usable GPU."""
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception as exc:
        problems.append("Cycles preferences unavailable (%s) - rendering on CPU" % exc)
        return None
    for backend in CYCLES_GPU_BACKENDS:
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


def _cycles_set_output_colorspace(image_settings, problems):
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


def _cycles_apply_quality(scene, quality, problems):
    """Apply a CYCLES_PRESETS entry to the scene. Returns a one-line summary."""
    preset = CYCLES_PRESETS[quality]
    render = scene.render
    cycles = scene.cycles

    render.engine = "CYCLES"
    backend = _cycles_setup_gpu(problems)
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
    colorspace = _cycles_set_output_colorspace(image_settings, problems)

    return "%s: Cycles %s, %d%%, max %d samples, %s denoise, EXR %s" % (
        quality.title(), backend or "CPU", preset["resolution_percentage"],
        preset["samples"], "OptiX" if denoiser == "OPTIX" else "OIDN",
        colorspace or "scene linear",
    )


def _setup_cycles(scene, view_layer, folder, basename, quality, include_comp_passes, problems):
    """Cycles output path + passes + quality. Returns (passes text, summary)."""
    # Trailing "." with no frame token - Blender appends the frame number
    # (default 4-digit padding) and extension itself, giving exactly
    # "<basename>.1001.exr" per the template's "..._v{version}.{SEQ}.exr".
    scene.render.filepath = os.path.join(folder, basename + ".")

    image_settings = scene.render.image_settings
    # Blender 4.2+ added ImageFormatSettings.media_type ('IMAGE' /
    # 'MULTI_LAYER_IMAGE' / 'VIDEO'), which restricts which file_format enum
    # values are legal - "OPEN_EXR" only under 'IMAGE', "OPEN_EXR_MULTILAYER"
    # only under 'MULTI_LAYER_IMAGE'. Mismatching the two raises a TypeError
    # (hit once already - see the project doc's bug #4). Older Blender
    # (pre-4.2) has no media_type attribute at all, so guard with hasattr.
    if include_comp_passes:
        _cycles_enable_comp_passes(view_layer)
        if hasattr(image_settings, "media_type"):
            image_settings.media_type = "MULTI_LAYER_IMAGE"
        image_settings.file_format = "OPEN_EXR_MULTILAYER"
        passes = "multi-layer EXR + comp passes"
    else:
        if hasattr(image_settings, "media_type"):
            image_settings.media_type = "IMAGE"
        image_settings.file_format = "OPEN_EXR"
        passes = "beauty only"

    scene.render.use_file_extension = True

    # Render engine / quality / ACEScg output. After the file format is
    # chosen above - exr_codec and color_depth depend on it.
    summary = _cycles_apply_quality(scene, quality, problems)
    return passes, summary


# ===========================================================================
# V-RAY
# ===========================================================================

VRAY_ENGINE = "VRAY_RENDER_RT"

# SettingsOutput.img_format item for OpenEXR (V-Ray's own enum, from
# plugins_desc/settings/SettingsOutput.custom.json: 0 PNG, 1 JPEG, 2 TIFF,
# 3 TGA, 4 SGI, 5 OpenEXR, 6 VRayImage). The Community Edition disables 5.
VRAY_IMG_FORMAT_EXR = "5"

# SettingsEXR.compression items.
VRAY_EXR_COMPRESSION = {"ZIPS": "3", "ZIP": "4", "PIZ": "5", "DWAA": "9", "DWAB": "10"}

# SettingsUnitsInfo.rgb_color_space items: 1 sRGB, 2 ACEScg.
VRAY_RGB_ACESCG = "2"

# ---------------------------------------------------------------------------
# V-Ray render channels (AOVs)
#
# V-Ray has no view-layer pass booleans: channels are nodes in the WORLD node
# tree, plugged into the "Render Channels Container", which feeds the
# "Channels" input of the V-Ray World Output. A channel node that is not
# linked into the container is NOT exported.
#
# Cycles pass -> V-Ray channel used here:
#   Normal                         -> Normals
#   Depth (Z)                      -> Z-Depth (true distance, channel named "Z")
#   Cryptomatte Object / Material  -> Cryptomatte, id_type 0 / 1
#   Diffuse Color/Direct/Indirect  -> Diffuse (filter) / Lighting / GI
#   Glossy Color/Direct/Indirect   -> Reflection Filter / Specular / Reflection
#   Emission                       -> Self-Illumination
#
# Every node this add-on creates is named "AOV_<x>", so running it again
# reuses the same nodes instead of piling up duplicates.
# ---------------------------------------------------------------------------

# (node name, node bl_idname, channel name written to the EXR, {plugin attr: value})
VRAY_COMP_CHANNELS = (
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

VRAY_DENOISER_NODE = "AOV_Denoiser"


def _vray_plugin_group(node):
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


def _vray_set_channel_param(node, attr, value, problems):
    """
    Set a V-Ray channel parameter on BOTH places it lives: the node's plugin
    property group and the matching input socket (e.g. "depth_white" ->
    socket "Depth White"). Channel nodes expose their parameters as sockets,
    and the exporter reads the socket value - setting only the property group
    is silently ignored at render time.
    """
    found = False
    group = _vray_plugin_group(node)
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


def _vray_world_channels(scene, problems):
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


def _vray_free_channel_socket(container):
    """A free 'Channel N' input on the container, adding one if all are used."""
    from vray_blender.nodes.sockets import addInput, moveExtendSocketToBottom

    for sock in container.inputs:
        if sock.bl_idname == "VRaySocketRenderChannel" and not sock.is_linked:
            return sock
    count = sum(1 for s in container.inputs if s.bl_idname == "VRaySocketRenderChannel")
    sock = addInput(container, "VRaySocketRenderChannel", "Channel %d" % (count + 1))
    moveExtendSocketToBottom(container)
    return sock


def _vray_enable_comp_channels(scene, problems):
    """Create/reuse the VRAY_COMP_CHANNELS nodes and plug them into the container."""
    ntree, container = _vray_world_channels(scene, problems)
    if container is None:
        return 0

    enabled = 0
    for i, (node_name, bl_idname, channel_name, attrs) in enumerate(VRAY_COMP_CHANNELS):
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

        _vray_set_channel_param(node, "name", channel_name, problems)
        for attr, value in attrs.items():
            _vray_set_channel_param(node, attr, value, problems)
        node.mute = False

        linked = any(l.from_node == node and l.to_node == container for l in ntree.links)
        if not linked:
            ntree.links.new(node.outputs[0], _vray_free_channel_socket(container))
        enabled += 1
    return enabled


def _vray_disable_comp_channels(scene, problems):
    """
    Beauty only: unplug this add-on's AOV_* channels from the container so
    they are not exported (the nodes are kept for the next comp-pass run).
    Channels added by hand are left as they are.
    """
    ntree, container = _vray_world_channels(scene, problems)
    if container is None:
        return []
    names = {c[0] for c in VRAY_COMP_CHANNELS}
    removed = []
    for link in list(ntree.links):
        if link.to_node == container and link.from_node.name in names:
            removed.append(link.from_node.name)
            ntree.links.remove(link)

    others = [l.from_node.name for l in ntree.links
              if l.to_node == container and l.from_node.name != VRAY_DENOISER_NODE
              and l.to_socket.bl_idname == "VRaySocketRenderChannel"]
    if others:
        problems.append("Beauty only, but these hand-made channels are still linked "
                        "and will be written: %s" % ", ".join(sorted(others)))
    return removed


def _vray_setup_denoiser(scene, gpu, problems):
    """
    Plug a V-Ray Denoiser channel into the container's Denoiser socket.
    NVIDIA AI denoiser on GPU renders (V-Ray falls back by itself when the
    card cannot run it), Intel Open Image Denoise on CPU renders.
    """
    ntree, container = _vray_world_channels(scene, problems)
    if container is None:
        return None

    node = ntree.nodes.get(VRAY_DENOISER_NODE)
    if node is None:
        node = next((n for n in ntree.nodes
                     if n.bl_idname == "VRayNodeRenderChannelDenoiser"), None)
    if node is None:
        node = ntree.nodes.new("VRayNodeRenderChannelDenoiser")
        node.name = node.label = VRAY_DENOISER_NODE
        node.location = (container.location.x - 320, container.location.y + 200)

    _vray_set_channel_param(node, "enabled", True, problems)
    _vray_set_channel_param(node, "engine", "1" if gpu else "2", problems)
    _vray_set_channel_param(node, "use_gpu", gpu, problems)
    _vray_set_channel_param(node, "mode", "2", problems)   # show the denoised result in the VFB
    _vray_set_channel_param(node, "denoise_alpha", True, problems)
    node.mute = False

    denoise_in = container.inputs.get("Denoiser")
    if denoise_in is None:
        problems.append("Render Channels Container has no Denoiser socket - denoiser not connected")
        return None
    if not denoise_in.is_linked or denoise_in.links[0].from_node != node:
        ntree.links.new(node.outputs[0], denoise_in)
    return "NVIDIA AI" if gpu else "Intel OIDN"


# ---------------------------------------------------------------------------
# V-Ray quality presets  (STUDIO_RENDER_QUALITY_PRESETS_2026_09_26, V-Ray)
#
# Both presets: V-Ray GPU (CUDA) progressive, a denoiser channel, EXR written
# in ACEScg. V-Ray stops each frame at whichever comes first: the noise
# threshold or the sample cap, so the sample count is a cap, not a cost. No
# time limit (0), matching the Cycles presets.
#
# Bit depth: V-Ray uses ONE bit depth for every channel in the EXR. Cryptomatte
# IDs and Z do not survive 16-bit half, so whenever comp passes are on the EXR
# is forced to 32-bit float with lossless ZIP, whatever the preset says. The
# preset's 16-bit / codec only apply to beauty-only renders.
# ---------------------------------------------------------------------------

VRAY_PRESETS = {
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
VRAY_GPU_BACKEND = "CUDA"


def _merge_exr_attribute(existing, key, value):
    """Set key=value in V-Ray's 'a=b;c=d' EXR extra-attribute string."""
    parts = [p for p in (existing or "").split(";") if p.strip()]
    parts = [p for p in parts if p.split("=", 1)[0].strip() != key]
    parts.append("%s=%s" % (key, value))
    return ";".join(parts)


# STUDIO_VRAY_EXR_REQUIRED_2026_11_21
def _vray_exr_supported(scene):
    """True if this V-Ray accepts EXR output. The free Community Edition
    disables it, and every other format holds one image per file, so V-Ray
    would write each render channel as a separate file. Probes the
    setting and puts the old value back on a refusal, so a refused run
    leaves the scene as it was."""
    out = scene.vray.SettingsOutput
    before = out.img_format
    try:
        out.img_format = VRAY_IMG_FORMAT_EXR
    except Exception:
        return False
    if out.img_format == VRAY_IMG_FORMAT_EXR:
        return True
    try:
        out.img_format = before
    except Exception:
        pass
    return False


def _vray_apply_quality(scene, quality, include_comp_passes, problems):
    """Apply a VRAY_PRESETS entry to the scene. Returns a one-line summary."""
    preset = VRAY_PRESETS[quality]
    render = scene.render
    vray = scene.vray

    try:
        render.engine = VRAY_ENGINE
    except Exception as exc:
        problems.append("Could not switch to V-Ray (%s)" % exc)
        return "V-Ray unavailable"

    exporter = vray.Exporter
    _set(exporter, "device_type", "GPU", problems)
    _set(exporter, "gpu_device_type", VRAY_GPU_BACKEND, problems)
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

    denoiser = _vray_setup_denoiser(scene, gpu, problems)

    # Output: ACEScg, EXR.
    _set(vray.SettingsUnitsInfo, "rgb_color_space", VRAY_RGB_ACESCG, problems)
    exr = vray.SettingsEXR
    if include_comp_passes:
        bits, codec = "32", "ZIPS"
    else:
        bits, codec = preset["beauty_bits"], preset["beauty_codec"]
    _set(exr, "bits_per_channel", bits, problems)
    _set(exr, "compression", VRAY_EXR_COMPRESSION[codec], problems)
    _set(exr, "multipart", False, problems)
    _set(exr, "extra_attributes",
         _merge_exr_attribute(exr.extra_attributes, "colorspace", "ACEScg"), problems)

    return "%s: V-Ray %s, %d%%, max %d samples, noise %.2f, %s denoise, EXR %s-bit %s ACEScg" % (
        quality.title(), ("GPU " + VRAY_GPU_BACKEND) if gpu else "CPU",
        preset["resolution_percentage"], preset["max_samples"],
        preset["noise_threshold"], denoiser or "no", bits, codec,
    )


def _vray_set_output(scene, folder, basename, problems):
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
    # tools agree with V-Ray.
    scene.render.filepath = os.path.join(folder, basename + ".")
    scene.render.use_file_extension = True


def _setup_vray(scene, folder, basename, quality, include_comp_passes, problems):
    """V-Ray output path + channels + quality. Returns (passes text, summary)."""
    _vray_set_output(scene, folder, basename, problems)
    if include_comp_passes:
        count = _vray_enable_comp_channels(scene, problems)
        passes = "%d render channels in one EXR" % count
    else:
        _vray_disable_comp_channels(scene, problems)
        passes = "beauty only"
    # After the channels - the EXR bit depth depends on whether comp passes are on.
    summary = _vray_apply_quality(scene, quality, include_comp_passes, problems)
    return passes, summary


# ===========================================================================
# OPERATOR
# ===========================================================================

class FLOW_OT_set_element_render_output_v3(bpy.types.Operator):
    """Resolve the pipeline render path for a CG element and set it up for Cycles or V-Ray"""

    bl_idname = "flow.set_element_render_output_v3"
    bl_label = "Set Element Render Output (Cycles / V-Ray)..."
    bl_options = {"REGISTER"}

    element_name: bpy.props.StringProperty(
        name="Element Name",
        description="Descriptive name for this CG element (e.g. smoke, debris) - alphanumeric only",
    )

    render_engine: bpy.props.EnumProperty(
        name="Renderer",
        description="Which renderer to set up the output and quality settings for",
        items=[
            ("CYCLES", "Cycles",
             "Cycles on the best GPU backend, OptiX denoise, multi-layer EXR"),
            ("VRAY", "V-Ray",
             "V-Ray GPU progressive, V-Ray denoiser, render channels in one EXR "
             "(needs V-Ray for Blender)"),
        ],
        default="CYCLES",
    )

    include_comp_passes: bpy.props.BoolProperty(
        name="Include Compositing Passes",
        description=(
            "Cycles: enable Normal, Depth (Z), Cryptomatte (Object/Material) and "
            "Diffuse/Glossy/Emission passes and write a multi-layer EXR. "
            "V-Ray: add the matching render channels (Normals, Z, Cryptomatte, "
            "Diffuse/Lighting/GI, Reflection/Specular, Self-Illumination) into "
            "one 32-bit EXR. Turn off for a quick beauty-only test render"
        ),
        default=True,
    )

    render_quality: bpy.props.EnumProperty(
        name="Render Quality",
        description="Render settings to apply along with the output path",
        items=[
            ("TEMP", "Temp",
             "Fast temp render: 50% resolution, max 256 samples, denoised, "
             "lighter light paths"),
            ("FINAL", "Final",
             "Full-quality element for comp: 100% resolution, max 1024 "
             "samples, denoised, fuller light paths"),
        ],
        default="TEMP",
    )

    def invoke(self, context, event):
        # Default the prompt to whatever renderer the scene already uses.
        self.render_engine = "VRAY" if context.scene.render.engine == VRAY_ENGINE else "CYCLES"
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "element_name")
        layout.prop(self, "render_engine", expand=True)
        if self.render_engine == "VRAY" and not _vray_available():
            layout.label(text="V-Ray for Blender is not enabled in this Blender", icon="ERROR")
        layout.prop(self, "render_quality", expand=True)
        layout.prop(self, "include_comp_passes")

    def execute(self, context):
        if self.render_engine == "VRAY" and not (_vray_available() and hasattr(context.scene, "vray")):
            self.report({"ERROR"}, "V-Ray for Blender is not enabled in this Blender - "
                        "choose Cycles or enable V-Ray.")
            return {"CANCELLED"}
        # STUDIO_VRAY_EXR_REQUIRED_2026_11_21: before any folder or setting changes.
        if self.render_engine == "VRAY" and not _vray_exr_supported(context.scene):
            self.report({"ERROR"}, "This V-Ray can't write EXR (the free Community "
                        "Edition disables it), so the passes can't go into one "
                        "EXR. Nothing was changed - use Cycles for this element, "
                        "or a licensed V-Ray.")
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

        scene = context.scene
        problems = []
        if self.render_engine == "VRAY":
            passes, summary = _setup_vray(scene, folder, basename, self.render_quality,
                                          self.include_comp_passes, problems)
        else:
            passes, summary = _setup_cycles(scene, context.view_layer, folder, basename,
                                            self.render_quality, self.include_comp_passes,
                                            problems)

        for problem in problems:
            self.report({"WARNING"}, problem)

        self.report({"INFO"}, "Render output set ({}; {}): {}".format(
            passes, summary, os.path.join(folder, basename + ".####.exr")))
        return {"FINISHED"}


def _menu_draw(self, context):
    self.layout.operator(FLOW_OT_set_element_render_output_v3.bl_idname, icon="RENDER_ANIMATION")


def register():
    bpy.utils.register_class(FLOW_OT_set_element_render_output_v3)
    bpy.types.TOPBAR_MT_render.append(_menu_draw)


def unregister():
    bpy.types.TOPBAR_MT_render.remove(_menu_draw)
    bpy.utils.unregister_class(FLOW_OT_set_element_render_output_v3)


if __name__ == "__main__":
    register()
