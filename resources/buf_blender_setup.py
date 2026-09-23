"""
buf_blender_setup.py -- one-time Blender setup for Flow Production Tracking.

WHO RUNS THIS: every artist, once per workstation, per Blender install.
HOW TO RUN IT:  inside Blender -- Scripting workspace, Open, then Run.
                (See BLENDER_SETUP.md. You never need to know where
                Blender's Python lives; this script works that out.)

WHAT IT DOES
------------
The tk-blender engine draws its whole UI with Qt, and Blender does not ship
Qt bindings. Without them the Flow menu never appears -- Blender just opens
looking completely normal, with the reason buried in the system console.

So: this installs PySide6 into a per-user folder and points the engine at it.

It deliberately does NOT install into Blender's own Program Files /
Applications tree, which is what the engine's upstream README tells you to
do. That needs admin rights, it is wiped by every Blender update, and on
this studio's managed Windows VMs it fails outright with WinError 5 partway
through -- leaving a half-written site-packages behind.

WHERE IT INSTALLS TO
--------------------
  Windows   %LOCALAPPDATA%\\BuffaloVFX\\blender_pyside6
  macOS     ~/Library/Application Support/BuffaloVFX/blender_pyside6
  Linux     ~/.local/share/BuffaloVFX/blender_pyside6

One folder normally serves every Blender 5.x on the machine, because PySide6
ships abi3 wheels that work across Python 3.9+. If a second Blender version
fails the check at the end, re-run this script there with PER_VERSION = True
below and it will use a version-suffixed folder instead.

SAFE TO RE-RUN. It checks before it installs and tells you if there was
nothing to do.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

# Set True only if one shared folder turns out not to serve every Blender
# version on this machine (see the module docstring).
PER_VERSION = False

PYSIDE6_REQUIREMENT = "PySide6"

ENV_VAR = "PYSIDE2_PYTHONPATH"
# ^ Yes, "2". The engine is a PySide6 fork that never renamed the variable it
#   reads. It is just a generic "extra libs" path. Do not "fix" it.


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------

def blender_version_tag() -> str:
    try:
        import bpy  # noqa
        return "%d.%d" % bpy.app.version[:2]
    except Exception:
        # Running under a bare interpreter: fall back to the python version,
        # which is what actually decides wheel compatibility anyway.
        return "py%d.%d" % sys.version_info[:2]


def find_bundled_python() -> Path:
    """
    Locate the Python interpreter Blender is running on.

    Inside Blender, sys.executable is the *Blender* binary, not python --
    bpy.app.binary_path_python was removed in 2.91. sys.prefix, however,
    still points at Blender's bundled python tree on every platform:

        macOS    /Applications/Blender.app/Contents/Resources/5.2/python
        Windows  C:\\Program Files\\Blender Foundation\\Blender 5.2\\5.2\\python
        Linux    /usr/share/blender/5.2/python

    so the interpreter is prefix/bin/python*.
    """
    bin_dir = Path(sys.prefix) / "bin"
    if not bin_dir.is_dir():
        raise RuntimeError(
            "Could not find Blender's bundled Python. Looked for:\n"
            "  %s\n"
            "This usually means the script is not running under Blender.\n"
            "Open it in Blender's Scripting workspace and press Run."
            % bin_dir
        )

    names = ["python.exe"] if os.name == "nt" else [
        "python%d.%d" % sys.version_info[:2], "python3", "python"
    ]
    for name in names:
        candidate = bin_dir / name
        if candidate.is_file():
            return candidate

    found = sorted(p.name for p in bin_dir.iterdir())
    raise RuntimeError(
        "No python interpreter in %s\nContents: %s" % (bin_dir, ", ".join(found))
    )


def target_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))

    name = "blender_pyside6"
    if PER_VERSION:
        name += "_" + blender_version_tag()
    return base / "BuffaloVFX" / name


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def already_working() -> bool:
    try:
        import PySide6.QtWidgets  # noqa
        import PySide6
        say("PySide6 %s already imports inside this Blender." % PySide6.__version__)
        return True
    except ImportError:
        return False


def ensure_pip(python: Path) -> None:
    probe = subprocess.run(
        [str(python), "-m", "pip", "--version"],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        say("pip present: %s" % probe.stdout.strip())
        return

    say("pip missing from Blender's Python; bootstrapping with ensurepip...")
    boot = subprocess.run(
        [str(python), "-m", "ensurepip", "--upgrade"],
        capture_output=True, text=True,
    )
    if boot.returncode != 0:
        raise RuntimeError(
            "Could not bootstrap pip.\n%s\n%s" % (boot.stdout, boot.stderr)
        )


def install(python: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(python), "-m", "pip", "install", "--upgrade",
        PYSIDE6_REQUIREMENT, "--target", str(dest),
    ]
    say("Installing:\n  %s" % " ".join('"%s"' % c if " " in c else c for c in cmd))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    for line in (proc.stdout or "").splitlines()[-12:]:
        print("    | " + line)
    if proc.returncode != 0:
        print((proc.stderr or "").strip())
        raise RuntimeError(
            "pip failed (exit %d).\n\n"
            "If this is a network/proxy error, the studio proxy may need to be\n"
            "set for this shell. If it is 'Access is denied', check that\n"
            "  %s\n"
            "is somewhere you can write -- it should be inside your own user\n"
            "profile, never Program Files." % (proc.returncode, dest)
        )


def verify(python: Path, dest: Path) -> str:
    """
    Import PySide6 in a *fresh* interpreter with only `dest` prepended, which
    is exactly the situation the engine creates at launch. Importing it in
    this already-running Blender would not prove anything -- the module may
    have been found somewhere else entirely.
    """
    code = (
        "import sys, site\n"
        "site.addsitedir(%r)\n"
        "import PySide6, PySide6.QtWidgets, PySide6.QtCore\n"
        "print(PySide6.__version__)\n" % str(dest)
    )
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "PySide6 installed but will not import.\n\n%s\n\n"
            "On a second Blender version this usually means the shared folder\n"
            "does not suit both. Set PER_VERSION = True at the top of this\n"
            "script and run it again in this Blender."
            % (proc.stderr or "").strip()
        )
    return proc.stdout.strip()


# ---------------------------------------------------------------------------

def say(msg: str) -> None:
    print("[BUF setup] " + msg)


def main() -> int:
    print("=" * 72)
    say("Blender -> Flow Production Tracking, Qt bindings setup")
    print("=" * 72)

    say("Blender    : %s" % blender_version_tag())
    say("Python     : %s" % sys.version.split()[0])
    say("sys.prefix : %s" % sys.prefix)

    dest = target_dir()
    say("Install to : %s" % dest)

    preset = os.environ.get(ENV_VAR)
    if preset:
        say("%s is currently: %s" % (ENV_VAR, preset))
        if Path(preset) != dest:
            say("  NOTE: that is not where this script installs. The launch hook"
                " or a setx/launchctl value may point somewhere else.")

    if already_working():
        say("Nothing to install.")
    else:
        python = find_bundled_python()
        say("Interpreter: %s" % python)
        ensure_pip(python)
        install(python, dest)

    python = find_bundled_python()
    version = verify(python, dest)
    say("Verified: PySide6 %s imports from %s" % (version, dest))

    if os.name == "nt":
        env_cmd = ('  Windows -- in PowerShell:\n'
                   '    setx %s "%s"' % (ENV_VAR, dest))
    elif sys.platform == "darwin":
        env_cmd = ('  macOS -- in Terminal:\n'
                   '    launchctl setenv %s "%s"\n'
                   '    (session-scoped: macOS forgets this on reboot, so it\n'
                   '     has to be re-run after a restart.)' % (ENV_VAR, dest))
    else:
        env_cmd = ('  Linux -- in your login environment:\n'
                   '    export %s="%s"' % (ENV_VAR, dest))

    # dedent BEFORE interpolating -- env_cmd carries its own indentation and
    # would otherwise reset the common prefix dedent() computes.
    body = textwrap.dedent("""\
        DONE -- but read this bit.

        Blender still has to be told where that folder is, via the
        %s environment variable.

        Ask your TD whether the studio config's launch hook already sets it
        (it is the `_setup_blender` function in
        hooks/tk-multi-launchapp/before_app_launch.py). If it does, you are
        finished -- skip to the last paragraph.

        If it does not, run this once:

        %s

        Either way, finish by quitting Flow Production Tracking Desktop
        COMPLETELY -- tray icon, Quit, not just closing the window -- then
        relaunch it and open Blender from there. The Flow menu should be in
        Blender's top bar.

        Launching Blender from the Dock / Start menu instead of from Desktop
        will never show the Flow menu. That is expected, not a broken setup:
        Toolkit injects itself at launch, so Toolkit has to do the launching.
        """)

    print()
    print("=" * 72)
    print(body % (ENV_VAR, env_cmd))
    print("=" * 72)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print()
        print("!" * 72)
        print("[BUF setup] FAILED\n")
        print(exc)
        print("!" * 72)
        sys.exit(1)
