"""Lineup settings (saved with the .blend): pins, solves and focal live on the camera (camera.data.lineup), the lidar
choice on the scene (scene.lineup).

A pin is one point on the lidar with a plate position per frame (the feature moves on the plate, not on the scan).
A line is a straight edge on the plate per frame, along a world axis or a lidar edge.
Each solve keys the camera on that frame and is recorded in keys.
"""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty,
                       PointerProperty, StringProperty)


class LineupPinFrame(bpy.types.PropertyGroup):
    frame: IntProperty()
    uv: FloatVectorProperty(name="Plate", size=2, description="Position on the plate, 0-1 from the bottom-left")
    error: FloatProperty(name="Error", default=-1.0, description="Reprojection error after the last solve on this "
                                                                 "frame, pixels (-1: not solved)")
    left_out: BoolProperty(description="The last solve on this frame left this pin out: too far off")
    far: BoolProperty(description="Too far off the camera the other pins and lines agree on, on this frame")


class LineupPin(bpy.types.PropertyGroup):
    use: BoolProperty(name="Use", default=True, description="Use this pin in the solve")
    frames: CollectionProperty(type=LineupPinFrame)
    target: PointerProperty(name="Object", type=bpy.types.Object, description="Lidar object the pin is on")
    co: FloatVectorProperty(name="Point", size=3, subtype="XYZ", description="Point on the lidar, object space")
    has_co: BoolProperty()
    # Before per-frame plate positions: one position for every frame. Moved into frames when the pin is next edited.
    uv: FloatVectorProperty(size=2, options={"HIDDEN"})
    has_uv: BoolProperty(options={"HIDDEN"})


LINE_AXES = [("X", "X", "Along world X"), ("Y", "Y", "Along world Y"),
             ("Z", "Z", "Along world Z (verticals, with the lidar Z up)"),
             ("EDGE", "Edge", "Along an edge picked on the lidar (two points on it)")]


class LineupLineFrame(bpy.types.PropertyGroup):
    frame: IntProperty()
    a: FloatVectorProperty(size=2, description="One end on the plate, 0-1 from the bottom-left")
    b: FloatVectorProperty(size=2, description="The other end on the plate")
    error: FloatProperty(default=-1.0, description="Error after the last solve on this frame, pixels (-1: not "
                                                   "solved)")
    left_out: BoolProperty(description="The last solve on this frame left this line out: too far off")
    far: BoolProperty(description="Too far off the camera the pins and other lines agree on, on this frame")


class LineupLine(bpy.types.PropertyGroup):
    """A straight edge drawn on the plate: along a world axis (rotation and focal) or along an edge picked on the
    lidar (rotation, focal and position)."""
    use: BoolProperty(name="Use", default=True, description="Use this line in the solve")
    axis: EnumProperty(name="Along", items=LINE_AXES, default="Z")
    frames: CollectionProperty(type=LineupLineFrame)
    target_a: PointerProperty(type=bpy.types.Object)
    co_a: FloatVectorProperty(size=3, subtype="XYZ", description="One point on the lidar edge, object space")
    target_b: PointerProperty(type=bpy.types.Object)
    co_b: FloatVectorProperty(size=3, subtype="XYZ", description="Another point on the lidar edge, object space")
    has_edge: BoolProperty()


class LineupKey(bpy.types.PropertyGroup):
    frame: IntProperty()
    pins: IntProperty()
    lines: IntProperty()
    rms: FloatProperty()
    lens: FloatProperty(unit="CAMERA")
    focal_sigma: FloatProperty(unit="CAMERA")
    at_limit: BoolProperty()
    position_fixed: BoolProperty(description="Lines only: rotation and focal solved, the camera kept where it was")
    focal_held: BoolProperty(description="Little on this frame fixed the focal: it is mostly the rough focal")
    left_out: IntProperty(description="Pins and lines the solve left out on this frame (too far off)")
    far: IntProperty(description="Pins and lines too far off on this frame (left out or not)")
    left_out_px: FloatProperty(description="How far off counted as too far on this frame, pixels")


def _goto_key(self, context):
    if 0 <= self.active_key < len(self.keys) and context.scene.frame_current != self.keys[self.active_key].frame:
        context.scene.frame_current = self.keys[self.active_key].frame


class LineupSettings(bpy.types.PropertyGroup):
    pins: CollectionProperty(type=LineupPin)
    active_pin: IntProperty(name="Active Pin", default=-1)
    lines: CollectionProperty(type=LineupLine)
    active_line: IntProperty(name="Active Line", default=-1)
    line_axis: EnumProperty(name="New Lines Along", items=LINE_AXES, default="Z",
                            description="Direction of the next line drawn (X, Y, Z or E while drawing)")
    keys: CollectionProperty(type=LineupKey)
    active_key: IntProperty(name="Solved Frame", default=-1, update=_goto_key)
    plate: PointerProperty(name="Plate", type=bpy.types.Image, description="Undistorted plate to line up to")
    focal_prior: FloatProperty(name="Rough Focal", default=35.0, min=1.0, soft_max=300.0, unit="CAMERA",
                               description="Rough focal length (mm, on this camera's sensor) the solve starts from")
    focal_range: FloatProperty(name="± Range", default=10.0, min=0.01, soft_max=50.0, unit="CAMERA",
                               description="The solved focal stays within Rough Focal ± this (mm); inside it the "
                                           "pins alone decide")
    lock_focal: BoolProperty(name="Lock Focal",
                             description="Solve the position and rotation only, using the rough focal as it is")
    lidar_accuracy: FloatProperty(name="Lidar Accuracy", default=0.02, min=0.0, soft_max=0.2, unit="LENGTH",
                                  description="How well the lidar points are known (lidar error and picking on it). "
                                              "Near pins and edges count less by it and must be further off to be "
                                              "flagged; distant ones barely change. 0: the lidar is exact")
    drop_outliers: BoolProperty(name="Drop Outliers",
                                description="Leave pins and lines that are too far off out of the solve. Off: a "
                                            "best fit over every pin and line in use, the far ones flagged red")
    auto_solve: BoolProperty(name="Auto Solve", default=True, description="Solve each time a pin is placed or moved")
    show_overlay: BoolProperty(name="Show Pins", default=True, description="Draw the pins in the viewport")


class LineupScene(bpy.types.PropertyGroup):
    lidar: PointerProperty(name="Lidar", type=bpy.types.Collection,
                           description="Collection holding the lidar to pick on (empty: every visible mesh and "
                                       "point cloud)")


CLASSES = (LineupPinFrame, LineupPin, LineupLineFrame, LineupLine, LineupKey, LineupSettings, LineupScene)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Camera.lineup = PointerProperty(type=LineupSettings)
    bpy.types.Scene.lineup = PointerProperty(type=LineupScene)


def unregister():
    del bpy.types.Scene.lineup
    del bpy.types.Camera.lineup
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
