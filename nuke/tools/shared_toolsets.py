# Buffalo VFX - Shared Tools menu
# ===============================
#
# Mark's request 2026-10-27: a menu of the gizmos and .nk files dropped into
#
#     <share>/buffalo_vfx/repo/installers/nuke/
#
# Lives on the left Nodes toolbar under  Buffalo VFX > Shared Tools, which
# also makes every entry findable from the Tab menu. The submenus mirror the
# folder tree, so the way to tidy the menu is to tidy the folder.
# "Rescan Shared Tools" (in both Buffalo VFX menus) picks up new drops without
# restarting Nuke.
#
# WHERE THE FOLDER IS
#   Found relative to this file, not hard-coded: config/nuke sits at
#   <share>/buffalo_vfx/repo/pipeline/config/nuke, so the folder is
#   ../../../installers/nuke from here - the same on macOS and Windows,
#   whatever the share is mounted as. BUF_NUKE_SHARED_TOOLS overrides it.
#
# HOW A TOOL GETS INTO THE SCRIPT - pasted, not loaded from a plugin path
#   Every entry is PASTED into the script, so the script carries its own
#   copy and does not need the share on the plugin path to open or render
#   (Deadline workers do not have it). A .gizmo is pasted as a Group - the
#   same nodes and knobs, just self-contained. The trade-off: changing a
#   gizmo on the share does not update scripts that already used it.
#
#   A .nk that is a whole saved script has its Root block and window layout
#   removed first - otherwise pasting it would overwrite this shot's format,
#   fps, frame range and OCIO settings and rearrange the artist's panels.
#
# WHAT IS LEFT OUT (edit the two lists below, not the code)
#   * Folders that need more than a paste to work: Cattery models (need
#     their .cat files and Cattery set-up), plugins/ (compiled plugins and
#     Python callbacks, e.g. LineDrawer), nuBridge (an application, not a
#     tool), bundled third-party Python.
#   * Nuke preference files (preferences*.nk) and autosaves.
#   Installers / archives (.dmg, .zip, .tgz) are never looked at - only
#   .gizmo and .nk files are.
#
# A missing or unreachable folder never stops Nuke starting: the menu shows
# one entry saying so, and the reason is printed to the Script Editor.

import fnmatch
import os
import re
import tempfile
import traceback

SUBMENU = "Shared Tools"
RESCAN_LABEL = "Rescan Shared Tools"
ENV_OVERRIDE = "BUF_NUKE_SHARED_TOOLS"

TOOL_EXTENSIONS = (".gizmo", ".nk")
MAX_DEPTH = 8

# Folder names never descended into (case-insensitive, fnmatch patterns).
SKIP_DIRS = (
    "cattery",
    "plugins",
    "nubridge*",
    "nkpd_third_party",
    "icons",
    "__pycache__",
)

# File names never listed (case-insensitive, fnmatch patterns).
SKIP_FILES = (
    "preferences*.nk",
    "*.autosave",
    "*_autosave*",
    "*~",
)

_NODES_MENU = "Nodes"
_PARENT = "Buffalo VFX"


def shared_tools_root():
    """The installers/nuke folder (may not exist)."""
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return os.path.normpath(override)
    here = os.path.dirname(os.path.abspath(__file__))  # config/nuke/tools
    nuke_dir = os.path.dirname(here)                    # config/nuke
    return os.path.normpath(os.path.join(nuke_dir, "..", "..", "..",
                                         "installers", "nuke"))


# ---------------------------------------------------------------------------
# Scanning (no nuke import - testable on a plain folder)
# ---------------------------------------------------------------------------

def _matches(name, patterns):
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, p) for p in patterns)


def scan(root):
    """[(menu path, file path)], menu path being "Sub/Folder/Name" relative
    to root. Sorted folder-first-then-name so the menu reads like the folder.
    """
    if not root or not os.path.isdir(root):
        return []
    found = []
    for directory, dirs, files in os.walk(root):
        rel = os.path.relpath(directory, root)
        parts = [] if rel == os.curdir else rel.split(os.sep)
        if len(parts) >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = sorted(
            (d for d in dirs
             if not d.startswith(".") and not _matches(d, SKIP_DIRS)),
            key=str.lower,
        )
        stems = {}
        for name in files:
            if name.startswith("."):
                continue
            stem, ext = os.path.splitext(name)
            if ext.lower() in TOOL_EXTENSIONS and not _matches(name, SKIP_FILES):
                stems.setdefault(stem.lower(), []).append(name)
        for name in sorted((n for group in stems.values() for n in group),
                           key=str.lower):
            stem, ext = os.path.splitext(name)
            label = stem
            if len(stems[stem.lower()]) > 1:
                label = "%s (%s)" % (stem, ext.lstrip(".").lower())
            # "/" and "\" would be read as submenus by Nuke.
            menu_parts = [p.replace("/", "_").replace("\\", "_")
                          for p in parts + [label]]
            found.append(("/".join(menu_parts), os.path.join(directory, name)))
    return found


# ---------------------------------------------------------------------------
# Turning a file into paste-able text (no nuke import - testable)
# ---------------------------------------------------------------------------

_GIZMO_RE = re.compile(r"^Gizmo(\s*)\{", re.MULTILINE)


# Top-level blocks a whole saved script carries that must not be pasted:
# Root would overwrite the shot's format / fps / range / OCIO, and the window
# layout would rearrange the artist's panels. Each ends at a "}" in column 0.
_SCRIPT_ONLY_BLOCKS = ("Root {", "define_window_layout_xml {")


def strip_root_block(text):
    """Remove top-level Root { ... } and window-layout blocks."""
    out, skipping = [], False
    for line in text.splitlines(True):
        if not skipping and line.startswith(_SCRIPT_ONLY_BLOCKS):
            skipping = True
            continue
        if skipping:
            if line.rstrip("\r\n") == "}":
                skipping = False
            continue
        out.append(line)
    return "".join(out)


def paste_text(path):
    """The text to paste for one tool file."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    text = strip_root_block(text)
    if path.lower().endswith(".gizmo"):
        text = _GIZMO_RE.sub(r"Group\1{", text, count=1)
    return text


def node_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem) or "SharedTool"
    if name[0].isdigit():
        name = "_" + name
    return name


# ---------------------------------------------------------------------------
# Nuke side
# ---------------------------------------------------------------------------

def insert(path):
    """Paste one tool into the current script."""
    import nuke
    if not os.path.isfile(path):
        nuke.message("Shared tool not found - it may have been moved or "
                     "deleted:\n\n%s\n\nUse Buffalo VFX > %s." % (path, RESCAN_LABEL))
        return
    handle, temp_path = tempfile.mkstemp(suffix=".nk", prefix="buf_shared_")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(paste_text(path))
        nuke.nodePaste(temp_path)
    except Exception as exc:
        nuke.tprint("[Shared Tools] could not insert %s:\n%s"
                    % (path, traceback.format_exc()))
        nuke.message("Could not insert shared tool:\n\n%s\n\n%s" % (path, exc))
        return
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    pasted = nuke.selectedNodes()
    if len(pasted) == 1:
        try:
            pasted[0].setName(node_name(path), uncollide=True)
        except Exception:
            pass


def _make_command(path):
    def _command():
        insert(path)
    return _command


def _populate(menu):
    # "Rescan Shared Tools" itself sits one level up, in menu.py's _TOOLS.
    root = shared_tools_root()
    if not os.path.isdir(root):
        print("[Shared Tools] folder not reachable: %s" % root)
        menu.addCommand("(folder not reachable - see Script Editor)", lambda: None)
        return 0
    tools = scan(root)
    if not tools:
        menu.addCommand("(no gizmos or .nk files found)", lambda: None)
    for menu_path, file_path in tools:
        menu.addCommand(menu_path, _make_command(file_path))
    print("[Shared Tools] %d tools from %s" % (len(tools), root))
    return len(tools)


def install(parent_menu=None):
    """Build Buffalo VFX > Shared Tools on the Nodes toolbar. Never raises."""
    try:
        import nuke
        if parent_menu is None:
            parent_menu = nuke.menu(_NODES_MENU).addMenu(_PARENT)
        _populate(parent_menu.addMenu(SUBMENU))
    except Exception:
        print("[Shared Tools] menu not built:\n%s" % traceback.format_exc())


def rescan():
    """Rebuild the Shared Tools submenu from the folder as it is now."""
    import nuke
    menu = nuke.menu(_NODES_MENU).findItem(_PARENT + "/" + SUBMENU)
    if menu is None:
        menu = nuke.menu(_NODES_MENU).addMenu(_PARENT).addMenu(SUBMENU)
    menu.clearMenu()
    count = _populate(menu)
    nuke.message("Shared Tools: %d tools found in\n\n%s"
                 % (count, shared_tools_root()))
