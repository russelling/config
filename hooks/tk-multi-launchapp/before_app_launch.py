# Copyright (c) 2026 Buffalo VFX / russelling config
#
# Before App Launch — prepend this config's nuke/ folder onto NUKE_PATH so
# CameraTracker film-back presets (and any future studio Nuke startup) load
# when launching Nuke / Nuke Studio from ShotGrid Desktop. Also points
# tk-blender at the studio's shared PySide6 install.

import os
import sys

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()

# Engines that boot Nuke's Python / CameraTracker film-back list
_NUKE_ENGINES = ("tk-nuke", "tk-nukestudio")

# Blender ships no Qt bindings, so tk-blender loads PySide6 from the directory
# named by PYSIDE2_PYTHONPATH (the engine keeps the PySide2-era variable name).
# Each platform folder holds wheels matching that platform's pinned Blender in
# software_paths.yml -- reinstall after a Blender upgrade that changes the
# bundled Python ABI.
_PYSIDE_PLATFORM_DIRS = {
    "darwin": "darwin",
    "win32": "win64",
    "linux": "linux",
}


class BeforeAppLaunch(HookBaseClass):
    def execute(
        self,
        app_path,
        app_args,
        version,
        engine_name,
        software_entity=None,
        **kwargs
    ):
        if engine_name == "tk-blender":
            self._setup_blender_pyside()
            return

        if engine_name not in _NUKE_ENGINES:
            return

        try:
            config_root = self.sgtk.pipeline_configuration.get_config_location()
        except Exception:
            # hooks/tk-multi-launchapp → config root
            config_root = os.path.abspath(
                os.path.join(self.disk_location, os.pardir, os.pardir)
            )

        nuke_startup = os.path.join(config_root, "nuke")
        if not os.path.isdir(nuke_startup):
            self.logger.warning(
                "Nuke startup folder missing (expected film-back presets): %s"
                % nuke_startup
            )
            return

        sgtk.util.append_path_to_env_var("NUKE_PATH", nuke_startup)
        self.logger.info("Added studio Nuke path to NUKE_PATH: %s" % nuke_startup)

    def _setup_blender_pyside(self):
        # tk-multi-launchapp applies the engine launcher's environment before
        # running this hook, and tk-blender's launcher defaults
        # PYSIDE2_PYTHONPATH to its own (non-existent) python/ext folder. So an
        # existing value only counts as a real override when it actually holds
        # Qt bindings -- otherwise we would always defer to that dead default.
        existing = os.environ.get("PYSIDE2_PYTHONPATH")
        if existing and self._provides_qt_bindings(existing):
            return

        platform_dir = None
        for prefix, name in _PYSIDE_PLATFORM_DIRS.items():
            if sys.platform.startswith(prefix):
                platform_dir = name
                break

        if platform_dir is None:
            self.logger.warning(
                "No shared PySide6 folder mapped for platform %s -- the "
                "ShotGrid menu will not appear in Blender." % sys.platform
            )
            return

        pyside_root = os.path.join(
            self.sgtk.pipeline_configuration.get_path(),
            "resources",
            "pyside6",
            platform_dir,
        )

        if not os.path.isdir(pyside_root):
            self.logger.warning(
                "Shared PySide6 install missing, so the ShotGrid menu will "
                "not appear in Blender. Expected: %s" % pyside_root
            )
            return

        os.environ["PYSIDE2_PYTHONPATH"] = pyside_root
        self.logger.info("Blender will load PySide6 from: %s" % pyside_root)

    @staticmethod
    def _provides_qt_bindings(path):
        return any(
            os.path.isdir(os.path.join(path, binding))
            for binding in ("PySide6", "PySide2")
        )
