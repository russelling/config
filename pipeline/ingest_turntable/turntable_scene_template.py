"""
turntable_scene_template.py -- builds a standard turntable rig inside
Blender: a 3-point light setup, a seamless ground/backdrop, and a camera.
The asset itself is spun 360 degrees rather than the camera, which keeps
lighting consistent across the whole render.

Only usable from inside a running Blender (imports bpy). Kept separate from
generate_turntable.py so the rig can be tuned/replaced independently of the
render/encode/publish logic.
"""
from __future__ import annotations

import math

import bpy
from bpy_extras import anim_utils


def _clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _set_render_engine(scene, requested: str) -> str:
    """Assign the render engine, tolerating Blender's EEVEE identifier
    rename. 4.2 replaced legacy EEVEE with "BLENDER_EEVEE_NEXT", so a single
    literal in config.yml cannot be valid across the versions in use here --
    an unknown identifier raises TypeError rather than degrading. Try the
    requested name first, then the other spelling."""
    candidates = [requested]
    if requested == "BLENDER_EEVEE":
        candidates.append("BLENDER_EEVEE_NEXT")
    elif requested == "BLENDER_EEVEE_NEXT":
        candidates.append("BLENDER_EEVEE")

    for name in candidates:
        try:
            scene.render.engine = name
            return name
        except TypeError:
            continue
    raise ValueError(
        f"Render engine {requested!r} is not available in this Blender "
        f"({bpy.app.version_string}); tried {candidates}."
    )


def _add_three_point_lighting(target_size: float):
    dist = target_size * 3
    energy = target_size * 400

    key = bpy.data.lights.new(name="TT_Key", type="AREA")
    key.energy = energy
    key_obj = bpy.data.objects.new("TT_Key", key)
    key_obj.location = (dist, -dist, dist)
    key_obj.rotation_euler = (math.radians(55), 0, math.radians(45))
    bpy.context.collection.objects.link(key_obj)

    fill = bpy.data.lights.new(name="TT_Fill", type="AREA")
    fill.energy = energy * 0.4
    fill_obj = bpy.data.objects.new("TT_Fill", fill)
    fill_obj.location = (-dist, -dist * 0.6, dist * 0.5)
    fill_obj.rotation_euler = (math.radians(70), 0, math.radians(-40))
    bpy.context.collection.objects.link(fill_obj)

    rim = bpy.data.lights.new(name="TT_Rim", type="AREA")
    rim.energy = energy * 0.6
    rim_obj = bpy.data.objects.new("TT_Rim", rim)
    rim_obj.location = (0, dist, dist * 0.8)
    rim_obj.rotation_euler = (math.radians(-60), 0, 0)
    bpy.context.collection.objects.link(rim_obj)


def _add_ground(target_size: float):
    bpy.ops.mesh.primitive_plane_add(size=target_size * 10, location=(0, 0, 0))
    ground = bpy.context.active_object
    ground.name = "TT_Ground"

    mat = bpy.data.materials.new(name="TT_GroundMat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (0.18, 0.18, 0.18, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.85
    ground.data.materials.append(mat)
    return ground


def _add_camera(asset_root_obj, target_size: float, resolution, fill: float = 0.85):
    import mathutils

    cam_data = bpy.data.cameras.new("TT_Camera")
    cam_data.lens = 50
    cam_obj = bpy.data.objects.new("TT_Camera", cam_data)
    cam_obj.location = (0, -target_size * 3.2, target_size * 1.1)
    cam_obj.rotation_euler = (math.radians(78), 0, 0)
    bpy.context.collection.objects.link(cam_obj)
    bpy.context.scene.camera = cam_obj

    scene = bpy.context.scene
    scene.render.resolution_x = resolution[0]
    scene.render.resolution_y = resolution[1]

    # Fit the camera to the asset's swept volume so the asset fills `fill`
    # fraction of frame regardless of its size/proportions. Resolution must
    # already be set above -- aspect ratio affects the fit.
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    coords = _swept_volume_coords(asset_root_obj)
    if coords:
        loc, _scale = cam_obj.camera_fit_coords(depsgraph, coords)
        # Pull back along the view axis so the fit fills `fill` of frame
        # rather than touching the edges.
        back = cam_obj.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, 1))
        horiz = mathutils.Vector((loc.x, loc.y, 0.0)).length
        pullback = (1.0 / fill - 1.0) * horiz
        cam_obj.location = loc + back * pullback

    # Near/far planes must scale with the rig. Blender's camera defaults
    # (0.1 / 1000) assume a metres-scale scene, so an asset authored in
    # cm/mm -- e.g. a ZBrush export ~700 units across, framed from ~2000
    # units back -- falls entirely beyond the default far plane and renders
    # as an empty frame, while the camera-parented chrome ball (much closer)
    # still shows. The far plane also has to clear the ground plane, which
    # _add_ground sizes at target_size * 10.
    dist = cam_obj.location.length
    cam_data.clip_start = max(dist * 1e-3, 1e-4)
    cam_data.clip_end = (dist + target_size * 10.0) * 2.0
    return cam_obj


def _bounds_size(obj) -> float:
    """Largest world-space extent of the renderable mesh geometry, used to
    scale lights/camera/ground to the imported asset."""
    coords = _mesh_world_corners(obj)
    if not coords:
        return 2.0

    xs = [c.x for c in coords]
    ys = [c.y for c in coords]
    zs = [c.z for c in coords]
    size = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    return max(size, 0.1)


def _mesh_world_corners(asset_root_obj):
    """World-space bbox corners of renderable MESH descendants only, using
    depsgraph-evaluated objects so armature deforms/modifiers are reflected.
    Empties, armatures/skeletons, lights etc. are deliberately excluded:
    skeletal imports (GLB->USD) can carry bound boxes vastly larger than the
    visible geometry, which poisons framing if included."""
    import mathutils

    depsgraph = bpy.context.evaluated_depsgraph_get()
    corners = []

    def collect(o):
        if o.type == "MESH" and not o.hide_render:
            o_eval = o.evaluated_get(depsgraph)
            for corner in o_eval.bound_box:
                corners.append(o_eval.matrix_world @ mathutils.Vector(corner))
        for child in o.children:
            collect(child)

    for child in asset_root_obj.children:
        collect(child)
    return corners


def _center_children_on_origin(asset_root_obj):
    """Shift the asset's top-level objects so the renderable geometry is
    centered on the Z (spin) axis and sits on the ground plane. Without
    this, an off-origin import orbits the axis instead of spinning in
    place."""
    coords = _mesh_world_corners(asset_root_obj)
    if not coords:
        return

    cx = (min(c.x for c in coords) + max(c.x for c in coords)) / 2
    cy = (min(c.y for c in coords) + max(c.y for c in coords)) / 2
    mz = min(c.z for c in coords)

    for child in asset_root_obj.children:
        child.location.x -= cx
        child.location.y -= cy
        child.location.z -= mz
    bpy.context.view_layer.update()


def _swept_volume_coords(asset_root_obj, samples: int = 16):
    """Flat list of world-space points covering the volume the renderable
    geometry sweeps through over a full revolution: every mesh bbox corner
    revolved around Z. Fitting the camera to these (rather than the static
    bbox) guarantees no frame-edge clipping at any rotation angle."""
    corners = _mesh_world_corners(asset_root_obj)

    coords = []
    for v in corners:
        radius = math.hypot(v.x, v.y)
        for i in range(samples):
            angle = i * 2.0 * math.pi / samples
            coords.extend((radius * math.cos(angle), radius * math.sin(angle), v.z))
    return coords


def _setup_hdri_world(hdri_path: str, backdrop_color=(0.03, 0.03, 0.03, 1.0)):
    """HDRI environment that lights the asset (and shows in the chrome
    ball's reflection) WITHOUT being visible behind the asset: a Light Path
    "Is Camera Ray" mix sends camera rays to a flat dark backdrop while all
    lighting/reflection lookups still see the HDRI. Is Camera Ray is the
    one Light Path output EEVEE reliably supports."""
    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    bpy.context.scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    nt.nodes.clear()

    out_node = nt.nodes.new("ShaderNodeOutputWorld")
    mix_node = nt.nodes.new("ShaderNodeMixShader")
    light_path = nt.nodes.new("ShaderNodeLightPath")

    bg_hdri = nt.nodes.new("ShaderNodeBackground")
    env_node = nt.nodes.new("ShaderNodeTexEnvironment")
    env_node.image = bpy.data.images.load(hdri_path)
    nt.links.new(env_node.outputs["Color"], bg_hdri.inputs["Color"])

    bg_cam = nt.nodes.new("ShaderNodeBackground")
    bg_cam.inputs["Color"].default_value = backdrop_color

    # fac=0 -> first shader (HDRI, for lighting/reflections);
    # fac=1 (camera rays) -> second shader (flat backdrop).
    nt.links.new(light_path.outputs["Is Camera Ray"], mix_node.inputs["Fac"])
    nt.links.new(bg_hdri.outputs["Background"], mix_node.inputs[1])
    nt.links.new(bg_cam.outputs["Background"], mix_node.inputs[2])
    nt.links.new(mix_node.outputs["Shader"], out_node.inputs["Surface"])


def _add_chrome_ball(cam_obj, ball_frac: float = 0.15, margin_frac: float = 0.05):
    """Chrome reference sphere parented to the camera, pinned to the
    lower-left of frame -- reflects the HDRI so reviewers can read the
    lighting environment. ball_frac is the ball's diameter as a fraction
    of frame height; margin_frac is the gap to the frame edges."""
    import mathutils

    scene = bpy.context.scene
    cam_data = cam_obj.data

    # Distance in front of the camera: a bit inside the subject distance so
    # the ball floats between camera and asset, well past the near clip.
    dist = max(cam_obj.location.length * 0.5, cam_data.clip_start * 20)

    # view_frame() gives the four view corners in camera space at unit
    # depth -- scaling by dist yields exact frame extents at that distance,
    # correct for any lens/sensor-fit/aspect combination.
    # view_frame() corner depth varies by convention -- normalize each
    # corner to z = -1 so scaling by dist is correct regardless.
    corners = [v / -v.z for v in cam_data.view_frame(scene=scene)]
    half_w = max(abs(v.x) for v in corners) * dist
    half_h = max(abs(v.y) for v in corners) * dist

    radius = ball_frac * half_h
    margin = margin_frac * 2.0 * half_h
    local_x = -(half_w - radius - margin)
    local_y = -(half_h - radius - margin)

    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, segments=48, ring_count=24)
    ball = bpy.context.active_object
    ball.name = "TT_ChromeBall"
    bpy.ops.object.shade_smooth()

    mat = bpy.data.materials.new(name="TT_ChromeMat")
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Metallic"].default_value = 1.0
        bsdf.inputs["Roughness"].default_value = 0.0
        bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    ball.data.materials.append(mat)

    if hasattr(ball, "visible_shadow"):
        ball.visible_shadow = False

    ball.parent = cam_obj
    ball.location = (local_x, local_y, -dist)
    ball.rotation_euler = (0, 0, 0)
    return ball


def build_turntable(
    asset_root_obj,
    frame_start: int,
    frame_end: int,
    turns: int,
    resolution,
    render_engine: str,
    hdri_path: str | None = None,
):
    """Given the already-imported asset's root object, build the lighting /
    ground / camera rig around it and keyframe a `turns`-revolution spin
    over [frame_start, frame_end]. Returns nothing; mutates the scene."""
    scene = bpy.context.scene
    scene.frame_start = frame_start
    scene.frame_end = frame_end
    _set_render_engine(scene, render_engine)

    _center_children_on_origin(asset_root_obj)
    size = _bounds_size(asset_root_obj)

    _add_ground(size)
    if hdri_path:
        _setup_hdri_world(hdri_path)
    else:
        _add_three_point_lighting(size)
    cam_obj = _add_camera(asset_root_obj, size, resolution)
    if hdri_path:
        _add_chrome_ball(cam_obj)

    asset_root_obj.rotation_mode = "XYZ"
    asset_root_obj.rotation_euler = (0, 0, 0)
    asset_root_obj.keyframe_insert(data_path="rotation_euler", index=2, frame=frame_start)
    asset_root_obj.rotation_euler = (0, 0, math.radians(360 * turns))
    asset_root_obj.keyframe_insert(data_path="rotation_euler", index=2, frame=frame_end)

    action = asset_root_obj.animation_data.action
    action_slot = asset_root_obj.animation_data.action_slot
    channelbag = anim_utils.action_get_channelbag_for_slot(action, action_slot)
    for fcurve in channelbag.fcurves:
        for kp in fcurve.keyframe_points:
            kp.interpolation = "LINEAR"

    if not hdri_path:
        # World background so unlit render regions aren't pure black.
        # (When an HDRI is in use, _setup_hdri_world owns the world nodes.)
        world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
        scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs["Color"].default_value = (0.03, 0.03, 0.03, 1.0)
            bg.inputs["Strength"].default_value = 1.0
