# Blender + Flow Production Tracking — workstation setup

**Who needs this:** anyone who will open Blender through Flow Production
Tracking Desktop. Once per machine, per Blender install.

**How long:** about five minutes, most of it a download.

**Admin rights:** not needed. If any step asks for them, you are following the
Blender engine's own upstream README instead of this one — stop and use this
one.

> Verified against the live config on **2026-09-17**: tk-blender v2.0.1
> (`icentric-dev` fork), tk-core v0.23.8, Blender 5.0 / 5.1 / 5.2.

---

## The short version

Blender ships no Qt libraries. The Flow engine is written entirely in Qt. So on
a fresh machine the engine's startup script fails on `import`, and **Blender
opens looking completely normal** — no Flow menu, no error dialog, nothing to
suggest anything went wrong.

Everything else is already configured on the share. The only per-machine step
is installing Qt (PySide6) into the copy of Python that ships inside Blender,
and there is a script that does it.

---

## Already done for you — no action required

These live in the config on the share and apply to every workstation:

| | |
|---|---|
| Blender engine | `icentric-dev/tk-blender` **v2.0.1** (PySide6). The older `diegogarciahuerta` v1.1.1 needs PySide2, which has no wheel for modern Blender's Python. |
| tk-core | v0.23.8 — above the v0.21.6 the fork requires for PySide6. |
| Launch icons | macOS/Linux get one **Blender** icon; Windows gets **Blender 5.2 / 5.1 / 5.0**, one per installed version. |
| Workfiles2 | New / Open / Save are pointed at the engine's own Blender scene-operation hook. Without that override they fail with "this file does not exist on disk". |
| Studio add-ons | `BLENDER_SYSTEM_SCRIPTS` is set at launch, so studio add-ons appear in Preferences → Add-ons with no "Install from File" step. |

You do not need to clone anything, edit any YAML, or run `tank` commands.

---

## The one step you do have to do

### 1. Open Blender — normally, from the Dock or Start menu

Not through Desktop. This step only needs Blender's own Python.

If you have more than one Blender version installed, do this in **each** of
them. Each version carries its own separate copy of Python.

### 2. Run the setup script

1. Click the **Scripting** tab along the top of Blender.
2. **Text → Open**, and choose:

   ```
   <share>/repo/pipeline/config/flow/current/config/resources/buf_blender_setup.py
   ```

   On the Mac Studio that is
   `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current/config/resources/buf_blender_setup.py`.
   If the share is mounted somewhere else on your machine, navigate to the same
   path under your own mount point.

3. Press **Run Script** (the ▶ button), or `Alt`+`P`.

Output goes to the **system console**, not the Blender window:

- **Windows** — Window → Toggle System Console
- **macOS / Linux** — the Terminal you launched Blender from; if you launched
  it from the Dock, open Window → Toggle System Console if present, otherwise
  just read the summary the script writes at the end, which also appears in the
  Scripting workspace's info line.

The script works out where Blender's Python lives on its own — you never type a
version number or a path. It ends with a block headed `DONE -- but read this
bit.` Read that block; it prints the exact next step **for your machine**.

It is safe to run again. If PySide6 is already there it says so and does
nothing.

### 3. Do what the script's closing block tells you

There are two possibilities, and the script's output says which one applies:

- **The launch hook sets the environment variable for you.** Nothing more to
  do. (This is the case once `patch_blender_pyside_hook.py` has been applied —
  ask your TD, or check whether `_setup_blender_pyside` exists in
  `hooks/tk-multi-launchapp/before_app_launch.py`.)

- **It does not.** Run the one line the script prints for your platform:

  | | |
  |---|---|
  | Windows, PowerShell | `setx PYSIDE2_PYTHONPATH "<the path the script printed>"` |
  | macOS, Terminal | `launchctl setenv PYSIDE2_PYTHONPATH "<the path the script printed>"` |

  On macOS this is **session-scoped** — macOS forgets it on reboot and you have
  to run it again. That is the main reason the hook version is preferable.

  On macOS it must be `launchctl setenv`, not an `export` in `.zshrc`. Desktop
  and Blender are launched by the Dock, not by your shell, so they never see
  anything a shell profile sets.

> The variable really is spelled `PYSIDE2_PYTHONPATH`, with a 2, even though
> what you installed is PySide**6**. The engine is a fork that never renamed
> it. Leave it alone.

### 4. Quit Desktop properly and relaunch

**Tray icon → Quit.** Closing the window is not enough — Desktop keeps running
and keeps the old environment.

Then relaunch Desktop, open your project, and launch Blender **from Desktop**.

---

## Check it worked

In Blender, look at the top bar. There should be a **Flow Production Tracking**
menu next to Help, showing your current context.

Open **File → Open** from that menu — the Workfiles2 browser should appear with
your Tasks in it.

If both work, you are done.

---

## When it doesn't work

### No Flow menu, Blender otherwise completely normal

The usual one. It means the Qt import failed, and it fails silently by design.

1. Did you launch Blender **from Desktop**? Launching it from the Dock or Start
   menu will never show the menu — Toolkit injects itself at launch, so Toolkit
   has to do the launching. This is expected behaviour, not a broken setup.
2. Did you **fully quit** Desktop (tray icon → Quit) after step 3?
3. On macOS, have you rebooted since running `launchctl setenv`? If so, run it
   again — or ask your TD to apply the hook patch so it stops mattering.
4. Open the system console and look for a message about PySide. **If it says
   `Could not import PySide2`, that is still this problem** — the message is
   from old code the fork never updated. You still need PySide6.
5. Re-run `buf_blender_setup.py`. It will tell you what it finds.

### `Cannot execute hook '...scene_operation_tk-blender.py' - this file does not exist on disk!`

Config-side, not your machine. tk-multi-workfiles2 has no native Blender
support and looks inside its own app bundle unless a settings block points it at
the engine's copy. This is fixed in the current config; if you are seeing it,
tell your TD which menu item produced it — there is more than one settings block
that needs the override and one may have been missed.

### The Blender icon is missing from Desktop entirely

Almost always Desktop's cache, not the config. Desktop caches each project's
computed command list and does not reliably pick up config changes on a simple
relaunch.

1. Tray icon → **Quit** (a real quit).
2. Delete the cache:
   - **Windows** — the `p*.basic.*` folders under `%APPDATA%\Shotgun`
   - **macOS** — `~/Library/Caches/Shotgun/<site>/site.basic.desktop/tk-desktop/shotgun_engine_commands_v1.sqlite`
3. Relaunch.

### The icon is there, but clicking it does nothing (Windows)

The launcher registers **Blender 5.2 / 5.1 / 5.0** without checking whether each
is installed, so an icon for a version you do not have simply fails to launch.

Check your real install folder name under `C:\Program Files\Blender Foundation\`.
The config expects `Blender 5.2` (capital B, two-part version). Some Blender
installers have produced `blender 5.2.0` instead. If yours does not match, send
your TD the exact folder name — it is a one-line config fix, not something to
work around locally.

### `pip` fails with `Access is denied` / `WinError 5`

You are installing into Blender's own folder rather than your user folder —
which is what the engine's upstream README says to do, and what does not work
on our managed VMs. Use `buf_blender_setup.py`; it targets a folder inside your
own user profile and needs no admin rights.

### A second Blender version still has no menu after setup

One PySide6 folder normally serves every Blender 5.x on a machine, because
PySide6 ships `abi3` wheels that work across Python 3.9+. If one version
disagrees, open `buf_blender_setup.py`, set `PER_VERSION = True` near the top,
and run it again inside that Blender. It will use a version-suffixed folder.

---

## Notes for the TD

**Why not put PySide6 on the share and skip the per-machine step?** The wheels
are platform- and architecture-specific binaries, so it would need a folder per
platform kept in step with every Blender upgrade, and Qt loading over SMB is
slow and flaky. Deferred, deliberately — but it is the obvious next move if the
artist count grows.

**Why the hook can set `PYSIDE2_PYTHONPATH` at all.** Verified in
tk-multi-launchapp v0.14.2 (`base_launcher.py`): `prepare_launch_for_engine()`
runs and does `os.environ.update(required_env)` **before**
`hook_before_app_launch` fires. tk-blender v2.0.1's `prepare_launch()` only
fills the variable in when it is unset, pointing it at `<engine>/python/ext`,
which does not exist in the checkout on the share. So the variable is always set
by the time the hook runs, and never usefully — which is why
`_setup_blender_pyside()` decides on **content** (does the folder contain a
`PySide6` directory?) rather than on whether the variable is set. An artist's own
`setx`/`launchctl` value still wins; the engine's placeholder does not; a machine
with nothing gets a logged warning instead of a silent stock Blender.

**Applying that patch** — `patch_blender_pyside_hook.py`, dry-run by default:

```
python3 patch_blender_pyside_hook.py            # shows what it would do
python3 patch_blender_pyside_hook.py --apply
```

It backs up to a numbered `.bak`, aborts unless each anchor matches exactly
once, and refuses to write a hook that will not compile. Machines already set up
are unaffected.

**Keep these two in step:** `target_dir()` in `buf_blender_setup.py` and
`_pyside_dir()` in `before_app_launch.py` are the two ends of the same
convention. If one moves, the other has to.

**Still unverified** (carried over from the 2026-09-12 session): the Blender
settings for `tk-multi-snapshot`, `tk-multi-breakdown` and
`tk-multi-setframerange` all reference `{engine}/...` hook paths that exist on
disk in the v2.0.1 checkout, but only Workfiles2's New/Open/Save has been tested
end to end.
