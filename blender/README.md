# Buffalo VFX Blender Tools

Custom studio Blender add-ons, auto-discovered by every Toolkit-launched
Blender session (via ShotGrid Desktop) — no manual "Install from File"
step once this is set up on a machine.

## Why this loads automatically

Blender scans a "system scripts" directory (in addition to its own
built-in paths and the "user scripts" directory) for `addons/`, `startup/`,
and `modules/` subfolders. `flow/current/config/hooks/tk-multi-launchapp/before_app_launch.py`
sets the `BLENDER_SYSTEM_SCRIPTS` environment variable to point at this
folder before every Toolkit-launched Blender session, so anything in
`addons/` here shows up in Blender's Preferences → Add-ons list, sourced
live from this path — not a separately installed copy.

This is a **different** mechanism from `BLENDER_USER_SCRIPTS`, which the
`tk-blender` engine itself already sets (pointing at its own bundled
resources) — the two don't collide.

## One-time step per machine

Being *discoverable* isn't the same as being *enabled* — Blender still
needs each add-on checked on in Preferences → Add-ons once per machine
(Blender remembers that across restarts after that). So the first time
this is set up on a given workstation:

1. Launch Blender through ShotGrid Desktop (so `BLENDER_SYSTEM_SCRIPTS` is
   set).
2. Edit → Preferences → Add-ons, search "Flow", enable
   **Flow - Set Element Render Output**.

From then on, every future fix pushed to `addons/set_element_render_output.py`
just needs the add-on disabled/re-enabled (or Blender restarted) to pick
up — no more re-running "Install from File" per fix, which is what earlier
sessions had to do when this add-on lived only inside the `tk-blender`
engine's own bundled install path.

## Migrating off the old location

Before this repo existed, the add-on lived at
`flow/current/install/git/tk-blender.git/v2.0.1/resources/scripts/addons/set_element_render_output.py`
(and a reference copy at `flow/current/config/blender/`). Both still exist
as of this writing — once the add-on here is confirmed working from
`BLENDER_SYSTEM_SCRIPTS`, remove the old manually-installed copy from
Blender's Add-ons list (or just leave it disabled) so there's only one
live copy to maintain going forward.

## Repo status

Not part of `FlowTrackingConfig`'s own git history — same pattern as
`nuke/` (see that folder's README), `BUF_Mac_watcher`, and
`ingest_pipeline`. Turn this into its own git repo whenever convenient;
nothing about how it loads depends on git.

## Structure

```
blender/
├── addons/
│   └── set_element_render_output.py
└── README.md
```

Add a new tool by dropping another single-file add-on (with its own
`bl_info` + `register()`/`unregister()`) into `addons/`. Multi-file
add-ons need their own subfolder with an `__init__.py` instead — ask if
one of those comes up, the wiring is slightly different.

## Current tools

- **Flow - Set Element Render Output** — resolves the pipeline's
  `ep_blender_shot_render_work` template and points the scene's render
  output at it for CG-element pre-renders, with an option to enable a
  standard compositing-pass set (Normal/Depth, Cryptomatte Object/Material,
  Diffuse/Glossy/Emission) and write a multi-layer EXR. See the project
  doc `blender-cg-render-and-step-tracking-2026-09-12.md` for full design
  and bug history.
