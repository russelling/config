# Copyright (c) 2018 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

"""
Hook which chooses an environment file to use based on the current context.
"""

from tank import Hook


class PickEnvironment(Hook):
    def execute(self, context, **kwargs):

        if context.source_entity:
            if context.source_entity["type"] == "Version":
                return "version"
            elif context.source_entity["type"] == "PublishedFile":
                return "publishedfile"
            elif context.source_entity["type"] == "Playlist":
                return "playlist"

        if context.project is None:
            return "site"

        if context.entity is None:
            return "project"

        if context.entity and context.step is None:
            if context.entity["type"] == "Shot":
                return "shot"
            if context.entity["type"] == "Asset":
                return "asset"
            if context.entity["type"] == "Sequence":
                return "sequence"

        if context.entity and context.step:
            if context.entity["type"] == "Shot":
                return "episode_shot_step"
            if context.entity["type"] == "Asset":
                return "asset_step"

        return None
