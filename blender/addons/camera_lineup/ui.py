"""3D Viewport > Sidebar > Lineup."""
import bpy

from . import operators, pick, solve


def _error_text(used, entry):
    if used and entry.left_out:
        return f"left out  {entry.error:.1f} px"
    if used and entry.far:
        return f"far  {entry.error:.1f} px"
    return f"{entry.error:.2f} px"


class LINEUP_UL_pins(bpy.types.UIList):
    """Pins, with their state on the current frame."""

    def draw_item(self, context, layout, data, pin, icon, active_data, active_propname, index):
        frame = context.scene.frame_current
        row = layout.row(align=True)
        row.prop(pin, "use", text="")
        row.label(text=str(index + 1))
        if not pick.has_3d(pin):
            row.label(text="no lidar point", icon="ERROR")
            return
        if pick.plate_uv(pin, frame) is None:
            sub = row.row()
            sub.active = False
            sub.label(text="not on this frame")
            return
        entry = pick.pin_frame(pin, frame)
        _i, key = operators.key_record(data, frame)
        if entry is not None and entry.error >= 0 and key is not None:
            sub = row.row()
            sub.alert = pin.use and (entry.left_out or entry.far or entry.error > max(2 * key.rms, 1.0))
            sub.label(text=_error_text(pin.use, entry))


class LINEUP_UL_lines(bpy.types.UIList):
    """Lines, with their state on the current frame."""

    def draw_item(self, context, layout, data, line, icon, active_data, active_propname, index):
        frame = context.scene.frame_current
        row = layout.row(align=True)
        row.prop(line, "use", text="")
        name = "Edge" if line.axis == "EDGE" else line.axis
        row.label(text=f"{name} {index + 1}")
        if not pick.line_has_3d(line):
            row.label(text="edge not picked", icon="ERROR")
            return
        entry = pick.line_frame(line, frame)
        if entry is None:
            sub = row.row()
            sub.active = False
            sub.label(text="not on this frame")
            return
        _i, key = operators.key_record(data, frame)
        if entry.error >= 0 and key is not None:
            sub = row.row()
            sub.alert = line.use and (entry.left_out or entry.far or entry.error > max(2 * key.rms, 1.0))
            sub.label(text=_error_text(line.use, entry))


class LINEUP_UL_keys(bpy.types.UIList):
    """Solved frames; clicking one jumps to it."""

    def draw_item(self, context, layout, data, key, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=str(key.frame), icon="DECORATE_KEYFRAME")
        row.label(text=f"{key.rms:.2f} px")
        row.label(text=f"{key.pins}p {key.lines}l" if key.lines else f"{key.pins}p")
        sub = row.row()
        sub.alert = key.at_limit
        sub.label(text=f"{key.lens:.1f} mm")


class LineupPanel:
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Lineup"


class VIEW3D_PT_lineup(LineupPanel, bpy.types.Panel):
    bl_label = "Camera Lineup"

    def draw(self, context):
        layout = self.layout
        camera = operators.lineup_camera(context)
        if camera is None:
            layout.label(text="Set a scene camera", icon="ERROR")
            return
        lineup = camera.data.lineup
        layout.label(text=camera.name, icon="CAMERA_DATA")

        box = layout.box()
        box.operator("lineup.load_plate", icon="IMAGE_DATA")
        if lineup.plate is not None:
            image = lineup.plate
            box.label(text=f"{image.name}  {image.size[0]} x {image.size[1]}")
            box.prop(image.colorspace_settings, "name", text="Colour Space")
            bg = operators.plate_background(camera)
            if bg is not None:
                row = box.row(align=True)
                row.prop(bg, "alpha", text="Opacity")
                row.prop(bg, "display_depth", text="")
                row.operator("lineup.toggle_plate_depth", text="", icon="ARROW_LEFTRIGHT")
            r = context.scene.render
            if image.size[0] and (r.resolution_x, r.resolution_y) != tuple(image.size):
                box.label(text="Render resolution differs from the plate", icon="ERROR")

        layout.prop(context.scene.lineup, "lidar")

        col = layout.column(align=True)
        col.prop(lineup, "focal_prior")
        sub = col.row(align=True)
        sub.active = not lineup.lock_focal
        sub.prop(lineup, "focal_range")
        col.prop(lineup, "lock_focal")


class VIEW3D_PT_lineup_pins(LineupPanel, bpy.types.Panel):
    bl_label = "Pins"
    bl_parent_id = "VIEW3D_PT_lineup"

    @classmethod
    def poll(cls, context):
        return operators.lineup_camera(context) is not None

    def draw(self, context):
        layout = self.layout
        lineup = operators.lineup_camera(context).data.lineup
        layout.operator("lineup.pick", icon="PIVOT_CURSOR", depress=pick.tool == "PINS")

        row = layout.row()
        row.template_list("LINEUP_UL_pins", "", lineup, "pins", lineup, "active_pin", rows=4)
        side = row.column(align=True)
        side.operator("lineup.remove_pin", text="", icon="REMOVE")
        side.separator()
        side.operator("lineup.clear_pins", text="", icon="TRASH")

        if 0 <= lineup.active_pin < len(lineup.pins):
            pin = lineup.pins[lineup.active_pin]
            col = layout.column(align=True)
            row = col.row(align=True)
            op = row.operator("lineup.pick", text="Re-pick Plate", icon="IMAGE_PLANE")
            op.index, op.what = lineup.active_pin, "PLATE"
            op = row.operator("lineup.pick", text="Re-pick Lidar", icon="MESH_DATA")
            op.index, op.what = lineup.active_pin, "GEO"
            col.operator("lineup.unplace_pin", icon="X")
        layout.prop(lineup, "show_overlay")


class VIEW3D_PT_lineup_lines(LineupPanel, bpy.types.Panel):
    bl_label = "Lines"
    bl_parent_id = "VIEW3D_PT_lineup"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return operators.lineup_camera(context) is not None

    def draw(self, context):
        layout = self.layout
        lineup = operators.lineup_camera(context).data.lineup
        row = layout.row(align=True)
        row.prop(lineup, "line_axis", expand=True)
        layout.operator("lineup.lines", icon="GREASEPENCIL", depress=pick.tool == "LINES")

        row = layout.row()
        row.template_list("LINEUP_UL_lines", "", lineup, "lines", lineup, "active_line", rows=3)
        side = row.column(align=True)
        side.operator("lineup.remove_line", text="", icon="REMOVE")
        side.separator()
        side.operator("lineup.clear_lines", text="", icon="TRASH")

        if 0 <= lineup.active_line < len(lineup.lines):
            line = lineup.lines[lineup.active_line]
            col = layout.column(align=True)
            if line.axis == "EDGE":
                text = "Re-pick Lidar Edge" if pick.line_has_3d(line) else "Pick Lidar Edge"
                col.operator("lineup.lines", text=text, icon="MESH_DATA").index = lineup.active_line
            col.operator("lineup.unplace_line", icon="X")


class VIEW3D_PT_lineup_solve(LineupPanel, bpy.types.Panel):
    bl_label = "Solve"
    bl_parent_id = "VIEW3D_PT_lineup"

    @classmethod
    def poll(cls, context):
        return operators.lineup_camera(context) is not None

    def draw(self, context):
        layout = self.layout
        camera = operators.lineup_camera(context)
        lineup = camera.data.lineup
        frame = context.scene.frame_current
        row = layout.row(align=True)
        row.scale_y = 1.4
        row.operator("lineup.solve", text=f"Solve Frame {frame}", icon="CON_CAMERASOLVER")
        row.prop(lineup, "auto_solve", text="", icon="FILE_REFRESH")
        col = layout.column(align=True)
        col.prop(lineup, "lidar_accuracy")
        col.prop(lineup, "drop_outliers")

        pins, axis, edges = operators.constraints(lineup, frame)
        if not operators.enough(lineup, frame):
            layout.label(text=f"Not enough on this frame: {len(pins)} pins, {len(axis) + len(edges)} lines",
                         icon="INFO")
            layout.label(text="Draw 2+ lines (rotation, focal) or add 4+ pins")
        elif not (axis or edges) and len(pins) < (solve.MIN_PINS if lineup.lock_focal else 6):
            layout.label(text=f"{len(pins)} pins: {6 if not lineup.lock_focal else solve.MIN_PINS}+ spread in depth "
                              f"recommended", icon="INFO")

        _i, key = operators.key_record(lineup, frame)
        col = layout.column(align=True)
        if key is None:
            col.label(text="Not solved on this frame")
        else:
            counted = f"{key.pins} pins" + (f", {key.lines} lines" if key.lines else "")
            col.label(text=f"RMS error: {key.rms:.2f} px  ({counted})")
            if key.left_out:
                warn = col.column(align=True)
                warn.alert = True
                warn.label(text=f"{key.left_out} left out: over {key.left_out_px:.1f} px + lidar accuracy",
                           icon="ERROR")
                warn.label(text="Fix or delete them (red in the lists)")
            elif key.far:
                warn = col.column(align=True)
                warn.alert = True
                warn.label(text=f"{key.far} far off: over {key.left_out_px:.1f} px + lidar accuracy, still in the fit",
                           icon="ERROR")
                warn.label(text="Fix them, raise Lidar Accuracy, untick Use, or Drop Outliers")
            focal = f"Focal: {key.lens:.2f} mm"
            if key.focal_sigma > 0:
                focal += f"  ±{key.focal_sigma:.2f}"
            col.label(text=focal)
            if key.position_fixed:
                col.label(text="Rotation and focal from lines; position kept", icon="INFO")
                col.label(text="Add 2+ pins to place the camera too")
                row = col.row(align=True)
                row.label(text="Facing the wrong way? Flip:")
                for axis in "XYZ":
                    row.operator("lineup.flip_facing", text=axis).axis = axis
            if key.focal_held:
                note = col.column(align=True)
                note.label(text="Focal is mostly the Rough Focal: these barely fix it", icon="INFO")
                note.label(text="Spread pins in depth, or draw horizontals on X and Y")
            if key.at_limit:
                warn = col.column(align=True)
                warn.alert = True
                warn.label(text="Focal at the edge of the range", icon="ERROR")
                warn.label(text="Change the Rough Focal or widen the ± Range")
        row = layout.row(align=True)
        row.operator("lineup.use_focal", icon="LOCKED")
        row.operator("lineup.delete_key", icon="KEY_DEHLT")

        if len(lineup.keys):
            layout.label(text="Solved Frames")
            layout.template_list("LINEUP_UL_keys", "", lineup, "keys", lineup, "active_key", rows=3)


CLASSES = (LINEUP_UL_pins, LINEUP_UL_lines, LINEUP_UL_keys, VIEW3D_PT_lineup, VIEW3D_PT_lineup_pins,
           VIEW3D_PT_lineup_lines, VIEW3D_PT_lineup_solve)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
