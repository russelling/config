"""Viewport overlay. In camera view through the lineup camera: a yellow cross where each pin is on the plate this
frame, a cyan square where its lidar point lands, and a red line between them (the error). Pins not placed on this
frame show only their lidar point, greyed; pins and lines the solve left out (too far off) are red. Lines are
drawn in their axis colour (X red, Y green, Z blue, lidar edge orange) with a white guide along where the camera says
they should run. In other views: the lidar points. While a
point is held on the plate, a loupe in the top-left corner of the viewport (top-right while the point is under it)
shows the plate magnified; Ctrl+wheel zooms it. While picking on the lidar: a white marker where a click would snap,
and for an edge line its first point with a line to the cursor. H (while a tool runs) hides what's on the plate."""
import blf
import bpy
import gpu
import numpy as np
from bpy_extras import view3d_utils
from gpu_extras.batch import batch_for_shader

from . import pick, solve

PLATE = (1.0, 0.8, 0.1, 1.0)
GEO = (0.15, 0.85, 1.0, 1.0)
ERROR = (1.0, 0.3, 0.3, 1.0)
UNUSED = (0.6, 0.6, 0.6, 0.8)
ACTIVE = (1.0, 1.0, 1.0, 1.0)
OUTLINE = (0.0, 0.0, 0.0, 0.7)
GUIDE = (1.0, 1.0, 1.0, 0.75)
LOUPE_SIZE = 180           # px on screen
LOUPE_MARGIN = 16          # px between the loupe and the edge of the viewport's visible part
LOUPE_CLEAR = 24           # px the held point must keep clear of the loupe
AXIS_COLOURS = {"X": (1.0, 0.35, 0.35, 1.0), "Y": (0.45, 0.9, 0.3, 1.0), "Z": (0.4, 0.6, 1.0, 1.0),
                "EDGE": (1.0, 0.55, 0.1, 1.0)}

_handle = None


def _cross(p, s):
    return [(p.x - s, p.y), (p.x + s, p.y), (p.x, p.y - s), (p.x, p.y + s)]


def _square(p, s):
    a, b, c, d = (p.x - s, p.y - s), (p.x + s, p.y - s), (p.x + s, p.y + s), (p.x - s, p.y + s)
    return [a, b, b, c, c, d, d, a]


def _lines(shader, viewport, segments, color, width):
    if not segments:
        return
    batch = batch_for_shader(shader, "LINES", {"pos": [(x, y, 0.0) for x, y in segments]})
    shader.uniform_float("viewportSize", viewport)
    shader.uniform_float("lineWidth", width)
    shader.uniform_float("color", color)
    batch.draw(shader)


def _draw():
    context = bpy.context
    _draw_pins(context)
    _draw_hover(context)
    _draw_loupe(context)


def _plate_shader():
    try:                                                      # linear texture -> display (sRGB), close enough here
        return gpu.shader.from_builtin("IMAGE_SCENE_LINEAR_TO_REC709_SRGB")
    except ValueError:
        return gpu.shader.from_builtin("IMAGE")


def _visible(area, region):
    """(left, bottom, right, top) of the part of region not under the area's other regions (the toolbar, sidebar and
    headers overlap the viewport with Region Overlap on), in region coordinates."""
    left, bottom, right, top = 0, 0, region.width, region.height
    for r in area.regions if area is not None else ():
        if r == region or r.width <= 1 or r.height <= 1:
            continue
        x0, y0 = r.x - region.x, r.y - region.y
        x1, y1 = x0 + r.width, y0 + r.height
        if x1 <= 0 or y1 <= 0 or x0 >= region.width or y0 >= region.height:
            continue                                          # beside the viewport, not over it
        if r.height >= region.height / 2:                     # a side panel
            if x0 + x1 < region.width:
                left = max(left, x1)
            else:
                right = min(right, x0)
        elif y0 + y1 < region.height:                         # a bar along the bottom or top
            bottom = max(bottom, y1)
        else:
            top = min(top, y0)
    return left, bottom, right, top


def loupe_corner(area, region, at, size, margin, clear):
    """Bottom-left of the loupe: the visible top-left corner, or the top-right while at is under (or near) it."""
    left, _bottom, right, top = _visible(area, region)
    y0 = top - margin - size
    x0 = left + margin
    if x0 - clear <= at.x <= x0 + size + clear and y0 - clear <= at.y:
        x0 = right - margin - size
    return x0, y0


def _draw_loupe(context):
    """The plate magnified around a point being placed, beside it, with a crosshair on the point."""
    loupe = pick.loupe
    region = context.region
    if loupe is None or region is None or loupe["region"] != region:
        return
    camera = context.scene.camera
    image = camera.data.lineup.plate if camera is not None and camera.type == "CAMERA" else None
    if image is None or not image.size[0]:
        return
    try:
        texture = gpu.texture.from_image(image)
    except Exception:                                         # image not loadable right now
        return
    scale = context.preferences.system.ui_scale
    size = LOUPE_SIZE * scale
    x0, y0 = loupe_corner(context.area, region, loupe["at"], size, LOUPE_MARGIN * scale, LOUPE_CLEAR * scale)
    u, v = loupe["uv"]
    hu, hv = pick.loupe_pixels / 2 / image.size[0], pick.loupe_pixels / 2 / image.size[1]
    corners = [(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size)]
    shader = _plate_shader()
    batch = batch_for_shader(shader, "TRI_FAN", {
        "pos": corners,
        "texCoord": [(u - hu, v - hv), (u + hu, v - hv), (u + hu, v + hv), (u - hu, v + hv)]})
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)

    cx, cy, r = x0 + size / 2, y0 + size / 2, 6 * scale
    lines = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    lines.bind()
    gpu.state.blend_set("ALPHA")
    border = [corners[0], corners[1], corners[1], corners[2], corners[2], corners[3], corners[3], corners[0]]
    cross = [(x0, cy), (cx - r, cy), (cx + r, cy), (x0 + size, cy),
             (cx, y0), (cx, cy - r), (cx, cy + r), (cx, y0 + size)]
    viewport = (region.width, region.height)
    _lines(lines, viewport, border + cross, OUTLINE, 3.0 * scale)
    _lines(lines, viewport, border, ACTIVE, 1.0 * scale)
    _lines(lines, viewport, cross, PLATE, 1.0 * scale)
    gpu.state.blend_set("NONE")

    font = 0
    blf.size(font, 11 * scale)
    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 3, 0.0, 0.0, 0.0, 1.0)
    blf.color(font, *ACTIVE)
    blf.position(font, x0 + 5 * scale, y0 + 5 * scale, 0)
    blf.draw(font, f"{size / pick.loupe_pixels:.1f}x  Ctrl+wheel")
    blf.disable(font, blf.SHADOW)


def _pin_shapes(context, lineup, region, rv3d, frame, size, shapes, labels):
    current = context.scene.frame_current
    for i, pin in enumerate(lineup.pins):
        active = i == lineup.active_pin
        uv = pick.plate_uv(pin, current)
        p2 = pick.uv_to_region(frame, uv) if frame and uv is not None else None
        p3 = None
        if pick.has_3d(pin):
            p3 = view3d_utils.location_3d_to_region_2d(region, rv3d, pick.pin_world(pin))
        placed = uv is not None                   # on the plate this frame; else only where its world point lands
        geo = GEO if pin.use and placed else UNUSED
        entry = pick.pin_frame(pin, current)
        far = pin.use and entry is not None and (entry.far or entry.left_out)    # too far off the solve
        plate = ACTIVE if active else ERROR if far else PLATE if pin.use else UNUSED
        if p2 is not None and p3 is not None:
            shapes.append(([tuple(p2), tuple(p3)], ERROR if pin.use else UNUSED))
        if p2 is not None:
            shapes.append((_cross(p2, size * (1.5 if active else 1)), plate))
        if p3 is not None:
            shapes.append((_square(p3, size * (0.8 if active else 0.6)), ACTIVE if active else geo))
        anchor = p2 if p2 is not None else p3
        if anchor is not None:
            labels.append((anchor, str(i + 1), ACTIVE if active else PLATE if p2 is not None else geo))


def _line_shapes(context, camera, lineup, region, rv3d, frame, size, shapes, labels):
    """Lines on this frame in their axis colour; for each, a white guide where the camera as it stands says the line
    should run (through the axis's vanishing point), or for lidar edges the edge itself (cyan)."""
    scene = context.scene
    current = scene.frame_current
    w, h = scene.render.resolution_x, scene.render.resolution_y
    R, C = pick.pose(camera)
    f, principal = pick.intrinsics(scene, camera)

    def to_region(px):
        return pick.uv_to_region(frame, (px[0] / w, px[1] / h))

    for i, line in enumerate(lineup.lines):
        active = i == lineup.active_line
        entry = pick.line_frame(line, current)
        far = line.use and entry is not None and (entry.far or entry.left_out)
        color = ACTIVE if active else ERROR if far else AXIS_COLOURS[line.axis] if line.use else UNUSED
        if line.axis == "EDGE" and pick.line_has_3d(line):
            p1, p2 = (view3d_utils.location_3d_to_region_2d(region, rv3d, p) for p in pick.edge_world(line))
            if p1 is not None and p2 is not None:
                shapes.append(([tuple(p1), tuple(p2)], GEO if line.use and entry is not None else UNUSED))
        if entry is None or frame is None:
            continue
        a, b = pick.uv_to_region(frame, entry.a), pick.uv_to_region(frame, entry.b)
        if line.axis != "EDGE":
            pa, pb = pick.plate_pixels(scene, entry.a), pick.plate_pixels(scene, entry.b)
            v = solve.vanishing(R, f, principal, np.array([pick.AXES[line.axis]]))[0]
            m = (pa + pb) / 2
            u = v[:2] - m * v[2]
            if np.linalg.norm(u) > 1e-9:
                u = u / np.linalg.norm(u) * np.linalg.norm(pb - pa) / 2
                shapes.append(([tuple(to_region(m - u)), tuple(to_region(m + u))], GUIDE))
        shapes.append(([tuple(a), tuple(b)], color))
        shapes.append((_square(a, size * 0.4), color))
        shapes.append((_square(b, size * 0.4), color))
        name = "E" if line.axis == "EDGE" else line.axis
        labels.append((a, f"{name}{i + 1}", color))


def _draw_pins(context):
    camera = context.scene.camera
    region, rv3d = context.region, context.region_data
    if camera is None or camera.type != "CAMERA" or rv3d is None:
        return
    lineup = camera.data.lineup
    if not lineup.show_overlay or not (lineup.pins or lineup.lines):
        return
    frame = None
    if pick.plate_hidden is None and rv3d.view_perspective == "CAMERA" and pick.view_camera(context) == camera:
        frame = pick.camera_frame(context.scene, camera, region, rv3d)

    scale = context.preferences.system.ui_scale
    size = 6 * scale
    shapes = []                                   # (segments, colour)
    labels = []                                   # (position, text, colour)
    _line_shapes(context, camera, lineup, region, rv3d, frame, size, shapes, labels)
    _pin_shapes(context, lineup, region, rv3d, frame, size, shapes, labels)

    viewport = (region.width, region.height)
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    gpu.state.blend_set("ALPHA")
    for segments, _color in shapes:
        _lines(shader, viewport, segments, OUTLINE, 3.5 * scale)
    for segments, color in shapes:
        _lines(shader, viewport, segments, color, 1.5 * scale)
    gpu.state.blend_set("NONE")

    font = 0
    blf.size(font, 12 * scale)
    blf.enable(font, blf.SHADOW)
    blf.shadow(font, 3, 0.0, 0.0, 0.0, 1.0)
    for p, text, color in labels:
        blf.color(font, *color)
        blf.position(font, p.x + size + 3, p.y + size + 1, 0)
        blf.draw(font, text)
    blf.disable(font, blf.SHADOW)


def _draw_hover(context):
    """Where a lidar click would snap, and an edge line's first point with a line from it to there."""
    region, rv3d = context.region, context.region_data
    if rv3d is None or (pick.hover is None and pick.edge_start is None):
        return
    scale = context.preferences.system.ui_scale
    size = 6 * scale
    hover, start = (view3d_utils.location_3d_to_region_2d(region, rv3d, p) if p is not None else None
                    for p in (pick.hover, pick.edge_start))
    shapes = []
    if hover is not None and start is not None:
        shapes.append(([tuple(start), tuple(hover)], AXIS_COLOURS["EDGE"]))
    if start is not None:
        shapes.append((_square(start, size * 0.8), AXIS_COLOURS["EDGE"]))
    if hover is not None:
        shapes.append((_cross(hover, size * 1.5) + _square(hover, size * 0.6), ACTIVE))
    if not shapes:
        return
    viewport = (region.width, region.height)
    shader = gpu.shader.from_builtin("POLYLINE_UNIFORM_COLOR")
    shader.bind()
    gpu.state.blend_set("ALPHA")
    for segments, _color in shapes:
        _lines(shader, viewport, segments, OUTLINE, 3.5 * scale)
    for segments, color in shapes:
        _lines(shader, viewport, segments, color, 1.5 * scale)
    gpu.state.blend_set("NONE")


def register():
    global _handle
    _handle = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_PIXEL")


def unregister():
    global _handle
    if _handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_handle, "WINDOW")
        _handle = None
