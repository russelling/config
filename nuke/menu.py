# Buffalo VFX Nuke tools
# =======================
#
# Auto-loaded by Nuke on every launch because this folder sits on
# NUKE_PATH -- wired up in
# flow/current/config/hooks/tk-multi-launchapp/before_app_launch.py,
# which fires for every Toolkit-launched Nuke/Nuke Studio session
# (via ShotGrid Desktop). Nuke automatically adds every NUKE_PATH
# directory to sys.path too, so "tools" below imports cleanly.
#
# This repo is intentionally NOT part of FlowTrackingConfig's own git
# history -- same pattern as BUF_Mac_watcher / ingest_pipeline (see
# CLAUDE_INSTRUCTIONS.md). It's checked out here as a sibling of flow/,
# and the hook above is what makes it load automatically.
#
# icon: icons/buf_icon.png is the studio "teardrop" mark (white rounded
# badge, black teardrop, transparent background) -- derived from
# shots/_globals/logo/teardrop_blk1.png with its solid black backing
# removed so it reads cleanly against Nuke's dark UI. Built as an
# absolute path below (os.path.dirname(__file__)) rather than a relative
# "icons/buf_icon.png" -- unambiguous regardless of Nuke's plugin-path
# icon search.
#
# By design (per Mark, 2026-09-13): the icon shows on the LEFT NODES
# TOOLBAR flyout only. The top "Buffalo VFX" pulldown menu stays plain
# text, no icon -- that's also just how Nuke's top menu bar tends to
# render anyway (it commonly ignores icons on plain menu items), so this
# keeps the two registrations honest about what actually shows where.
#
# Registered in two places: the top "Buffalo VFX" menu (nuke.menu("Nuke"),
# text only) and a matching "Buffalo VFX" flyout on the left Nodes
# toolbar (nuke.toolbar("Nodes"), with the icon) -- same commands either
# way, both built from the _TOOLS list below so they can't drift out of
# sync even though one carries an icon and the other doesn't.
#
# To add a new tool:
#   1. Write it as a module under tools/, exposing one function that
#      builds/runs the tool (see tools/cg_element_relight.py for the
#      pattern -- a build_*() function that returns/creates the node).
#   2. Add one (display name, function) tuple to _TOOLS below.
#
# A tool that fails to import must not take the whole menu down with it.
# QT Frame Preview depends on the watcher repo being reachable on the
# share; if it isn't, that one entry is dropped and the rest still load,
# with the reason printed to the script editor rather than swallowed.

import os
import traceback

import nuke


# STUDIO_NUKE_PYTHON_LIBS_2026_10_28 -- shared third-party Python packages (NumPy, for
# mmColorTarget) from repo/pipeline/deploy/nuke_python, matched to this
# Nuke's Python version + OS. Must run before the tools below import.
try:
    from tools import nuke_python_libs
    nuke_python_libs.install()
except Exception:
    print("[Buffalo VFX] shared Python packages not added:\n%s" % traceback.format_exc())

from tools import cg_element_relight

_ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "buf_icon.png").replace("\\", "/")

_TOOLS = [
    ("CG Element Relight", cg_element_relight.build_cg_element_relight),
    # Next tool goes here, e.g.:
    # ("Some New Tool", some_new_tool.build_some_new_tool),
]

try:
    from tools import qt_frame_preview
    _TOOLS.append(("QT Frame Preview", qt_frame_preview.build_qt_frame_preview))
except Exception:
    print("[Buffalo VFX] QT Frame Preview not loaded:\n%s" % traceback.format_exc())

# STUDIO_SHARED_TOOLS_2026_10_27 -- gizmos and .nk files from
# <share>/buffalo_vfx/repo/installers/nuke, under the Nodes-toolbar
# Buffalo VFX flyout (and so in the Tab menu). See tools/shared_toolsets.py.
# Only the Rescan command goes in _TOOLS: buffalo_menu.py turns every item
# in the top-level menu into a Flow command, so the tree stays off it.
try:
    from tools import shared_toolsets
    _TOOLS.append((shared_toolsets.RESCAN_LABEL, shared_toolsets.rescan))
except Exception:
    shared_toolsets = None
    print("[Buffalo VFX] Shared Tools not loaded:\n%s" % traceback.format_exc())


# STUDIO_RENDER_REVIEW_READ_2026_10_28 -- after a local render a Read of the new EXRs
# appears next to the Write (one per Write, repointed on each render).
# 'Read From Selected Write' does the same by hand, e.g. after a farm
# render. See tools/render_review_read.py.
try:
    from tools import render_review_read
    render_review_read.register()
    _TOOLS.append((render_review_read.MENU_LABEL, render_review_read.read_from_selected))
except Exception:
    print("[Buffalo VFX] Render Review Read not loaded:\n%s" % traceback.format_exc())


def _register(parent_menu, icon):
    submenu = parent_menu.addMenu("Buffalo VFX", icon=icon)
    for name, fn in _TOOLS:
        submenu.addCommand(name, fn, icon=icon)
    return submenu


_register(nuke.menu("Nuke"), icon=None)
_nodes_submenu = _register(nuke.toolbar("Nodes"), icon=_ICON)
if shared_toolsets is not None:
    shared_toolsets.install(_nodes_submenu)


# SDV LD-Plugins
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Anamorphic_Standard_Degree_4", "nuke.createNode('LD_3DE4_Anamorphic_Standard_Degree_4')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Anamorphic_Rescaled_Degree_4", "nuke.createNode('LD_3DE4_Anamorphic_Rescaled_Degree_4')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Anamorphic_Standard_Degree_6", "nuke.createNode('LD_3DE4_Anamorphic_Standard_Degree_6')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Anamorphic_Rescaled_Degree_6", "nuke.createNode('LD_3DE4_Anamorphic_Rescaled_Degree_6')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Anamorphic_Stabilizer_Degree_6", "nuke.createNode('LD_3DE4_Anamorphic_Stabilizer_Degree_6')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Radial_Standard_Degree_4", "nuke.createNode('LD_3DE4_Radial_Standard_Degree_4')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Radial_Stabilizer_Degree_6", "nuke.createNode('LD_3DE4_Radial_Stabilizer_Degree_6')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE4_Radial_Fisheye_Degree_8", "nuke.createNode('LD_3DE4_Radial_Fisheye_Degree_8')")
nuke.menu("Nodes").addCommand("3DE4/LD_3DE_Classic_LD_Model", "nuke.createNode('LD_3DE_Classic_LD_Model')")


# ---------------------------------------------------------------------------
# Marker Timebar  --  STUDIO_MARKER_TIMEBAR_2026_11_17
# bratgot/Timebarmarkers @ d3622f3 (MIT), Python version, in
# third_party/marker_timebar/. Custom Tools > Marker Timebar (Alt+M) /
# Marker Timebar Panel (Alt+Shift+M). Markers are stored on the Root node.
# ---------------------------------------------------------------------------
def _buf_marker_timebar():
    import os
    import sys
    import nuke
    import __main__
    try:
        here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        here = None
    candidates = ([here] if here else []) + list(nuke.pluginPath())
    folder = None
    for base in candidates:
        path = os.path.join(base, "third_party", "marker_timebar")
        if os.path.isfile(os.path.join(path, "marker_timebar.py")):
            folder = path
            break
    if folder is None:
        print("[marker_timebar] not loaded: third_party/marker_timebar not found "
              "next to menu.py or on the plugin path")
        return
    if folder not in sys.path:
        sys.path.append(folder)
    import marker_timebar
    __main__.marker_timebar = marker_timebar   # menu commands are strings
    # Nuke 16+ Viewer support - STUDIO_MARKER_TIMEBAR_NUKE17_2026_11_17
    try:
        import buf_marker_timebar
        buf_marker_timebar.install(marker_timebar)
    except Exception as exc:
        print("[marker_timebar] Nuke 17 adapter not loaded: %r" % (exc,))
    marker_timebar.register()
    print("[marker_timebar] loaded from %s" % folder)


try:
    _buf_marker_timebar()
except Exception as _buf_tbm_exc:
    print("[marker_timebar] not loaded: %r" % (_buf_tbm_exc,))
