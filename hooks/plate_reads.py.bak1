# Copyright (c) Studio. All Rights Reserved.
"""
plate_reads.py

Finds a shot's delivered plates on disk and turns them into Read nodes.

Used from two places, so the two can never disagree about what "the plates"
are:

  * scene_operation_tk-nuke.py, when Workfiles2 creates the FIRST version of
    a shot's script (the colour-template build).
  * The "Load Shot Plates" menu command, for re-running it or picking up a
    plate that landed after the script was made.

WHAT THE PLATES FOLDER ACTUALLY LOOKS LIKE
------------------------------------------
Deliveries are one folder per element, holding a resolution folder, holding
the frames:

    plates/
        301_001_0050_bg01/4608x2592/301_001_0050_bg01_1001.exr
        301_001_0050_bg01_v02/4608x2592/301_001_0050_bg01_v02_1001.exr
        301_001_0050_el01/4608x2592/301_001_0050_el01_1001.exr
        301_002_0010_REF1_v01/4608x2592/301_002_0010_REF1_v01_1001.exr
        301_001_0050.cc, 301_001_0050_bg01.cdl, *.cube, stills, misc folders

Element naming is not consistent across vendors - bg01 and BG1 both appear,
the _v## suffix is sometimes there and sometimes not, a resolution folder is
sometimes empty, and non-plate folders (artwork drops, texture jpegs) sit in
the same place. So this SCANS rather than resolving a template: a template
would have to describe a convention the deliveries do not actually keep. The
plates FOLDER still comes from the config (ep_shot_plates_area); only its
contents are discovered.

Folders with no EXRs in them are simply never seen, which is what filters
out the artwork drops for free.

ONE READ PER ELEMENT, LATEST VERSION
------------------------------------
bg01 and bg01_v02 are the same element redelivered, so only the highest
version gets a node; the older ones are named in the node's label so the
artist knows they exist. Elements are ordered bg, then el, then ref, then
anything else, so the graph reads top to bottom the way a comp is built.

COLOUR
------
Plates are delivered in ACES. The Read's colorspace knob is set to the first
name in PLATE_COLORSPACE_CANDIDATES that this OCIO config actually offers -
config naming varies ("ACES2065-1", "ACES - ACES2065-1", ...) and setting a
name the config does not have would silently leave the knob on something
else. If none of them exist, the node falls back to raw and SAYS SO in its
label rather than quietly mis-tagging the plate.
"""

import os
import re

# Frame token: 301_001_0050_bg01_1001.exr, and the older 301_001_0050.1001.exr
PLATE_SEQ_RE = re.compile(
    r"^(?P<base>.+?)[._](?P<frame>\d{3,8})\.(?P<ext>exr)$", re.IGNORECASE
)

# Trailing delivery version on an element folder: bg01_v02 -> bg01, 2
ELEMENT_VERSION_RE = re.compile(r"^(?P<name>.+?)_v(?P<version>\d+)$", re.IGNORECASE)

# WxH resolution folder, e.g. 4608x2592
RESOLUTION_RE = re.compile(r"^(?P<w>\d+)\s*x\s*(?P<h>\d+)$", re.IGNORECASE)

# Order elements the way a comp is built rather than alphabetically.
ELEMENT_ORDER = ("bg", "el", "ref")

# In config-name order of preference. ACES2065-1 is the delivery/interchange
# space; ACEScg is the working space - if a vendor hands over ACEScg the
# second group matches. Change this list, not the code, if the show moves.
PLATE_COLORSPACE_CANDIDATES = (
    "ACES2065-1",
    "ACES - ACES2065-1",
    "aces2065-1",
    "ACEScg",
    "ACES - ACEScg",
    "acescg",
)


# ---------------------------------------------------------------------------
# Scanning (no nuke import - unit testable off a plain folder tree)
# ---------------------------------------------------------------------------

def _is_hidden(name):
    return name.startswith(".") or name.startswith("._")


def _sequences_in_dir(directory):
    """
    Group the EXRs sitting directly in one directory into sequences.

    Returns a list of {base, pad, frames} sorted biggest first. A directory
    normally holds exactly one sequence; more than one means the vendor put
    two passes in the same folder, and the biggest is the plate.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []

    groups = {}
    for name in names:
        if _is_hidden(name) or not name.lower().endswith(".exr"):
            continue
        match = PLATE_SEQ_RE.match(name)
        if match:
            base, frame = match.group("base"), match.group("frame")
            separator = name[len(base)]
            key = (base, len(frame), separator)
            groups.setdefault(key, []).append(int(frame))
        else:
            # A single frame with no frame number - still worth a node.
            groups.setdefault((os.path.splitext(name)[0], 0, ""), []).append(None)

    out = []
    for (base, pad, separator), frames in groups.items():
        real = sorted(f for f in frames if f is not None)
        out.append({
            "base": base,
            "pad": pad,
            "separator": separator,
            "frames": real,
            "count": len(frames),
        })
    out.sort(key=lambda g: g["count"], reverse=True)
    return out


def _nuke_path(directory, base, pad, separator):
    """
    Read-ready path: '#'-padded for a sequence, literal for a single frame.

    The separator is whichever character actually sat between the name and
    the frame number on disk ('_' in 301_001_0050_bg01_1001.exr, '.' in the
    older 301_001_0050.1001.exr) - rebuilding it with the wrong one gives a
    Read that finds nothing.
    """
    if pad:
        return os.path.join(directory, "%s%s%s.exr" % (base, separator, "#" * pad))
    return os.path.join(directory, "%s.exr" % base)


def _split_element(folder_name, shot_code):
    """
    ('301_001_0050_bg01_v02', '301_001_0050') -> ('bg01', 2)

    Falls back to the whole folder name when it does not carry the shot code,
    so an oddly named delivery still gets a node rather than being dropped.
    """
    name = folder_name
    if shot_code and name.lower().startswith(shot_code.lower() + "_"):
        name = name[len(shot_code) + 1:]
    version = 0
    match = ELEMENT_VERSION_RE.match(name)
    if match:
        name = match.group("name")
        version = int(match.group("version"))
    return name, version


def _element_sort_key(element):
    """bg before el before ref before everything else, then by name."""
    lowered = (element or "").lower()
    for index, prefix in enumerate(ELEMENT_ORDER):
        if lowered.startswith(prefix):
            return (index, lowered)
    return (len(ELEMENT_ORDER), lowered)


def _candidate_dirs(plates_dir):
    """
    (element_folder_name, directory, depth) for every place plates can hide:
    the plates folder itself (depth 0), each element folder (1), and each
    element's resolution folder (2). No deeper - past that is somebody's
    working folder, not a delivery.
    """
    yield "", plates_dir, 0
    try:
        entries = sorted(os.listdir(plates_dir))
    except OSError:
        return
    for entry in entries:
        if _is_hidden(entry):
            continue
        element_dir = os.path.join(plates_dir, entry)
        if not os.path.isdir(element_dir):
            continue
        yield entry, element_dir, 1
        try:
            children = sorted(os.listdir(element_dir))
        except OSError:
            continue
        for child in children:
            if _is_hidden(child):
                continue
            child_dir = os.path.join(element_dir, child)
            if os.path.isdir(child_dir):
                yield entry, child_dir, 2


def _resolution_pixels(directory):
    """Pixel count from a WxH folder name, for choosing between resolutions."""
    match = RESOLUTION_RE.match(os.path.basename(directory))
    if not match:
        return 0
    return int(match.group("w")) * int(match.group("h"))


def scan_plates(plates_dir, shot_code=None, latest_only=True):
    """
    Every plate element found under plates_dir, best delivery first.

    Each record carries the keys the colour-template build already expects
    (nuke_path / first / last / label / count) plus the element metadata:

        element     'bg01'          - grouping name, version stripped
        version     2               - delivery version, 0 if unversioned
        folder      '/.../bg01_v02' - where it was found
        resolution  '4608x2592'     - or None
        superseded  [1]             - older versions of this element on disk
        alternates  ['3840x2160']   - other resolution folders with frames

    latest_only keeps one record per element (the highest version). Pass
    False to get them all.
    """
    if not plates_dir or not os.path.isdir(plates_dir):
        return []

    found = []
    for folder_name, directory, depth in _candidate_dirs(plates_dir):
        groups = _sequences_in_dir(directory)
        if not groups:
            continue
        group = groups[0]
        element, version = _split_element(folder_name or "", shot_code)
        if not element:
            # EXRs loose in plates/ - name the element after the file itself.
            element = group["base"]
        frames = group["frames"]
        found.append({
            "element": element,
            "key": element.lower(),
            "version": version,
            "folder": directory,
            "resolution": os.path.basename(directory) if depth == 2 else None,
            "nuke_path": _nuke_path(directory, group["base"], group["pad"],
                                    group["separator"]),
            "label": group["base"],
            "first": frames[0] if frames else 1,
            "last": frames[-1] if frames else 1,
            "count": group["count"],
            "superseded": [],
            "alternates": [],
        })

    # One record per element: highest version, then most frames, then the
    # biggest resolution. Everything it beat is recorded on it so the label
    # can say what else is on disk.
    by_element = {}
    for record in found:
        by_element.setdefault(record["key"], []).append(record)

    results = []
    for key, records in by_element.items():
        records.sort(
            key=lambda r: (r["version"], r["count"], _resolution_pixels(r["folder"])),
            reverse=True,
        )
        if not latest_only:
            results.extend(records)
            continue
        best = records[0]
        best["superseded"] = sorted(
            {r["version"] for r in records[1:] if r["version"] != best["version"]}
        )
        best["alternates"] = [
            r["resolution"] for r in records[1:]
            if r["version"] == best["version"] and r["resolution"]
            and r["resolution"] != best["resolution"]
        ]
        results.append(best)

    results.sort(key=lambda r: (_element_sort_key(r["element"]), -r["version"]))
    return results


def describe(record):
    """One line for a log or a node label."""
    bits = [record["element"]]
    if record["version"]:
        bits.append("v%02d" % record["version"])
    if record["resolution"]:
        bits.append(record["resolution"])
    return "%s  (%d-%d, %d frames)" % (
        "  ".join(bits), record["first"], record["last"], record["count"]
    )


# ---------------------------------------------------------------------------
# Node building (needs nuke)
# ---------------------------------------------------------------------------

def resolve_plate_colorspace(read_node):
    """
    Set the Read's colorspace to the first candidate this OCIO config offers.

    Returns the name used, or None if the config has none of them - in which
    case the caller should fall back to raw and say so, rather than leave the
    knob on a default that means something else.
    """
    try:
        knob = read_node["colorspace"]
    except Exception:
        return None

    try:
        available = list(knob.values())
    except Exception:
        available = []

    for name in PLATE_COLORSPACE_CANDIDATES:
        match = name
        if available:
            # Configs often prefix the role, e.g. "ACES/ACES2065-1".
            hits = [v for v in available
                    if v == name or v.split("/")[-1] == name]
            if not hits:
                continue
            match = hits[0]
        try:
            knob.setValue(match)
        except Exception:
            continue
        try:
            if knob.value() in (match, name) or knob.value().endswith(name):
                return knob.value()
        except Exception:
            return match
    return None


def existing_read_paths():
    """Normalised file paths of the Read nodes already in this script."""
    import nuke
    paths = set()
    for node in nuke.allNodes("Read"):
        try:
            value = node["file"].value()
        except Exception:
            continue
        if value:
            paths.add(os.path.normpath(value))
    return paths


def make_read(record, primary=False, note=""):
    """
    Create one Read node for a plate record and return it.

    Frame range comes from the frames actually on disk, not from the script,
    so a short delivery reads short instead of reporting missing frames.
    """
    import nuke
    read = nuke.createNode("Read", inpanel=False)
    read["file"].setValue(record["nuke_path"].replace("\\", "/"))
    read["first"].setValue(record["first"])
    read["last"].setValue(record["last"])
    read["origfirst"].setValue(record["first"])
    read["origlast"].setValue(record["last"])

    colorspace = resolve_plate_colorspace(read)
    if colorspace:
        colour_note = colorspace
    else:
        # The config has none of the ACES names - do not pretend.
        read["raw"].setValue(True)
        try:
            read["colorspace"].setValue("raw")
        except Exception:
            pass
        colour_note = "raw — SET COLORSPACE MANUALLY"

    lines = ["PLATE: %s" % record["element"] if primary
             else "PLATE (extra): %s" % record["element"]]
    if record["version"]:
        lines[0] += " v%02d" % record["version"]
    lines.append("%s — %d-%d" % (colour_note, record["first"], record["last"]))
    if record["resolution"]:
        lines.append(record["resolution"])
    if record.get("superseded"):
        # Version 0 is "no _v## suffix at all", not "v00".
        lines.append("older on disk: %s" % ", ".join(
            "unversioned" if v == 0 else "v%02d" % v
            for v in record["superseded"]))
    if record.get("alternates"):
        lines.append("also at: %s" % ", ".join(record["alternates"]))
    if note:
        lines.append(note)
    read["label"].setValue("\n".join(lines))
    return read


# ---------------------------------------------------------------------------
# Menu command
# ---------------------------------------------------------------------------

def plates_dir_for_context(tk, context):
    """
    The shot's plates folder, from the config's own template.

    ep_shot_plates_area is the anchor; if an older config predates it, the
    plates folder beside shot_base is the fallback so this keeps working
    rather than failing on a template lookup.
    """
    try:
        fields = context.as_template_fields(tk.templates["ep_nuke_shot_work"])
    except Exception:
        return None

    for template_name, suffix in (("ep_shot_plates_area", ""),
                                  ("shot_base", "plates")):
        template = tk.templates.get(template_name)
        if not template:
            continue
        try:
            needed = {k: fields[k] for k in template.keys if k in fields}
            path = template.apply_fields(needed)
        except Exception:
            continue
        return os.path.join(path, suffix) if suffix else path
    return None


def load_shot_plates():
    """
    "Load Shot Plates" menu command: add a Read for every plate element on
    disk that the script does not already have.

    Deliberately additive - it never deletes or repoints an existing Read,
    so running it twice is harmless and running it after a redelivery adds
    just the new element.
    """
    import nuke
    import sgtk

    engine = sgtk.platform.current_engine()
    if engine is None:
        nuke.message("Flow Production Tracking is not running in this session.")
        return

    context = engine.context
    if not (context and context.entity and context.entity.get("type") == "Shot"):
        nuke.message("Load Shot Plates works in a Shot context.\n\n"
                     "Current context: %s" % context)
        return

    plates_dir = plates_dir_for_context(engine.sgtk, context)
    if not plates_dir or not os.path.isdir(plates_dir):
        nuke.message("No plates folder for this shot:\n\n%s"
                     % (plates_dir or "(could not resolve)"))
        return

    shot_code = (context.entity or {}).get("name")
    records = scan_plates(plates_dir, shot_code=shot_code)
    if not records:
        nuke.message("No plate EXRs found under:\n\n%s" % plates_dir)
        return

    already = existing_read_paths()
    created, skipped = [], []
    for record in records:
        if os.path.normpath(record["nuke_path"]) in already:
            skipped.append(describe(record))
            continue
        for selected in nuke.selectedNodes():
            selected.setSelected(False)
        make_read(record, primary=not created)
        created.append(describe(record))

    message = []
    if created:
        message.append("Added %d plate Read node%s:\n  %s"
                       % (len(created), "" if len(created) == 1 else "s",
                          "\n  ".join(created)))
    if skipped:
        message.append("Already in the script:\n  %s" % "\n  ".join(skipped))
    nuke.message("\n\n".join(message) or "Nothing to do.")


def register(engine):
    """Register the menu command with the Flow Production Tracking menu."""
    engine.register_command(
        "Load Shot Plates",
        load_shot_plates,
        {"short_name": "load_shot_plates",
         "description": "Create a Read node for each plate element delivered "
                        "for this shot."},
    )
