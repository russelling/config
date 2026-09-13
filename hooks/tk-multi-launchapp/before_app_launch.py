# Copyright (c) 2026 Buffalo VFX / russelling config
#
# Before App Launch
# ==================
#
# Nuke: prepend this config's nuke/ folder onto NUKE_PATH so CameraTracker
# film-back presets load, and ALSO append the separate nuke/ tools repo
# (a sibling of flow/, NOT part of FlowTrackingConfig's own git history --
# see that folder's README.md) so custom studio tools' menu.py auto-loads
# for every Toolkit-launched Nuke / Nuke Studio session.
#
# Blender: point BLENDER_SYSTEM_SCRIPTS at the separate blender/ tools
# repo (same non-FlowTrackingConfig-git pattern, see that folder's
# README.md) so its addons/ show up in Blender's Add-ons list without a
# manual "Install from File" step. Distinct from BLENDER_USER_SCRIPTS,
# which the tk-blender engine itself already sets for its own bundled
# resources -- the two don't collide.

import os

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()

_NUKE_ENGINES = ("tk-nuke", "tk-nukestudio")
_BLENDER_ENGINES = ("tk-blender",)


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
        if engine_name in _NUKE_ENGINES:
            self._setup_nuke()
        elif engine_name in _BLENDER_ENGINES:
            self._setup_blender()

    # -- helpers --------------------------------------------------------

    def _config_root(self):
        try:
            return self.sgtk.pipeline_configuration.get_config_location()
        except Exception:
            # hooks/tk-multi-launchapp -> config root
            return os.path.abspath(
                os.path.join(self.disk_location, os.pardir, os.pardir)
            )

    def _pipeline_repo_root(self, config_root):
        # config_root is <repo>/pipeline/config/flow/current/config --
        # three levels up is <repo>/pipeline/config, where the nuke/ and
        # blender/ tool repos live as siblings of flow/.
        return os.path.abspath(
            os.path.join(config_root, os.pardir, os.pardir, os.pardir)
        )

    # -- Nuke -------------------------------------------------------------

    def _setup_nuke(self):
        config_root = self._config_root()

        # Existing studio Nuke startup (CameraTracker film-back presets).
        nuke_startup = os.path.join(config_root, "nuke")
        if os.path.isdir(nuke_startup):
            sgtk.util.append_path_to_env_var("NUKE_PATH", nuke_startup)
            self.logger.info(
                "Added studio Nuke path to NUKE_PATH: %s" % nuke_startup
            )
        else:
            self.logger.warning(
                "Nuke startup folder missing (expected film-back presets): %s"
                % nuke_startup
            )

        # Separate custom-tools repo (menu.py + tools/) -- see nuke/README.md.
        nuke_tools = os.path.join(self._pipeline_repo_root(config_root), "nuke")
        if os.path.isdir(nuke_tools) and os.path.normpath(nuke_tools) != os.path.normpath(nuke_startup):
            sgtk.util.append_path_to_env_var("NUKE_PATH", nuke_tools)
            self.logger.info("Added Nuke tools repo to NUKE_PATH: %s" % nuke_tools)
        elif not os.path.isdir(nuke_tools):
            self.logger.warning("Nuke tools repo missing: %s" % nuke_tools)

    # -- Blender ----------------------------------------------------------

    def _setup_blender(self):
        config_root = self._config_root()

        blender_tools = os.path.join(self._pipeline_repo_root(config_root), "blender")
        if os.path.isdir(blender_tools):
            os.environ["BLENDER_SYSTEM_SCRIPTS"] = blender_tools
            self.logger.info(
                "Set BLENDER_SYSTEM_SCRIPTS to Blender tools repo: %s" % blender_tools
            )
        else:
            self.logger.warning("Blender tools repo missing: %s" % blender_tools)
