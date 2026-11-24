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

EVERY SEQUENCE, LATEST FIRST
----------------------------
Every EXR sequence under plates/ gets a Read: every element, every
redelivered version, every resolution folder, every sequence sharing a
folder, down to MAX_SCAN_DEPTH folders (so plates/test/test_05/4608x2592/ is
found too). bg01 and bg01_v02 are still recognised as the same element - the
highest version is its "best" record and sorts first; lower versions are
labelled OLDER DELIVERY. Elements are ordered bg, then el, then ref, then
anything else, so the first record is the plate the colour template wires in.

scan_plates(..., latest_only=True) gives the old one-per-element behaviour.
(Changed 2026-09-26. Before that: latest version of each element only, the
biggest sequence per folder only, two folders deep only.)

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

# Frame token = the WHOLE trailing digit run before .exr (the lazy stem is
# what guarantees that). The separator before it is optional - all three
# occur on the share:
#   301_001_0050_bg01_1001.exr            '_'
#   301_001_0050.1001.exr                 '.'
#   302_001_0010_bg01_INHPULL1001.exr     none (in-house pulls)
PLATE_SEQ_RE = re.compile(
    r"^(?P<stem>.*?)(?P<frame>\d+)\.(?P<ext>exr)$", re.IGNORECASE
)
FRAME_DIGITS_MIN, FRAME_DIGITS_MAX = 3, 8

# Folders below plates/ to search. plates/<element>/<WxH>/ is depth 2,
# plates/test/test_05/<WxH>/ is depth 3.
MAX_SCAN_DEPTH = 5

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


# STUDIO_PLATE_READS_LEAN_2026_11_16
_FRAME_TOKEN_RE = re.compile(r"(#+|%0?\d*d|\d+)(\.exr)$", re.IGNORECASE)


def path_key(path):
    """Identity of a plate sequence for 'is this Read already here?':
    last three path parts (element folder / resolution folder / file),
    lower case, '/' separators, frame token collapsed to '#'. Immune to
    drive letters, path substitutions and #### vs %04d."""
    if not path:
        return ""
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    tail = "/".join(parts[-3:]).lower()
    return _FRAME_TOKEN_RE.sub(r"#\2", tail)


# ---------------------------------------------------------------------------
# Scanning (no nuke import - unit testable off a plain folder tree)
# ---------------------------------------------------------------------------

def _is_hidden(name):
    return name.startswith(".") or name.startswith("._")


def _split_frame(name):
    """
    '301_001_0050_bg01_1001.exr' -> ('301_001_0050_bg01', '_', '1001').
    None when the name carries no usable frame number.
    """
    match = PLATE_SEQ_RE.match(name)
    if not match:
        return None
    frame = match.group("frame")
    if not FRAME_DIGITS_MIN <= len(frame) <= FRAME_DIGITS_MAX:
        return None
    stem = match.group("stem")
    if stem and stem[-1] in "._":
        return stem[:-1], stem[-1], frame
    return stem, "", frame


def _sequences_in_dir(directory):
    """
    Group the EXRs sitting directly in one directory into sequences.

    Returns a list of {base, pad, separator, frames, count}, biggest first.
    A directory normally holds one sequence; if a vendor put two passes in
    the same folder, both come back.
    """
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return []

    groups = {}
    for name in names:
        if _is_hidden(name) or not name.lower().endswith(".exr"):
            continue
        split = _split_frame(name)
        if split:
            base, separator, frame = split
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
    out.sort(key=lambda g: (-g["count"], g["base"]))
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


def _walk_dirs(plates_dir):
    """
    (directory, depth) for plates_dir (depth 0) and every folder under it
    down to MAX_SCAN_DEPTH, hidden folders skipped, in a stable order.
    """
    for root, dirs, _files in os.walk(plates_dir):
        rel = os.path.relpath(root, plates_dir)
        depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
        dirs[:] = sorted(d for d in dirs if not _is_hidden(d))
        if depth >= MAX_SCAN_DEPTH:
            dirs[:] = []
        yield root, depth


def _element_folder(plates_dir, directory):
    """
    (folder that names the element, resolution) for one directory.

    A WxH resolution folder is named by its parent; plates/ itself names
    nothing (loose EXRs are named after the file instead).
    """
    if os.path.normpath(directory) == os.path.normpath(plates_dir):
        return "", None
    name = os.path.basename(directory)
    if RESOLUTION_RE.match(name):
        parent = os.path.dirname(directory)
        if os.path.normpath(parent) == os.path.normpath(plates_dir):
            return "", name
        return os.path.basename(parent), name
    return name, None


def _resolution_pixels(directory):
    """Pixel count from a WxH folder name, for choosing between resolutions."""
    match = RESOLUTION_RE.match(os.path.basename(directory))
    if not match:
        return 0
    return int(match.group("w")) * int(match.group("h"))


def scan_plates(plates_dir, shot_code=None, latest_only=False):
    """
    Every EXR sequence found under plates_dir, best delivery first.

    Each record carries the keys the colour-template build expects
    (nuke_path / first / last / label / count) plus:

        element     'bg01'          - grouping name, version stripped
        version     2               - delivery version, 0 if unversioned
        folder      '/.../bg01_v02/4608x2592'
        relpath     'bg01_v02/4608x2592' - folder relative to plates/
        resolution  '4608x2592'     - or None
        best        True            - the one record chosen for its element
        older       False           - True when a higher version of the
                                      same element is on disk
        superseded  [1]             - (best only) older versions on disk
        alternates  ['3840x2160']   - (best only) other resolutions

    latest_only=True keeps just the best record per element.
    """
    if not plates_dir or not os.path.isdir(plates_dir):
        return []

    found = []
    for directory, _depth in _walk_dirs(plates_dir):
        groups = _sequences_in_dir(directory)
        if not groups:
            continue
        folder_name, resolution = _element_folder(plates_dir, directory)
        for index, group in enumerate(groups):
            if folder_name and index == 0:
                element, version = _split_element(folder_name, shot_code)
            else:
                # Loose in plates/, or a second sequence sharing a folder:
                # name it after the file.
                element, version = _split_element(group["base"], shot_code)
            if not element:
                element = group["base"] or os.path.basename(directory)
            frames = group["frames"]
            found.append({
                "element": element,
                "key": element.lower(),
                "version": version,
                "folder": directory,
                "relpath": os.path.relpath(directory, plates_dir),
                "resolution": resolution,
                "nuke_path": _nuke_path(directory, group["base"], group["pad"],
                                        group["separator"]),
                "label": group["base"],
                "first": frames[0] if frames else 1,
                "last": frames[-1] if frames else 1,
                "count": group["count"],
                "best": False,
                "older": False,
                "superseded": [],
                "alternates": [],
            })

    # Per element: highest version, then most frames, then biggest
    # resolution is "best". Everything it beat is recorded on it.
    by_element = {}
    for record in found:
        by_element.setdefault(record["key"], []).append(record)

    results = []
    for records in by_element.values():
        records.sort(
            key=lambda r: (r["version"], r["count"], _resolution_pixels(r["folder"])),
            reverse=True,
        )
        best = records[0]
        best["best"] = True
        best["superseded"] = sorted(
            {r["version"] for r in records[1:] if r["version"] != best["version"]}
        )
        best["alternates"] = [
            r["resolution"] for r in records[1:]
            if r["version"] == best["version"] and r["resolution"]
            and r["resolution"] != best["resolution"]
        ]
        for record in records[1:]:
            record["older"] = record["version"] < best["version"]
        results.extend(records[:1] if latest_only else records)

    results.sort(key=lambda r: (_element_sort_key(r["element"]), not r["best"],
                                -r["version"], r["relpath"].lower()))
    return results


def describe(record):
    """One line for a log or a node label."""
    bits = [record["element"]]
    if record["version"]:
        bits.append("v%02d" % record["version"])
    if record["resolution"]:
        bits.append(record["resolution"])
    if record.get("older"):
        bits.append("[older delivery]")
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
    """path_key()s of the Read nodes already in this script."""
    return set(existing_reads())


def existing_reads():
    """{path_key: Read node} for the top-level Reads of this script.
    STUDIO_PLATE_READS_LEAN_2026_11_16"""
    import nuke
    out = {}
    for node in nuke.allNodes("Read", nuke.root()):
        try:
            value = node["file"].value()
        except Exception:
            continue
        key = path_key(value)
        if key and key not in out:
            out[key] = node
    return out


def find_read(record):
    """The Read already in the script for this plate record, or None.
    A miss prints what was compared, for the Script Editor."""
    reads = existing_reads()
    want = path_key(record.get("nuke_path"))
    node = reads.get(want)
    if node is None:
        print("[plate_reads] no Read matches %r (have: %s)"
              % (want, ", ".join(sorted(reads)) or "none"))
    return node


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

    if not resolve_plate_colorspace(read):
        # The config has none of the ACES names: read raw (plates are
        # treated as ACEScg working space - scene_operation_tk-nuke.py).
        read["raw"].setValue(True)
        try:
            read["colorspace"].setValue("raw")
        except Exception:
            pass

    # STUDIO_PLATE_READS_LEAN_2026_11_16 (Mark): one short line,
    # "BG01 v01  1001-1259". primary / note are kept as arguments for
    # callers but no longer shown.
    label = record["element"]
    if record["version"]:
        label += " v%02d" % record["version"]
    label += "  %d-%d" % (record["first"], record["last"])
    if record.get("older"):
        label += "\nOLDER DELIVERY"
    read["label"].setValue(label)
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
        if path_key(record["nuke_path"]) in already:
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
