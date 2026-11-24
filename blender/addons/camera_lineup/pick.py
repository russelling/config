"""Picking and camera maths shared by the operators and the overlay: plate positions in a camera view, points on
the lidar, and conversion between Blender cameras and the solver's (R, C, f)."""
import bpy
import numpy as np
from bpy_extras import view3d_utils
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

POINT_RADIUS = 12          # px: how close the mouse must be to a lidar point (point clouds only)

# The pin / line tools are toggles: tool is the one that should be running ("PINS", "LINES" or None) and session
# counts starts, so a tool still running from before stops on its next event. undo_generation counts undos / redos.
tool = None
session = 0
undo_generation = 0
# While a point is held on the plate: {"region": region, "at": region position, "uv": plate position} for the loupe.
loupe = None
# (camera name, frame) of an auto-solve not keyed yet: keyed when the tool stops, on Solve, a frame change or a save.
unkeyed = None
# While picking on the lidar: the world point a click would snap to (hover), and an edge line's first point.
hover = None
edge_start = None
# (camera name, whether its plate image was turned off) while H hides the plate and what's drawn on it, else None.
plate_hidden = None
# Plate pixels across the loupe (Ctrl+wheel while holding); kept for the rest of the session.
LOUPE_PIXELS = 36
LOUPE_ZOOM = (8, 400)
loupe_pixels = LOUPE_PIXELS


# Cameras ------------------------------------------------------------------------------------------------------------

def intrinsics(scene, camera):
    """(focal px, principal point px) of camera on the render resolution, from its view frame (so sensor fit and
    lens shift are Blender's own). Assumes square pixels."""
    w, h = scene.render.resolution_x, scene.render.resolution_y
    frame = camera.data.view_frame(scene=scene)
    xs = [v.x for v in frame]
    ys = [v.y for v in frame]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    focal = w * abs(frame[0].z) / (x1 - x0)
    return focal, np.array([-x0 / (x1 - x0) * w, -y0 / (y1 - y0) * h])


def pose(camera):
    """(R, C) of a camera object: R world -> camera, C its centre."""
    m = camera.matrix_world
    return np.array(m.to_3x3().normalized()).T, np.array(m.translation)


def set_pose(camera, R, C):
    """Place camera at (R, C), keeping its Euler rotation continuous with what it was (no flips between keys)."""
    m = Matrix(np.asarray(R).T.tolist()).to_4x4()
    m.translation = Vector(C)
    before = camera.rotation_euler.copy()
    camera.matrix_world = m
    if camera.rotation_mode not in {"QUATERNION", "AXIS_ANGLE"}:
        camera.rotation_euler.make_compatible(before)


def plate_pixels(scene, uv):
    return np.array([uv[0] * scene.render.resolution_x, uv[1] * scene.render.resolution_y])


AXES = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}


def has_3d(pin):
    """The pin has its point on the lidar."""
    return pin.has_co and pin.target is not None


def pin_world(pin):
    return pin.target.matrix_world @ Vector(pin.co)


def edge_world(line):
    """(p1, p2): the lidar edge's two points in world space."""
    return line.target_a.matrix_world @ Vector(line.co_a), line.target_b.matrix_world @ Vector(line.co_b)


# Pins per frame -----------------------------------------------------------------------------------------------------

def pin_frame(pin, frame):
    """The pin's entry for frame, or None."""
    for f in pin.frames:
        if f.frame == frame:
            return f
    return None


def plate_uv(pin, frame):
    """The pin's plate position on frame, or None if it is not placed there."""
    f = pin_frame(pin, frame)
    if f is not None:
        return tuple(f.uv)
    if pin.has_uv and not len(pin.frames):                    # older pin: one position for every frame
        return tuple(pin.uv)
    return None


def set_plate_uv(pin, frame, uv):
    """Place pin on the plate on frame; returns its frame entry."""
    if pin.has_uv and not len(pin.frames):
        old = pin.frames.add()
        old.frame, old.uv = frame, pin.uv
    pin.has_uv = False
    f = pin_frame(pin, frame)
    if f is None:
        f = pin.frames.add()
        f.frame = frame
    f.uv = uv
    f.error = -1.0
    return f


def unset_plate_uv(pin, frame):
    if pin.has_uv and not len(pin.frames):
        pin.has_uv = False
    for i, f in enumerate(pin.frames):
        if f.frame == frame:
            pin.frames.remove(i)
            return


def ready(pin, frame):
    """The pin has both its world point and a plate position on frame."""
    return has_3d(pin) and plate_uv(pin, frame) is not None


# Lines per frame ----------------------------------------------------------------------------------------------------

def line_frame(line, frame):
    """The line's entry (its ends on the plate) for frame, or None."""
    for f in line.frames:
        if f.frame == frame:
            return f
    return None


def set_line_ends(line, frame, a, b):
    """Place line on the plate on frame (ends a, b as 0-1 plate positions); returns its frame entry."""
    f = line_frame(line, frame)
    if f is None:
        f = line.frames.add()
        f.frame = frame
    f.a, f.b = a, b
    f.error = -1.0
    return f


def unset_line(line, frame):
    for i, f in enumerate(line.frames):
        if f.frame == frame:
            line.frames.remove(i)
            return


def line_has_3d(line):
    """An axis line knows its direction; an edge line needs both its lidar points."""
    return line.axis != "EDGE" or (line.has_edge and line.target_a is not None and line.target_b is not None)


def line_ready(line, frame):
    return line_has_3d(line) and line_frame(line, frame) is not None


# Camera view <-> plate ----------------------------------------------------------------------------------------------

def view_camera(context):
    """The camera a 3D view looks through in camera view (its local camera, else the scene's)."""
    space = context.space_data
    if space is not None and getattr(space, "use_local_camera", False) and space.camera:
        return space.camera
    return context.scene.camera


def camera_frame(scene, camera, region, rv3d):
    """(x0, y0, x1, y1): the camera frame in region pixels, or None if it is off screen."""
    corners = [camera.matrix_world @ v for v in camera.data.view_frame(scene=scene)]
    pts = [view3d_utils.location_3d_to_region_2d(region, rv3d, c) for c in corners]
    if any(p is None for p in pts):
        return None
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def uv_to_region(frame, uv):
    x0, y0, x1, y1 = frame
    return Vector((x0 + uv[0] * (x1 - x0), y0 + uv[1] * (y1 - y0)))


def region_to_uv(frame, p):
    x0, y0, x1, y1 = frame
    return (p[0] - x0) / (x1 - x0), (p[1] - y0) / (y1 - y0)


# Lidar --------------------------------------------------------------------------------------------------------------

def lidar_objects(context):
    coll = context.scene.lineup.lidar
    objs = coll.all_objects if coll else context.view_layer.objects
    return [o for o in objs if o.type in {"MESH", "POINTCLOUD"} and o.visible_get(view_layer=context.view_layer)]


def _positions(obj_eval):
    """Object-space point positions (N, 3) float32 of an evaluated mesh or point cloud."""
    data = obj_eval.data
    if obj_eval.type == "MESH":
        co = np.empty(len(data.vertices) * 3, np.float32)
        data.vertices.foreach_get("co", co)
    else:
        attr = data.attributes.get("position")
        if attr is None:
            return np.empty((0, 3), np.float32)
        co = np.empty(len(attr.data) * 3, np.float32)
        attr.data.foreach_get("vector", co)
    return co.reshape(-1, 3)


class Picker:
    """Snaps to the lidar: ray casts onto meshes with faces, nearest point on screen for points-only meshes and
    point clouds. Built once per tool session (BVH trees of a big scan take a moment), and again after an undo.
    Objects are kept by name and looked up on a hit, since undo replaces them."""

    def __init__(self, context):
        depsgraph = context.evaluated_depsgraph_get()
        self.surfaces = []      # (object name, BVHTree in object space, matrix_world)
        self.clouds = []        # (object name, positions (N, 3) object space, matrix_world as numpy)
        for o in lidar_objects(context):
            ev = o.evaluated_get(depsgraph)
            if o.type == "MESH" and len(ev.data.polygons):
                self.surfaces.append((o.name, BVHTree.FromObject(o, depsgraph), o.matrix_world.copy()))
            else:
                co = _positions(ev)
                if len(co):
                    self.clouds.append((o.name, co, np.array(o.matrix_world, np.float32)))

    def __bool__(self):
        return bool(self.surfaces or self.clouds)

    def pick_ray(self, origin, direction):
        """(distance, object, object-space point) of the nearest surface hit along a world ray, or None."""
        best = None
        for o, bvh, m in self.surfaces:
            inv = m.inverted()
            hit = bvh.ray_cast(inv @ origin, (inv.to_3x3() @ direction).normalized())[0]
            if hit is not None:
                dist = (m @ hit - origin).length
                if best is None or dist < best[0]:
                    best = (dist, bpy.data.objects.get(o), hit)
        return best

    def pick_points(self, perspective, size, mouse, origin):
        """(distance, object, object-space point) of the frontmost point within POINT_RADIUS of mouse, or None.
        perspective is the view's 4x4 perspective matrix, size the region (width, height)."""
        perspective = np.array(perspective, np.float32)
        size = np.asarray(size, np.float32)
        mouse = np.asarray(mouse, np.float32)
        origin = np.asarray(origin, np.float32)
        best = None
        for o, co, m in self.clouds:
            world = co @ m[:3, :3].T + m[:3, 3]
            clip = world @ perspective[:, :3].T + perspective[:, 3]
            w = clip[:, 3]
            with np.errstate(divide="ignore", invalid="ignore"):
                ndc = clip[:, :3] / w[:, None]
            screen = (ndc[:, :2] * 0.5 + 0.5) * size
            near = (w > 0) & (np.sum((screen - mouse) ** 2, axis=1) < POINT_RADIUS ** 2)
            if not near.any():
                continue
            idx = np.flatnonzero(near)
            i = idx[np.argmin(ndc[idx, 2])]
            dist = float(np.linalg.norm(world[i] - origin))
            if best is None or dist < best[0]:
                best = (dist, bpy.data.objects.get(o), Vector(co[i].tolist()))
        return best

    def pick(self, region, rv3d, mouse):
        """(object, object-space point) under mouse (region pixels), or None."""
        origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouse)
        direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouse)
        hits = [h for h in (self.pick_ray(origin, direction),
                            self.pick_points(rv3d.perspective_matrix, (region.width, region.height), mouse, origin))
                if h is not None]
        if not hits:
            return None
        _dist, obj, co = min(hits, key=lambda h: h[0])
        return (obj, co) if obj is not None else None
