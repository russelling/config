"""
sg_utils.py -- shared ShotGrid / Toolkit helpers for the ingest + turntable
pipeline.

Credentials are read from environment variables only, never from config.yml
or source code, per CLAUDE_INSTRUCTIONS.md rule "No credentials or real
paths in commits":

    SG_SERVER        ShotGrid site URL (falls back to config.yml shotgrid.site)
    SG_SCRIPT_NAME    Name of the ShotGrid API script key
    SG_SCRIPT_KEY     The script key's application key

Set these in the environment the watcher/service runs under (e.g. a systemd
EnvironmentFile, a Windows service's environment block, or a local .env
loaded by your process manager) -- not in this repo.
"""
from __future__ import annotations

import os
import platform
import logging
from pathlib import Path
from typing import Any, Optional

import yaml

log = logging.getLogger("ingest_turntable")

CONFIG_PATH = Path(__file__).parent / "config.yml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def platform_key() -> str:
    system = platform.system().lower()
    if system == "darwin":
        return "mac"
    if system == "windows":
        return "windows"
    return "linux"


def resolve_platform_path(entry: dict, key_prefix: str = "") -> str:
    """Resolve one of {linux,mac,windows}_path / {linux,mac,windows} keys
    for the current OS, matching the linux_path/mac_path/windows_path
    convention used throughout FlowTrackingConfig's roots.yml / paths.yml."""
    plat = platform_key()
    for candidate in (f"{key_prefix}{plat}_path", plat):
        if candidate in entry:
            return entry[candidate]
    raise KeyError(f"No path defined for platform '{plat}' in {entry!r}")


def get_executable(config: dict, name: str) -> str:
    plat = platform_key()
    return config["executables"][name][plat]


def get_watch_folder(config: dict) -> Path:
    return Path(resolve_platform_path(config["watch_folder"]))


def get_log_dir(config: dict) -> Path:
    plat = platform_key()
    return Path(config["logging"][f"log_dir_{plat}"])


def connect() -> Any:
    """Return an authenticated shotgun_api3.Shotgun connection."""
    import shotgun_api3  # imported lazily so scripts without SG needs can skip it

    config = load_config()
    site = os.environ.get("SG_SERVER") or config["shotgrid"]["site"]
    script_name = os.environ.get("SG_SCRIPT_NAME")
    script_key = os.environ.get("SG_SCRIPT_KEY")

    if not script_name or not script_key:
        raise EnvironmentError(
            "SG_SCRIPT_NAME and SG_SCRIPT_KEY must be set in the environment. "
            "See sg_utils.py module docstring."
        )
    if not site or site.startswith("PLACEHOLDER"):
        raise EnvironmentError(
            "ShotGrid site is not configured. Set shotgrid.site in config.yml "
            "or the SG_SERVER environment variable."
        )

    return shotgun_api3.Shotgun(site, script_name=script_name, api_key=script_key)


def get_toolkit(project_id: int):
    """Bootstrap an sgtk instance for the given Project, so we can resolve
    templates.yml paths and use tk.create_filesystem_structure() rather than
    hand-rolling path joins that could drift from templates.yml."""
    import sgtk

    tk = sgtk.sgtk_from_entity("Project", project_id)
    return tk


def find_or_create_asset(
    sg: Any,
    project_id: int,
    asset_code: str,
    asset_type: str,
) -> dict:
    existing = sg.find_one(
        "Asset",
        [["project", "is", {"type": "Project", "id": project_id}], ["code", "is", asset_code]],
        ["code", "sg_asset_type"],
    )
    if existing:
        log.info("Found existing Asset %s (id=%s)", asset_code, existing["id"])
        return existing

    log.info("Creating new Asset %s [%s]", asset_code, asset_type)
    return sg.create(
        "Asset",
        {
            "project": {"type": "Project", "id": project_id},
            "code": asset_code,
            "sg_asset_type": asset_type,
        },
    )


def find_or_create_task(
    sg: Any,
    entity: dict,
    project_id: int,
    step_code: str,
) -> Optional[dict]:
    """Find (or create) a Task on `entity` for the named Pipeline Step. Steps
    must already exist in ShotGrid -- per CLAUDE_INSTRUCTIONS.md rule "Step
    codes are sacred", this will raise rather than silently invent a step."""
    step = sg.find_one("Step", [["short_name", "is", step_code]], ["code", "short_name"])
    if not step:
        raise ValueError(
            f"Pipeline Step '{step_code}' does not exist in ShotGrid. "
            f"Create it (Shot/Asset step list) before running ingest -- "
            f"see README.md 'ShotGrid setup'."
        )

    task = sg.find_one(
        "Task",
        [["entity", "is", entity], ["step", "is", step]],
        ["content"],
    )
    if task:
        return task

    return sg.create(
        "Task",
        {
            "project": {"type": "Project", "id": project_id},
            "entity": entity,
            "step": step,
            "content": step["code"],
        },
    )


def valid_list_values(sg: Any, entity_type: str, field_name: str) -> Optional[set]:
    """Return the set of valid values for a ShotGrid list field, or None if
    it can't be determined. Same pattern BUF_Mac_watcher's qt_watcher.py uses
    (_valid_list_values) to avoid setting a list field to an unconfigured
    option -- used here at watcher startup to verify config.yml's
    ingest.asset_type_folders actually match ShotGrid's live sg_asset_type
    schema, so a typo'd folder name fails loudly instead of silently
    mis-typing every Asset dropped into it."""
    try:
        schema = sg.schema_field_read(entity_type, field_name)
        props = schema.get(field_name, {}).get("properties", {})
        valid = props.get("valid_values", {}).get("value")
        if valid:
            return set(valid)
    except Exception as exc:
        log.warning("Could not read schema for %s.%s: %s", entity_type, field_name, exc)
    return None


def next_version_number(sg: Any, entity: dict, published_file_type: str) -> int:
    existing = sg.find(
        "PublishedFile",
        [["entity", "is", entity], ["published_file_type.PublishedFileType.code", "is", published_file_type]],
        ["version_number"],
        order=[{"field_name": "version_number", "direction": "desc"}],
    )
    return (existing[0]["version_number"] + 1) if existing else 1
