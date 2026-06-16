"""
post_engine_init_register_render_callback.py

Toolkit post_engine_init hook for tk-nuke. Runs once, automatically, every
time the Nuke engine finishes starting up. Its only job is to import
render_complete_callback.py (by file path, since hooks/ is not on
sys.path by default) and call its register() function so that
nuke.addAfterRender() is actually wired up.

Without this hook, render_complete_callback.py's register() function is
defined but never called, and the render-complete dialog / flag-JSON
pipeline never fires.
"""

import os
import sys
import importlib.util

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()


class PostEngineInit(HookBaseClass):
    def execute(self, **kwargs):
        engine = self.parent.engine
        try:
            hooks_dir = os.path.dirname(os.path.abspath(__file__))
            module_path = os.path.join(hooks_dir, "render_complete_callback.py")

            spec = importlib.util.spec_from_file_location(
                "render_complete_callback", module_path
            )
            render_complete_callback = importlib.util.module_from_spec(spec)
            sys.modules["render_complete_callback"] = render_complete_callback
            spec.loader.exec_module(render_complete_callback)

            render_complete_callback.register()
            engine.logger.info(
                "render_complete_callback: registered nuke.addAfterRender() callback."
            )
        except Exception as e:
            engine.logger.error(
                "render_complete_callback: failed to register callback: %s" % e
            )
