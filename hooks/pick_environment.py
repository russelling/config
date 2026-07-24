# Copyright (c) Studio. All Rights Reserved.
"""
pick_environment hook – routes context to the correct environment YAML.

Episodic routing:
  Shot is episodic when Shot.sg_sequence is populated and that Sequence
  has its standard episode field linked to an Episode.

Environment map:
  project           - No entity context
  shot              - Shot, no step (legacy)
  shot_step         - Shot + step (legacy sequence without episode)
  episode_shot_step - Shot + step + Sequence.episode populated (episodic)
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

        if et == "Episode":
            return "project"

        return "project"

    def _shot_env(self, context):
        """Return 'episode_shot_step' when Shot's Sequence has an episode link."""
        try:
            result = self.parent.shotgun.find_one(
                "Shot", [["id", "is", context.entity["id"]]], ["sg_sequence"]
            )
            sg_sequence = result.get("sg_sequence") if result else None
            if not sg_sequence:
                return "shot_step"
            sequence = self.parent.shotgun.find_one(
                "Sequence", [["id", "is", sg_sequence["id"]]], ["episode"]
            )
            if sequence and sequence.get("episode"):
                return "episode_shot_step"
        except Exception:
            pass
        return "shot_step"
