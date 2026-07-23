# Copyright (c) 2026 Buffalo VFX / russelling config
#
# Before App Launch — prepend this config's nuke/ folder onto NUKE_PATH so
# CameraTracker film-back presets (and any future studio Nuke startup) load
# when launching Nuke / Nuke Studio from ShotGrid Desktop.

import os

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()

# Engines that boot Nuke's Python / CameraTracker film-back list
_NUKE_ENGINES = ("tk-nuke", "tk-nukestudio")


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
