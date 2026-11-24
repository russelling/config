"""Operators: load a plate, place and drag pins, draw perspective lines, solve and keyframe the camera per frame,
manage pins, lines and solves."""
import os
import re
import time

import bpy
import numpy as np
from bpy.props import EnumProperty, IntProperty, StringProperty
from bpy.app.handlers import persistent
from bpy.types import Operator
from bpy_extras import view3d_utils
from bpy_extras.io_utils import ImportHelper
from mathutils import Vector

from . import pick, props, solve


def lineup_camera(context):
    cam = context.scene.camera
    return cam if cam is not None and cam.type == "CAMERA" else None


def redraw(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def plate_background(camera):
    """The camera background image showing the lineup plate, or None."""
    plate = camera.data.lineup.plate
    for bg in camera.data.background_images:
        if bg.source == "IMAGE" and bg.image is not None and bg.image == plate:
            return bg
    return None


def hide_plate(camera):
    """Hide camera's plate image and what's drawn on the plate (H while a tool runs), for clicking the lidar."""
    if pick.plate_hidden is not None:
        return
    bg = plate_background(camera)
    turned_off = bg is not None and bg.show_background_image
    if turned_off:
        bg.show_background_image = False
    pick.plate_hidden = (camera.name, turned_off)


def show_plate():
    """Undo hide_plate."""
    if pick.plate_hidden is None:
        return
    name, turned_off = pick.plate_hidden
    pick.plate_hidden = None
    camera = bpy.data.objects.get(name)
    if turned_off and camera is not None and camera.type == "CAMERA":
        bg = plate_background(camera)
        if bg is not None:
            bg.show_background_image = True


def frame_timelines(context):
    """View All in every timeline / dope sheet, so a new frame range is in view."""
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "DOPESHEET_EDITOR":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            with context.temp_override(window=window, area=area, region=region):
                try:
                    bpy.ops.action.view_all()
                except RuntimeError:                          # e.g. nothing to frame in this editor
                    pass


def sequence_range(filepath):
    """(first, last, picked) frame numbers of the image sequence filepath belongs to (plate.1001.exr,
    plate_0042.png), or None for a lone file."""
    directory, name = os.path.split(bpy.path.abspath(filepath))
    m = re.match(r"^(.*?)(\d+)(\.[^.]+)$", name)
    if m is None:
        return None
    head, digits, tail = m.groups()
    pattern = re.compile(re.escape(head) + r"(\d+)" + re.escape(tail) + "$", re.IGNORECASE)
    try:
        frames = sorted(int(n.group(1)) for n in map(pattern.match, os.listdir(directory or ".")) if n)
    except OSError:
        return None
    if len(frames) < 2:
        return None
    return frames[0], frames[-1], int(digits)


ROTATION_PATHS = {"QUATERNION": "rotation_quaternion", "AXIS_ANGLE": "rotation_axis_angle"}
LIMIT_HINT = "change the Rough Focal or widen the ± Range"
WILD = 10                  # a solve off by more than this many times the outlier distance (RMS) isn't applied
                           # (40 px on an HD plate)


def camera_keys(camera):
    """(owner, data path) of everything a solve keys."""
    return ((camera, "location"), (camera, ROTATION_PATHS.get(camera.rotation_mode, "rotation_euler")),
            (camera.data, "lens"))


def key_record(lineup, frame, create=False):
    """(index, LineupKey) of the solve on frame, or (-1, None); create adds it in frame order."""
    for i, k in enumerate(lineup.keys):
        if k.frame == frame:
            return i, k
    if not create:
        return -1, None
    lineup.keys.add().frame = frame
    i = sum(k.frame < frame for k in lineup.keys)
    lineup.keys.move(len(lineup.keys) - 1, i)
    return i, lineup.keys[i]


def constraints(lineup, frame, used=True):
    """(pins, axis lines, edge lines) ready on frame (only those in use, unless used=False)."""
    pins = [p for p in lineup.pins if pick.ready(p, frame) and (p.use or not used)]
    lines = [ln for ln in lineup.lines if pick.line_ready(ln, frame) and (ln.use or not used)]
    return pins, [ln for ln in lines if ln.axis != "EDGE"], [ln for ln in lines if ln.axis == "EDGE"]


def enough(lineup, frame):
    """Whether frame has enough pins and lines to solve (as solve.solve counts them). Lines alone (2+ along axes)
    are enough for rotation and focal."""
    pins, axis, edges = constraints(lineup, frame)
    if len(pins) + len(edges) < solve.MIN_POSITIONAL:
        return len(axis) >= 2
    unknowns = 6 if lineup.lock_focal else 7
    return 2 * len(pins) + len(axis) + 2 * len(edges) > unknowns


def _segment(scene, line, frame):
    f = pick.line_frame(line, frame)
    return pick.plate_pixels(scene, f.a), pick.plate_pixels(scene, f.b)


def key_camera(camera, frame):
    for owner, path in camera_keys(camera):
        owner.keyframe_insert(path, frame=frame)
    if pick.unkeyed == (camera.name, frame):
        pick.unkeyed = None


def key_unkeyed():
    """Key the auto-solve waiting to be keyed, if any."""
    if pick.unkeyed is None:
        return
    name, frame = pick.unkeyed
    pick.unkeyed = None
    camera = bpy.data.objects.get(name)
    if camera is not None and camera.type == "CAMERA":
        key_camera(camera, frame)


def solve_camera(scene, camera, key=True):
    """Solve camera on the current frame from the pins and lines placed there: moves it, sets its lens and keys both
    on this frame (or, with key=False, leaves that to key_unkeyed: auto-solve while placing pins, so the keys aren't
    rewritten on every click). A solve too far off to be right is not applied. Returns (ok, message)."""
    cam = camera.data
    lineup = cam.lineup
    frame = scene.frame_current
    if cam.type != "PERSP":
        return False, "Lineup needs a perspective camera"
    if abs(scene.render.pixel_aspect_x - scene.render.pixel_aspect_y) > 1e-6:
        return False, "Lineup needs square pixels (Output > Format > Aspect)"
    pins, axis, edges = constraints(lineup, frame)

    focal, principal = pick.intrinsics(scene, camera)
    prior = focal * lineup.focal_prior / cam.lens           # rough focal in pixels
    points = np.array([pick.pin_world(p) for p in pins]).reshape(-1, 3)
    pixels = np.array([pick.plate_pixels(scene, pick.plate_uv(p, frame)) for p in pins]).reshape(-1, 2)
    axis_lines = [(*_segment(scene, ln, frame), np.array(pick.AXES[ln.axis])) for ln in axis]
    edge_lines = [(*_segment(scene, ln, frame), *map(np.array, pick.edge_world(ln))) for ln in edges]
    R, C = pick.pose(camera)
    try:
        focal_range = 0.0 if lineup.lock_focal else prior * lineup.focal_range / lineup.focal_prior
        sol = solve.solve(points, pixels, prior, principal, focal_range=focal_range, initial=(R, C),
                          axis_lines=axis_lines, edge_lines=edge_lines, plate_width=scene.render.resolution_x,
                          drop_outliers=lineup.drop_outliers, lidar_sigma=lineup.lidar_accuracy)
    except ValueError as e:
        return False, f"Frame {frame}: {e}"
    if sol.rms > WILD * solve.OUTLIER_PX * scene.render.resolution_x / solve.REFERENCE_WIDTH:
        return False, (f"Frame {frame}: no camera fits (RMS {sol.rms:.0f} px), left as it was. Check the pins (a lidar "
                       f"point on the wrong feature?) and the Rough Focal")

    if pick.unkeyed is not None and pick.unkeyed != (camera.name, frame):
        key_unkeyed()                                         # another camera or frame's auto-solve
    pick.set_pose(camera, sol.R, sol.C)
    cam.lens = lineup.focal_prior * sol.f / prior
    if key:
        key_camera(camera, frame)
    else:
        pick.unkeyed = (camera.name, frame)

    # Errors for everything on this frame, in use or not; which ones are too far off, and which the solve left out.
    all_pins, all_axis, all_edges = constraints(lineup, frame, used=False)

    def marked(masks):
        return {x.as_pointer() for items, mask in zip((pins, axis, edges), masks) for x, on in zip(items, mask) if on}

    out, far = marked(sol.left_out), marked(sol.far)
    if all_pins:
        world = np.array([pick.pin_world(p) for p in all_pins])
        projected, _depth = solve.project(sol.R, sol.C, sol.f, principal, world)
        for p, xy in zip(all_pins, projected):
            uv = pick.plate_uv(p, frame)
            entry = pick.set_plate_uv(p, frame, uv)          # also moves an older pin onto this frame
            entry.error = float(np.linalg.norm(xy - pick.plate_pixels(scene, uv)))
            entry.left_out = p.as_pointer() in out
            entry.far = p.as_pointer() in far
    for ln in all_axis:
        a, b = _segment(scene, ln, frame)
        r = solve.axis_residuals(sol.R, sol.f, principal, a[None], b[None], np.array([pick.AXES[ln.axis]]))
        pick.line_frame(ln, frame).error = float(abs(r[0]))
        pick.line_frame(ln, frame).left_out = ln.as_pointer() in out
        pick.line_frame(ln, frame).far = ln.as_pointer() in far
    for ln in all_edges:
        a, b = _segment(scene, ln, frame)
        p1, p2 = pick.edge_world(ln)
        r = solve.edge_residuals(sol.R, sol.C, sol.f, principal, a[None], b[None], np.array([p1]), np.array([p2]))
        pick.line_frame(ln, frame).error = float(np.sqrt(np.mean(r ** 2))) if r is not None else -1.0
        pick.line_frame(ln, frame).left_out = ln.as_pointer() in out
        pick.line_frame(ln, frame).far = ln.as_pointer() in far
    i, key = key_record(lineup, frame, create=True)
    key.pins = len(pins)
    key.lines = len(axis) + len(edges)
    key.rms = sol.rms
    key.left_out = len(out)
    key.far = len(far)
    key.left_out_px = sol.outlier_px
    key.lens = cam.lens
    key.focal_sigma = sol.focal_sigma * cam.lens / sol.f
    key.at_limit = sol.at_limit and not lineup.lock_focal
    key.focal_held = not sol.focal_determined and not lineup.lock_focal
    lineup.active_key = i

    focal_text = f"{cam.lens:.2f} mm" + (f" ±{key.focal_sigma:.2f}" if key.focal_sigma > 0 else "")
    if key.at_limit:
        focal_text += f" at the edge of the range: {LIMIT_HINT}"
    if key.focal_held:
        focal_text += (" (mostly the Rough Focal: these barely fix it; spread pins in depth, or draw horizontals "
                       "on X and Y)")
    key.position_fixed = sol.position_fixed
    counted = f"{len(pins)} pins" + (f", {key.lines} lines" if key.lines else "")
    placed = ""
    if sol.position_fixed:
        placed = "  (rotation and focal from the lines; position kept: add 2 pins to place it)"
    dropped = ""
    if out:
        dropped = f", {len(out)} left out (over {sol.outlier_px:.1f} px + lidar accuracy)"
    elif far:
        dropped = f", {len(far)} far off (over {sol.outlier_px:.1f} px + lidar accuracy), still in the fit"
    return True, f"Frame {frame}: {counted}{dropped}, RMS {sol.rms:.2f} px, focal {focal_text}{placed}"


class LINEUP_OT_load_plate(Operator, ImportHelper):
    """Load an undistorted plate onto the scene camera: background image, render resolution, rough focal"""
    bl_idname = "lineup.load_plate"
    bl_label = "Load Plate"
    bl_options = {"REGISTER", "UNDO"}

    filter_glob: StringProperty(default="*.exr;*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.dpx;*.hdr", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return lineup_camera(context) is not None

    def execute(self, context):
        camera = lineup_camera(context)
        try:
            image = bpy.data.images.load(self.filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        w, h = image.size
        if not w or not h:
            self.report({"ERROR"}, f"Could not read {os.path.basename(self.filepath)}")
            return {"CANCELLED"}
        seq = sequence_range(self.filepath)
        image.source = "SEQUENCE" if seq else "FILE"
        scene = context.scene
        scene.render.resolution_x, scene.render.resolution_y = w, h
        scene.render.resolution_percentage = 100
        scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1.0

        cam = camera.data
        lineup = cam.lineup
        bg = plate_background(camera)
        lineup.plate = image
        if bg is None:
            bg = cam.background_images.new()
            bg.display_depth = "FRONT"
            bg.alpha = 0.5
        bg.image = image
        bg.frame_method = "FIT"
        cam.show_background_images = True
        lineup.focal_prior = cam.lens
        frames = ""
        if seq:
            # File numbers on the timeline: plate.1001 shows on frame 1001. Scrub to choose the lineup frame.
            first, last, picked = seq
            user = bg.image_user
            user.frame_start = first
            user.frame_offset = first - 1
            user.frame_duration = last - first + 1
            user.use_auto_refresh = True
            user.use_cyclic = False
            scene.frame_start, scene.frame_end = first, last
            scene.frame_set(picked)
            frame_timelines(context)
            frames = f", frames {first}-{last}"
        redraw(context)
        self.report({"INFO"}, f"Plate {image.name}: {w} x {h}{frames}")
        return {"FINISHED"}


class LINEUP_OT_toggle_plate_depth(Operator):
    """Flip the plate between in front of and behind the geometry"""
    bl_idname = "lineup.toggle_plate_depth"
    bl_label = "Toggle Plate Front/Back"

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        return camera is not None and plate_background(camera) is not None

    def execute(self, context):
        bg = plate_background(lineup_camera(context))
        bg.display_depth = "BACK" if bg.display_depth == "FRONT" else "FRONT"
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_solve(Operator):
    """Solve the scene camera on this frame from the pins and lines placed here, and keyframe it"""
    bl_idname = "lineup.solve"
    bl_label = "Solve"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return lineup_camera(context) is not None

    def execute(self, context):
        pick.tool = None                                      # Solve switches the pin / line tools off
        ok, message = solve_camera(context.scene, lineup_camera(context))
        redraw(context)
        self.report({"INFO"} if ok else {"WARNING"}, message)
        return {"FINISHED"} if ok else {"CANCELLED"}


def _view3d_under_mouse(context, event):
    """(region, rv3d, mouse in region pixels, space) of the 3D view main region under the mouse, or None (also when
    the mouse is over a sidebar, header or toolbar)."""
    x, y = event.mouse_x, event.mouse_y
    for area in context.window.screen.areas:
        if area.type != "VIEW_3D" or not (area.x <= x < area.x + area.width and area.y <= y < area.y + area.height):
            continue
        over_ui = any(r.type != "WINDOW" and r.width > 1 and r.x <= x < r.x + r.width and r.y <= y < r.y + r.height
                      for r in area.regions)
        if over_ui:
            return None
        for r in area.regions:
            if r.type == "WINDOW" and r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                return r, r.data, Vector((x - r.x, y - r.y)), area.spaces.active
    return None


DRAG_RADIUS = 10           # px: how close a click must be to a marker or line end to drag it
HOVER_EVERY = 0.03         # s: least time between snap previews on the lidar (a big cloud takes a moment)
PLATE_HINT = "H: hide / show the plate"
FINE = 0.1                 # held point speed with Shift
ZOOM_STEP = 1.25           # loupe zoom per Ctrl+wheel notch
STOP_HINT = "Stop: click the button again, Solve, Esc or right-click"


class _PlateTool:
    """What the pin and line tools share. They are toggles: clicking the lit button again, starting the other tool,
    or Solve stops the running one (pick.tool / pick.session say which one should run; a tool stops on its next event
    once it isn't that one). Nothing Blender owns is kept on the tool between events, since undo replaces it all: the
    camera is looked up each time, and the lidar snapping is rebuilt after an undo."""

    TOOL = ""

    @property
    def camera(self):
        return lineup_camera(bpy.context)

    def _toggled_off(self, context):
        """Clicking the lit button turns the tool off (the running one stops on its next event)."""
        if self.index < 0 and pick.tool == self.TOOL:
            pick.tool = None
            redraw(context)
            return True
        return False

    def _begin(self, context):
        pick.session += 1
        pick.tool = self.TOOL
        self.session = pick.session
        self.generation = pick.undo_generation
        self._prompt(context)
        context.window_manager.modal_handler_add(self)
        redraw(context)
        return {"RUNNING_MODAL"}

    def _replaced(self):
        """Another pin / line tool has started since this one (it may be working on what this one left half done,
        e.g. Re-pick Lidar on a half-placed pin), so that is left as it is."""
        return pick.session != self.session

    def _hold(self, region, rect, at, mouse):
        """Start holding a point on the plate at region position at (the mouse is at mouse)."""
        self.drag_view = (region, rect)
        self.drag_pos = Vector(at)
        self.last_mouse = Vector(mouse)
        pick.loupe = {"region": region, "at": self.drag_pos.copy(),
                      "uv": _clamp_uv(pick.region_to_uv(rect, self.drag_pos))}
        return pick.loupe["uv"]

    def _zoomed(self, context, event):
        """Ctrl+wheel while holding a point zooms the loupe."""
        if not event.ctrl or event.type not in {"WHEELUPMOUSE", "WHEELDOWNMOUSE"}:
            return False
        step = 1 / ZOOM_STEP if event.type == "WHEELUPMOUSE" else ZOOM_STEP
        pick.loupe_pixels = min(max(pick.loupe_pixels * step, pick.LOUPE_ZOOM[0]), pick.LOUPE_ZOOM[1])
        redraw(context)
        return True

    def _held_uv(self, event):
        """Move the held point with the mouse (a tenth as fast with Shift); its plate position."""
        region, rect = self.drag_view
        mouse = Vector((event.mouse_x - region.x, event.mouse_y - region.y))
        if event.type == "MOUSEMOVE":
            self.drag_pos += (mouse - self.last_mouse) * (FINE if event.shift else 1.0)
        self.last_mouse = mouse
        uv = _clamp_uv(pick.region_to_uv(rect, self.drag_pos))
        pick.loupe = {"region": region, "at": self.drag_pos.copy(), "uv": uv}
        return uv

    def _plate_key(self, context, event):
        """H hides or shows the plate and what's drawn on it (always taken here: H would hide objects)."""
        if event.type != "H" or event.ctrl or event.alt or event.shift or event.oskey:
            return False
        if event.value == "PRESS":
            if pick.plate_hidden is None:
                hide_plate(self.camera)
            else:
                show_plate()
            redraw(context)
        return True

    def _hover(self, context, event):
        """Show where a lidar click would snap (throttled: snapping to a big cloud takes a moment)."""
        now = time.perf_counter()
        if now - getattr(self, "hovered", 0.0) < HOVER_EVERY:
            return
        self.hovered = now
        under = _view3d_under_mouse(context, event)
        hit = self.picker.pick(*under[:3]) if under is not None and self.picker else None
        before = pick.hover
        pick.hover = hit[0].matrix_world @ hit[1] if hit is not None else None
        if pick.hover is not None or before is not None:
            redraw(context)

    def _end(self, context):
        key_unkeyed()
        show_plate()
        pick.loupe = pick.hover = pick.edge_start = None
        if not self._replaced():
            pick.tool = None
        context.workspace.status_text_set(None)
        redraw(context)

    def cancel(self, context):
        """Blender stopped the tool (e.g. a file load)."""
        self._end(context)

    def _status(self, context, text):
        context.workspace.status_text_set(f"{text}   ·   {STOP_HINT}")

    def _stopped(self, context, event):
        """The tool should stop: switched off or over to the other tool, the camera gone, or Esc / right-click."""
        if pick.session != self.session or pick.tool != self.TOOL or self.camera is None:
            return True
        return event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS"

    def _undone(self, context):
        """After an undo: snapping rebuilt, and whatever was half done dropped (it may no longer exist)."""
        if self.generation == pick.undo_generation:
            return False
        self.generation = pick.undo_generation
        self.picker = pick.Picker(context)
        self._reset()
        self._prompt(context)
        return True

    def _plate_rect(self, context, region, rv3d, space):
        """The camera frame in region pixels if this view looks through the lineup camera, else None (and a hint)."""
        camera = space.camera if space.use_local_camera and space.camera else context.scene.camera
        if rv3d.view_perspective != "CAMERA" or camera != self.camera:
            self.report({"WARNING"}, f"Click the plate in camera view through {self.camera.name}")
            return None
        return pick.camera_frame(context.scene, self.camera, region, rv3d)

    def _auto_solve(self, context):
        lineup = self.camera.data.lineup
        if lineup.auto_solve and enough(lineup, context.scene.frame_current):
            ok, message = solve_camera(context.scene, self.camera, key=False)
            self.report({"INFO"} if ok else {"WARNING"}, message)


NEW_POINT = "NEW"          # dragging: a new point held on the plate, not yet a pin


def _clamp_uv(uv):
    return min(max(uv[0], 0.0), 1.0), min(max(uv[1], 0.0), 1.0)


class LINEUP_OT_pick(_PlateTool, Operator):
    """Add pins (on / off): click a feature on the plate (camera view), then the same feature on the lidar (any 3D
    view). Drag a pin's marker to move it on this frame"""
    bl_idname = "lineup.pick"
    bl_label = "Add Pins"
    bl_options = {"REGISTER", "UNDO"}
    TOOL = "PINS"

    index: IntProperty(default=-1, options={"SKIP_SAVE"}, description="Pin to re-pick (-1: add new pins)")
    what: EnumProperty(items=[("BOTH", "Both", "Plate, then lidar"), ("PLATE", "Plate", "Plate only (re-pick)"),
                              ("GEO", "Lidar", "Lidar only (re-pick)")], default="BOTH", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return lineup_camera(context) is not None and context.area is not None

    def invoke(self, context, event):
        if self._toggled_off(context):
            return {"FINISHED"}
        lineup = self.camera.data.lineup
        if self.index >= len(lineup.pins):
            return {"CANCELLED"}
        if self.index < 0:
            self.what = "BOTH"                                # new pins: plate, then lidar
        self.picker = pick.Picker(context) if self.what != "PLATE" else None
        if self.what != "PLATE" and not self.picker:
            self.report({"WARNING"}, "No visible lidar to pick on (check the Lidar collection)")
            return {"CANCELLED"}
        self.new_pin = self.index < 0
        self.step = "GEO" if self.what == "GEO" else "PLATE"
        self.current = self.index
        self.dragging = None
        if self.index >= 0:
            lineup.active_pin = self.index
        return self._begin(context)

    def _prompt(self, context):
        n = (len(self.camera.data.lineup.pins) if self.new_pin and self.step == "PLATE" else self.current) + 1
        frame = context.scene.frame_current
        if self.step == "GEO":
            text = f"Pin {n}: click the same feature on the lidar (any 3D view)   ·   {PLATE_HINT}"
        elif self.new_pin:
            text = (f"Frame {frame}: press on a feature on the plate, release to place (Shift: fine, Ctrl+wheel: zoom)   ·   drag a "
                    f"marker to move that pin   ·   Ctrl: new pin next to a marker")
        else:
            text = f"Pin {n}: click the feature on the plate (camera view), frame {frame}"
        self._status(context, text)

    def _reset(self):
        self.step = "PLATE" if self.new_pin or self.what != "GEO" else "GEO"
        self.dragging = None

    def _finish(self, context):
        camera = self.camera
        if camera is not None and self.new_pin and self.step == "GEO" and self.what == "BOTH" and not self._replaced():
            lineup = camera.data.lineup
            if 0 <= self.current < len(lineup.pins):
                lineup.pins.remove(self.current)              # half-placed pin
                lineup.active_pin = min(lineup.active_pin, len(lineup.pins) - 1)
        self._end(context)
        return {"FINISHED"}

    def _placed(self, context):
        """A pin is complete: solve if asked, then carry on or stop."""
        self._auto_solve(context)
        if not self.new_pin:
            return self._finish(context)
        self.step = "PLATE"
        self._prompt(context)
        return {"RUNNING_MODAL"}

    def _marker_under(self, context, region, rv3d, rect, mouse):
        """(pin index, region position) of the pin marker nearest mouse on this frame: its plate cross if placed
        here, else where its world point lands. None if none is within DRAG_RADIUS."""
        frame = context.scene.frame_current
        radius = DRAG_RADIUS * context.preferences.system.ui_scale
        best = None
        for i, pin in enumerate(self.camera.data.lineup.pins):
            uv = pick.plate_uv(pin, frame)
            if uv is not None:
                p = pick.uv_to_region(rect, uv)
            elif pick.has_3d(pin):
                p = view3d_utils.location_3d_to_region_2d(region, rv3d, pick.pin_world(pin))
            else:
                continue
            if p is not None and (p - mouse).length < radius and (best is None or (p - mouse).length < best[0]):
                best = ((p - mouse).length, i, p)
        return best[1:] if best else None

    def _drag(self, context, event):
        """A point held on the plate: a pin's marker being moved, or (NEW_POINT) where the next pin goes. It
        follows the mouse and lands on release."""
        if self._zoomed(context, event):
            return {"RUNNING_MODAL"}
        uv = self._held_uv(event)
        pins = self.camera.data.lineup.pins
        moving = self.dragging != NEW_POINT
        if moving and not 0 <= self.dragging < len(pins):     # the pin went (undo, removed)
            self.dragging = pick.loupe = None
            return {"RUNNING_MODAL"}
        if moving and event.type in {"MOUSEMOVE", "LEFTMOUSE"}:
            pick.set_plate_uv(pins[self.dragging], context.scene.frame_current, uv)
        redraw(context)
        if event.type != "LEFTMOUSE" or event.value != "RELEASE":
            return {"RUNNING_MODAL"}
        self.dragging = pick.loupe = None
        if not moving:
            return self._place(context, uv)
        self._auto_solve(context)
        self._prompt(context)
        return {"RUNNING_MODAL"}

    def _place(self, context, uv):
        """The plate position of a new (or re-picked) pin is chosen: on to the lidar, or done."""
        lineup = self.camera.data.lineup
        if self.new_pin:
            lineup.pins.add()
            self.current = lineup.active_pin = len(lineup.pins) - 1
        elif not 0 <= self.current < len(lineup.pins):
            return self._finish(context)
        pick.set_plate_uv(lineup.pins[self.current], context.scene.frame_current, uv)
        redraw(context)
        if self.what == "PLATE":
            return self._placed(context)
        self.step = "GEO"
        self._prompt(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self._stopped(context, event):
            return self._finish(context)
        if self._undone(context):
            return {"PASS_THROUGH"}
        if self.dragging is not None:
            return self._drag(context, event)
        if self._plate_key(context, event):
            return {"RUNNING_MODAL"}
        if event.type == "MOUSEMOVE" and self.step == "GEO":
            self._hover(context, event)
        elif pick.hover is not None and self.step != "GEO":
            pick.hover = None
            redraw(context)
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        under = _view3d_under_mouse(context, event)
        if under is None:
            return {"PASS_THROUGH"}
        region, rv3d, mouse, space = under
        lineup = self.camera.data.lineup
        frame = context.scene.frame_current
        if self.step == "GEO" and not 0 <= self.current < len(lineup.pins):
            if not self.new_pin:
                return self._finish(context)                  # the pin being re-picked is gone
            self._reset()

        if self.step == "PLATE":
            rect = self._plate_rect(context, region, rv3d, space)
            if rect is None:
                return {"RUNNING_MODAL"}
            if self.new_pin and not event.ctrl:
                hit = self._marker_under(context, region, rv3d, rect, mouse)
                if hit is not None:
                    self.dragging, marker = hit
                    lineup.active_pin = self.dragging
                    pick.set_plate_uv(lineup.pins[self.dragging], frame, self._hold(region, rect, marker, mouse))
                    redraw(context)
                    return {"RUNNING_MODAL"}
            uv = pick.region_to_uv(rect, mouse)
            if not (0 <= uv[0] <= 1 and 0 <= uv[1] <= 1):
                self.report({"WARNING"}, "Click inside the camera frame")
                return {"RUNNING_MODAL"}
            self.dragging = NEW_POINT                         # placed on release
            self._hold(region, rect, mouse, mouse)
            redraw(context)
            return {"RUNNING_MODAL"}

        hit = self.picker.pick(region, rv3d, mouse)
        if hit is None:
            self.report({"WARNING"}, "No lidar under the mouse")
            return {"RUNNING_MODAL"}
        pin = lineup.pins[self.current]
        pin.target, pin.co = hit
        pin.has_co = True
        pick.hover = None
        redraw(context)
        return self._placed(context)


AXIS_KEYS = {"X": "X", "Y": "Y", "Z": "Z", "E": "EDGE"}


class LINEUP_OT_lines(_PlateTool, Operator):
    """Draw lines (on / off): drag along a straight edge on the plate (camera view), or click each end (zoom and pan
    in between). X / Y / Z: the world axis it runs along; E: an edge you then click twice on the lidar. Drag a line's
    end to move it on this frame"""
    bl_idname = "lineup.lines"
    bl_label = "Draw Lines"
    bl_options = {"REGISTER", "UNDO"}
    TOOL = "LINES"

    index: IntProperty(default=-1, options={"SKIP_SAVE"},
                       description="Line whose lidar edge to (re-)pick (-1: draw new lines)")

    @classmethod
    def poll(cls, context):
        return lineup_camera(context) is not None and context.area is not None

    def invoke(self, context, event):
        if self._toggled_off(context):
            return {"FINISHED"}
        lineup = self.camera.data.lineup
        if self.index >= len(lineup.lines):
            return {"CANCELLED"}
        self.picker = pick.Picker(context)
        # IDLE, DRAW (new line, end held), STRETCH (new line, end following the mouse until the next click), MOVE (an
        # end held), EDGE_A / EDGE_B (lidar clicks)
        self.state = "IDLE"
        self.current = self.index
        self.new_line = False
        if self.index >= 0:
            if not self.picker:
                self.report({"WARNING"}, "No visible lidar to pick on (check the Lidar collection)")
                return {"CANCELLED"}
            lineup.active_line = self.index
            self.state = "EDGE_A"
        return self._begin(context)

    def _prompt(self, context):
        lineup = self.camera.data.lineup
        if self.state == "EDGE_A":
            text = f"Line {self.current + 1}: click a point on that edge on the lidar (any 3D view)   ·   {PLATE_HINT}"
        elif self.state == "EDGE_B":
            text = (f"Line {self.current + 1}: click another point along the same edge on the lidar, well apart   ·   "
                    f"{PLATE_HINT}")
        elif self.state == "STRETCH":
            text = (f"Line {self.current + 1}: click where it ends (hold for the loupe; Shift: fine)   ·   wheel / middle "
                    f"mouse: zoom and pan   ·   X / Y / Z / E: direction   ·   Esc: drop it")
        else:
            axis = {k: n for k, n, _d in props.LINE_AXES}[lineup.line_axis]
            text = (f"Frame {context.scene.frame_current}: drag along a straight {axis} edge on the plate, or click "
                    f"each end (Shift: fine, Ctrl+wheel: zoom)   ·   X / Y / Z / E: change direction   ·   drag a line "
                    f"end to move it   ·   {PLATE_HINT}")
        self._status(context, text)

    def _reset(self):
        self.state = "EDGE_A" if self.index >= 0 else "IDLE"
        self.new_line = False
        pick.hover = pick.edge_start = None

    def _finish(self, context):
        camera = self.camera
        if (camera is not None and self.new_line and self.state in {"DRAW", "STRETCH", "EDGE_A", "EDGE_B"}
                and not self._replaced()):
            lineup = camera.data.lineup
            if 0 <= self.current < len(lineup.lines):
                lineup.lines.remove(self.current)             # its lidar edge never picked
                lineup.active_line = min(lineup.active_line, len(lineup.lines) - 1)
        self._end(context)
        return {"FINISHED"}

    def _end_under(self, context, region, rect, mouse):
        """(line index, 'a' or 'b', region position) of the line end nearest mouse on this frame, or None."""
        frame = context.scene.frame_current
        radius = DRAG_RADIUS * context.preferences.system.ui_scale
        best = None
        for i, line in enumerate(self.camera.data.lineup.lines):
            f = pick.line_frame(line, frame)
            if f is None:
                continue
            for end in ("a", "b"):
                p = pick.uv_to_region(rect, getattr(f, end))
                d = (p - mouse).length
                if d < radius and (best is None or d < best[0]):
                    best = (d, i, end, p)
        return best[1:] if best else None

    def _drag(self, context, event):
        if self._zoomed(context, event):
            return {"RUNNING_MODAL"}
        _region, rect = self.drag_view
        uv = self._held_uv(event)
        lineup = self.camera.data.lineup
        frame = context.scene.frame_current
        if not 0 <= self.current < len(lineup.lines) or pick.line_frame(lineup.lines[self.current], frame) is None:
            self.state = "IDLE"                               # the line went (undo, removed)
            pick.loupe = None
            return {"RUNNING_MODAL"}
        line = lineup.lines[self.current]
        if event.type in {"MOUSEMOVE", "LEFTMOUSE"}:
            setattr(pick.line_frame(line, frame), self.drag_end, uv)
            redraw(context)
        if event.type != "LEFTMOUSE" or event.value != "RELEASE":
            return {"RUNNING_MODAL"}
        pick.loupe = None
        if self.state == "DRAW":
            f = pick.line_frame(line, frame)
            if (pick.uv_to_region(rect, f.a) - pick.uv_to_region(rect, f.b)).length < 2 * DRAG_RADIUS:
                self.state = "STRETCH"                        # a click, not a drag: the end follows to the next click
                self._prompt(context)
                redraw(context)
                return {"RUNNING_MODAL"}
            if line.axis == "EDGE":
                if not self.picker:
                    self.report({"WARNING"}, "No visible lidar for an edge line: use X, Y or Z")
                    line.axis = "Z"
                else:
                    self.state = "EDGE_A"
                    self._prompt(context)
                    return {"RUNNING_MODAL"}
        self.state = "IDLE"
        self.new_line = False
        self._auto_solve(context)
        self._prompt(context)
        return {"RUNNING_MODAL"}

    def _drop_stretched(self, context):
        """Esc / right-click while a line's end follows the mouse: that line goes, the tool carries on."""
        lineup = self.camera.data.lineup
        if self.new_line and 0 <= self.current < len(lineup.lines):
            lineup.lines.remove(self.current)
            lineup.active_line = min(lineup.active_line, len(lineup.lines) - 1)
        self.state = "IDLE"
        self.new_line = False
        self._prompt(context)
        redraw(context)
        return {"RUNNING_MODAL"}

    def _stretch(self, context, event):
        """A new line's end follows the mouse (no button held) until the next click; the view can be zoomed and
        panned meanwhile. The click holds the end (loupe, Shift: fine) and lands it on release."""
        if event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS":
            return self._drop_stretched(context)
        lineup = self.camera.data.lineup
        frame = context.scene.frame_current
        if not 0 <= self.current < len(lineup.lines) or pick.line_frame(lineup.lines[self.current], frame) is None:
            self._reset()
            return {"PASS_THROUGH"}
        under = _view3d_under_mouse(context, event)
        if event.type not in {"MOUSEMOVE", "LEFTMOUSE"} or under is None:
            return {"PASS_THROUGH"}                           # zoom, pan and the rest work as usual
        region, rv3d, mouse, space = under
        camera = space.camera if space.use_local_camera and space.camera else context.scene.camera
        if rv3d.view_perspective != "CAMERA" or camera != self.camera:
            return {"PASS_THROUGH"}
        rect = pick.camera_frame(context.scene, self.camera, region, rv3d)
        line = lineup.lines[self.current]
        if event.type == "MOUSEMOVE":
            pick.line_frame(line, frame).b = _clamp_uv(pick.region_to_uv(rect, mouse))
            redraw(context)
            return {"PASS_THROUGH"}
        if event.value != "PRESS":
            return {"RUNNING_MODAL"}
        self.drag_end = "b"
        pick.line_frame(line, frame).b = self._hold(region, rect, mouse, mouse)
        self.state = "DRAW"
        redraw(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if self.state == "STRETCH" and event.type in {"ESC", "RIGHTMOUSE"} and event.value == "PRESS" \
                and pick.session == self.session and pick.tool == self.TOOL and self.camera is not None:
            return self._drop_stretched(context)              # Esc drops the line, not the tool
        if self._stopped(context, event):
            return self._finish(context)
        if self._undone(context):
            return {"PASS_THROUGH"}
        if self.state in {"DRAW", "MOVE"}:
            return self._drag(context, event)
        if self._plate_key(context, event):
            return {"RUNNING_MODAL"}
        lineup = self.camera.data.lineup
        if self.state != "IDLE" and not 0 <= self.current < len(lineup.lines):
            if self.index >= 0:
                return self._finish(context)                  # the line being re-picked is gone
            self._reset()
        if event.type in AXIS_KEYS and not (event.ctrl or event.alt or event.shift or event.oskey):
            # Always taken here, never passed on (X would delete in the 3D view).
            if self.state in {"IDLE", "STRETCH"} and event.value == "PRESS":
                lineup.line_axis = AXIS_KEYS[event.type]
                if self.state == "STRETCH":
                    lineup.lines[self.current].axis = lineup.line_axis
                self._prompt(context)
                redraw(context)
            return {"RUNNING_MODAL"}
        if self.state == "STRETCH":
            return self._stretch(context, event)
        if event.type == "MOUSEMOVE" and self.state in {"EDGE_A", "EDGE_B"}:
            self._hover(context, event)
        if event.type != "LEFTMOUSE" or event.value != "PRESS":
            return {"PASS_THROUGH"}
        under = _view3d_under_mouse(context, event)
        if under is None:
            return {"PASS_THROUGH"}
        region, rv3d, mouse, space = under
        frame = context.scene.frame_current

        if self.state in {"EDGE_A", "EDGE_B"}:
            hit = self.picker.pick(region, rv3d, mouse)
            if hit is None:
                self.report({"WARNING"}, "No lidar under the mouse")
                return {"RUNNING_MODAL"}
            line = lineup.lines[self.current]
            if self.state == "EDGE_A":
                line.target_a, line.co_a = hit
                line.has_edge = False
                pick.edge_start = hit[0].matrix_world @ hit[1]
                self.state = "EDGE_B"
                self._prompt(context)
                return {"RUNNING_MODAL"}
            line.target_b, line.co_b = hit
            p1, p2 = pick.edge_world(line)
            if (p1 - p2).length < 1e-4:
                self.report({"WARNING"}, "Pick two different points along the edge")
                return {"RUNNING_MODAL"}
            line.has_edge = True
            self.new_line = False
            pick.hover = pick.edge_start = None
            redraw(context)
            if self.index >= 0:                               # re-picking one line's edge
                self._auto_solve(context)
                return self._finish(context)
            self.state = "IDLE"
            self._auto_solve(context)
            self._prompt(context)
            return {"RUNNING_MODAL"}

        rect = self._plate_rect(context, region, rv3d, space)
        if rect is None:
            return {"RUNNING_MODAL"}
        hit = None if event.ctrl else self._end_under(context, region, rect, mouse)
        if hit is not None:
            self.current, self.drag_end, p = hit
            self._hold(region, rect, p, mouse)
            lineup.active_line = self.current
            self.state = "MOVE"
            redraw(context)
            return {"RUNNING_MODAL"}
        uv = pick.region_to_uv(rect, mouse)
        if not (0 <= uv[0] <= 1 and 0 <= uv[1] <= 1):
            self.report({"WARNING"}, "Click inside the camera frame")
            return {"RUNNING_MODAL"}
        line = lineup.lines.add()
        line.axis = lineup.line_axis
        pick.set_line_ends(line, frame, uv, uv)
        self.current = lineup.active_line = len(lineup.lines) - 1
        self.new_line = True
        self.drag_end = "b"
        self._hold(region, rect, mouse, mouse)
        self.state = "DRAW"
        redraw(context)
        return {"RUNNING_MODAL"}


def _active_pin(context):
    camera = lineup_camera(context)
    if camera is None:
        return None
    lineup = camera.data.lineup
    return lineup.pins[lineup.active_pin] if 0 <= lineup.active_pin < len(lineup.pins) else None


class LINEUP_OT_unplace_pin(Operator):
    """Take the active pin off the plate on this frame (the feature is hidden or out of shot here)"""
    bl_idname = "lineup.unplace_pin"
    bl_label = "Remove From Frame"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        pin = _active_pin(context)
        return pin is not None and pick.plate_uv(pin, context.scene.frame_current) is not None

    def execute(self, context):
        pick.unset_plate_uv(_active_pin(context), context.scene.frame_current)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_remove_pin(Operator):
    """Remove a pin (from every frame)"""
    bl_idname = "lineup.remove_pin"
    bl_label = "Remove Pin"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, description="Pin to remove (-1: the active pin)")

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        return camera is not None and len(camera.data.lineup.pins) > 0

    def execute(self, context):
        lineup = lineup_camera(context).data.lineup
        index = lineup.active_pin if self.index < 0 else self.index
        if not 0 <= index < len(lineup.pins):
            return {"CANCELLED"}
        lineup.pins.remove(index)
        lineup.active_pin = min(index, len(lineup.pins) - 1)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_clear_pins(Operator):
    """Remove every pin from the scene camera (its solved keyframes stay)"""
    bl_idname = "lineup.clear_pins"
    bl_label = "Clear Pins"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        return camera is not None and len(camera.data.lineup.pins) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        lineup = lineup_camera(context).data.lineup
        lineup.pins.clear()
        lineup.active_pin = -1
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_delete_key(Operator):
    """Delete the solve on this frame: the camera's keyframes here and the record"""
    bl_idname = "lineup.delete_key"
    bl_label = "Delete Solve"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        return camera is not None and key_record(camera.data.lineup, context.scene.frame_current)[1] is not None

    def execute(self, context):
        camera = lineup_camera(context)
        lineup = camera.data.lineup
        frame = context.scene.frame_current
        if pick.unkeyed == (camera.name, frame):
            pick.unkeyed = None                               # an auto-solve not keyed yet: it never will be
        for owner, path in camera_keys(camera):
            try:
                owner.keyframe_delete(path, frame=frame)
            except RuntimeError:                              # not keyed here (removed by hand)
                pass
        i, _key = key_record(lineup, frame)
        lineup.keys.remove(i)
        for pin in lineup.pins:
            entry = pick.pin_frame(pin, frame)
            if entry is not None:
                entry.error = -1.0
        lineup.active_key = min(i, len(lineup.keys) - 1)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_flip_facing(Operator):
    """Turn the camera 180° about a world axis, in place. Lines don't say which way along an axis is positive, so a
    lines-only solve fits four ways round (as it is, or turned about X, Y or Z): this picks another (and keys it on
    this frame)"""
    bl_idname = "lineup.flip_facing"
    bl_label = "Flip"
    bl_options = {"REGISTER", "UNDO"}

    axis: EnumProperty(items=[("X", "X", "Turn 180° about world X"), ("Y", "Y", "Turn 180° about world Y"),
                              ("Z", "Z", "Turn 180° about world Z (the vertical)")], default="Z")

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        if camera is None:
            return False
        _i, key = key_record(camera.data.lineup, context.scene.frame_current)
        return key is not None and key.position_fixed

    def execute(self, context):
        camera = lineup_camera(context)
        R, C = pick.pose(camera)
        turn = np.diag([-1.0, -1.0, -1.0])
        i = "XYZ".index(self.axis)
        turn[i, i] = 1.0                                               # 180° about the axis: the other two reversed
        pick.set_pose(camera, R @ turn, C)
        key_camera(camera, context.scene.frame_current)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_use_focal(Operator):
    """Lock the focal to the camera's lens on this frame, so every other frame solves with the same lens"""
    bl_idname = "lineup.use_focal"
    bl_label = "Use This Focal"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return lineup_camera(context) is not None

    def execute(self, context):
        camera = lineup_camera(context)
        camera.data.lineup.focal_prior = camera.data.lens
        camera.data.lineup.lock_focal = True
        self.report({"INFO"}, f"Focal locked at {camera.data.lens:.2f} mm")
        return {"FINISHED"}


def _active_line(context):
    camera = lineup_camera(context)
    if camera is None:
        return None
    lineup = camera.data.lineup
    return lineup.lines[lineup.active_line] if 0 <= lineup.active_line < len(lineup.lines) else None


class LINEUP_OT_unplace_line(Operator):
    """Take the active line off the plate on this frame (the edge is hidden or out of shot here)"""
    bl_idname = "lineup.unplace_line"
    bl_label = "Remove From Frame"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        line = _active_line(context)
        return line is not None and pick.line_frame(line, context.scene.frame_current) is not None

    def execute(self, context):
        pick.unset_line(_active_line(context), context.scene.frame_current)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_remove_line(Operator):
    """Remove the active line (from every frame)"""
    bl_idname = "lineup.remove_line"
    bl_label = "Remove Line"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _active_line(context) is not None

    def execute(self, context):
        lineup = lineup_camera(context).data.lineup
        index = lineup.active_line
        lineup.lines.remove(index)
        lineup.active_line = min(index, len(lineup.lines) - 1)
        redraw(context)
        return {"FINISHED"}


class LINEUP_OT_clear_lines(Operator):
    """Remove every line from the scene camera (its solved keyframes stay)"""
    bl_idname = "lineup.clear_lines"
    bl_label = "Clear Lines"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        camera = lineup_camera(context)
        return camera is not None and len(camera.data.lineup.lines) > 0

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        lineup = lineup_camera(context).data.lineup
        lineup.lines.clear()
        lineup.active_line = -1
        redraw(context)
        return {"FINISHED"}


CLASSES = (LINEUP_OT_load_plate, LINEUP_OT_toggle_plate_depth, LINEUP_OT_solve, LINEUP_OT_pick, LINEUP_OT_lines,
           LINEUP_OT_unplace_pin, LINEUP_OT_remove_pin, LINEUP_OT_clear_pins, LINEUP_OT_unplace_line,
           LINEUP_OT_remove_line, LINEUP_OT_clear_lines, LINEUP_OT_delete_key, LINEUP_OT_flip_facing,
           LINEUP_OT_use_focal)

_keymaps = []


@persistent
def _on_undo(*_args):
    """Undo / redo replace every object and camera: a running pin or line tool rebuilds on its next event. An
    auto-solve waiting to be keyed is undone with it."""
    pick.undo_generation += 1
    pick.unkeyed = None


@persistent
def _on_load(*_args):
    pick.tool = None
    pick.unkeyed = None
    pick.plate_hidden = pick.hover = pick.edge_start = None


@persistent
def _on_frame_or_save(*_args):
    """Before the frame changes (the keys would replace the camera's pose)."""
    key_unkeyed()


@persistent
def _on_save(*_args):
    """Before a save: the pending key, and the plate shown again if H hid it (so it isn't saved hidden)."""
    key_unkeyed()
    show_plate()


HANDLERS = ((bpy.app.handlers.undo_post, _on_undo), (bpy.app.handlers.redo_post, _on_undo),
            (bpy.app.handlers.load_post, _on_load), (bpy.app.handlers.frame_change_pre, _on_frame_or_save),
            (bpy.app.handlers.save_pre, _on_save))


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    for handlers, fn in HANDLERS:
        handlers.append(fn)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc:                                                   # None in background mode
        km = kc.keymaps.new(name="3D View", space_type="VIEW_3D")
        _keymaps.append((km, km.keymap_items.new(LINEUP_OT_toggle_plate_depth.bl_idname, "F", "PRESS", alt=True)))


def unregister():
    for handlers, fn in HANDLERS:
        if fn in handlers:
            handlers.remove(fn)
    pick.tool = None
    for km, kmi in _keymaps:
        km.keymap_items.remove(kmi)
    _keymaps.clear()
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
