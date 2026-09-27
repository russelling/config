# Copyright (c) Studio. All Rights Reserved.
"""
shot_workdir.py

Makes the current shot's folder (shots/{Episode}/{Scene}/{Shot}, the
`shot_base` template) the place Nuke works from:

  * root 'project_directory' knob -> the shot folder, so relative paths
    resolve there (only when set_script_knob=True: the scene_operation hook
    does this on open / save as / new; a bare context change does not, so it
    never marks a script modified);
  * the process working directory -> the shot folder;
  * file-browser favourites in the left column of every Nuke file browser:
    Shot, Shot plates, Shot renders, Shot elements, Shot reference. They are
    repointed on every context change, so they always mean the shot you are
    in, and removed when the context is not a shot.

Called from hooks/scene_operation_tk-nuke.py and the core hooks
engine_init.py and context_change.py (added 2026-09-26). Never raises.
"""

import os

# (favourite name, folder under the shot). A favourite is only added when its
# folder exists.
FAVOURITES = (
    ("Shot", ""),
    ("Shot plates", "plates"),
    ("Shot renders", "render/work"),
    ("Shot elements", "render/elements"),
    ("Shot reference", "reference"),
)


def shot_dir_for_context(tk, context):
    """The shot folder for a Shot context, or None."""
    entity = getattr(context, "entity", None) or {}
    if entity.get("type") != "Shot":
        return None
    template = tk.templates.get("shot_base")
    if template is None:
        return None
    try:
        fields = context.as_template_fields(template)
        return template.apply_fields(fields)
    except Exception:
        return None


def _favourite_kinds(nuke):
    kinds = 0
    for flag in ("IMAGE", "GEO", "SCRIPT"):
        kinds |= getattr(nuke, flag, 0)
    return kinds


def _remove_favourites(nuke, kinds):
    for name, _sub in FAVOURITES:
        try:
            nuke.removeFavoriteDir(name, kinds)
        except Exception:
            try:
                nuke.removeFavoriteDir(name)
            except Exception:
                pass


def apply(tk, context, set_script_knob=True):
    """Point Nuke at the context's shot folder. Returns that folder or None."""
    try:
        import nuke
    except Exception:
        return None
    kinds = _favourite_kinds(nuke)
    try:
        shot_dir = shot_dir_for_context(tk, context)
        _remove_favourites(nuke, kinds)
        if not shot_dir or not os.path.isdir(shot_dir):
            return None
        shot_dir_nuke = shot_dir.replace("\\", "/")

        if set_script_knob:
            try:
                nuke.root()["project_directory"].setValue(shot_dir_nuke)
            except Exception:
                pass
        try:
            os.chdir(shot_dir)
        except OSError:
            pass

        for name, sub in FAVOURITES:
            path = os.path.join(shot_dir, sub) if sub else shot_dir
            if not os.path.isdir(path):
                continue
            try:
                nuke.addFavoriteDir(name, path.replace("\\", "/") + "/", kinds)
            except Exception:
                pass
        return shot_dir_nuke
    except Exception:
        return None
