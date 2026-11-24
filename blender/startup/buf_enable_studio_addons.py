# Buffalo VFX -- keep the studio add-ons switched on.
#
# STUDIO_BLENDER_ADDON_AUTOENABLE_2026_11_02
#
# Lives in repo/pipeline/config/blender/startup/. Every Flow-launched Blender
# points BLENDER_SYSTEM_SCRIPTS at repo/pipeline/config/blender/
# (hooks/tk-multi-launchapp/before_app_launch.py), and Blender runs every
# module in that folder's startup/ on launch -- so this runs on every
# machine, every launch, with nothing to install.
#
# Why it exists: "enabled" for an add-on is stored only in the user's own
# preferences (userpref.blend). If those are not saved on quit, or the
# profile is reset between sessions (managed / streamed desktops), the add-on
# comes back unticked and has to be re-enabled by hand every time. This
# enables the add-ons below on every launch instead of relying on that.
#
# To keep another studio add-on on, add its module name (the file name
# without .py, or the folder name for a multi-file add-on) to STUDIO_ADDONS.
# An add-on missing from this machine is reported and skipped, never fatal.

import bpy

STUDIO_ADDONS = (
    "set_element_render_output_v2",   # Flow - Set Element Render Output
)

_LOG = "[Buffalo VFX startup]"


def _enable_studio_addons():
    import addon_utils

    available = {m.__name__ for m in addon_utils.modules(refresh=False)}
    for name in STUDIO_ADDONS:
        if name not in available:
            print("%s add-on %r not found on this machine's add-on paths -- "
                  "skipped. Was Blender launched through Flow?" % (_LOG, name))
            continue
        _default, loaded = addon_utils.check(name)
        if loaded:
            continue
        try:
            addon_utils.enable(name, default_set=True, persistent=True)
            print("%s enabled add-on %s" % (_LOG, name))
        except Exception as exc:  # never stop Blender starting
            print("%s could not enable %s: %s" % (_LOG, name, exc))
    return None   # one-shot timer


def register():
    # Deferred: Blender enables the add-ons saved in preferences right after
    # startup scripts run. Going after that means this only fills the gap
    # instead of racing it.
    bpy.app.timers.register(_enable_studio_addons, first_interval=0.5,
                            persistent=True)


def unregister():
    pass
