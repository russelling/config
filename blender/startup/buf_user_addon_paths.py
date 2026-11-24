# buf_user_addon_paths.py -- Blender startup module (BLENDER_SYSTEM_SCRIPTS/startup)
#
# Why this exists
# ---------------
# A Flow launch of Blender replaces BOTH of the script folders a third-party
# installer (V-Ray for Blender, etc.) puts its add-on in:
#
#   BLENDER_USER_SCRIPTS   -> tk-blender's <engine>/resources/scripts
#                             (tk-blender v2.0.1 startup.py, prepare_launch)
#   BLENDER_SYSTEM_SCRIPTS -> the studio blender/ tools repo
#                             (hooks/tk-multi-launchapp/before_app_launch.py)
#
# so an add-on that works on a manual launch is simply not found under Flow.
# Blender then fails to enable it from preferences, and with auto-save
# preferences that failure can be saved as "off".
#
# What it does
# ------------
# Only when launched by Flow: adds the artist's DEFAULT user and system script
# folders (the ones a manual launch uses) to Preferences > File Paths >
# Script Directories, then refreshes sys.path. Startup modules run before
# Blender enables the add-ons saved in preferences, so those add-ons then load
# normally, from where they are installed, with their own preferences intact.
# Nothing is copied and no add-on is named here.
#
# The entry persists in userpref.blend. That is harmless: on a manual launch
# the folder is already a script path and Blender de-duplicates it.
#
# Every action, and every folder skipped, is printed with a [BUF] prefix.

import os
import sys

import bpy

ENTRY_PREFIX = "Flow: "


def _log(msg):
    print("[BUF] user add-on paths: %s" % msg)


def _version_dir():
    return "%d.%d" % bpy.app.version[:2]


def _default_system_scripts():
    """Blender's default SYSTEM scripts folder, which BLENDER_SYSTEM_SCRIPTS replaces."""
    ver = _version_dir()
    if sys.platform == "darwin":
        return os.path.join("/Library/Application Support/Blender", ver, "scripts")
    if sys.platform == "win32":
        base = os.environ.get("ProgramData", r"C:\ProgramData")
        return os.path.join(base, "Blender Foundation", "Blender", ver, "scripts")
    return os.path.join("/usr/share/blender", ver, "scripts")


def _candidates():
    # resource_path('USER') ignores BLENDER_USER_SCRIPTS (verified in
    # Blender 5.3), so this is the per-user folder a manual launch uses:
    #   macOS   ~/Library/Application Support/Blender/<ver>/scripts
    #   Windows %APPDATA%\Blender Foundation\Blender\<ver>\scripts
    user_base = bpy.utils.resource_path("USER")
    out = []
    if user_base:
        out.append(("user scripts", os.path.join(user_base, "scripts")))
    out.append(("system scripts", _default_system_scripts()))
    return out


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _launched_by_flow():
    return os.environ.get("SGTK_ENGINE") == "tk-blender"


def register():
    if not _launched_by_flow():
        return  # manual launch: Blender already uses these folders

    prefs = bpy.context.preferences
    in_use = {_norm(p) for p in bpy.utils.script_paths()}
    added = False

    for label, path in _candidates():
        if not os.path.isdir(os.path.join(path, "addons")):
            _log("%s: no add-ons folder at %s -- skipped" % (label, path))
            continue
        if _norm(path) in in_use:
            _log("%s: already a script path: %s" % (label, path))
            continue
        entry = prefs.filepaths.script_directories.new()
        entry.name = ENTRY_PREFIX + label
        entry.directory = path
        in_use.add(_norm(path))
        added = True
        _log("%s: added to Script Directories: %s" % (label, path))

    if added:
        bpy.utils.refresh_script_paths()


def unregister():
    pass
