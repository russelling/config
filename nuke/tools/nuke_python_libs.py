# -*- coding: utf-8 -*-
"""
Shared third-party Python packages for Nuke - currently NumPy, which
mmColorTarget's "Calculate Matrix" button needs and Nuke does not ship
(Mark, 2026-10-28: "No module named numpy" on the workstations).

Instead of pip-installing into every workstation's Nuke install (admin rights,
per machine, per Nuke version), the packages live once on the share:

    repo/pipeline/deploy/nuke_python/cp<XY>-<platform>/
        cp310 / cp311 / cp312 : numpy 1.26.4      cp313 : numpy 2.2.6
        platforms             : win_amd64, macos_arm64
    repo/pipeline/deploy/nuke_python/_wheels/   the official PyPI wheels they
                                                were unpacked from

install() (called first thing from menu.py, so GUI sessions only) APPENDS the
folder matching the running Python version + OS to sys.path - appended, so
anything Nuke itself ships always wins. Nothing is imported here: NumPy only
loads when a tool asks for it. If there is no matching folder (a new Nuke on a
newer Python), it says so in the Script Editor rather than failing later with
a bare "No module named numpy".

BUF_NUKE_PYTHON_LIBS overrides the root folder.
"""

import os
import platform
import sys

ENV_OVERRIDE = "BUF_NUKE_PYTHON_LIBS"
LOG = "[nuke-python-libs]"


def libs_root():
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return os.path.normpath(override)
    here = os.path.dirname(os.path.abspath(__file__))       # config/nuke/tools
    config_dir = os.path.dirname(os.path.dirname(here))      # repo/pipeline/config
    return os.path.normpath(os.path.join(config_dir, "..", "deploy", "nuke_python"))


def platform_tag():
    if sys.platform.startswith("win"):
        return "win_amd64" if sys.maxsize > 2 ** 32 else None
    if sys.platform == "darwin":
        return "macos_arm64" if platform.machine() == "arm64" else None
    return None


def libs_folder():
    tag = platform_tag()
    if tag is None:
        return None
    return os.path.join(
        libs_root(), "cp%d%d-%s" % (sys.version_info[0], sys.version_info[1], tag))


def install():
    """Add the matching folder to sys.path. Returns it, or None (logged)."""
    folder = libs_folder()
    if folder is None or not os.path.isdir(folder):
        print("%s no shared packages for Python %d.%d on %s %s (looked for %s) - "
              "NumPy tools such as mmColorTarget's Calculate Matrix will not work."
              % (LOG, sys.version_info[0], sys.version_info[1], sys.platform,
                 platform.machine(), folder))
        return None
    if folder not in sys.path:
        sys.path.append(folder)
    print("%s added %s" % (LOG, folder))
    return folder
