# Buffalo VFX — Pipeline Config Session Handoff
**Date:** 2026-06-14  
**Repo:** `github.com/russelling/config` (main branch)  
**Config network path (Mac):** `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/repo/pipeline/config/flow/current`  
**Config network path (Windows):** `C:\Volumes\atv-post-lucid3\atv-buffalo-s03\buffalo_vfx\repo\pipeline\config\flow\current`  
**ShotGrid site:** `buffalovfx.shotgrid.autodesk.com`  
**Project:** Buffalo S3 (ID 1343), Pipeline Configuration ID 463 "Primary"

---

## Current Pipeline State

### ✅ Working
- `./tank validate` passes cleanly (only informational unused-template warnings)
- Nuke launches correctly in `episode_shot_step` environment via `pick_environment.py`
- Workfiles2 File Open/Save/New work correctly in episodic shot context
- File naming: `{Shot}_{Step}_v{version}.nk` e.g. `301_001_020_temp_v001.nk`
- Folder creation works via `./tank Task {id} folders`
- Color pipeline node graph builds automatically on first file creation
- Render-complete callback fires after Write node render, shows dialog, writes flag JSON
- Unreal integration added (tk-unreal v1.3.1) — works on Windows, not Mac (known limitation)

### 🔧 In Progress — QT Review Pipeline
The render-to-review pipeline is partially built. Here is the full architecture:

**1. Render-complete callback** ✅ WORKING  
`config/hooks/render_complete_callback.py`  
- Fires automatically via `nuke.addAfterRender()` after every Write node render
- Shows dialog: Submitted For (SG list field dropdown) + Description (text box)
- Writes flag JSON to: `shots/{Episode}/{Scene}/{Shot}/render/work/{Shot}_{Step}_{output}_v{version}/.render_complete_{Shot}_{Step}_{output}_v{version}.json`
- Flag JSON contains: project/entity IDs, shot/episode/scene/step/version, artist, date, frame range, cut in/out, EXR path, submitted_for, description, task_id

**2. Mac Studio watcher daemon** ⏳ NOT YET DEPLOYED  
`config/scripts/qt_watcher.py` (file exists in repo, not yet deployed/running)  
- Polls `shots/` tree every 30 seconds for `.render_complete_*.json` flag files
- For each flag: resolves output paths, invokes Nuke batch bake, uploads to ShotGrid, renames flag to `.processed_*`
- Needs to run as `launchd` service on the Mac Studio

**3. Nuke batch bake/slate/burn-in** ⏳ NOT YET TESTED  
`config/scripts/qt_bake_slate_burnin.py` (file exists in repo, not yet tested)  
- Run via `nuke -t qt_bake_slate_burnin.py <flag_json> <output_path_1> <output_path_2>`
- Applies full color bake: ACEScg → LogC4 → CDL → Show LUT → Rec.709
- Builds slate frame: show logo, Episode/Scene/Shot, Step, Version, Artist, Date, Frame range, Submitted For, Description
- Burn-ins: upper-left "In House - {artist}", upper-right date, lower-left shot+version, bottom-center frame counter (color-coded: white=edit range, yellow=handles), bottom-right timecode
- Writes ProRes QT to both shot review folder AND `editorial/to_editorial/{YYYY}_{MM}_{DD}/`
- Uploads QT to ShotGrid as a Version

**4. launchd plist** ⏳ NOT YET INSTALLED  
`config/scripts/com.buffalovfx.qtwatcher.plist`  
Install with:
```bash
cp com.buffalovfx.qtwatcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
```

**5. tk-nuke-quickreview color bake** ⏳ NOT YET DONE  
The Quick Review proxy output needs the same full color bake applied so in-app review matches the final look.

---

## Entity & Folder Hierarchy
- Show → Episode (e.g. `301`) → Scene (e.g. `001`) → Shot (e.g. `301_001_020`)
- Folder: `shots/{Episode}/{Scene}/{Shot}/{Step}/nuke/work/`
- Pipeline steps: `online` → `track` → `roto` → `layout` → `temp` → `comp` → `review`
- Step short codes in ShotGrid must match exactly (lowercase): `online`, `track`, `roto`, `layout`, `temp`, `comp`, `review`

## Software Stack
| DCC | Engine | Status |
|-----|--------|--------|
| Nuke 17.0 / 15.1 | tk-nuke v0.16.3 | ✅ Working |
| Maya | tk-maya | ✅ Configured |
| After Effects | tk-aftereffects | ✅ Configured |
| Unreal Engine 5.7 | tk-unreal v1.3.1 | ✅ Windows only |
| Blender | tk-blender | ⏳ Not yet configured |

## Color Pipeline
- Working space: ACEScg
- Camera plates: Read raw + OCIOColorSpace LogC4 → ACEScg
- VFX pulls: Read raw (already ACEScg, no conversion)
- EXR renders: raw ACEScg output (no bake)
- Review QTs: ACEScg → LogC4 → CDL → Show LUT → Rec.709 (external process)
- Show LUT: `ARRILogC4_SEV_S3_V3_digital_R709.cube` at `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/color/luts/`
- Show logo: `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/shots/GLOBALS/logo/teardrop.png`
- OCIO camera input: `Input - ARRI - Curve - LogC4 - EI800`

## Key Templates
| Template | Definition |
|----------|------------|
| `ep_nuke_shot_work` | `shots/{Episode}/{Scene}/{Shot}/{Step}/nuke/work/{Shot}_{Step}_v{version}.{nuke_extension}` |
| `ep_nuke_shot_render_work` | `@shot_base/render/work/{Shot}_{Step}_{output}_v{version}/{Shot}_{Step}_{output}_v{version}.{SEQ}.exr` |
| `ep_nuke_shot_render_flag` | `@shot_base/render/work/{Shot}_{Step}_{output}_v{version}/.render_complete_{Shot}_{Step}_{output}_v{version}.json` |
| `ep_nuke_shot_render_movie` | `@shot_base/review/{Shot}_{Step}_v{version}.mov` |
| `editorial_to_editorial_movie` | `editorial/to_editorial/{YYYY}_{MM}_{DD}/{Shot}_{Step}_{nuke.output}_v{version}.mov` |
| `show_logo` | `shots/GLOBALS/logo/teardrop.png` |

## Key Files Modified This Session
- `config/hooks/scene_operation_tk-nuke.py` — color template builder, save/saveas/new operations
- `config/hooks/render_complete_callback.py` — render-complete flag writer
- `config/hooks/pick_environment.py` — routes all Shot+step to `episode_shot_step`
- `config/core/templates.yml` — added episodic, QT pipeline, and Unreal templates
- `config/core/schema/project/shots/episode/scene/shot/step.yml` — `create_with_parent: false`
- `config/env/episode_shot_step.yml` — primary shot working environment
- `config/env/includes/unreal/` — full Unreal integration (frameworks, templates, settings)
- `config/env/includes/settings/tk-multi-workfiles2.yml` — Shot hierarchy uses `[code]` not `[sg_episode, sg_scene, code]`

## ShotGrid Requirements
- `sg_submitted_for` list field on **Version** entity must be created with valid values (e.g. "Internal Review", "Supervisor", "Editorial", "Client")
- Shot entity should have `sg_cut_in` / `sg_cut_out` fields populated for handle color-coding in burn-ins
- Unreal Software entity: Engine = `tk-unreal`, executable paths set for UE 5.7

## Immediate Next Steps
1. **Deploy watcher** — copy `qt_watcher.py` and `qt_bake_slate_burnin.py` to `config/scripts/` on the Mac Studio, create `logs/` folder, install launchd plist
2. **Test end-to-end QT pipeline** — render a shot, confirm flag is picked up, QT baked, uploaded to ShotGrid
3. **tk-nuke-quickreview color bake** — update Quick Review to apply full color bake
4. **Windows Unreal testing** — launch Unreal from asset task on Windows workstation
5. **Task templates in ShotGrid** — set up task templates for shot and asset workflows
6. **Blender integration** — add tk-blender to the config

## Known Issues / Watch Points
- Stale `.pyc` cache in `config/hooks/__pycache__/` can cause hooks to load old code — delete when hook changes aren't being picked up
- Windows cache path: `C:\Users\{user}\AppData\Roaming\Shotgun\` — clear `p*.basic.*` folders after config changes
- `tank validate` network timeout at end (app store version check) is benign — not a config error
- `qt_watcher.py` uses `nuke.output` as field key in `fields` dict — must be `output` (without `nuke.` prefix) when applying to templates
- `ep_nuke_shot_render_flag` template: the `{output}` key maps to the Write node name lowercased

## How to Resume
Paste this at the start of a new session:
```
I'm continuing work on my Flow Production Tracking pipeline config.
Repository: https://github.com/russelling/config (main branch)
Please read this handoff doc for full context, then let's continue.
The current item I want to work on is: [DESCRIBE TASK HERE]
```
