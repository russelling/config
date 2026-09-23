# Copyright (c) Studio. All Rights Reserved.
"""
engine_init.py

Toolkit core hook, called automatically every time an engine finishes
starting. Used here to register config-level commands that are not apps.

Currently: the Nuke "Load Shot Plates" command, which adds a Read node for
each plate element delivered for the current shot. The same code runs
automatically when Workfiles2 creates the first version of a shot's script
(see hooks/scene_operation_tk-nuke.py); this makes it available on demand
too, for a plate that landed after the script was made.

hooks/ is not on sys.path for a core hook, so plate_reads.py is imported by
file path the same way post_engine_init_register_render_callback.py does it.

Anything that fails here is logged and swallowed: a missing menu command is
an inconvenience, an engine that will not start is a stopped artist.
"""

import os
import sys
import importlib.util

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()


class EngineInit(HookBaseClass):

    @staticmethod
    def _log(engine, level, message):
        """Log without ever raising - this runs during engine startup, and an
        exception escaping here is a Nuke that will not open."""
        try:
            getattr(engine.logger, level)(message)
        except Exception:
            pass

    def execute(self, engine, **kwargs):
        if getattr(engine, "name", None) != "tk-nuke":
            return
        try:
            config_root = os.path.dirname(          # <config>/
                os.path.dirname(                    # <config>/core/
                    os.path.dirname(os.path.abspath(__file__))  # core/hooks/
                )
            )
            module_path = os.path.join(config_root, "hooks", "plate_reads.py")
            if not os.path.exists(module_path):
                self._log(engine, "warning",
                          "plate_reads: not found at %s - 'Load Shot Plates' "
                          "not registered." % module_path)
                return

            spec = importlib.util.spec_from_file_location(
                "plate_reads", module_path
            )
            plate_reads = importlib.util.module_from_spec(spec)
            sys.modules["plate_reads"] = plate_reads
            spec.loader.exec_module(plate_reads)

            plate_reads.register(engine)
            self._log(engine, "info",
                      "plate_reads: registered 'Load Shot Plates' command.")
        except Exception as exc:
            self._log(engine, "error",
                      "plate_reads: failed to register 'Load Shot Plates': %s"
                      % exc)
