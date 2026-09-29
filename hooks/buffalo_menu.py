# Copyright (c) Studio. All Rights Reserved.
"""
buffalo_menu.py -- put every studio command under
"Flow Production Tracking > Buffalo VFX" in Nuke.

STUDIO_BUFFALO_MENU_2026_09_29 (Mark's request 2026-09-29):

  1. The "Buffalo VFX" top-level menu that config/nuke/menu.py builds moves
     INTO the Flow Production Tracking menu.
  2. Everything tk-nuke used to file under "Other Items" goes into that same
     "Buffalo VFX" submenu.

HOW TK-NUKE BUILDS THAT MENU (verified in tk-nuke v0.16.3
python/tk_nuke/menu_generation.py, NukeMenuGenerator.create_menu):
commands are grouped by `properties["app"].display_name`; a command with no
app lands in the hard-coded "Other Items" group. Groups with more than one
command become a submenu. So:

  * "Other Items" -> "Buffalo VFX" is done by giving every app-less command
    (Load Shot Plates, the four Dailies Viewer commands, Rebuild Write Node)
    a stand-in "app" whose display_name is "Buffalo VFX" -- see
    _StudioMenuGroup. tk-core v0.23.8 only overwrites properties["app"] when
    an app is being initialised, which is never true in a core hook, so the
    value sticks. Every attribute tk-core / tk-nuke read off a command's app
    is on the stand-in (display_name, instance_name, documentation_url,
    log_metric) -- grep'd, not guessed.

  * The config/nuke tools (CG Element Relight, QT Frame Preview, ...) are
    read off the top-level "Buffalo VFX" menu menu.py built, registered as
    Toolkit commands that invoke the ORIGINAL menu item by path, and the
    top-level menu is then hidden (nuke.MenuItem.setVisible). Nothing about
    menu.py or its _TOOLS list is assumed, so a tool added there later
    appears here with no change to this file. The left Nodes-toolbar
    "Buffalo VFX" flyout menu.py also builds is left alone.

WHY IT RUNS FROM TWO PLACES: core/hooks/engine_init.py (engine start) and
core/hooks/context_change.py (every context change). tk-core drops every
app-less command on a context change and tk-nuke rebuilds its menu before
the core context_change hook runs -- see
claude/core-hook-commands-lost-on-context-change-2026-09-17.md. Both hooks
call organise() LAST, after the command modules have registered.

ORDER AND SEPARATORS (STUDIO_BUFFALO_MENU_LAYOUT_2026_09_29, Mark's request
2026-09-29): tk-nuke always sorts a group alphabetically and cannot add
separators, so after tk-nuke has built the menu, arrange() clears the
"Buffalo VFX" submenu and re-adds the same commands (same callbacks, same
icons) in the order of _LAYOUT below, with separators. Commands _LAYOUT does
not name (a new menu.py tool, QT Color Trace, ...) follow at the end in the
order they were registered, so nothing ever disappears. _LAYOUT names that
are not registered are skipped, and no separator is doubled or left dangling.

Everything here logs and swallows: a misfiled menu item is an
inconvenience, a Nuke that will not start is a stopped artist.
"""

import re

import nuke

STUDIO_MENU_NAME = "Buffalo VFX"

# Top-level menu bar that config/nuke/menu.py adds "Buffalo VFX" to.
_TOP_MENU_BAR = "Nuke"

# Command types tk-nuke files somewhere other than the app groups -- left
# exactly where they are.
_NOT_ADOPTED_TYPES = ("context_menu", "node", "panel")

# One deferred retry per Nuke session, in case config/nuke/menu.py had not
# built its menu yet when the engine started. Kept on the nuke module because
# this file is re-executed fresh on every context change.
_RETRY_FLAG = "_buf_studio_menu_retry_scheduled"
_RETRY_DELAY_MS = 3000

# Top-to-bottom order of Flow Production Tracking > Buffalo VFX. Command names
# exactly as registered; None is a separator. The Dailies Viewer names are set
# in hooks/dailies_viewer.py register(), the config/nuke ones by the labels in
# config/nuke/menu.py.
_LAYOUT = (
    "Dailies Viewer - Enable",
    "Dailies Viewer - Disable",
    "Dailies Viewer - Rebuild",
    "Trace Dailies Match (this shot)",
    None,
    "Load Shot Plates",
    "Rebuild Write Node",
    None,
    "QT Frame Preview",
    "CG Element Relight",
)


class _StudioMenuGroup(object):
    """Stand-in for a Toolkit app, used only as a command's
    properties["app"] so tk-nuke files the command under "Buffalo VFX"."""

    display_name = STUDIO_MENU_NAME
    instance_name = "buffalo_vfx"
    name = "buffalo_vfx"
    description = "Buffalo VFX studio tools"
    documentation_url = None

    def log_metric(self, *args, **kwargs):
        # tk-core's register_command callback wrapper calls this on click.
        pass

    def __repr__(self):
        return "<Buffalo VFX studio menu group>"


_GROUP = _StudioMenuGroup()


def _log(engine, level, message):
    try:
        getattr(engine.logger, level)("buffalo_menu: %s" % message)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# config/nuke tools: read the top-level menu, register, hide it
# ---------------------------------------------------------------------------

def _top_menu():
    try:
        return nuke.menu(_TOP_MENU_BAR).findItem(STUDIO_MENU_NAME)
    except Exception:
        return None


def _walk(menu, prefix=""):
    """(path, MenuItem) for every command under `menu`, submenus flattened to
    "Sub/Item" -- the same path syntax Nuke's addCommand turns back into a
    submenu, so nesting survives the move."""
    for item in menu.items():
        name = item.name()
        if not name:
            continue  # separator
        path = prefix + name
        if isinstance(item, nuke.Menu):
            for child in _walk(item, path + "/"):
                yield child
        else:
            yield path, item


def _make_invoker(path):
    full_path = STUDIO_MENU_NAME + "/" + path

    def _invoke():
        # Look the item up on every click rather than holding a MenuItem:
        # Nuke menu handles can expire (see tk-nuke's destroy_menu notes).
        item = nuke.menu(_TOP_MENU_BAR).findItem(full_path)
        if item is None:
            nuke.message(
                "Buffalo VFX: the tool '%s' is no longer in "
                "config/nuke/menu.py.\n\nRestart Nuke if it was just "
                "renamed." % path
            )
            return
        item.invoke()

    return _invoke


def _short_name(path):
    return "buf_" + re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")


def harvest_tools(engine):
    """Register each config/nuke menu.py tool as a Toolkit command in the
    Buffalo VFX group, then hide the top-level menu. Returns the number of
    commands added, or None if the top-level menu does not exist (yet)."""
    top = _top_menu()
    if top is None:
        return None
    added = 0
    for path, item in list(_walk(top)):
        if path in engine.commands:
            continue
        properties = {
            "app": _GROUP,
            "short_name": _short_name(path),
            "description": "Buffalo VFX tool (config/nuke/menu.py): %s" % path,
        }
        try:
            icon = item.icon()
        except Exception:
            icon = None
        if icon:
            properties["icon"] = icon
        engine.register_command(path, _make_invoker(path), properties)
        added += 1
    try:
        top.setVisible(False)
    except Exception as exc:
        _log(engine, "warning",
             "could not hide the top-level '%s' menu (%s) -- the tools "
             "are in the Flow menu as well." % (STUDIO_MENU_NAME, exc))
    return added


# ---------------------------------------------------------------------------
# "Other Items" -> "Buffalo VFX"
# ---------------------------------------------------------------------------

def adopt_app_less_commands(engine):
    """Give every command with no app (tk-nuke's "Other Items") the Buffalo
    VFX group. Returns the command names moved."""
    moved = []
    for name, command in engine.commands.items():
        properties = command.get("properties")
        if properties is None or properties.get("app") is not None:
            continue
        if properties.get("type", "default") in _NOT_ADOPTED_TYPES:
            continue
        properties["app"] = _GROUP
        moved.append(name)
    return sorted(moved)


# ---------------------------------------------------------------------------
# Order + separators inside the Buffalo VFX submenu
# ---------------------------------------------------------------------------

def _is_studio_command(command):
    app = (command.get("properties") or {}).get("app")
    return (getattr(app, "display_name", None) == STUDIO_MENU_NAME
            and getattr(app, "instance_name", None) == _GROUP.instance_name)


def ordered_entries(names):
    """_LAYOUT applied to the registered studio command names: a list of
    names and None (separator), unknown names appended, no leading, trailing
    or doubled separators."""
    present = set(names)
    entries = [e for e in _LAYOUT if e is None or e in present]
    listed = set(e for e in _LAYOUT if e is not None)
    extras = [n for n in names if n not in listed]
    if extras:
        entries.append(None)
        entries.extend(extras)
    tidy = []
    for entry in entries:
        if entry is None and (not tidy or tidy[-1] is None):
            continue
        tidy.append(entry)
    while tidy and tidy[-1] is None:
        tidy.pop()
    return tidy


def arrange(engine):
    """Re-lay the Buffalo VFX submenu tk-nuke just built. Returns True if it
    did, False if there was nothing to arrange."""
    if not getattr(engine, "has_ui", False):
        return False
    generator = getattr(engine, "menu_generator", None)
    menu_name = getattr(generator, "menu_name", None)
    if not menu_name:
        return False
    submenu = nuke.menu(_TOP_MENU_BAR).findItem(menu_name + "/" + STUDIO_MENU_NAME)
    if submenu is None or not isinstance(submenu, nuke.Menu):
        return False  # fewer than two studio commands: tk-nuke made no submenu

    commands = [(n, c) for n, c in engine.commands.items() if _is_studio_command(c)]
    by_name = dict(commands)
    entries = ordered_entries([n for n, _ in commands])
    missing = [e for e in _LAYOUT if e is not None and e not in by_name]

    submenu.clearMenu()
    for entry in entries:
        if entry is None:
            submenu.addSeparator()
            continue
        command = by_name[entry]
        icon = (command.get("properties") or {}).get("icon")
        if icon:
            submenu.addCommand(entry, command["callback"], icon=icon)
        else:
            submenu.addCommand(entry, command["callback"])
    if missing:
        _log(engine, "info", "layout names not registered here (skipped): %s"
             % missing)
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def rebuild_menu(engine):
    """Rebuild tk-nuke's Flow menu under the same conditions tk-nuke itself
    uses (no UI in batch; Nuke Studio env pre-loading turns rebuilds off)."""
    try:
        if (getattr(engine, "has_ui", False)
                and getattr(engine, "_context_change_menu_rebuild", True)
                and getattr(engine, "menu_generator", None) is not None):
            engine.menu_generator.create_menu()
            return True
    except Exception as exc:
        _log(engine, "error", "menu rebuild failed: %s" % exc)
    return False


def organise(engine):
    """Harvest the config/nuke tools, adopt "Other Items", rebuild the menu.
    Call LAST, after every other command module has registered."""
    if getattr(engine, "name", None) != "tk-nuke":
        return
    try:
        harvested = harvest_tools(engine)
        moved = adopt_app_less_commands(engine)
        _log(engine, "info",
             "tools from config/nuke: %s; moved from Other Items: %s"
             % ("menu not built yet" if harvested is None else harvested,
                moved or "(none)"))
        if harvested or moved:
            rebuild_menu(engine)
        # Always: tk-nuke may have rebuilt the menu (sorted, no separators)
        # without us, e.g. just before the context_change hook runs.
        arrange(engine)
        if harvested is None:
            _schedule_retry(engine)
    except Exception as exc:
        _log(engine, "error", "organise failed: %s" % exc)


def _schedule_retry(engine):
    """config/nuke/menu.py may run after the engine starts. Every menu.py on
    NUKE_PATH has run by the time Qt's event loop does, so a single-shot
    timer is enough -- once per session."""
    if getattr(nuke, _RETRY_FLAG, False):
        return
    try:
        from sgtk.platform.qt import QtCore
    except Exception as exc:
        _log(engine, "warning", "no Qt for the deferred retry: %s" % exc)
        return
    setattr(nuke, _RETRY_FLAG, True)

    def _retry():
        try:
            import sgtk
            current = sgtk.platform.current_engine()
        except Exception:
            return
        if current is None:
            return
        if harvest_tools(current):
            adopt_app_less_commands(current)
            rebuild_menu(current)
            arrange(current)
            _log(current, "info", "deferred retry picked up the config/nuke tools.")
        elif _top_menu() is None:
            _log(current, "warning",
                 "no top-level '%s' menu to move -- is config/nuke on "
                 "NUKE_PATH? (see before_app_launch.py)" % STUDIO_MENU_NAME)

    QtCore.QTimer.singleShot(_RETRY_DELAY_MS, _retry)
    _log(engine, "info", "top-level '%s' menu not built yet; retrying in %ds."
         % (STUDIO_MENU_NAME, _RETRY_DELAY_MS // 1000))
