# episodic-pipeline branch

Adds episodic TV production support to the tk-config-default2 base.
All original default config files are preserved for backward compatibility.

## Folder structure

```
{project}/
  {Episode}/        e.g. 301/
    {Scene}/         e.g. 010/
      {Shot}/        e.g. 010/
        {Step}/      e.g. comp/
          work/
          publish/
          render/work/
          render/pub/
        plates/
        reference/
        review/
```

## File naming

```
Work:    301_010_010_comp_v003.nk
Render:  301_010_010_comp_v003.0001.exr
Review:  301_010_010_comp_v003.mov
Asset:   HeroCharacter_model_v001.ma
```

## New files in this branch

| File | Purpose |
|---|---|
| `core/schema/project/episode.yml` | Dynamic Episode folder |
| `core/schema/project/episode/scene.yml` | Scene folder filtered by sg_episode |
| `core/schema/project/episode/scene/shot.yml` | Shot folder filtered by sg_scene |
| `core/schema/project/episode/scene/shot/step.yml` | Pipeline step folder |
| `hooks/pick_environment.py` | Routes Shot+Scene context to episode_shot_step |
| `hooks/scene_operation_tk-nuke.py` | Auto-versioning + color pipeline on first open |
| `env/episode_shot_step.yml` | New environment for episodic shot work |
| `env/includes/settings/tk-nuke-episodic.yml` | Nuke engine config for episodic context |
| `core/templates.yml` | ep_ path templates + Episode/Scene keys |

## Required ShotGrid admin steps

1. Add `sg_episode` entity link field on **Scene** (links Scene -> Episode)
2. Add `sg_scene` entity link field on **Shot** (links Shot -> Scene)
3. Set `OCIO_CAMERA_INPUT` in `hooks/scene_operation_tk-nuke.py` to your camera log space
4. Drop show LUT into `color/luts/` and replace `SHOW_LUT_NAME` in `core/templates.yml`
5. Run `tank Episode 301 create_folders` to create directory structure on disk

## Pipeline steps

**Shot:** dev -> model -> rig -> anim -> fx -> light -> comp -> mograph -> editorial -> deliverable

**Asset:** model -> rig -> lookdev -> fx
