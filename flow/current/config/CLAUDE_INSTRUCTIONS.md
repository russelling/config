# CLAUDE_INSTRUCTIONS.md

# FlowTrackingConfig – AI Session Instructions

> Commit this file to the root of the `russelling/FlowTrackingConfig` repository
> (i.e. `config/flow/current/config/`). **It is not there as of 2026-09-15** —
> the only live copy is the Claude Project's "Project instructions" field, so
> the two have to be kept in step by hand.
>
> At the start of any new session: **"Read CLAUDE_INSTRUCTIONS.md and continue
> work on my FlowTrackingConfig project."**

**Last verified against the live config: 2026-09-15.** Everything in the tables
below was read off the share on that date, not carried over. Where a claim is
unverified it says so.

-----

## Project Identity

|Field           |Value                                                              |
|----------------|-------------------------------------------------------------------|
|Repository      |<https://github.com/russelling/config> (remote is `config`, not `FlowTrackingConfig`)|
|Purpose         |Flow Production Tracking (ShotGrid) Toolkit pipeline configuration |
|Production type |TV / Episodic                                                      |
|Entity hierarchy|**Show → Episode → Scene → Shot** (see below)                      |
|Asset hierarchy |Show → Assets → Asset Type → Asset                                 |
|Pipeline config |`pc_id: 463`, `pc_name: Primary`, `project_id: 1343`, `buffalo_vfx`|
|Platforms       |Linux · macOS · Windows (all three must be supported in every path)|

**The hierarchy has a Scene level.** Earlier versions of this file said
Episode → Shot. The live folder schema is
`shots/{episode}/{scene}/{shot}/`, and `shot_plates_dir()` in the QT bake
builds exactly that path from the flag's `episode` + `sequence|scene`. Sequence
entities still exist in ShotGrid and carry bid/budget fields
(`sg_bid_sequence`, `sg_efc`, `cuts`) but **no longer own any folder** — see
`claude/shot-scene-sequence-schema-audit-2026-08-19.md`.

-----

## Software Stack

|DCC          |Engine                  |Primary contexts                                           |
|-------------|------------------------|-----------------------------------------------------------|
|Maya         |tk-maya                 |Modeling, Rigging, Animation, FX, Lighting (shots + assets)|
|Nuke         |tk-nuke                 |Compositing, DMP (shots only)                              |
|Blender      |tk-blender              |Supplemental modeling / previz (shots + assets)            |
|Unreal Engine|tk-unreal               |Virtual production / real-time rendering                   |
|After Effects|tk-aftereffects         |Mograph, editorial finishing (shots only)                  |
|Premiere Pro |(launch only, no engine)|Editorial (project level only)                             |

> **Gap:** the shot schema has `maya`, `nuke`, `blender`, `afx` step folders but
> **no `unreal`** — assets have one, shots do not. If Unreal is genuinely used
> on shots there is nowhere to save the work.

-----

## Repository Layout — as it actually is

`repo/pipeline/` on the share is the working root. Several siblings are
**separate git repos**, checked out next to each other:

```
repo/pipeline/
├── config/
│   ├── flow/
│   │   ├── current/config/        ← THIS repo (github.com/russelling/config)
│   │   └── alts/
│   │       ├── BUF_Mac_watcher/   ← separate repo: qt_watcher + the QT bake
│   │       ├── ingest_pipeline/   ← separate repo: vendor ingest → turntable
│   │       ├── shotgrid_resolve_bridge/  ← destined for dcc/resolve
│   │       └── BUF_Cloud_Render/  ← superseded by Deadline; mine its G6 guide
│   └── nuke/                      ← separate repo, on NUKE_PATH
│       ├── menu.py
│       └── tools/                 ← cg_element_relight, qt_bake_bridge,
│                                     qt_frame_preview, qt_color_trace
├── buffalo-pipeline/              ← the `buf` package + shows/*.yml
├── deploy/python-selected/        ← Aug migration scripts (JSON archived)
└── budgeting/, converters/, dev/
```

### The Toolkit config itself

```
config/flow/current/config/
├── info.yml
├── .gitignore
├── core/
│   ├── roots.yml, pipeline_configuration.yml, templates.yml
│   ├── hooks/pick_environment.py          ← the STOCK one (see routing below)
│   └── schema/project/
│       ├── shots/episode/scene/shot/
│       │   ├── step/{maya,nuke,blender,afx}/{work,publish}
│       │   ├── render/{work,publish}
│       │   └── plates/, reference/, review/
│       └── assets/asset_type/asset/
│           ├── step/work/{maya,nuke,blender,unreal}
│           ├── render/{work,turntable}, review/turntable
│           └── publish/, source/, work/{ingest,turntable}
├── hooks/
│   ├── pick_environment.py                ← the EPISODIC one (see below)
│   ├── render_complete_callback.py, plate_reads.py, scene_operation_tk-nuke.py
│   └── tk-multi-launchapp/before_app_launch.py   ← puts config/nuke on NUKE_PATH
└── env/
    ├── project.yml, site.yml, shot.yml, asset.yml, sequence.yml,
    │   version.yml, publishedfile.yml, playlist.yml,
    │   episode_shot_step.yml, asset_step.yml, shot_step.yml
    └── includes/
        ├── app_locations.yml, engine_locations.yml, frameworks.yml,
        │   software_paths.yml, templates.yml
        ├── settings/tk-*.yml
        └── unreal/
```

`core/schema/project/sequences/` was **removed 2026-09-14** — orphaned
tk-config-default2 boilerplate for a hierarchy this show does not use.

-----

## Naming Conventions

### Work / render files

```
{Shot}_{vendor_code}_{Step}[_{name}]_v{version}.{ext}
# e.g.  301_017_0020_INH_comp_v001.nk
#       301_017_0020_INH_comp_v001.1001.exr
```

`vendor_code` defaults to `INH` (in-house) when absent — the QT burn-in does
`vendor_code or "INH"`.

### Entity codes — as actually used

- Episodes: `301`, `302` … (**not** `EP101`)
- Scenes: `301_017` (episode + underscore + 3-digit scene)
- Shots: `301_017_0020` (scene + underscore + **4-digit** shot number)
- Assets: PascalCase — `HeroChair`
- Asset types: `Character`, `Prop`, `Environment`, `Vehicle`, `FX`

> The asset tree currently contains **two incompatible naming schemes** — a
> flag was found at `assets/set/set_south_stairwell/wf_stage_03/previz/` whose
> renders were at `assets/Environment/s112_Stairway_Set/render/work/`. Mark is
> reorganising. `ingest_pipeline` derives asset type from the standing
> `incoming/<Type>/` folder, which only stays correct if the type vocabulary on
> disk is single-valued. See `claude/template-and-hook-usage-audit.md`.

### Padding

- Work / publish versions: 3 digits (`v001`)
- Frame numbers: 4 digits (`0001`)

-----

## Pipeline Steps

Shot: `dev` → `model` → `rig` → `anim` → `fx` → `light` → `comp` → `mograph`
→ `editorial` → `deliverable`  (plus `temp`, used by test renders)

Asset: `model` → `rig` → `lookdev` → `fx`  (plus `turntable`)

> Step codes in ShotGrid **must match** the `{Step}` key in
> `core/templates.yml`. Flag a mismatch rather than changing one side.
> **Not verified against ShotGrid's Pipeline Steps list** — do that before
> relying on this list.

-----

## Environment Routing — READ THIS, THE OLD TABLE WAS WRONG

**There are TWO `pick_environment.py` hooks with different behaviour**, and
which one is live has been an open question since 2026-08-19:

| File | Behaviour |
|---|---|
| `core/hooks/pick_environment.py` | Stock 2018 file. Routes **every** Shot+Step to `episode_shot_step`. Never returns `shot_step`. |
| `hooks/pick_environment.py` | Episodic. Checks `Shot.sg_scene`; falls back to `shot_step` when it is empty. |

Toolkit resolves this hook from `core/hooks/`, so the stock file is **probably**
what runs — which would make `hooks/pick_environment.py` and `env/shot_step.yml`
dead code. Both were deliberately left in place pending the test.

**The test:** open a shot with **no** `sg_scene` through Desktop.
Loads `shot_step` → the root hook is live. Loads `episode_shot_step` → the stock
hook is live.

What the stock hook returns:

|Context|Environment|
|---|---|
|`source_entity` Version / PublishedFile / Playlist|`version` / `publishedfile` / `playlist`|
|No project|`site`|
|No entity|`project`|
|Shot / Asset / Sequence, no step|`shot` / `asset` / `sequence`|
|Shot + step|**`episode_shot_step`**|
|Asset + step|`asset_step`|

-----

## App Versions in Use — corrected 2026-09-15

Read from `env/includes/app_locations.yml`, `engine_locations.yml` and
`unreal/tk-unreal-location.yml`. **Almost every line of the previous table was
wrong.**

|App / Engine             |Pinned            |Previously documented|
|-------------------------|------------------|---------------------|
|tk-desktop               |v2.8.5            |v2.4.7 ✗|
|tk-desktop2              |v1.6.2            |—|
|tk-maya                  |v0.13.11          |v0.14.0 ✗|
|tk-nuke                  |v0.16.3           |v0.14.1 ✗|
|tk-blender               |**v2.0.1** (git, `icentric-dev/tk-blender`)|v1.0.0 ✗|
|tk-unreal                |**v1.3.1** (github_release)|v1.4.4 ✗|
|tk-aftereffects          |v0.4.8            |v1.5.0 ✗|
|tk-multi-launchapp       |v0.14.2           |missing|
|tk-multi-workfiles2      |v0.16.2           |v0.13.4 ✗|
|tk-multi-publish2        |v2.10.8           |v2.9.1 ✗|
|tk-multi-snapshot        |v0.10.3           |v0.9.2 ✗|
|tk-multi-breakdown2      |v0.4.5            |listed as tk-maya-breakdown2 v1.2.3 ✗|
|tk-multi-reviewsubmission|v1.3.1            |listed as tk-nuke-reviewsubmission v0.5.0 ✗|
|tk-nuke-writenode        |v1.7.2            |v1.3.8 ✗|
|tk-multi-loader2         |v1.25.6           |—|
|tk-shell                 |v0.10.3           |—|

> **`tk-unreal-location.yml`'s own comment says "Pinned to v1.4.4" while the
> `version:` key says `v1.3.1`.** The comment is wrong; fix one or the other.

> When upgrading, update **both** the relevant `env/*.yml` and this table.
> A table nobody trusts is worse than no table.

-----

## QT Review Rendering (qt_watcher)

|Field|Value|
|---|---|
|Repository|<https://github.com/russelling/BUF_Mac_watcher> — **separate repo**|
|Purpose|Render monitor + QT baking/upload for shot and asset turntable renders|
|Runs as|macOS LaunchAgent `com.buffalovfx.qtwatcher`, on the Mac Studio (`mrmacstudio`)|
|Entry point|`scripts/qt_watcher.py`, via the Flow desktop app's bundled Python 3.11|
|Deploy path|`config/flow/alts/BUF_Mac_watcher/`|

A polling daemon, independent of this Toolkit config, that turns raw EXR
renders into reviewable ShotGrid Versions. It is the single place colour
science, slates and burn-ins are implemented for both shots and assets —
**nothing else in the pipeline should encode a QuickTime or create a review
Version directly**; write a flag file and let qt_watcher do it.

Polls `SHOTS_ROOT` and `ASSETS_ROOT` every 30s for `.render_complete_*.json`
flags via recursive `os.walk()`, bakes with `qt_bake_oiio.py` (OIIO + FFmpeg,
licence-free — the Nuke bake `qt_bake_slate_burnin.py` is kept out of rotation
to avoid needing a Nuke licence on a headless machine), uploads the QT as a
Version, then renames the flag to `.processed_*.json`.

### The colour pipeline, in order

```
source EXR
  ↓  --colorconvert <source space> arri_logc4      ← see below
LogC4
  ↓  --ociofiletransform <shot CDL>                ← shots only, if found
  ↓  --ociofiletransform <LUT>                     ← per-shot → show → fallback
Rec.709
  ↓  de-squeeze (PAR) → framing → clamp 0-1 → 8-bit PNG → ProRes 422 HQ
  ↓  ffmpeg drawtext: 2.39 mask + burn-ins
```

**Source colourspace is resolved per flag, and logged on every bake.** This was
a silent constant (`ACEScg`) until 2026-09-15, when it was found that shot
renders are actually in `aces_interchange` — which resolves to **ACES2065-1
(AP0)**, not ACEScg (AP1). AP0 and AP1 share a white point, so neutrals were
identical and only saturated colour was wrong; that is why it went unnoticed.

|Producer|Source space|
|---|---|
|Shots (Nuke comps)|`aces_interchange`|
|Asset turntables|`ACEScg` — what `generate_turntable.py` writes|
|Any flag carrying `source_colorspace`|that, outright|

> **Unverified:** what `publish_turntable_unreal.py` actually writes. If it is
> not ACEScg its turntables have the bug shots had.

**LUT selection** prefers the shot's own `plates/<shot>_<element>/*.cube`, then
the show LUT chosen by the flag's `camera`, then — if that file is missing — a
generic ACES display transform, which is *not the show look* and logs loudly.
Camera aliases: `arri`/`alexa`/`alexa35`/`logc4`, `red`/`log3g10`/**`nikon`**/
**`zr`**/**`nikon zr`**, `sony`/`venice`/`slog3`. The Nikon ZR records R3D with
RED colour science, so it takes the **RED** LUT.

**Framing** (2026-09-10): every QT is exactly 1920x1080. The image fills the
width; taller-than-16:9 is centre-cropped, wider is letterboxed. **Sides are
never cropped** — except anamorphic shots (PAR ≠ 1.0), which get a **1.22x
blow-up** so the QT shows the extraction: 211px off each side. The slate
thumbnails take the same crop.

> **THE TRAP:** `oiiotool --info` reports the DATA window, not the frame. Read
> the *display* window and `--croptofull` before resizing. See
> `claude/qt-delivery-framing.md`.

### Flag file contract

Minimum for an asset turntable (see `qt_watcher.py` for the authoritative list):

```json
{
  "type": "asset_turntable",
  "entity_name": "HeroChair", "asset_type": "Prop", "step": "turntable",
  "version": 1,
  "project_id": 1343, "entity_id": 456, "entity_type": "Asset", "task_id": 789,
  "frame_first": 1, "frame_last": 120,
  "exr_path_pattern": ".../HeroChair_turntable_v001.%04d.exr",
  "start_timecode": null, "artist": "Ingest Pipeline",
  "date": "2026-07-13", "submitted_for": null,
  "description": "Automated turntable render generated on ingest."
}
```

Shots additionally use `shot_code`, **`episode`**, **`sequence`** (or `scene`),
`vendor_code`, `camera`. `episode` + `sequence` are load-bearing: without them
`shot_plates_dir()` returns `None`, so **no CDL and no per-shot LUT are found**
and the QT bakes ungraded.

Optional: `source_colorspace`, `show_lut_path`, `include_slate`, `skip_color`,
`color_pipe: {log_convert, cdl, show_lut}`, `aspect_mask`, `aspect_mask_ratio`,
`aspect_mask_opacity`, `review_proxy`, `output_paths`.

> An explicit `null` is **not** the same as an absent key — the bake reads with
> `.get(key, DEFAULT)`, so a null overrides the default. This broke the
> `buf.flags` round-trip once; see `claude/anamorphic-blowup-qt-bake.md`.

### Known launchd gotcha: StandardOutPath on a network volume

launchd's daemon-spawn context does not carry session-level SMB auth, so
`open()` for a new log file on the share fails when launchd does it — the job
dies with `EX_CONFIG (78)` and **nothing is written anywhere**, not even a
traceback. Fixed for `ingestturntable` by pointing stdout/stderr at
`~/Library/Logs/buffalovfx/`. **`qt_watcher.plist` still points at the share** —
check with `launchctl print gui/<uid>/com.buffalovfx.qtwatcher | grep -E
"runs|state|last exit"` before assuming a script problem.

`launchctl print` is far more informative than `launchctl list`. After failed
load/unload cycles prefer `bootout` + `bootstrap` over `unload`/`load`.

### Rules for Claude — qt_watcher

1. **Never re-implement QT baking.** Write a flag; let qt_watcher bake it.
2. **The source colourspace is now per-flag.** Any new producer must either
   write the documented default for its type or declare `source_colorspace`.
3. **BUF_Mac_watcher is a separate repo.** Changes to `qt_watcher.py` /
   `qt_bake_oiio.py` happen there. Call out cross-repo impacts explicitly.
4. `entity_type` defaults to `"Shot"` in `upload_version()` — any
   asset-producing flag writer MUST set `"entity_type": "Asset"`.

-----

## Ingest + Turntable Pipeline (ingest_pipeline)

|Field|Value|
|---|---|
|Repository|<https://github.com/russelling/ingest_pipeline> — **separate repo**|
|Runs as|macOS LaunchAgent `com.buffalovfx.ingestturntable`|
|Entry point|`watch_folder.py`|
|Deploy path|`config/flow/alts/ingest_pipeline/`|

Watches `incoming/<AssetType>/` for vendor deliveries: creates/finds the Asset,
converts geometry to USD via headless Blender, publishes it, renders a 360°
turntable, then hands off to qt_watcher via a flag rather than encoding a QT.

### Rules for Claude — ingest_pipeline

1. **Separate repo.** Changes happen there; call out cross-repo impacts.
2. **Asset type comes from the standing `incoming/<Type>/` folder, never
   guessed.** Filename/content inference was deliberately removed.
3. `.max` support needs the `io_scene_max` Blender extension plus
   `config.yml ingest.max_import.addon_module` set for that install.
4. **`core/templates.yml` must not contain literal tabs.** One tab broke
   `sgtk_from_path()` for every consumer of this config. Verify with
   `python3 -c "import yaml; yaml.safe_load(open('core/templates.yml'))"`
   before committing.
5. `shotgrid.project_id` and `ingest.max_import.addon_module` were still
   placeholders at last check — ingest refuses deliveries until set.

-----

## Nuke tools (`config/nuke/`, separate repo, on NUKE_PATH)

Loaded automatically by `hooks/tk-multi-launchapp/before_app_launch.py`.
Add a tool as a module under `tools/` exposing one `build_*()` function, then
one entry in `menu.py`'s `_TOOLS`.

|Tool|What it does|
|---|---|
|`cg_element_relight`|CG element relight graph|
|`qt_bake_bridge`|Imports `qt_bake_oiio.py` and reports what it draws — the single source of truth for any Nuke-side preview|
|`qt_frame_preview`|Runs the **real** bake on one frame and Reads it back. Menu: **QT Frame Preview**|
|`qt_color_trace`|Prints the whole colour decision chain for one shot, plus the exact oiiotool command and the equivalent Nuke graph|

> `qt_slate_burnin.py/` (a *directory* with a `.py` name) is the abandoned
> August hand-port. Not loaded by anything. Retire it.

-----

## The `buf` package (`buffalo-pipeline/`)

Studio tooling extracted from the watcher so it can be deployed to a Deadline
worker as a conda package: `settings`, `color`, `flags`, `bake`, `sg`, `show`.
Show-specific facts live in `shows/<name>.yml` (`BUF_SHOW=lucid_s3`), so
starting the next show is writing one file.

**Design rule: no silent fallbacks.** Every substitution is reported; anything
that would produce wrong output refuses instead.

Nothing in production imports it yet. Pixel parity with the live bake is proven
for shots; asset turntables deferred. See
`claude/deadline-migration-plan-2026-09-13.md`.

-----

## Rules for Claude Across Sessions

1. **Check this file first**, then the relevant `claude/*.md` project doc. Do
   not introduce patterns that contradict them.
2. **Verify before writing.** Read the actual source and the actual files on
   the share; never write code against a remembered API or a stale doc. Most
   errors in this project's history came from acting on a document instead of
   the thing it described — including this file.
3. **Cross-platform paths always** — `linux_path`, `mac_path`, `windows_path`.
4. **Templates before schema.** Define the schema `.yml`, then the template.
5. **Anchor pattern for shared settings** — `env/includes/settings/`, merged
   with `<<:`. No inline duplication across env files.
6. **Version pinning.** Pin every `location:`; update the table above.
7. **Step codes are sacred.** Flag a mismatch, never silently change one side.
8. **Update the WIP list** as items are resolved or found.
9. **No credentials or real paths in commits.** `core/shotgun.yml` leaked a
   live API key to a public repo once; it is gitignored and the key rotated.
10. **One concern per file.**
11. **Separate repos stay separate** — BUF_Mac_watcher, ingest_pipeline,
    config/nuke. Call out cross-repo impacts explicitly rather than assuming.
12. **Changes to live files ship as a patch script**, not hand edits: dry-run by
    default, anchors verified for an exact occurrence count, `compile()` or YAML
    check before writing, numbered `.bak`, idempotent. This has caught a
    half-applied edit already.
13. **Say plainly what Claude has done vs what Mark must run.** Name the machine
    (`mrmacstudio` vs a workstation) and the interpreter — bare `python3` on
    macOS has no PyYAML. Never use placeholder-shaped paths; they get pasted
    literally.
14. **Don't run index-writing git commands** (`git status`, `git add`) against
    the config repo from a sandbox — it leaves a `.git/index.lock` that cannot
    be removed from there. Use `git --no-optional-locks`.

-----

## Work-in-Progress / Known Gaps

### Colour / QT

- [ ] **Re-bake decision** — every shot QT baked before the 2026-09-15
      source-colourspace fix is wrong in saturated colour. Neutrals are fine.
      Decide what needs re-delivering.
- [ ] **Verify `publish_turntable_unreal.py`'s output colourspace.** If it is
      not ACEScg, its turntables have the bug shots had.
- [ ] **Verify `generate_turntable.py` still writes ACEScg.**
- [ ] **Pin a `fontfile` for the burn-ins.** Only the slate title passes one;
      the burn-ins fall back to whatever fontconfig resolves on the watcher
      machine, so review QTs are not reproducible across machines.
- [ ] **Asset-turntable pixel parity** for `buf.bake` — deferred while the
      asset tree is reorganised.
- [ ] **The colour-pipeline templates are unused** — `ep_shot_cdl`,
      `ep_shot_lut`, `ep_shot_show_lut`, `ep_shot_plates(_area)` are defined but
      nothing resolves them; the bake globs the plates folder instead. Two
      statements of where CDLs live, nothing keeping them in agreement.
- [ ] **LogC4 flavour** — the bake uses full `arri_logc4` (curve + AWG4); Nuke
      recreations usually use the curve-only input transform. Settle it with
      `qt_frame_preview`.

### Config

- [ ] **Which `pick_environment.py` is live** — gates `env/shot_step.yml` and
      `hooks/pick_environment.py`. One Desktop launch settles it.
- [ ] **Defer per-DCC folder creation** — `defer_dcc_folders.py` is written and
      tested but **not yet applied**. Adds `defer_creation: <engine>` to 8
      schema files so folders appear on launch instead of for every Step.
- [ ] **Retire 38 DCC templates** for flame/hiero/houdini/mari/max/mobu/
      photoshop — the matching settings files are already gone.
- [ ] **Shots have no `unreal` step folder.**
- [ ] **`tank cache_apps`** has not been run.
- [ ] **Permissions / chmod** — `process_folder_creation.py` still has a
      placeholder.
- [ ] **Season support** — hierarchy is Episode-only above Scene.
- [ ] **Unreal publish workflow** — plugins stubbed.
- [ ] **Premiere Pro launcher** — no engine; may need a custom hook.
- [ ] **Review submission** — wired for Nuke only.
- [ ] **USD pipeline** — templates exist, publish plugin not written.
- [ ] **Stereo / multi-view render templates** — not added.

### Housekeeping

- [ ] `_removed_2026-09-14/` (2.7 GB quarantine) — delete once satisfied.
- [ ] `alts/unreal config/` (655 MB third-party download) — keep or archive?
- [ ] Retire `config/nuke/qt_slate_burnin.py/`.
- [ ] Mine `BUF_Cloud_Render/docs/AWS_G6_Unreal_PathTracer_Render_Guide.md`,
      then archive that repo.

-----

## Project docs

Detail lives in the Claude Project's `claude/*.md`. The ones most likely to
matter: `source-colourspace-and-zr-lut-fix.md`, `qt-delivery-framing.md`,
`anamorphic-blowup-qt-bake.md`, `deadline-migration-plan-2026-09-13.md`,
`pipeline-cleanup-manifest-2026-09-14.md`,
`template-and-hook-usage-audit.md`, `defer-dcc-folder-creation.md`,
`qt-slate-burnin-nuke-tool.md`, `shot-scene-sequence-schema-audit-2026-08-19.md`.

-----

## How to Resume a Session

```
I'm continuing work on my Flow Production Tracking pipeline config.
Read CLAUDE_INSTRUCTIONS.md for full context, then let's continue.
The current item I want to work on is: [DESCRIBE TASK HERE]
```
