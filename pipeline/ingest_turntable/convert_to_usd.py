"""
convert_to_usd.py -- converts an incoming asset source file (FBX / OBJ /
Alembic / glTF) to pipeline-standard USD.

This module has two lives:

1. Imported by ingest_asset.py in a normal Python process -- call
   `convert_to_usd(source, dest, blender_exe)` and it shells out to Blender
   in --background mode to do the actual conversion.

2. Executed *by* Blender itself:
       blender --background --factory-startup --python convert_to_usd.py -- \
           --input <source> --output <dest>
   In this mode `bpy` is importable and the script performs the import/export.

Supported input extensions: fbx, obj, abc, gltf, glb, usd/usdc/usda/usdz
(the last group is passed through unchanged -- no conversion needed).
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("ingest_turntable.convert_to_usd")

try:
    import bpy  # noqa: F401
    RUNNING_IN_BLENDER = True
except ImportError:
    RUNNING_IN_BLENDER = False


PASSTHROUGH_EXTENSIONS = {"usd", "usdc", "usda", "usdz"}
SUPPORTED_EXTENSIONS = {"fbx", "obj", "abc", "gltf", "glb"} | PASSTHROUGH_EXTENSIONS


# ---------------------------------------------------------------------------
# Driver side (plain Python, called from ingest_asset.py)
# ---------------------------------------------------------------------------
def convert_to_usd(source: Path, dest: Path, blender_exe: str) -> Path:
    """Convert `source` (fbx/obj/abc/gltf/glb) into a USD file at `dest`,
    by launching Blender headless. Returns `dest`. Raises CalledProcessError
    on failure -- caller is responsible for logging / moving the delivery to
    the _failed/ folder."""
    ext = source.suffix.lower().lstrip(".")
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported source extension '.{ext}' for {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    if ext in PASSTHROUGH_EXTENSIONS:
        # Already USD -- just copy it into the publish location so the rest
        # of the pipeline (turntable render) always reads from the same
        # template-resolved path.
        import shutil
        shutil.copy2(source, dest)
        log.info("Source is already USD, copied %s -> %s", source, dest)
        return dest

    cmd = [
        blender_exe,
        "--background",
        "--factory-startup",
        "--python",
        str(Path(__file__).resolve()),
        "--",
        "--input",
        str(source),
        "--output",
        str(dest),
    ]
    log.info("Running Blender USD conversion: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Blender conversion failed:\nSTDOUT:\n%s\nSTDERR:\n%s", result.stdout, result.stderr)
        raise subprocess.CalledProcessError(result.returncode, cmd, output=result.stdout, stderr=result.stderr)

    if not dest.exists():
        raise RuntimeError(f"Blender reported success but {dest} was not created")

    log.info("Converted %s -> %s", source, dest)
    return dest


# ---------------------------------------------------------------------------
# Blender side (executed inside `blender --background --python`)
# ---------------------------------------------------------------------------
def _parse_blender_args():
    # Blender puts its own args before "--"; only what's after belongs to us.
    argv = sys.argv
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def _import_source(bpy_module, source: Path):
    ext = source.suffix.lower().lstrip(".")
    if ext == "fbx":
        bpy_module.ops.import_scene.fbx(filepath=str(source))
    elif ext == "obj":
        # Blender 4.x uses wm.obj_import; fall back to legacy import_scene.obj
        if hasattr(bpy_module.ops.wm, "obj_import"):
            bpy_module.ops.wm.obj_import(filepath=str(source))
        else:
            bpy_module.ops.import_scene.obj(filepath=str(source))
    elif ext == "abc":
        bpy_module.ops.wm.alembic_import(filepath=str(source))
    elif ext in ("gltf", "glb"):
        bpy_module.ops.import_scene.gltf(filepath=str(source))
    else:
        raise ValueError(f"No Blender importer wired up for .{ext}")


def _do_conversion_in_blender(input_path: str, output_path: str):
    import bpy

    source = Path(input_path)
    dest = Path(output_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Start from a clean scene -- --factory-startup already avoids the
    # user's local startup.blend, this clears the default cube/light/camera.
    bpy.ops.wm.read_factory_settings(use_empty=True)

    _import_source(bpy, source)

    bpy.ops.wm.usd_export(
        filepath=str(dest),
        export_materials=True,
        export_textures=True,
        export_uvmaps=True,
        export_normals=True,
        export_animation=False,
        evaluation_mode="RENDER",
    )
    print(f"[convert_to_usd] wrote {dest}")


if __name__ == "__main__" and RUNNING_IN_BLENDER:
    args = _parse_blender_args()
    _do_conversion_in_blender(args.input, args.output)
