# Buffalo VFX — Pipeline Configuration Reference

> Flow Production Tracking (ShotGrid) Toolkit · TV/Episodic · Last updated June 2026  
> Config repo: `github.com/russelling/config` · branch: `main`

---

## Project identity

| Field | Value |
|---|---|
| ShotGrid site | `buffalovfx.shotgrid.autodesk.com` |
| Config repo | `github.com/russelling/config` |
| Production type | TV / Episodic |
| Platforms | macOS · Windows · Linux |
| Config location (Windows) | `C:\Volumes\atv-post-lucid3\atv-buffalo-s03\buffalo_vfx\buffalo_flow_config` |

---

## Entity hierarchy

```
Show (Project)
  └─ Episode          code: 301
       └─ Sequence    code: 001  · linked to Episode via episode
            └─ Shot   code: 301_001_010  · linked to Sequence via sg_sequence
                 └─ Task  · tied to Pipeline Step e.g. comp, temp
```

The `pick_environment.py` hook checks whether `Shot.sg_sequence` is populated
and that Sequence has its `episode` field set.
If so → `episode_shot_step` environment loads.
If not → falls back to `shot_step`.
Every episodic shot must be linked to a Sequence whose episode is set.

---

## On-disk folder structure

Full example: Episode `301`, Sequence `001`, Shot `301_001_010`.

```
shots/
  301/                         ← Episode code
    001/                       ← Sequence code
      301_001_010/                 ← Shot code
        plates/                  ← source plates, shared across all steps
        render/                  ← all EXR render outputs, shared
        review/                  ← dailies / quicktime movies, shared
        reference/               ← reference material, shared
        temp/                    ← Pipeline Step folder
          nuke/
            301_001_010_temp_v001.nk
            snapshots/
        comp/                    ← Pipeline Step folder
          nuke/
            301_001_010_comp_v001.nk
            snapshots/
```

### Key design decisions

- `plates`, `render`, `review`, `reference` live at the **shot level** — shared across all steps.
- No `work/` or `publish/` subfolder — scripts sit directly inside `nuke/` under the step.
- `shot_root` alias includes `{Step}` and is used for script folders.
- `shot_base` alias stops at `{Shot}` and is used for shared folders.

---

## File naming convention

Pattern: `{Shot}_{Step}_v{version}.{ext}`

The Shot code (`301_001_010`) already contains the full `{Episode}_{Sequence}_{shot_num}` composite, so filenames do not repeat the episode and sequence separately.

| Type | Example |
|---|---|
| Nuke script | `301_001_010_comp_v003.nk` |
| Nuke snapshot | `301_001_010_comp_v003_20260601.nk` |
| Maya scene | `301_001_010_comp_v001.ma` |
| EXR render sequence | `301_001_010_comp_v003/301_001_010_comp_v003.0001.exr` |
| Review quicktime | `301_001_010_comp_v003.mov` |
| Plates sequence | `301_001_010_plate.0001.exr` |
| CDL grade file | `301_001_010.cc` |

**Version padding:** 3 digits — `v001`  
**Frame padding:** 4 digits — `0001`

---

## Pipeline steps

### Shot steps (full order)
`dev` → `model` → `rig` → `anim` → `fx` → `light` → **`temp`** → **`comp`** → `mograph` → `editorial` → `deliverable`

Primary steps for this production: **`temp`** and **`comp`**.

### Asset steps
`model` → `rig` → `lookdev` → `fx`

### Intended workflow
All shots begin as `temp`. Once the cut is locked, selected shots are either finished in-house as `comp` or sent to an outside vendor. The `review/` folder at the shot level holds dailies from both steps, keeping version history unified per shot in ShotGrid.

---

## Software stack

| DCC | Engine | Contexts | Notes |
|---|---|---|---|
| Nuke 17.0 / 15.1 | `tk-nuke` | Shot steps | Primary comp tool. Studio CameraTracker film backs in `nuke/init.py` (Alexa 35, Nikon ZR) via Desktop `before_app_launch` → `NUKE_PATH`. |
| Maya | `tk-maya` | Shots + Assets | Modeling, rigging, animation, FX, lighting |
| Blender | `tk-blender` | Shots + Assets | Supplemental modeling / previz |
| Unreal Engine | `tk-unreal` | Shots | Virtual production. Hardcoded launcher in config. |
| After Effects | `tk-aftereffects` | Shots | Mograph, editorial finishing |
| Premiere Pro | none | Project | Launch only — no Toolkit engine |

---

## Environment routing

| Context | Environment loaded | Key settings file |
|---|---|---|
| No entity | `project.yml` | `tk-desktop.yml` |
| Shot, no step | `project.yml` | — |
| Shot + step, `sg_sequence` + Sequence.episode | `episode_shot_step.yml` | `tk-nuke-episodic.yml` |
| Shot + step, no episode link | `shot_step.yml` | `tk-nuke.yml` |
| Asset + step | `asset_step.yml` | `tk-maya.yml` |

---

## Key template aliases

| Alias | Resolves to | Used for |
|---|---|---|
| `shot_root` | `shots/{Episode}/{Sequence}/{Shot}/{Step}` | Step-level folders: nuke/, maya/ |
| `shot_base` | `shots/{Episode}/{Sequence}/{Shot}` | Shot-level: plates/, render/, review/ |
| `asset_root` | `assets/{sg_asset_type}/{Asset}/{Step}` | All asset templates |

---

## Template keys

| Key | Type | Example | Notes |
|---|---|---|---|
| `Episode` | str | `301` | ShotGrid Episode.code |
| `Sequence` | str | `001` | ShotGrid Sequence.code |
| `Shot` | str | `301_001_010` | ShotGrid Shot.code |
| `Step` | str | `comp` | Pipeline step short code |
| `version` | int (pad 3) | `001` | Work and publish versions |
| `SEQ` | sequence (pad 4) | `0001` | Frame sequences |
| `nuke_extension` | str | `nk` | Default: `nk`. Also: `nknc` |
| `maya_extension` | str | `ma` | Default: `ma`. Also: `mb` |
| `sg_asset_type` | str | `Character` | Asset type folder name |
| `Asset` | str | `HeroCharacter` | PascalCase |

---

## Entity naming conventions

| Entity | Pattern | Examples |
|---|---|---|
| Episodes | Show prefix + 3-digit number | `301` `302` `303` |
| Sequences | 3-digit number | `001` `002` `003` |
| Shots | Episode code + `_` + sequence + `_` + shot number (×10) | `301_001_010` `301_001_020` |
| Assets | PascalCase | `HeroCharacter` `CityBlock_A` |
| Asset types | Title case | `Character` `Prop` `Environment` `Vehicle` `FX` |

---

## Review workflow

Two tools are available in Nuke. Both write to `shots/301/001/301_001_010/review/` and create a ShotGrid Version.

| Tool | What it does |
|---|---|
| **Quick review** | Renders a proxy H.264 .mov from the comp. No Write nodes executed. Fast — use for dailies during temp work. |
| **Submit for review** | More control over slates and burnins. Also proxy only. Use for formal submissions. |

Write nodes (full EXR renders to `render/`) must be executed **manually** by the artist. Full renders are expected only when a shot graduates to `comp` or is being delivered.

---

## Colour pipeline

| File | Location | Template |
|---|---|---|
| Plate sequence | `shots/301/001/301_001_010/plates/301_001_010_plate.0001.exr` | `ep_shot_plates` |
| CDL grade (.cc) | `shots/301/001/301_001_010/plates/301_001_010.cc` | `ep_shot_cdl` |
| Show LUT | `color/luts/ARRILogC4_SEV_S3_V3_digital_R709.cube` | `ep_shot_show_lut` |

---

## App versions

| App / Engine | Version |
|---|---|
| `tk-desktop` | v2.8.5 |
| `tk-maya` | v0.14.0 |
| `tk-nuke` | v0.14.1 |
| `tk-blender` | v1.0.0 |
| `tk-unreal` | v1.4.4 |
| `tk-aftereffects` | v1.5.0 |
| `tk-multi-workfiles2` | v0.13.4 |
| `tk-multi-publish2` | v2.9.1 |
| `tk-multi-snapshot` | v0.9.2 |
| `tk-nuke-writenode` | v1.3.8 |
| `tk-nuke-reviewsubmission` | v0.5.0 |

---

## Critical: manual patches to installed apps

### tk-unreal engine.py — UE 5.7 Qt compatibility patch

`install/github/ue4plugins/tk-unreal/v1.3.1/engine.py` has been manually patched to fix an `AttributeError: 'NoneType' object has no attribute 'QApplication'` crash on UE 5.7. The patched source of record is committed to `hooks/tk-unreal/engine.py` in the config repo, but Toolkit does **not** automatically apply it — it loads from the `install/` folder at runtime.

**If `tank cache_apps` is run and re-downloads tk-unreal, this patch will be overwritten and must be reapplied.**

To reapply:
```bash
cp "/path/to/config/hooks/tk-unreal/engine.py" \
   "/path/to/config/install/github/ue4plugins/tk-unreal/v1.3.1/engine.py"
```

The patch wraps `init_qt_app()` in a try/except and guards against `QtGui` being `None` when UE 5.7 manages Qt internally.

### tk-framework-unrealqt framework.py — UE 5.7 Qt compatibility patch

`install/github/ue4plugins/tk-framework-unrealqt/v1.3.1/framework.py` has been manually patched to fix `NoneType` errors in `tk-framework-shotgunutils` and `tk-multi-shotgunpanel` on UE 5.7. The patched source of record is committed to `hooks/tk-framework-unrealqt/framework.py`.

The patch changes the "Qt already available" early-return check to verify that `QtGui` is **fully** available (not just partially). In UE 5.5+, Unreal exposes its internal Qt but `QtGui` comes back as `None`, causing all apps that depend on `shotgunutils` to fail. The patch forces the bundled PySide6 vendor to activate instead.

**If `tank cache_apps` re-downloads `tk-framework-unrealqt`, this patch will be overwritten and must be reapplied.**

To reapply:
```bash
cp "/path/to/config/hooks/tk-framework-unrealqt/framework.py" \
   "/path/to/config/install/github/ue4plugins/tk-framework-unrealqt/v1.3.1/framework.py"
```

### tk-framework-unrealqt vendor zip — must be manually installed

`tank cache_apps` does **not** download the platform-specific PySide6 vendor zips — only the base framework code. The vendor zip must be downloaded and unpacked manually into the install folder.

**If the install folder is wiped and re-cached, run this on the Mac:**
```bash
cd "/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/buffalo_flow_config/install/github/ue4plugins/tk-framework-unrealqt/v1.3.1/"
curl -L -o unrealqt-py311-win.zip \
  "https://github.com/ue4plugins/tk-framework-unrealqt/releases/download/v1.3.1/v1.3.1-py3.11-win.zip"
unzip unrealqt-py311-win.zip
# When prompted to replace existing files, press N
```

Required vendor path after install: `python/vendors/py3.11/windows/`

UE 5.7 uses Python 3.11.8. The py3.11 Windows vendor is the correct one.

---

## Outstanding items (WIP)

- [x] `tank cache_apps` — completed on Windows workstation
- [ ] Unreal publish workflow — `tk-unreal` publish plugins are stubbed; full `.umap` export pipeline not yet defined
- [ ] Premiere Pro launcher — launch-only; no `tk-premiere` engine exists
- [ ] Maya and Blender review submission hooks not yet configured
- [ ] USD pipeline — templates exist; USD export publish plugin not yet written
- [ ] Permissions — `process_folder_creation.py` hook has placeholder `os.chmod`; set actual permissions
- [ ] Season support — hierarchy is Episode-only; Season entity not yet accounted for

---

## Rules for Claude across sessions

1. Read this file before writing any YAML or Python.
2. Cross-platform paths always — every path needs `linux_path`, `mac_path`, `windows_path`.
3. Templates before schema — define schema .yml first, then add template keys.
4. `shot_base` for shared shot-level folders (plates, render, review, reference). `shot_root` for step-level folders (nuke/, maya/).
5. No `work/` or `publish/` subfolders under steps — scripts sit directly in `nuke/`, `maya/` etc.
6. Step codes must exactly match ShotGrid Pipeline Steps. Flag mismatches, never silently change one side.
7. Version pinning — all `location:` blocks must pin to a specific version.
8. Update the WIP list when items are resolved or new gaps found.
9. No credentials or real paths in commits — use `config/paths.local.yml` (gitignored).
10. Prefer includes over duplication — shared blocks go in `env/includes/settings/`.




---

## 2026-06-16 session — major progress notes

### ✅ What got working today

- **Unreal Engine 5.7 integration** — ShotGrid menu, Loader, Publish, Workspace Info all functional on Windows. Required:
  - `launch_builtin_plugins: []` in `tk-unreal.yml`
  - `command_name: unreal_engine` in `tk-multi-launchapp.yml`
  - `tank cache_apps` to download `tk-framework-unrealqt`
  - Patched `engine.py` (`init_qt_app` Qt None-guard) — committed as `hooks/tk-unreal/engine.py`
  - Patched `framework.py` (forces bundled PySide6 for partial-Qt UE 5.5+) — committed as `hooks/tk-framework-unrealqt/framework.py`
  - Manually unpacked `v1.3.1-py3.11-win.zip` vendor zip into install folder
- **Software entity for D: drive Unreal install** — created separately in ShotGrid Admin, with custom icon (uploaded). User decided to move D: install to C: instead so this may not be needed long-term.
- **Mac Studio QT watcher daemon** — now running as a launchd service on `Buffalo-VFX-Perforce` (Buffalo's Mac Studio), using `/usr/bin/python3` + `PYTHONPATH` to import sgtk from `install/core/python`. Plist at `~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist`. Logs at `/Volumes/.../buffalo_flow_config/logs/`. Scripts in separate repo `BUF_Mac_watcher`.
- **OIIO+FFmpeg bake routing** — `qt_watcher.py` now routes both shot and asset turntables through `qt_bake_oiio.py`, avoiding Nuke render license dependency.
- **ShotGrid Script user for daemon authentication** — added to `core/shotgun.yml`. Verify it's gitignored before pushing.
- **Render-complete flag JSON now uses Mac-style paths** — `render_complete_callback.py` now resolves `exr_path_pattern` via the `ep_nuke_shot_render_work` template with explicit `platform="mac"`, so flags are portable across Windows-rendered / Mac-watched setups.
- **Turntable render templates** — `unreal_asset_turntable_render`, `unreal_asset_turntable_flag`, `unreal_asset_turntable_movie`, `unreal_maya_asset_fbx_publish`, `unreal_maya_turntable_*`, `unreal_movie_publish`, `unreal_asset_publish` all added to `core/templates.yml`.

### ⏳ Outstanding — pick up here tomorrow

**The render-complete dialog (`Submitted For` / `Description`) still does not appear when rendering from Nuke.** The root cause is partially understood but not fully diagnosed:

- `tk_after_render` knob mechanism was wired into `scene_operation_tk-nuke.py` via `nuke.addOnUserCreate(_wire_tk_after_render, nodeClass="WriteTank")` — needs WriteTank nodes to fire
- Color pipeline builder was upgraded to call `wn_app.create_new_write_node("Primary EXR (32-bit)")` instead of `nuke.createNode("Write", ...)` — but in the `prepare_new` workfiles2 path it silently falls back to plain Write
- `prepare_new` branch added to `scene_operation_tk-nuke.py` (correct op string — `prepare_new`, NOT `prepare_new_scene`) — does fire and does build the color graph
- Builder runs `nuke.scriptSaveAs(...)` before calling `_build_color_template(...)` so `create_new_write_node` has a context — but it still fails into the `except` branch
- Critically: neither `nuke.tprint` nor `nuke.warning` from the fallback `except` block produces any visible output in Script Editor or `tk-nuke.log`
- Running `wn_app.create_new_write_node("Primary EXR (32-bit)")` **directly in the Script Editor on a saved script succeeds** — produces a `FlowProductionTrackingWrite1` node
- So the failure is specific to the timing/context of `prepare_new` execution. Next diagnostic step: **write any exception inside the `except` block to a file in `/Volumes/.../logs/scene_op_debug.log` instead of using Nuke's logging APIs**, since the standard channels are silent for unknown reasons.

### Process risks discovered today

- **`config` repo is not git-cloned on the Windows render machine.** All fixes pushed to GitHub must be pulled on the MacBook (via GitHub Desktop) and propagate to Windows via the network share. Missing this step was the root cause of multiple "fix didn't take effect" symptoms during the session.
- **Windows Toolkit cache** at `C:\Users\{user}\AppData\Roaming\Shotgun\buffalovfx\p*.*` must be cleared after almost any engine settings or hook change, otherwise stale config persists.
- The `BUF_Mac_watcher` repo is intentionally separate from `config` — do **not** copy its scripts into `config/scripts/` (would commit to the wrong repo). Plist points directly at the `BUF_Mac_watcher` location on the network share.
