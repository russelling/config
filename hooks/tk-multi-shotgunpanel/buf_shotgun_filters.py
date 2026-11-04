# Copyright (c) Studio. All Rights Reserved.
"""
buf_shotgun_filters.py -- Flow Production Tracking panel: plates on a Task.

STUDIO_PANEL_TASK_PLATES_2026_11_03

Inherits the panel's own hook ({self}/shotgun_filters.py). In Nuke the
panel opens on the artist's Task, and the stock Versions tab of a Task
lists only Versions whose sg_task is that Task -- plates have no task, so
they never showed there. This widens that one query to:

    sg_task is <this Task>
    OR (entity is <the Task's Shot>  AND  sg_plt_lineup_frame_start is set)

i.e. the task's own versions plus the shot's plates. Every other tab and
location is the stock behaviour.

Wired in env/includes/settings/tk-multi-shotgunpanel.yml under
settings.tk-multi-shotgunpanel.nuke:

    shotgun_filters_hook: "{self}/shotgun_filters.py:{config}/tk-multi-shotgunpanel/buf_shotgun_filters.py"

Fails safe: if the Task's Shot or the plate field can't be read, the stock
filter is returned unchanged.
"""

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()

PLATE_MARKER_FIELD = "sg_plt_lineup_frame_start"   # same as hooks/plate_sg.py

_CACHE = {}


class BufShotgunFilters(HookBaseClass):

    def _task_shot(self, task_id):
        key = ("task_shot", task_id)
        if key not in _CACHE:
            row = self.parent.shotgun.find_one(
                "Task", [["id", "is", task_id]], ["entity"]) or {}
            entity = row.get("entity")
            _CACHE[key] = ({"type": "Shot", "id": entity["id"]}
                           if entity and entity.get("type") == "Shot" else None)
        return _CACHE[key]

    def _has_marker(self):
        if "marker" not in _CACHE:
            _CACHE["marker"] = PLATE_MARKER_FIELD in self.parent.shotgun.schema_field_read(
                "Version", PLATE_MARKER_FIELD)
        return _CACHE["marker"]

    def get_link_filters(self, sg_location, entity_type, context_project, context_user):
        filters = super(BufShotgunFilters, self).get_link_filters(
            sg_location, entity_type, context_project, context_user)
        if sg_location.entity_type != "Task" or entity_type != "Version":
            return filters
        task_filter = ["sg_task", "is", sg_location.entity_dict]
        if task_filter not in filters:
            return filters
        try:
            shot = self._task_shot(sg_location.entity_id)
            if shot is None or not self._has_marker():
                return filters
        except Exception as exc:
            self.parent.logger.warning(
                "buf_shotgun_filters: shot plates not added to the Task's "
                "Versions tab (%s)" % exc)
            return filters
        widened = {
            "filter_operator": "any",
            "filters": [
                task_filter,
                {"filter_operator": "all",
                 "filters": [["entity", "is", shot],
                             [PLATE_MARKER_FIELD, "is_not", None]]},
            ],
        }
        return [widened if f == task_filter else f for f in filters]
