# Copyright (c) Studio. All Rights Reserved.
"""
dailies_viewer.py

Makes the Nuke Viewer show the shot's CDL + show/per-camera LUT as an "Input
Process", so a compositor working in ACEScg can flip on a chain that matches
what qt_watcher actually bakes for dailies -- without changing the working
colour space of the script itself.

STATUS: NEW, UNTESTED IN REAL NUKE. Built against read source only (per
CLAUDE_INSTRUCTIONS.md rule 2, this is the best that can be done without a
live Nuke session). See "THINGS TO VERIFY ON THE REAL NUKE" at the bottom of
this docstring before relying on it. Every Nuke-version-dependent knob name
or enum value is wrapped so a mismatch logs loudly and skips that one stage,
rather than raising and aborting the whole build.

WHY THIS DOES NOT RE-IMPLEMENT THE BAKE
----------------------------------------
Same rule this whole pipeline already follows for the Nuke-side QT preview
tools (config/nuke/tools/qt_bake_bridge.py, qt_color_trace.py): a second
hand-written copy of "how does the CDL/LUT get chosen" drifts the moment the
bake changes. So this module live-imports qt_bake_oiio.py and calls its own
resolvers (find_shot_cdl, cdl_as_cc, resolve_lut_path, resolve_show_lut)
rather than re-deriving the element-priority / extension-priority / ambiguity
rules here.

CDL LOADING (updated 2026-09-17): qt_bake_oiio.cdl_as_cc() exists because
oiiotool's --ociofiletransform CLI has no way to pass a cccid, so the bake
pre-extracts one ColorCorrection out of a .ccc/.cdl into a standalone .cc.
That is an oiiotool limitation, not an OCIO or Nuke one -- OCIO's own
CDLTransform.CreateFromFile(src, id) takes a cccid directly (verified
against PyOpenColorIO 2.5.2), and Nuke's OCIOCDLTransform node wraps exactly
that: a `file` knob plus a `cccid` knob. So inside Nuke there is no need to
pre-extract anything -- cdl_as_cc() is still called (against a throwaway
tempfile) but ONLY to learn which correction id the bake's own selection
rule would choose (id matching the filename stem, else the first one);
build_dailies_viewer_group() then points the node's `file` knob at the
ORIGINAL .ccc/.cdl/.cc and writes that id straight into `cccid`. This also
removes a real bug: pointing at the pre-extracted file meant a saved script
reopened later referenced a temp path that may no longer exist.

CORRECTION 2026-09-18: the line above originally said Nuke populates
`cccid` as an enum from the file's corrections, and had this code verify
the id against `cccid.values()` first (the same tolerant-match pattern the
colourspace knobs use) -- that description came from `ocionuke/cdl.py`,
OCIO's open-source *Python* gizmo, not from Nuke's actual built-in node.
`nuke_cdl_cccid_probe.py` (run 2026-09-18) showed the real
`OCIOCDLTransform.cccid` is an `EvalString_Knob` -- a plain free-text
field with no `.values()` at all -- which is why every knob-ordering nudge
in that probe came back with the same empty list: there was nothing to
enumerate. (The node does have a `select_cccid` knob, likely an
interactive picker for the Properties panel, but nothing here needs it.)
So `cccid` is now set directly to the id `_extract_cc_id()` already read
out of the source file -- no matching/verification step, and none is
needed, since that id came from parsing the same multi-correction file in
the first place.

The ONE place this can't call the bake's function unmodified is
resolve_lut_path(data), which internally calls the bake's own
shot_plates_dir(data) -- a macOS-only hardcoded-path resolver. This module's
plates folder comes from Toolkit instead (plate_reads.plates_dir_for_context,
so it's a live decision from the exact same helper the Nuke script build
already trusts), so resolve_lut_path's own shot_plates_dir lookup is monkey-
patched for the duration of one call and restored immediately after -- every
byte of the actual matching logic (BG-element priority, .cube-before-.lut,
the "several matches, log them all" warning) still comes from qt_bake_oiio.py
itself, never copied.

WHY THIS DOES NOT SHARE qt_bake_bridge.py
-------------------------------------------
qt_bake_bridge.py lives in config/nuke/tools/ (a different repo, on
NUKE_PATH) and is written for tools that run generically, outside any
Toolkit context. This file needs the live Toolkit context (Shot entity,
Episode/Scene/Shot fields) the same way plate_reads.py's load_shot_plates()
does, so it belongs next to plate_reads.py in hooks/ and is registered the
same way (register(engine)), not wired through config/nuke/menu.py's
_TOOLS. Importing qt_bake_oiio.py by explicit path is therefore duplicated
here in miniature (about 15 lines) rather than importing config/nuke/tools/
qt_bake_bridge.py from a different repo in the "wrong" direction. If that
duplication ever bothers someone, the fix is to move the loader into a
small shared module both repos import -- not to re-derive the bake's rules
by hand.

THE ACES 1.3 / "-latest" DIVERGENCE -- READ THIS BEFORE TRUSTING A NAME
------------------------------------------------------------------------
qt_bake_oiio.py resolves its OCIO names (OCIO_LOGC4="arri_logc4",
OCIO_REC709_DISPLAY="Gamma 2.2 Rec.709 - Display",
OCIO_REC709_VIEW="ACES 2.0 - SDR 100 nits (Rec.709)") against
"ocio://studio-config-latest". As of the 2026-09-17 patch
(patch_nuke_ocio_pin_aces13.py), nuke/init.py pins Nuke's OWN active config
to "ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1" instead -- a DIFFERENT,
older-labelled config, because "-latest" does not even load in this Nuke's
bundled OCIO (see claude/nuke-viewer-aces-defaults-2026-09-17.md). This tool
runs inside Nuke and can only use colourspaces/displays/views that exist in
Nuke's own active config -- it cannot load a second OCIO config just for this
chain. There is no guarantee "ACES 2.0 - SDR 100 nits (Rec.709)" exists by
that name in an ACES-1.3-labelled config; ACES 1.x configs commonly name the
equivalent view something like "ACES 1.0 - SDR Video (Rec.709 limited)".

So every colourspace/display/view name below is VERIFIED against Nuke's own
node dropdowns at build time (the same defensive pattern plate_reads.py uses
for Read colorspace: try the exact name, then a "role/Name" suffix match,
else log loudly and skip that stage) rather than assumed to exist. A
mismatch here is a real, separate, already-flagged production question (does
the rest of the pipeline move off "-latest" to match Nuke's pin, or does
Nuke's pin get reconsidered) -- not something this tool should paper over by
guessing a name.

CAMERA FIELD -- NOT YET WIRED
------------------------------
resolve_show_lut() picks a per-camera show LUT keyed on a "camera" string,
which the QT bake reads from the flag JSON. Nuke has no flag JSON, and the
ShotGrid field that would carry a Shot's camera has not been confirmed yet
(open item from claude/per-camera-show-lut-design.md). Rather than guess a
field name and query ShotGrid for it, every command in this file prompts
once for an optional camera override (blank = the bake's own ARRI default).
Wire up the real field lookup once it's confirmed and this prompt can go
away.

THINGS TO VERIFY ON THE REAL NUKE (cannot be checked from here)
------------------------------------------------------------------
1. Viewer knobs 'input_process' (bool) and 'input_process_node' (string,
   node name) -- these are real, documented Viewer knobs (confirmed against
   Foundry's own "Input Process" docs and third-party viewer-sync tooling
   that reads/writes them), so high confidence.
2. 'viewerInputOrder' -- controls whether the Input Process runs before or
   after the Viewer Process. Confirmed to EXIST and be meaningful (Foundry
   docs: "before viewer process" / "after viewer process"), but the exact
   Python-visible enum string is a best guess here. If enable_dailies_viewer
   logs that it could not set this knob, open the Viewer settings (press S
   on the viewer), find the Input Process order control, and read
   nuke.activeViewer().node()['viewerInputOrder'].values() in the Script
   Editor to get the exact strings -- then this file's ORDER_BEFORE constant
   just needs updating to match.
3. OCIOCDLTransform / OCIOFileTransform knob names ('read_from_file',
   'working_space', etc.) -- set through a try/except helper so a wrong name
   logs and is skipped rather than crashing the build; check the Script
   Editor output after a build for any "could not set" lines and fix the
   knob name here if Nuke's version differs.
4. On Linux Nuke sessions: core/roots.yml has linux_path: null for the
   primary storage root, so ANY Toolkit-template-based path (plates folder
   included) fails to resolve there today. That is a real, pre-existing
   pipeline gap, not something this tool works around.
--------------------------------------------------------------------------
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUP_NAME = "BUF_DailiesViewer"

# The primary storage root's mac_path, from core/roots.yml. Used only to
# translate the bake's hardcoded-macOS SHOW_LUT_DIR/SHOW_LUT_PATH constants
# into whatever platform this Nuke session is actually running on, via
# tk.roots["primary"]. Keep this in step with core/roots.yml by hand -- it
# is duplicated here on purpose (this file must not import Toolkit config
# YAML to get one string) but ANY roots.yml change needs a matching edit
# here or this translation silently stops working. Flag a mismatch, per
# CLAUDE_INSTRUCTIONS.md rule 7's spirit -- don't silently guess.
MAC_PRIMARY_ROOT = "/Volumes/atv-post-lucid3/atv-buffalo-s03"

# Best-guess Python-visible values for the Input Process order knob. See
# "THINGS TO VERIFY" item 2 above -- update if the real Nuke reports
# different strings.
ORDER_BEFORE_VIEWER_PROCESS = "before viewer process"

# Name candidates, in order, for every OCIO name this chain needs. Verified
# 2026-09-17 against the actual built-in configs with PyOpenColorIO:
#
#   ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1   (what nuke/init.py pins)
#       ACEScg      -> "ACEScg"      aliases: ACES - ACEScg, lin_ap1
#       ARRI LogC4  -> "ARRI LogC4"  aliases: arri_logc4
#       displays    : Rec.1886 Rec.709 - Display, sRGB - Display, ...
#                     NO "Gamma 2.2 Rec.709 - Display"
#       views       : ACES 1.0 - SDR Video, ...
#                     NO "ACES 2.0 - SDR 100 nits (Rec.709)"
#
#   ocio://studio-config-latest  == studio-config-v4.0.0_aces-v2.0_ocio-v2.5
#       ACEScg / ARRI LogC4 same names+aliases, and it DOES have
#       "Gamma 2.2 Rec.709 - Display" / "ACES 2.0 - SDR 100 nits (Rec.709)".
#
# So the colourspace stage works under either config, but the display/view
# FALLBACK names in qt_bake_oiio.py only exist in the ACES 2.0 config. Under
# the ACES 1.3 pin the 1.3 equivalents below are tried instead, and the
# substitution is logged -- that fallback is not the show look either way.
ACESCG_CANDIDATES = ("ACEScg", "ACES - ACEScg", "lin_ap1")
# Nuke's knobs list colourspaces by NAME, not by OCIO alias, so the display
# name goes first here; "arri_logc4" (the alias qt_bake_oiio.py passes to
# oiiotool, which does accept aliases) is kept as a fallback.
LOGC4_CANDIDATES = ("ARRI LogC4", "arri_logc4")
REC709_DISPLAY_CANDIDATES = ("Gamma 2.2 Rec.709 - Display",
                             "Rec.1886 Rec.709 - Display",
                             "sRGB - Display")
REC709_VIEW_CANDIDATES = ("ACES 2.0 - SDR 100 nits (Rec.709)",
                          "ACES 1.0 - SDR Video")


class DailiesViewerError(RuntimeError):
    """Something needed to build the dailies chain could not be found."""


_CACHE = {}


# ---------------------------------------------------------------------------
# Live-importing qt_bake_oiio.py and plate_reads.py
# ---------------------------------------------------------------------------

def _default_bake_search_paths():
    """qt_bake_oiio.py candidates, most specific first.

    hooks/ (this file's folder) and flow/alts/BUF_Mac_watcher/scripts/ are
    both under config/flow/ on the share -- hooks/ is
    config/flow/current/config/hooks/, so two hops up reaches config/flow/.
    Mirrors the relative-hop trick qt_bake_bridge.py uses from a different
    starting point, so it holds regardless of platform or mount syntax.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    env = os.environ.get("BUF_QT_BAKE")
    if env:
        candidates.append(env)
    for up in (2, 3, 4, 5):
        base = here
        for _ in range(up):
            base = os.path.dirname(base)
        candidates.append(os.path.join(
            base, "flow", "alts", "BUF_Mac_watcher", "scripts", "qt_bake_oiio.py"))
        candidates.append(os.path.join(
            base, "config", "flow", "alts", "BUF_Mac_watcher", "scripts",
            "qt_bake_oiio.py"))
    return candidates


_REQUIRED_BAKE_CALLABLES = (
    "find_shot_cdl", "cdl_as_cc", "resolve_lut_path", "resolve_show_lut",
    "shot_plates_dir", "is_shot_context",
)
_REQUIRED_BAKE_CONSTANTS = (
    "OCIO_LOGC4", "OCIO_REC709_DISPLAY", "OCIO_REC709_VIEW",
    "SHOW_LUT_DIR", "SHOW_LUT_PATH", "SHOW_LUTS_BY_CAMERA",
    "LUT_EXTENSIONS", "CDL_EXTENSIONS",
)


def _load_bake(path=None, force=False):
    """Import qt_bake_oiio.py by explicit file path. Raises, never guesses."""
    if not force and "bake" in _CACHE and (path is None or _CACHE.get("bake_path") == path):
        return _CACHE["bake"]

    tried = []
    resolved = None
    for candidate in ([path] if path else _default_bake_search_paths()):
        if candidate and os.path.isfile(candidate):
            resolved = candidate
            break
        tried.append(candidate)
    if resolved is None:
        raise DailiesViewerError(
            "Could not find qt_bake_oiio.py. Looked for:\n  %s\n"
            "Set BUF_QT_BAKE to its full path, or check the share is mounted."
            % "\n  ".join(str(t) for t in tried if t))

    spec = importlib.util.spec_from_file_location("_buf_dailies_qt_bake_oiio", resolved)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_buf_dailies_qt_bake_oiio"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DailiesViewerError("Found %s but it would not import: %s" % (resolved, exc))

    missing = [n for n in _REQUIRED_BAKE_CALLABLES if not callable(getattr(module, n, None))]
    missing += [n for n in _REQUIRED_BAKE_CONSTANTS if not hasattr(module, n)]
    if missing:
        raise DailiesViewerError(
            "%s is not the bake this tool understands -- missing: %s\n"
            "The bake has changed shape; this file needs updating to match."
            % (resolved, ", ".join(missing)))

    _CACHE["bake"] = module
    _CACHE["bake_path"] = resolved
    return module


def _load_plate_reads():
    """Import plate_reads.py by explicit path. Same folder as this file, so
    no relative-hop guessing is needed -- just the folder this file is in.
    """
    if "plate_reads" in _CACHE:
        return _CACHE["plate_reads"]
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "plate_reads.py")
    if not os.path.isfile(path):
        raise DailiesViewerError(
            "plate_reads.py not found next to dailies_viewer.py at %s -- "
            "both must live in the same hooks/ folder." % path)
    spec = importlib.util.spec_from_file_location("_buf_dailies_plate_reads", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_buf_dailies_plate_reads"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise DailiesViewerError("Found %s but it would not import: %s" % (path, exc))
    if not callable(getattr(module, "plates_dir_for_context", None)):
        raise DailiesViewerError(
            "%s has no plates_dir_for_context() -- it has changed shape; "
            "this file needs updating to match." % path)
    _CACHE["plate_reads"] = module
    return module


def _extract_cc_id(cc_path):
    """Read back the `id` attribute cdl_as_cc() wrote onto the correction it
    chose. Used only to learn which correction the bake's own selection rule
    picked -- never to build a colour transform from this file; the node
    itself reads the ORIGINAL .ccc/.cdl. Returns None if unreadable or the
    correction carries no id (cdl_as_cc() preserves whatever was there,
    which can be empty -- Nuke falls back to matching by file position when
    that happens, so None here is a legitimate outcome, not just a failure).
    """
    if not cc_path:
        return None
    try:
        import xml.etree.ElementTree as ET
        root = ET.parse(cc_path).getroot()
    except Exception as exc:
        print("[dailies_viewer] could not re-read id from %s: %s" % (cc_path, exc))
        return None
    return root.get("id") or None


# ---------------------------------------------------------------------------
# Cross-platform translation of the bake's hardcoded-macOS show-LUT paths
# ---------------------------------------------------------------------------

def _to_platform_path(mac_abs_path, tk):
    """Re-root a bake-hardcoded macOS path onto whatever platform tk is on.

    qt_bake_oiio.py's SHOW_LUT_DIR/SHOW_LUT_PATH are literal macOS paths
    under MAC_PRIMARY_ROOT. This computes the path relative to that root and
    rejoins it under tk.roots["primary"] -- the SAME primary storage root,
    resolved for whatever OS Nuke is actually running on. If the path is not
    under MAC_PRIMARY_ROOT, or the primary root has no path configured for
    this platform (Linux today -- see core/roots.yml linux_path: null),
    returns None rather than a path that looks plausible but is wrong.
    """
    if not mac_abs_path:
        return None
    try:
        rel = os.path.relpath(mac_abs_path, MAC_PRIMARY_ROOT)
    except ValueError:
        return None
    if rel.startswith(".."):
        return None
    try:
        primary = tk.roots.get("primary")
    except Exception:
        primary = None
    if not primary:
        return None
    return os.path.normpath(os.path.join(primary, rel))


# ---------------------------------------------------------------------------
# Resolving the chain for the current shot
# ---------------------------------------------------------------------------

def resolve_dailies_chain(tk, context, camera_override=None):
    """
    Work out this shot's CDL + LUT exactly the way qt_bake_oiio.py would,
    using a Toolkit-resolved (cross-platform-correct) plates folder instead
    of the bake's own macOS-hardcoded one.

    Returns a dict; see the keys set below. Never raises for an ordinary
    "nothing found" case -- those are recorded in "reasons" so the caller
    can show them. Raises DailiesViewerError only if the bake/plate_reads
    modules themselves can't be loaded.
    """
    result = {
        "ok": False,
        "reasons": [],
        "shot_code": None,
        "plates_dir": None,
        "cdl_raw_path": None,
        "cdl_cc_path": None,
        "cdl_cccid": None,
        "lut_path": None,
        "used_show_lut": False,
        "show_lut_reason": None,
        "display_fallback": False,
        "logc4_name": None,
        "display_name": None,
        "view_name": None,
    }

    if not (context and context.entity and context.entity.get("type") == "Shot"):
        result["reasons"].append("Not a Shot context.")
        return result

    m = _load_bake()
    pr = _load_plate_reads()

    shot_code = (context.entity or {}).get("name")
    result["shot_code"] = shot_code

    plates_dir = pr.plates_dir_for_context(tk, context)
    result["plates_dir"] = plates_dir
    if not plates_dir or not os.path.isdir(plates_dir):
        result["reasons"].append(
            "No plates folder resolved (or it does not exist on disk yet): %s"
            % plates_dir)
        plates_dir = None

    # ---- CDL: find_shot_cdl(plates_dir, shot_code) is plates_dir-parameterized
    # already -- call it unmodified, no monkeypatching needed.
    cdl_raw = m.find_shot_cdl(plates_dir, shot_code) if plates_dir else None
    result["cdl_raw_path"] = cdl_raw
    if cdl_raw:
        if cdl_raw.lower().endswith(".cc"):
            # Single bare correction -- nothing to select, no cccid needed.
            result["cdl_cc_path"] = cdl_raw
        else:
            # .ccc/.cdl: run the bake's own selection rule (via a throwaway
            # extracted file) purely to learn which correction id it picked.
            # The node itself will read the ORIGINAL file directly -- see the
            # "CDL LOADING" note at the top of this file.
            cdl_cc = m.cdl_as_cc(cdl_raw, tempfile.gettempdir())
            result["cdl_cc_path"] = cdl_cc
            if cdl_cc:
                result["cdl_cccid"] = _extract_cc_id(cdl_cc)
            else:
                result["reasons"].append(
                    "CDL found (%s) but could not be parsed to determine "
                    "which correction to use -- this stage will be skipped; "
                    "working image will be UNGRADED relative to dailies."
                    % cdl_raw)
    else:
        result["reasons"].append(
            "No CDL found under %s -- working image will be UNGRADED "
            "relative to dailies." % plates_dir)

    # ---- Per-shot LUT: resolve_lut_path(data) is NOT plates_dir-parameterized
    # -- it calls the bake's own shot_plates_dir(data), which is macOS-only.
    # Monkeypatch just that one function for the duration of this call so
    # every OTHER rule (BG-element priority, .cube-before-.lut, the
    # log-every-candidate ambiguity warning) still runs unmodified.
    lut_path = None
    if plates_dir:
        fake_data = {"type": "shot", "shot_code": shot_code}
        original_spd = m.shot_plates_dir
        m.shot_plates_dir = lambda _data, _pd=plates_dir: _pd
        try:
            lut_path = m.resolve_lut_path(fake_data)
        finally:
            m.shot_plates_dir = original_spd
    result["lut_path"] = lut_path

    camera = (camera_override or "").strip().lower()
    if not lut_path:
        show_data = {"camera": camera} if camera else {}
        show_lut_mac, why = m.resolve_show_lut(show_data)
        show_lut = _to_platform_path(show_lut_mac, tk)
        result["used_show_lut"] = True
        result["show_lut_reason"] = why
        if show_lut and os.path.exists(show_lut):
            result["lut_path"] = show_lut
        else:
            result["display_fallback"] = True
            reason = ("Show LUT missing (%s -> %s)" % (why, show_lut)
                      if show_lut else
                      "Show LUT path could not be translated to this "
                      "platform (%s) -- see core/roots.yml, primary root, "
                      "linux_path" % os.name)
            result["reasons"].append(
                "%s -- falling back to the generic ACES display transform, "
                "which is NOT the show look." % reason)

    result["logc4_name"] = m.OCIO_LOGC4
    result["display_name"] = m.OCIO_REC709_DISPLAY
    result["view_name"] = m.OCIO_REC709_VIEW
    result["ok"] = True
    return result


# ---------------------------------------------------------------------------
# Building the Nuke node chain
# ---------------------------------------------------------------------------

def _knob_choices(node, knob_name):
    """The values a knob offers, with Nuke's "family\tFamily/Name" display
    strings reduced to the plain name so they can be compared."""
    try:
        knob = node[knob_name]
    except Exception:
        return None, []
    try:
        raw = list(knob.values())
    except Exception:
        raw = []
    return knob, raw


def _match_choice(raw_values, desired):
    """Find `desired` among a knob's offered values, and return the string
    that is actually valid to pass to knob.setValue() for the matching entry.

    Nuke presents OCIO names in several shapes:
      - a bare name / "Family/Name" path (nuke-default) -- the WHOLE string
        is both what's shown and what's settable.
      - a tab-separated "value\tFamily/Name (Family)" display string (the
        ACES-alias configs) -- only the piece BEFORE the tab is a real,
        settable colorspace name; the rest is display-only annotation.

    Returning the full raw string for the second shape is what produced
    "Invalid input LUT selected: ACEScg\tACEScg (ACEScg)" (the tab renders
    invisibly and the trailing "(ACEScg)" gets clipped in Nuke's error
    banner, which is why it looked like a stray "[" after "ACEScg").
    """
    for value in raw_values:
        value = str(value)
        settable = value.split("\t", 1)[0].strip()
        pieces = set()
        for part in value.split("\t"):
            part = part.strip()
            if not part:
                continue
            pieces.add(part)
            pieces.add(part.split("/")[-1])
        if desired in pieces:
            return settable
    return None


def _set_colorspace_knob(node, knob_name, candidates, what=None):
    """Set an OCIO colorspace/display/view knob to the FIRST of `candidates`
    this config actually offers. Mirrors plate_reads.py's
    resolve_plate_colorspace defensive pattern -- never assume a name from
    qt_bake_oiio.py exists in whatever OCIO config Nuke is actually running
    (see the config note at the top of this file). Returns the name used,
    or None; logs on failure.
    """
    if isinstance(candidates, str):
        candidates = (candidates,)
    label = what or knob_name
    knob, raw = _knob_choices(node, knob_name)
    if knob is None:
        print("[dailies_viewer] %s has no knob %r in this Nuke version"
              % (node.Class(), knob_name))
        return None

    for index, desired in enumerate(candidates):
        match = _match_choice(raw, desired) if raw else desired
        if match is None:
            continue
        try:
            knob.setValue(match)
        except Exception as exc:
            print("[dailies_viewer] %s.%s: could not set to %r: %s"
                  % (node.Class(), knob_name, match, exc))
            continue
        if index:
            print("[dailies_viewer] %s: %r is not in this config; used %r "
                  "instead" % (label, candidates[0], desired))
        return desired

    print("[dailies_viewer] %s.%s: none of %s exist in this Nuke session's "
          "active OCIO config. Offered: %s"
          % (node.Class(), knob_name, list(candidates), raw))
    return None


def _try_set(node, knob_name, value):
    """Best-effort knob set for stuff that varies across Nuke versions
    (OCIOCDLTransform/OCIOFileTransform knobs) -- log and continue, never
    raise, since the node still does SOMETHING useful without it.
    """
    try:
        node[knob_name].setValue(value)
        return True
    except Exception as exc:
        print("[dailies_viewer] %s: could not set %s=%r (%s) -- check this "
              "Nuke version's knob name" % (node.Class(), knob_name, value, exc))
        return False


def check_session_config():
    """Is this Nuke session on an ACES config at all?

    The chain is only meaningful if the script's working space is ACEScg:
    the bake's grade is applied in LogC4 reached FROM ACEScg. A script still
    on Nuke's default config (working space "linear", colorspaces named
    "Colorspaces/ARRILogC4" etc.) has no ACEScg to convert from, so building
    the chain there produces a wrong picture rather than a dailies match.

    nuke/init.py pins the studio ACES config with nuke.knobDefault(), which
    applies to NEW scripts only -- a script saved earlier keeps whatever
    Root.colorManagement / OCIO_config it was saved with. That is the most
    likely reason for a session that is not on it.

    Returns (ok, message).
    """
    import nuke

    root = nuke.root()
    bits = []
    for name in ("colorManagement", "OCIO_config", "customOCIOConfigPath"):
        try:
            bits.append("%s=%r" % (name, root[name].value()))
        except Exception:
            bits.append("%s=<no knob>" % name)
    state = ", ".join(bits)

    probe = nuke.createNode("OCIOColorSpace", inpanel=False)
    try:
        _knob, raw = _knob_choices(probe, "in_colorspace")
        found = _match_choice(raw, ACESCG_CANDIDATES[0]) if raw else None
        if found is None:
            for alias in ACESCG_CANDIDATES[1:]:
                found = _match_choice(raw, alias)
                if found is not None:
                    break
    finally:
        nuke.delete(probe)

    if found is not None:
        return True, state
    return False, (
        "This script is not on the studio ACES OCIO config, so there is no "
        "ACEScg working space to build a dailies match from.\n\n%s\n\n"
        "nuke/init.py pins the config for NEW scripts only -- a script saved "
        "before that pin keeps its own setting. Check Project Settings > "
        "Color. Switching an existing script's config also changes what every "
        "existing Read node's colorspace name means, so that is a decision to "
        "make deliberately, not something this tool will do for you." % state)


def build_dailies_viewer_group(chain):
    """
    (Re)build the BUF_DailiesViewer Group from a resolve_dailies_chain()
    result. Deletes any prior group of the same name first, so this is
    idempotent -- safe to call again after a redelivery or a shot switch.

    Chain, in order (matches qt_color_trace.py section 6 exactly):
      Input
        -> OCIOColorSpace   ACEScg -> arri_logc4        (if verified)
        -> OCIOCDLTransform file=<original .ccc/.cdl/.cc> (if a CDL was found)
            cccid=<matched correction>
        -> OCIOFileTransform file=<lut>                  (if a LUT was found)
           OR OCIODisplay   display/view fallback        (if no LUT at all)
        -> Clamp 0..1                                    (the bake clamps here)
      Output

    Returns the Group node.
    """
    import nuke

    existing = nuke.toNode(GROUP_NAME)
    if existing is not None:
        nuke.delete(existing)

    grp = nuke.createNode("Group", inpanel=False)
    grp.setName(GROUP_NAME)
    grp["label"].setValue("Dailies match\n(CDL + LUT preview)")

    notes = nuke.String_Knob("BUF_dailies_notes", "notes")
    notes.setValue("; ".join(chain.get("reasons", [])) or "matched cleanly")
    notes.setEnabled(False)
    grp.addKnob(notes)

    with grp:
        inp = nuke.createNode("Input", inpanel=False)
        last = inp

        cs = nuke.createNode("OCIOColorSpace", inpanel=False)
        used_in = _set_colorspace_knob(cs, "in_colorspace", ACESCG_CANDIDATES,
                                       what="working space")
        used_out = _set_colorspace_knob(cs, "out_colorspace", LOGC4_CANDIDATES,
                                        what="LogC4")
        if not (used_in and used_out):
            # REFUSE. The CDL and the .cube both expect LogC4; feeding them
            # scene-linear ACEScg instead bakes a dark, contrasty picture that
            # looks like a broken grade rather than a missing stage. A partial
            # chain here is worse than no chain.
            nuke.delete(cs)
            nuke.delete(inp)
            nuke.delete(grp)
            raise DailiesViewerError(
                "Could not build the working-space -> LogC4 stage: %s not "
                "found in this Nuke session's active OCIO config.\n\n"
                "Refusing to build the rest of the chain -- the CDL and the "
                "show/per-shot LUT both expect LogC4 input, so without this "
                "stage the Viewer would show a wrong (dark, contrasty) image "
                "rather than a dailies match.\n\n"
                "Run Trace Dailies Match and check the Script Editor for the "
                "list of names this config does offer."
                % ("ACEScg" if not used_in else "ARRI LogC4"))
        cs.setInput(0, last)
        last = cs

        if chain.get("cdl_cc_path"):
            # Point at the ORIGINAL .ccc/.cdl/.cc (never the throwaway
            # extracted file -- that path can go stale after the script is
            # reopened). `cccid` on Nuke's built-in OCIOCDLTransform is a
            # plain free-text (EvalString) knob, not an enum -- confirmed via
            # nuke_cdl_cccid_probe.py 2026-09-18, see the CDL LOADING
            # correction note at the top of this file. So the id
            # _extract_cc_id() already read out of THIS SAME source file is
            # written directly; there is no Nuke-side list to verify it
            # against, and none is needed.
            cdl = nuke.createNode("OCIOCDLTransform", inpanel=False)
            _try_set(cdl, "file", chain["cdl_raw_path"].replace("\\", "/"))
            _try_set(cdl, "read_from_file", True)
            # working_space: see the "working_space FIX" note above the LUT
            # stage below -- this is Nuke's declared working colourspace
            # (ACEScg for this pipeline), not the upstream buffer's actual
            # LogC4 encoding. Set explicitly rather than left on whatever
            # Nuke's own default happens to be.
            _set_colorspace_knob(cdl, "working_space", ACESCG_CANDIDATES,
                                 what="CDL working space")
            if chain.get("cdl_cccid"):
                _try_set(cdl, "cccid", chain["cdl_cccid"])
            cdl["label"].setValue("CDL: %s" % os.path.basename(chain["cdl_raw_path"] or ""))
            cdl.setInput(0, last)
            last = cdl

        if chain.get("lut_path") and not chain.get("display_fallback"):
            ft = nuke.createNode("OCIOFileTransform", inpanel=False)
            _try_set(ft, "file", chain["lut_path"].replace("\\", "/"))
            # working_space FIX (2026-09-18, Mark's real-Nuke find): this
            # knob is NOT "what colourspace is the incoming buffer actually
            # encoded in" (it was set to LogC4 on that assumption, matching
            # what the OCIOColorSpace node upstream actually produces, and
            # matching how qt_bake_oiio.py's oiiotool --ociofiletransform
            # CLI call treats its own working-space argument) -- on Nuke's
            # OCIOFileTransform node it instead reads as Nuke's DECLARED
            # working colourspace for the script (ACEScg here), independent
            # of whatever the immediate upstream node produced. Setting it
            # to LogC4 was the actual cause of the wrong grade: Mark
            # confirmed ACEScg is correct by testing on the real Nuke. The
            # CDL node above was already fine because it was never
            # explicitly touched and Nuke's own default already matched
            # Root's working space -- now set explicitly for both, so
            # neither depends on an implicit default.
            _set_colorspace_knob(ft, "working_space", ACESCG_CANDIDATES,
                                 what="LUT working space")
            ft["label"].setValue(
                ("show LUT" if chain.get("used_show_lut") else "per-shot LUT")
                + ": " + os.path.basename(chain["lut_path"]))
            ft.setInput(0, last)
            last = ft
        elif chain.get("display_fallback"):
            disp = nuke.createNode("OCIODisplay", inpanel=False)
            ok_d = _set_colorspace_knob(disp, "display",
                                        REC709_DISPLAY_CANDIDATES,
                                        what="fallback display")
            ok_v = _set_colorspace_knob(disp, "view", REC709_VIEW_CANDIDATES,
                                        what="fallback view")
            if ok_d and ok_v:
                disp["label"].setValue("fallback display transform\n(NOT the show look)")
                disp.setInput(0, last)
                last = disp
            else:
                nuke.delete(disp)
                print("[dailies_viewer] could not verify the display/view "
                      "fallback names in this OCIO config either -- chain "
                      "ends at LogC4 with NO display transform. See the "
                      "ACES 1.3 / '-latest' divergence note at the top of "
                      "this file.")

        clamp = nuke.createNode("Clamp", inpanel=False)
        _try_set(clamp, "minimum", 0.0)
        _try_set(clamp, "maximum", 1.0)
        _try_set(clamp, "channels", "rgb")
        clamp.setInput(0, last)

        out = nuke.createNode("Output", inpanel=False)
        out.setInput(0, clamp)

    return grp


# ---------------------------------------------------------------------------
# Wiring into the Viewer's Input Process
# ---------------------------------------------------------------------------

def enable_dailies_viewer(vnode=None):
    """Point the given (or active) Viewer's Input Process at the group,
    turn it on, run it BEFORE the Viewer Process, and blank the Viewer
    Process to raw so the studio ACES Rec.709 transform is not applied on
    top of a chain that already ends in display-referred Rec.709. The
    previous Viewer Process value is stashed on the group so it can be put
    back exactly by disable_dailies_viewer().
    """
    import nuke

    grp = nuke.toNode(GROUP_NAME)
    if grp is None:
        raise DailiesViewerError(
            "No %s node yet -- build the chain first." % GROUP_NAME)

    if vnode is None:
        v = nuke.activeViewer()
        if v is None:
            raise DailiesViewerError("No active Viewer.")
        vnode = v.node()

    stash = grp.knob("BUF_prev_viewer_process")
    if stash is None:
        stash = nuke.String_Knob("BUF_prev_viewer_process", "")
        stash.setVisible(False)
        grp.addKnob(stash)

    active_knob = vnode.knob("BUF_dailies_active")
    already_active = bool(active_knob and active_knob.value())
    if not already_active:
        try:
            stash.setValue(vnode["viewerProcess"].value())
        except Exception:
            stash.setValue("")

    try:
        vnode["input_process"].setValue(True)
        vnode["input_process_node"].setValue(grp.name())
    except Exception as exc:
        raise DailiesViewerError(
            "Could not set input_process/input_process_node on the Viewer "
            "-- these knob names may differ in this Nuke version: %s" % exc)

    try:
        vnode["viewerInputOrder"].setValue(ORDER_BEFORE_VIEWER_PROCESS)
    except Exception as exc:
        print("[dailies_viewer] could not set viewerInputOrder=%r (%s) -- "
              "open the Viewer settings (press S) and set the Input Process "
              "order to run BEFORE the Viewer Process by hand; then read "
              "nuke.activeViewer().node()['viewerInputOrder'].values() in "
              "the Script Editor and update ORDER_BEFORE_VIEWER_PROCESS in "
              "this file to match." % (ORDER_BEFORE_VIEWER_PROCESS, exc))

    try:
        vnode["viewerProcess"].setValue("None")
    except Exception as exc:
        print("[dailies_viewer] could not blank viewerProcess (%s) -- the "
              "studio ACES Rec.709 transform may still be applied ON TOP "
              "of this chain's own Rec.709 output. Set the Viewer's "
              "viewerProcess to 'None' by hand while previewing dailies "
              "match." % exc)

    if active_knob is None:
        active_knob = nuke.Boolean_Knob("BUF_dailies_active", "")
        active_knob.setVisible(False)
        vnode.addKnob(active_knob)
    active_knob.setValue(True)


def disable_dailies_viewer(vnode=None):
    """Turn the Viewer's Input Process off and restore whatever
    viewerProcess it had before enable_dailies_viewer() touched it.
    """
    import nuke

    if vnode is None:
        v = nuke.activeViewer()
        if v is None:
            raise DailiesViewerError("No active Viewer.")
        vnode = v.node()

    try:
        vnode["input_process"].setValue(False)
    except Exception as exc:
        print("[dailies_viewer] could not clear input_process: %s" % exc)

    grp = nuke.toNode(GROUP_NAME)
    prev = None
    if grp is not None:
        stash = grp.knob("BUF_prev_viewer_process")
        if stash is not None:
            prev = stash.value()

    if prev:
        try:
            vnode["viewerProcess"].setValue(prev)
        except Exception as exc:
            print("[dailies_viewer] could not restore viewerProcess to %r "
                  "(%s) -- set it back to the studio default by hand." % (prev, exc))

    active_knob = vnode.knob("BUF_dailies_active")
    if active_knob is not None:
        active_knob.setValue(False)


# ---------------------------------------------------------------------------
# Zero-mutation trace (safe to run any time, changes nothing)
# ---------------------------------------------------------------------------

def _section(title):
    print("\n" + title)
    print("-" * len(title))


def trace_dailies_match(camera_override=None):
    """Print the same decision chain qt_color_trace.py prints for a flag,
    but sourced from the LIVE Toolkit context instead of a flag file, and
    without touching the node graph. Run this before build_dailies_viewer_
    group() to see what it's about to do, or any time to sanity-check why
    the working image might not be matching dailies.
    """
    import nuke
    import sgtk

    engine = sgtk.platform.current_engine()
    if engine is None:
        nuke.message("Flow Production Tracking is not running in this session.")
        return
    context = engine.context
    tk = engine.sgtk

    _section("0. This session's colour management")
    ok, state = check_session_config()
    print("  %s" % state)
    if ok:
        print("  ACEScg is available -- the chain can be built.")
    else:
        print("  ^^ NOT an ACES config: nothing below can be applied.")

    _section("1. Context")
    if not (context and context.entity and context.entity.get("type") == "Shot"):
        print("  Not a Shot context: %s" % context)
        nuke.message("Dailies match trace only works in a Shot context.\n\n"
                     "Current context: %s" % context)
        return
    print("  shot          : %s" % context.entity.get("name"))
    print("  context       : %s" % context)

    chain = resolve_dailies_chain(tk, context, camera_override=camera_override)

    _section("2. Plates / CDL")
    print("  plates folder : %s" % chain["plates_dir"])
    if chain["cdl_raw_path"]:
        print("  CDL file      : %s" % chain["cdl_raw_path"])
        if chain["cdl_cccid"]:
            print("  cccid         : %s (Nuke reads the file above directly)"
                  % chain["cdl_cccid"])
        elif not chain["cdl_raw_path"].lower().endswith(".cc"):
            print("  cccid         : none determined -- Nuke will use its "
                  "own default selection for this file")
    else:
        print("  CDL           : none found")

    _section("3. LUT")
    if chain["lut_path"]:
        kind = "show LUT (%s)" % chain["show_lut_reason"] if chain["used_show_lut"] else "per-shot LUT"
        print("  %-14s: %s" % (kind, chain["lut_path"]))
    elif chain["display_fallback"]:
        print("  FALLBACK      : generic display transform (%s / %s)"
              % (chain["display_name"], chain["view_name"]))
    else:
        print("  LUT           : none")

    _section("4. Nuke chain this would build")
    print("  Input")
    print("  OCIOColorSpace   ACEScg -> %s   (verified against this OCIO "
          "config at build time)" % chain["logc4_name"])
    if chain["cdl_cc_path"]:
        print("  OCIOCDLTransform file: %s" % chain["cdl_raw_path"])
        if chain["cdl_cccid"]:
            print("                   cccid: %s" % chain["cdl_cccid"])
    else:
        print("  (no CDL stage)")
    if chain["lut_path"] and not chain["display_fallback"]:
        print("  OCIOFileTransform file: %s" % chain["lut_path"])
    elif chain["display_fallback"]:
        print("  OCIODisplay      display=%s view=%s"
              % (chain["display_name"], chain["view_name"]))
    else:
        print("  (no LUT/display stage)")
    print("  Clamp 0..1")
    print("  Output")

    if chain["reasons"]:
        _section("5. Notes")
        for r in chain["reasons"]:
            print("  - %s" % r)
    print()


# ---------------------------------------------------------------------------
# Menu commands
# ---------------------------------------------------------------------------

def _ask_camera():
    import nuke
    return nuke.getInput(
        "Camera override (blank = bake default / ARRI).\n"
        "The ShotGrid camera field for per-camera show LUTs isn't wired up "
        "yet -- type arri / red / sony to test one of those show LUTs.", "")


def _reload_self():
    """Re-exec THIS file fresh from disk and return the resulting module.

    Found 2026-09-18: engine_init.py and context_change.py only import
    dailies_viewer.py (and so only pick up an on-disk edit) at registration
    time -- engine start, or the NEXT context change. The menu commands
    (cmd_trace etc. below) are plain function references handed to
    register_command() at that moment, and a Python function keeps using
    the module namespace it was DEFINED in. So a fix saved to this file
    while Nuke is already open on a shot kept silently running the
    previously-imported version -- same symptoms, same log lines, no
    context change in between to explain it. This bit Mark directly: the
    cccid fix landed on disk, but the open session re-ran the pre-fix code
    because no context change had happened since.

    Every actual command below now reloads this file fresh before doing
    anything, so a menu click always runs current on-disk code -- no
    context change or Nuke restart required. Cost is negligible (re-parsing
    one ~40KB .py file); deliberately NOT cached in sys.modules here so it
    can never itself go stale.
    """
    here = os.path.abspath(__file__)
    spec = importlib.util.spec_from_file_location("dailies_viewer", here)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_trace():
    trace_dailies_match(camera_override=_ask_camera())


def _run_rebuild():
    import nuke
    import sgtk

    engine = sgtk.platform.current_engine()
    if engine is None:
        nuke.message("Flow Production Tracking is not running in this session.")
        return
    context = engine.context
    if not (context and context.entity and context.entity.get("type") == "Shot"):
        nuke.message("This works in a Shot context.\n\nCurrent context: %s" % context)
        return

    ok, state = check_session_config()
    if not ok:
        nuke.message(state)
        return

    camera = _ask_camera()
    chain = resolve_dailies_chain(engine.sgtk, context, camera_override=camera)
    try:
        grp = build_dailies_viewer_group(chain)
    except DailiesViewerError as exc:
        nuke.message(str(exc))
        return
    nuke.message(
        "Built/updated %s.\n\n%s"
        % (grp.name(), "\n".join(chain["reasons"]) or "Matched cleanly."))


def _run_enable():
    import nuke
    try:
        if nuke.toNode(GROUP_NAME) is None:
            _run_rebuild()
            if nuke.toNode(GROUP_NAME) is None:
                return
        enable_dailies_viewer()
        nuke.message(
            "Dailies-match Input Process is ON.\n\n"
            "If the picture does not look right, check the Script Editor "
            "output for any 'could not set' warnings -- some knob names "
            "vary by Nuke version and are logged rather than guessed.")
    except DailiesViewerError as exc:
        nuke.message(str(exc))


def _run_disable():
    import nuke
    try:
        disable_dailies_viewer()
        nuke.message("Dailies-match Input Process is OFF.")
    except DailiesViewerError as exc:
        nuke.message(str(exc))


# These four are what register() below actually hands to
# engine.register_command() -- each reloads this file fresh (see
# _reload_self()'s docstring) and dispatches into the reloaded module's
# _run_* implementation, so menu clicks can never run stale code.
def cmd_trace():
    _reload_self()._run_trace()


def cmd_rebuild():
    _reload_self()._run_rebuild()


def cmd_enable():
    _reload_self()._run_enable()


def cmd_disable():
    _reload_self()._run_disable()


def register(engine):
    """Register the menu commands with the Flow Production Tracking menu.
    Same pattern as plate_reads.py's register(engine) -- call this from
    wherever that one is already being called from.
    """
    engine.register_command(
        "Trace Dailies Match (this shot)",
        cmd_trace,
        {"short_name": "trace_dailies_match",
         "description": "Print the CDL/LUT decision chain for this shot, "
                        "the same way qt_color_trace.py does for a flag. "
                        "Changes nothing."},
    )
    engine.register_command(
        "Rebuild Dailies Viewer Chain",
        cmd_rebuild,
        {"short_name": "rebuild_dailies_viewer",
         "description": "(Re)build the BUF_DailiesViewer node group for "
                        "this shot's current CDL/LUT, without turning it on."},
    )
    engine.register_command(
        "Enable Dailies Viewer (Input Process)",
        cmd_enable,
        {"short_name": "enable_dailies_viewer",
         "description": "Build if needed, then show this shot's dailies "
                        "match in the Viewer via Input Process."},
    )
    engine.register_command(
        "Disable Dailies Viewer (Input Process)",
        cmd_disable,
        {"short_name": "disable_dailies_viewer",
         "description": "Turn the dailies-match Input Process off and "
                        "restore the studio Viewer Process."},
    )
