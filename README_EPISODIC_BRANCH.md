# episodic-pipeline branch

Adds episodic TV production support to the tk-config-default2 base.
All original default config files are preserved for backward compatibility.

## Folder structure

```
{project}/
  shots/
    {Episode}/        e.g. 301/
      {Sequence}/     e.g. 301_019/   (includes episode)
        {Shot}/       e.g. 301_019_010/
          {Step}/     e.g. comp/
            nuke/
            maya/
          plates/
          reference/
          review/
          render/
```

## File naming

```
Work:    301_019_010_comp_v003.nk
Render:  301_019_010_comp_v003.0001.exr
Review:  301_019_010_comp_v003.mov
Asset:   HeroCharacter_model_v001.ma
```

## New files in this branch

| File | Purpose |
|---|---|
| `core/schema/project/shots/episode.yml` | Dynamic Episode folder |
| `core/schema/project/shots/episode/sequence.yml` | Sequence folder filtered by `episode` |
| `core/schema/project/shots/episode/sequence/shot.yml` | Shot folder filtered by `sg_sequence` |
| `core/schema/project/shots/episode/sequence/shot/step.yml` | Pipeline step folder |
| `hooks/pick_environment.py` | Routes Shot+Sequence(episode) context to episode_shot_step |
| `hooks/scene_operation_tk-nuke.py` | Auto-versioning + color pipeline on first open |
| `env/episode_shot_step.yml` | New environment for episodic shot work |
| `env/includes/settings/tk-nuke-episodic.yml` | Nuke engine config for episodic context |
| `core/templates.yml` | ep_ path templates + Episode/Sequence keys |

## Required ShotGrid admin steps

1. Link each **Sequence** to its parent Episode via the standard `episode` field
2. Name Sequence.code as `{Episode}_{seq}` (e.g. `301_019`) so the folder includes the episode
3. Link each **Shot** to its Sequence via the standard `sg_sequence` field; Shot.code = `{Sequence}_{shot}` (e.g. `301_019_010`)
4. Set `OCIO_CAMERA_INPUT` in `hooks/scene_operation_tk-nuke.py` to your camera log space
5. Drop show LUT into `color/luts/` and replace `SHOW_LUT_NAME` in `core/templates.yml`
6. Run `tank Episode 301 create_folders` to create directory structure on disk

## Pipeline steps

**Shot:** dev -> model -> rig -> anim -> fx -> light -> comp -> mograph -> editorial -> deliverable

**Asset:** model -> rig -> lookdev -> fx
