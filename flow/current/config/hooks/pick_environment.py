# Copyright (c) Studio. All Rights Reserved.
"""
pick_environment hook – routes context to the correct environment YAML.

Episodic routing:
  Shot is episodic when Shot.sg_scene is populated (linked to a Scene entity).
  Scene links to Episode via Scene.sg_episode.

Environment map:
  project           - No entity context
  shot              - Shot, no step (legacy)
  shot_step         - Shot + step (legacy sequence)
  episode_shot_step - Shot + step + sg_scene populated (episodic)
  asset             - Asset, no step
  asset_step        - Asset + step
  sequence          - Sequence entity
"""

import sgtk
HookBaseClass = sgtk.get_hook_baseclass()


class PickEnvironment(HookBaseClass):

    def execute(self, context, **kwargs):
        if context.source_entity:
            src_type = context.source_entity.get("type")
            if src_type == "PublishedFile":
                if context.entity is None:
                    return "project"
                et = context.entity["type"]
                if context.step is None:
                    return "asset" if et == "Asset" else "shot"
                return "asset_step" if et == "Asset" else self._shot_env(context)

        if context.entity is None:
            return "project"

        et = context.entity["type"]

        if et == "Shot":
            return self._shot_env(context) if context.step else "shot"

        if et == "Asset":
            return "asset_step" if context.step else "asset"

        if et == "Sequence":
            return "sequence"

        if et in ("Episode", "Scene"):
            return "project"

        return "project"

    def _shot_env(self, context):
        """Return 'episode_shot_step' when Shot.sg_scene is populated."""
        try:
            result = self.parent.shotgun.find_one(
                "Shot", [["id", "is", context.entity["id"]]], ["sg_scene"]
            )
            if result and result.get("sg_scene"):
                return "episode_shot_step"
        except Exception:
            pass
        return "shot_step"
