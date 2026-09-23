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
#
# Blender, second thing: set PYSIDE2_PYTHONPATH to the per-user folder
# buf_blender_setup.py installs PySide6 into, so artists no longer run
# setx / launchctl setenv by hand. (The variable really is named "2" --
# tk-blender v2.0.1 is a PySide6 fork that never renamed it.) See
# BLENDER_SETUP.md, and _setup_blender_pyside() below for why this hook
# gets the last word over the engine's own default.

import os
import sys

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()

_NUKE_ENGINES = ("tk-nuke", "tk-nukestudio")
_BLENDER_ENGINES = ("tk-blender",)

# Where buf_blender_setup.py installs PySide6. Per-user and never inside
# Blender's own install tree: that needs admin rights, is wiped by every
# Blender update, and fails outright (WinError 5) on the managed Windows VMs.
# Keep these two in step with target_dir() in buf_blender_setup.py.
_PYSIDE_PARENT = "BuffaloVFX"
_PYSIDE_DIRNAME = "blender_pyside6"


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

        self._setup_blender_pyside()

    def _pyside_dir(self):
        """
        The studio's per-user PySide6 folder for this platform.

        Mirrors target_dir() in buf_blender_setup.py. If you change one,
        change the other -- they are the two ends of the same convention.
        """
        if sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA") or os.path.join(
                os.path.expanduser("~"), "AppData", "Local"
            )
        elif sys.platform == "darwin":
            base = os.path.join(
                os.path.expanduser("~"), "Library", "Application Support"
            )
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.join(
                os.path.expanduser("~"), ".local", "share"
            )
        return os.path.join(base, _PYSIDE_PARENT, _PYSIDE_DIRNAME)

    @staticmethod
    def _holds_pyside(path):
        return bool(path) and os.path.isdir(os.path.join(path, "PySide6"))

    def _setup_blender_pyside(self):
        """
        Point PYSIDE2_PYTHONPATH at a folder that actually contains PySide6.

        tk-blender draws its entire UI with Qt and Blender ships no Qt
        bindings, so without this the engine's startup script aborts on
        import and Blender opens looking completely stock -- no dialog, no
        menu, the reason only in the system console.

        ORDERING -- why this hook gets the last word (verified in
        tk-multi-launchapp v0.14.2 base_launcher.py):

            prepare_launch_for_engine()   -> os.environ.update(required_env)
            execute_hook("hook_before_app_launch")   <- we are here
            execute_hook("hook_app_launch")

        and tk-blender v2.0.1's prepare_launch() only fills the variable in
        when it is unset, pointing it at its own <engine>/python/ext (which
        is not present in the checkout on the share). So by now the variable
        is always set, and never usefully.

        That makes "is it set?" a useless question, so the decision is made
        on CONTENT instead -- an artist's real setx/launchctl value still
        wins, the engine's empty placeholder does not, and a machine with no
        PySide6 anywhere gets a warning rather than a silent stock Blender.
        """
        studio = self._pyside_dir()
        current = os.environ.get("PYSIDE2_PYTHONPATH")

        if self._holds_pyside(current):
            self.logger.info(
                "PYSIDE2_PYTHONPATH already resolves to a real PySide6: %s"
                % current
            )
            return

        if self._holds_pyside(studio):
            if current:
                self.logger.info(
                    "PYSIDE2_PYTHONPATH was %s, which holds no PySide6; using "
                    "the studio folder instead: %s" % (current, studio)
                )
            else:
                self.logger.info("Set PYSIDE2_PYTHONPATH: %s" % studio)
            os.environ["PYSIDE2_PYTHONPATH"] = studio
            return

        self.logger.warning(
            "No PySide6 found for Blender on this machine -- looked in %s%s. "
            "Blender will launch WITHOUT the Flow menu. Fix: run "
            "buf_blender_setup.py from Blender's Scripting workspace (see "
            "BLENDER_SETUP.md). Leaving PYSIDE2_PYTHONPATH untouched."
            % (studio, (" and %s" % current) if current else "")
        )
