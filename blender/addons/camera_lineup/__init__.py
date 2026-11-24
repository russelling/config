"""Camera Lineup: solve a camera to an undistorted plate from pins placed on the plate and on lidar geometry. Each
solve keyframes the camera on its frame, so a shot can be lined up frame by frame where it is needed.

    solve       pure: pose + focal from 2D-3D pins (numpy only, testable with any Python)
    pick        camera maths, plate <-> camera view, snapping to the lidar
    props       camera.data.lineup (pins, focal), scene.lineup (lidar collection)
    operators   load plate, add / re-pick pins, solve, remove / clear pins
    draw        viewport overlay of the pins and their errors
    ui          3D Viewport > Sidebar > Lineup

bpy is only imported on register(), so camera_lineup.solve can be used without Blender.
"""

bl_info = {
    "name": "Camera Lineup",
    "author": "AuraFX",
    "version": (0, 9, 0),
    "blender": (5, 2, 0),
    "location": "3D Viewport > Sidebar > Lineup",
    "description": "Line a camera up to a plate: pins on lidar and perspective lines; solve and keyframe",
    "category": "Camera",
}


def _modules():
    from . import draw, operators, props, ui
    return (props, operators, ui, draw)


def _reload():
    """Re-enabling the add-on (or Reload Scripts) only re-runs this file: reload the rest so edits are picked up."""
    import importlib

    from . import draw, operators, pick, props, solve, ui
    for m in (solve, pick, props, operators, ui, draw):
        importlib.reload(m)


def register():
    _reload()
    for m in _modules():
        m.register()


def unregister():
    for m in reversed(_modules()):
        m.unregister()
