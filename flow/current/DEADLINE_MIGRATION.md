# Buffalo VFX — Migrating QT Review Pipeline from Watcher to Deadline

## Overview

The current QT review pipeline uses a custom Mac Studio watcher daemon (`qt_watcher.py`) 
that polls the shots tree for render-complete flag files and invokes a Nuke batch bake script.

This document describes how to replace the watcher with Thinkbox Deadline once it is set up,
while keeping the bake script (`qt_bake_slate_burnin.py`) and color pipeline unchanged.

---

## Current Architecture (Watcher)

```
Artist renders in Nuke
    ↓
render_complete_callback.py fires (afterRender)
    ↓
Dialog: Submitted For + Description
    ↓
Flag JSON written to:
  shots/{Ep}/{Scene}/{Shot}/render/work/{Shot}_{Step}_{output}_v{version}/
    .render_complete_{Shot}_{Step}_{output}_v{version}.json
    ↓
qt_watcher.py (Mac Studio launchd daemon) polls every 30s
    ↓
Invokes: nuke -t qt_bake_slate_burnin.py <flag> <out1> <out2>
    ↓
QT written to shot review folder + editorial/to_editorial/{date}/
    ↓
ShotGrid Version created + movie uploaded
```

---

## Target Architecture (Deadline)

```
Artist renders in Nuke
    ↓
render_complete_callback.py fires (afterRender)
    ↓
Dialog: Submitted For + Description
    ↓
Deadline job submitted via deadlinecommand or Python API
  (replaces flag file write entirely)
    ↓
Deadline Worker on Mac Studio picks up job
    ↓
Executes: nuke -t qt_bake_slate_burnin.py <flag> <out1> <out2>
    ↓
QT written to shot review folder + editorial/to_editorial/{date}/
    ↓
ShotGrid Version created + movie uploaded
    (can be handled by qt_bake_slate_burnin.py OR Deadline's SG connector)
```

---

## What Changes

### 1. `render_complete_callback.py` — Replace flag write with Deadline submission

The dialog stays exactly the same. Replace the flag file writing block at the end of
`run_render_complete()` with a Deadline job submission.

**Current code (flag write):**
```python
flag_dir = os.path.dirname(flag_path)
if not os.path.exists(flag_dir):
    os.makedirs(flag_dir)

with open(flag_path, "w") as f:
    json.dump(flag_data, f, indent=2)
```

**Replace with (Deadline submission):**
```python
import subprocess
import json

# Write a temporary job info file
job_info = {
    "Plugin": "CommandLine",
    "Name": "QT Bake - %s_%s_v%03d" % (flag_data["shot_code"], flag_data["step"], flag_data["version"]),
    "Frames": "0",
    "ChunkSize": "1",
    "Pool": "qt_bake",          # create a Deadline pool called "qt_bake" assigned to Mac Studio
    "MachineLimit": "1",
    "Comment": flag_data.get("description", ""),
    "ExtraInfo0": flag_data["shot_code"],
    "ExtraInfo1": flag_data["step"],
    "ExtraInfo2": str(flag_data["version"]),
    "ExtraInfo3": flag_data.get("submitted_for", ""),
}

plugin_info = {
    "Executable": "/Applications/Nuke17.0v1/Nuke17.0.app/Contents/MacOS/Nuke17.0",
    "Arguments": "-t %s %s %s %s" % (
        BAKE_SCRIPT_PATH,
        _encode_flag_data(flag_data),   # see note below
        shot_movie_path,
        editorial_movie_path,
    ),
    "Shell": "default",
}

# Submit via deadlinecommand
job_info_path = "/tmp/deadline_job_info.job"
plugin_info_path = "/tmp/deadline_plugin_info.job"

with open(job_info_path, "w") as f:
    for k, v in job_info.items():
        f.write("%s=%s\n" % (k, v))

with open(plugin_info_path, "w") as f:
    for k, v in plugin_info.items():
        f.write("%s=%s\n" % (k, v))

result = subprocess.run(
    ["deadlinecommand", "-SubmitMultipleJobs", job_info_path, plugin_info_path],
    capture_output=True, text=True
)
if result.returncode != 0:
    nuke.warning("[render_complete] Deadline submission failed: %s" % result.stderr)
else:
    nuke.tprint("[render_complete] Deadline job submitted: %s" % result.stdout)
```

**Note on passing flag data to the bake script:**
The flag JSON data needs to reach `qt_bake_slate_burnin.py`. Two options:
- **Option A (simplest):** Still write the flag JSON file to disk, but instead of the 
  watcher picking it up, pass the flag path as the first argument to the Deadline job.
  This is the easiest migration — keep the flag file write, just submit a Deadline job
  instead of relying on the watcher to find it.
- **Option B (cleaner):** Pass flag data inline as a base64-encoded JSON argument.
  Removes the flag file entirely but requires updating `qt_bake_slate_burnin.py`
  to accept inline data.

**Recommended: Option A** — minimal changes, easiest to debug.

### 2. `qt_bake_slate_burnin.py` — No changes required for Option A

The bake script already accepts a flag JSON path as its first argument. No changes needed.

If using Option B (inline data), update `main()` to detect whether `sys.argv[1]` is a
file path or base64-encoded JSON:
```python
import base64
arg = sys.argv[1]
if os.path.exists(arg):
    with open(arg) as f:
        data = json.load(f)
else:
    data = json.loads(base64.b64decode(arg).decode("utf-8"))
```

### 3. Remove `qt_watcher.py` and launchd plist

Once Deadline is handling job pickup, the watcher is no longer needed:
```bash
launchctl unload ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
rm ~/Library/LaunchAgents/com.buffalovfx.qtwatcher.plist
```

The `qt_watcher.py` and `com.buffalovfx.qtwatcher.plist` files can be archived or deleted
from `config/scripts/`.

---

## Deadline Setup Requirements

### Repository
- Install Deadline Repository on the Mac Studio or a dedicated machine accessible from
  all networks via the shared storage mount
- Recommended path: `/Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx/deadline_repo`

### Workers
- Install Deadline Worker on the Mac Studio (for QT bakes)
- Install Deadline Worker on artist Mac and Windows workstations (for future render farm use)
- All workers point to the same Repository path

### Pool
- Create a Deadline Pool called `qt_bake`
- Assign the Mac Studio worker to this pool
- The job submission in `render_complete_callback.py` targets this pool

### Path Mapping
Configure Deadline's path mapping to translate between Mac and Windows paths:
```
Mac:     /Volumes/atv-post-lucid3/atv-buffalo-s03/buffalo_vfx
Windows: C:\Volumes\atv-post-lucid3\atv-buffalo-s03\buffalo_vfx
```
This mirrors the existing Toolkit roots configuration.

### ShotGrid Integration (optional)
Deadline has a built-in ShotGrid connector. If enabled, you can remove the ShotGrid
Version creation and upload code from `qt_bake_slate_burnin.py` and let Deadline handle
it via the connector. This is optional — the current bake script handles upload fine.

### deadlinecommand path
`deadlinecommand` must be on the PATH of the Nuke process on artist machines.
Typical locations:
- Mac: `/Applications/Deadline10/Resources/deadlinecommand`
- Windows: `C:\Program Files\Thinkbox\Deadline10\bin\deadlinecommand.exe`

Add the appropriate path to the submission code or set it as an environment variable.

---

## Migration Steps (when ready)

1. **Install Deadline** — Repository on shared storage, Workers on Mac Studio + artist machines
2. **Create `qt_bake` pool** — assign Mac Studio worker
3. **Configure path mapping** — Mac ↔ Windows as above
4. **Update `render_complete_callback.py`** — replace flag write with Deadline submission
   (keeping Option A flag file write for the bake script to read)
5. **Test submission** — render a frame, confirm Deadline job appears in Monitor, 
   confirm Mac Studio picks it up and bakes the QT
6. **Verify ShotGrid upload** — confirm Version appears in ShotGrid with correct
   description and submitted_for values
7. **Remove watcher** — unload launchd plist, archive `qt_watcher.py`
8. **Optional** — switch to Deadline ShotGrid connector and remove upload code from
   `qt_bake_slate_burnin.py`

---

## Files Affected

| File | Action |
|------|--------|
| `config/hooks/render_complete_callback.py` | Replace flag write with Deadline submission |
| `config/scripts/qt_bake_slate_burnin.py` | No change (Option A) or minor update (Option B) |
| `config/scripts/qt_watcher.py` | Archive/delete |
| `config/scripts/com.buffalovfx.qtwatcher.plist` | Unload and delete |

---

## Files Unchanged

- `config/hooks/scene_operation_tk-nuke.py` — no changes
- `config/core/templates.yml` — `ep_nuke_shot_render_flag` template stays (flag file 
  still written for Option A)
- `qt_bake_slate_burnin.py` color bake, slate, burn-in logic — identical

---

## Notes

- The `deadlinecommand` Python API (`import Deadline.DeadlineConnect`) is an alternative
  to subprocess and works better in environments where `deadlinecommand` isn't on PATH.
  Requires Deadline's Python API to be installed and on `sys.path` in the Nuke environment.
- For remote artists (different network), Deadline's RCS (Remote Connection Server) handles
  the connection back to the Repository — artists don't need direct access to the Repository
  path, only to the RCS port.
- If the Mac Studio is ever unavailable, Deadline will queue the job and process it when 
  the worker comes back online — more reliable than the polling watcher.
