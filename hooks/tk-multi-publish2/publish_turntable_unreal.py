# Buffalo VFX - Unreal turntable publish hook
# Fires after a static mesh / blueprint asset publish in Unreal.
#
# Pipeline:
#   1. Spawn asset in a transient turntable level (grey ground, 3-point lights,
#      orbital camera)
#   2. Add a 120-frame level sequence that rotates the camera 360 degrees
#   3. Render 120 EXR frames via Movie Render Queue
#   4. Write a .render_complete flag JSON (same format as the Nuke callback)
#   5. qt_watcher.py on the Mac Studio picks it up and runs qt_bake_slate_burnin.py
#
# Template used for EXR output:
#   unreal_asset_turntable_render:
#     definition: '@asset_root/render/work/{Asset}_turntable_v{version}/{Asset}_turntable_v{version}.{SEQ}.exr'
#
# Flag JSON written to:
#   unreal_asset_turntable_flag:
#     definition: '@asset_root/render/work/{Asset}_turntable_v{version}/.render_complete_{Asset}_turntable_v{version}.json'

import datetime
import json
import math
import os

import sgtk

HookBaseClass = sgtk.get_hook_baseclass()


class UnrealTurntablePublish(HookBaseClass):
    """
    Publish plugin that generates a 360-degree turntable render for a static
    mesh or blueprint asset after it has been published to ShotGrid.
    """

    # -------------------------------------------------------------------------
    # Plugin identity
    # -------------------------------------------------------------------------

    @property
    def name(self):
        return "Render Turntable and Submit for Review"

    @property
    def description(self):
        return (
            "Generates a 360-degree turntable EXR render of the published asset "
            "inside Unreal, then writes a flag file for qt_watcher to bake and "
            "upload to ShotGrid as a Version."
        )

    @property
    def settings(self):
        base = super(UnrealTurntablePublish, self).settings or {}
        base["Render Template"] = {
            "type": "template",
            "default": "unreal_asset_turntable_render",
            "description": "Template for EXR frame output path.",
        }
        base["Flag Template"] = {
            "type": "template",
            "default": "unreal_asset_turntable_flag",
            "description": "Template for render-complete flag JSON path.",
        }
        base["Frame Count"] = {
            "type": "int",
            "default": 120,
            "description": "Number of frames for one full 360-degree rotation.",
        }
        base["Orbit Radius"] = {
            "type": "int",
            "default": 720,
            "description": "Camera orbit radius in Unreal units (cm).",
        }
        base["Orbit Height"] = {
            "type": "int",
            "default": 180,
            "description": "Camera height above origin in Unreal units (cm).",
        }
        return base

    @property
    def item_filters(self):
        # Only fire on Unreal asset items that have been published
        return ["unreal.asset.staticmesh", "unreal.asset.blueprint"]

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def accept(self, settings, item):
        publisher = self.parent
        engine = publisher.engine

        # Must be running inside Unreal
        if engine.name != "tk-unreal":
            return {"accepted": False}

        # Must have a valid asset context
        if not item.context.entity:
            self.logger.warning("No entity in context — skipping turntable.")
            return {"accepted": False, "enabled": False}

        return {"accepted": True, "enabled": True, "checked": True}

    def validate(self, settings, item):
        publisher = self.parent
        render_template = publisher.get_template_by_name(
            settings["Render Template"].value
        )
        flag_template = publisher.get_template_by_name(
            settings["Flag Template"].value
        )

        if not render_template:
            self.logger.error(
                "Could not find render template: %s"
                % settings["Render Template"].value
            )
            return False

        if not flag_template:
            self.logger.error(
                "Could not find flag template: %s"
                % settings["Flag Template"].value
            )
            return False

        # Stash templates on item for publish step
        item.properties["turntable_render_template"] = render_template
        item.properties["turntable_flag_template"] = flag_template
        return True

    # -------------------------------------------------------------------------
    # Publish
    # -------------------------------------------------------------------------

    def publish(self, settings, item):
        import unreal

        publisher = self.parent
        context = item.context
        engine = publisher.engine

        asset_name = context.entity["name"]
        asset_type = context.entity.get("sg_asset_type", "Asset")
        step = context.step["name"] if context.step else "model"
        version_number = self._get_next_version(item)

        render_template = item.properties["turntable_render_template"]
        flag_template = item.properties["turntable_flag_template"]

        template_fields = {
            "Asset": asset_name,
            "sg_asset_type": asset_type,
            "version": version_number,
            "SEQ": 1,
        }

        render_dir = os.path.dirname(
            render_template.apply_fields(
                dict(template_fields, SEQ=1)
            )
        )
        render_path = render_template.apply_fields(template_fields)
        flag_path = flag_template.apply_fields(
            {k: v for k, v in template_fields.items() if k != "SEQ"}
        )

        if not os.path.exists(render_dir):
            os.makedirs(render_dir)

        self.logger.info("Building turntable scene for: %s" % asset_name)

        # Resolve the published asset path from item properties
        asset_content_path = item.properties.get(
            "sg_publish_data", {}
        ).get("path", {}).get("local_path", "")

        # Build the turntable scene and render
        success = self._render_turntable(
            unreal=unreal,
            asset_content_path=asset_content_path,
            render_output_dir=render_dir,
            render_filename_prefix="%s_turntable_v%03d" % (asset_name, version_number),
            frame_count=settings["Frame Count"].value,
            orbit_radius=settings["Orbit Radius"].value,
            orbit_height=settings["Orbit Height"].value,
        )

        if not success:
            self.logger.error("Turntable render failed.")
            return False

        # Write flag JSON for qt_watcher
        self._write_flag(
            flag_path=flag_path,
            render_dir=render_dir,
            asset_name=asset_name,
            asset_type=asset_type,
            step=step,
            version_number=version_number,
            frame_count=settings["Frame Count"].value,
            context=context,
            item=item,
        )

        self.logger.info(
            "Turntable render complete. Flag written to: %s" % flag_path
        )
        return True

    # -------------------------------------------------------------------------
    # Turntable scene builder
    # -------------------------------------------------------------------------

    def _render_turntable(
        self,
        unreal,
        asset_content_path,
        render_output_dir,
        render_filename_prefix,
        frame_count,
        orbit_radius,
        orbit_height,
    ):
        """
        Creates a transient world, populates it with a ground plane, 3-point
        lighting rig, spawns the asset at origin, adds a 120-frame level
        sequence with orbital camera animation, and renders via Movie Render
        Queue.

        Returns True on success, False on failure.
        """
        import unreal

        # ── Create transient world ────────────────────────────────────────────
        world = unreal.EditorLevelLibrary.get_editor_world()
        transient_level_name = "/Temp/TurntableLevel_%s" % render_filename_prefix

        try:
            # ── Ground plane ─────────────────────────────────────────────────
            plane_mesh = unreal.load_asset(
                "/Engine/BasicShapes/Plane.Plane"
            )
            plane_actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
                plane_mesh,
                unreal.Vector(0, 0, -5),
                unreal.Rotator(0, 0, 0),
            )
            plane_actor.set_actor_scale3d(unreal.Vector(20, 20, 1))

            # Apply a neutral grey material to the ground plane
            grey_material = unreal.load_asset(
                "/Engine/BasicMaterials/BasicMaterial.BasicMaterial"
            )
            if grey_material:
                mesh_comp = plane_actor.get_component_by_class(
                    unreal.StaticMeshComponent
                )
                if mesh_comp:
                    mesh_comp.set_material(0, grey_material)

            # ── Spawn asset ───────────────────────────────────────────────────
            if asset_content_path and unreal.EditorAssetLibrary.does_asset_exist(
                asset_content_path
            ):
                asset_obj = unreal.load_asset(asset_content_path)
                asset_actor = unreal.EditorLevelLibrary.spawn_actor_from_object(
                    asset_obj,
                    unreal.Vector(0, 0, 0),
                    unreal.Rotator(0, 0, 0),
                )
            else:
                self.logger.warning(
                    "Could not find asset at content path: %s — "
                    "rendering empty turntable." % asset_content_path
                )
                asset_actor = None

            # ── 3-point lighting rig ──────────────────────────────────────────
            # Key light (warm, front-left)
            key_light = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.DirectionalLight,
                unreal.Vector(0, 0, 500),
                unreal.Rotator(-45, -45, 0),
            )
            key_light_comp = key_light.get_component_by_class(
                unreal.DirectionalLightComponent
            )
            if key_light_comp:
                key_light_comp.set_intensity(8.0)
                key_light_comp.set_light_color(
                    unreal.LinearColor(1.0, 0.97, 0.9, 1.0)
                )

            # Fill light (cool, front-right, half intensity)
            fill_light = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.DirectionalLight,
                unreal.Vector(0, 0, 500),
                unreal.Rotator(-30, 135, 0),
            )
            fill_light_comp = fill_light.get_component_by_class(
                unreal.DirectionalLightComponent
            )
            if fill_light_comp:
                fill_light_comp.set_intensity(3.0)
                fill_light_comp.set_light_color(
                    unreal.LinearColor(0.8, 0.88, 1.0, 1.0)
                )

            # Rim light (bright, from behind)
            rim_light = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.DirectionalLight,
                unreal.Vector(0, 0, 500),
                unreal.Rotator(-60, -135, 0),
            )
            rim_light_comp = rim_light.get_component_by_class(
                unreal.DirectionalLightComponent
            )
            if rim_light_comp:
                rim_light_comp.set_intensity(5.0)
                rim_light_comp.set_light_color(
                    unreal.LinearColor(1.0, 1.0, 1.0, 1.0)
                )

            # Disable sky light / atmospheric effects for clean neutral bg
            sky_actors = unreal.GameplayStatics.get_all_actors_of_class(
                world, unreal.SkyLight
            )
            for sky in sky_actors:
                sky.set_actor_hidden_in_game(True)
                sky.modify()

            # ── Level sequence with orbital camera ───────────────────────────
            seq_path = "/Temp/%s_sequence" % render_filename_prefix
            sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
                "%s_sequence" % render_filename_prefix,
                "/Temp",
                unreal.LevelSequence,
                unreal.LevelSequenceFactoryNew(),
            )

            sequence.set_display_rate(unreal.FrameRate(24, 1))
            sequence.set_playback_start(0)
            sequence.set_playback_end(frame_count)

            # Add camera
            camera_actor = unreal.EditorLevelLibrary.spawn_actor_from_class(
                unreal.CameraActor,
                unreal.Vector(orbit_radius, 0, orbit_height),
                unreal.Rotator(0, 180, 0),
            )

            # Point camera at origin
            look_at = unreal.MathLibrary.find_look_at_rotation(
                unreal.Vector(orbit_radius, 0, orbit_height),
                unreal.Vector(0, 0, 0),
            )
            camera_actor.set_actor_rotation(look_at, False)

            # Add camera to sequence and animate orbit
            camera_binding = sequence.add_possessable(camera_actor)
            transform_track = camera_binding.add_track(
                unreal.MovieScene3DTransformTrack
            )
            transform_section = transform_track.add_section()
            transform_section.set_range(
                unreal.SequencerScriptingRange(
                    has_start_value=True,
                    has_end_value=True,
                    inclusive_start=unreal.FrameNumber(0),
                    exclusive_end=unreal.FrameNumber(frame_count),
                )
            )

            # Set keyframes for camera position at each frame around orbit
            channels = transform_section.get_all_channels()
            # channels order: tx, ty, tz, rx, ry, rz
            tx_channel = channels[0]
            ty_channel = channels[1]
            tz_channel = channels[2]
            rx_channel = channels[3]
            ry_channel = channels[4]

            for frame_idx in range(frame_count + 1):
                angle_rad = (2 * math.pi * frame_idx) / frame_count
                cx = orbit_radius * math.cos(angle_rad)
                cy = orbit_radius * math.sin(angle_rad)
                cz = orbit_height

                look = unreal.MathLibrary.find_look_at_rotation(
                    unreal.Vector(cx, cy, cz),
                    unreal.Vector(0, 0, 0),
                )

                frame_num = unreal.FrameNumber(frame_idx)
                tx_channel.add_key(frame_num, cx)
                ty_channel.add_key(frame_num, cy)
                tz_channel.set_default(cz)
                rx_channel.add_key(frame_num, look.pitch)
                ry_channel.add_key(frame_num, look.yaw)

            # ── Movie Render Queue ────────────────────────────────────────────
            mrq_subsystem = unreal.get_editor_subsystem(
                unreal.MoviePipelineQueueSubsystem
            )
            queue = mrq_subsystem.get_queue()
            job = queue.allocate_new_job(unreal.MoviePipelineExecutorJob)

            job.set_sequence(unreal.SoftObjectPath(sequence.get_path_name()))
            job.set_map(unreal.SoftObjectPath(world.get_path_name()))
            job.job_name = render_filename_prefix

            # Configure output settings
            config = job.get_configuration()

            output_setting = config.find_or_add_setting_by_class(
                unreal.MoviePipelineOutputSetting
            )
            output_setting.output_directory = unreal.DirectoryPath(
                path=render_output_dir
            )
            output_setting.file_name_format = render_filename_prefix + ".{frame_number}"
            output_setting.output_frame_rate = unreal.FrameRate(24, 1)
            output_setting.override_existing_output = True
            output_setting.zero_pad_frame_numbers = 4

            # EXR output — DWAB lossy compression for compact review renders.
            # Visually transparent for turntable QT purposes; 5-10x smaller than PIZ.
            # Switch to PIZ or ZIP if lossless is ever required.
            exr_setting = config.find_or_add_setting_by_class(
                unreal.MoviePipelineImageSequenceOutput_EXR
            )
            exr_setting.compression = unreal.EXRCompressionFormat.DWAB

            # High quality anti-aliasing
            aa_setting = config.find_or_add_setting_by_class(
                unreal.MoviePipelineAntiAliasingSetting
            )
            aa_setting.spatial_sample_count = 4
            aa_setting.temporal_sample_count = 4

            # Execute render synchronously
            executor = unreal.MoviePipelinePIEExecutor()
            mrq_subsystem.render_queue_with_executor_instance(executor)

            self.logger.info(
                "Movie Render Queue job submitted: %s" % render_filename_prefix
            )
            return True

        except Exception as e:
            self.logger.error("Turntable scene build failed: %s" % str(e))
            import traceback
            self.logger.error(traceback.format_exc())
            return False

    # -------------------------------------------------------------------------
    # Flag JSON writer
    # -------------------------------------------------------------------------

    def _write_flag(
        self,
        flag_path,
        render_dir,
        asset_name,
        asset_type,
        step,
        version_number,
        frame_count,
        context,
        item,
    ):
        """
        Writes a .render_complete flag JSON in the same format as the Nuke
        render_complete_callback.py so qt_watcher.py can pick it up.
        """
        publisher = self.parent
        sg = publisher.shotgun

        project_id = context.project["id"] if context.project else None
        entity_id = context.entity["id"] if context.entity else None
        task_id = context.task["id"] if context.task else None

        # Get current ShotGrid user
        current_user = sgtk.util.get_current_user(publisher.sgtk)
        artist = current_user.get("name", "unknown") if current_user else "unknown"

        # EXR sequence path with frame token
        exr_path = os.path.join(
            render_dir,
            "%s_turntable_v%03d.####.exr" % (asset_name, version_number),
        )

        flag_data = {
            "type": "asset_turntable",
            "project_id": project_id,
            "entity_type": "Asset",
            "entity_id": entity_id,
            "entity_name": asset_name,
            "asset_type": asset_type,
            "step": step,
            "version": version_number,
            "artist": artist,
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "frame_range": [1, frame_count],
            "exr_path": exr_path,
            "render_dir": render_dir,
            "submitted_for": "Internal Review",
            "description": "Auto-generated turntable — %s v%03d"
            % (asset_name, version_number),
            "task_id": task_id,
            "source": "tk-unreal-turntable",
        }

        flag_dir = os.path.dirname(flag_path)
        if not os.path.exists(flag_dir):
            os.makedirs(flag_dir)

        with open(flag_path, "w") as f:
            json.dump(flag_data, f, indent=2)

        self.logger.info("Flag JSON written to: %s" % flag_path)

    # -------------------------------------------------------------------------
    # Version number helper
    # -------------------------------------------------------------------------

    def _get_next_version(self, item):
        """
        Returns the next available version number for turntable renders of
        this asset by scanning the render directory for existing versions.
        """
        publisher = self.parent
        context = item.context
        asset_name = context.entity["name"]
        asset_type = context.entity.get("sg_asset_type", "Asset")

        render_template = item.properties.get("turntable_render_template")
        if not render_template:
            return 1

        # Find existing versions
        fields = {
            "Asset": asset_name,
            "sg_asset_type": asset_type,
        }
        try:
            existing = publisher.sgtk.paths_from_template(
                render_template, fields, skip_keys=["version", "SEQ"]
            )
            if not existing:
                return 1
            versions = []
            for path in existing:
                f = render_template.get_fields(path)
                if "version" in f:
                    versions.append(f["version"])
            return max(versions) + 1 if versions else 1
        except Exception:
            return 1

    def finalize(self, settings, item):
        self.logger.info(
            "Turntable submitted for review. "
            "qt_watcher will pick up the flag and bake the QT."
        )


