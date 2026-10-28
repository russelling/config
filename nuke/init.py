# Nuke startup (loaded via NUKE_PATH from ShotGrid Desktop before_app_launch).
# Registers studio film-back presets for Camera / CameraTracker, and sets the
# studio default OCIO config for brand-new scripts.

import nuke

try:
    import nukescripts.camerapresets as camerapresets
except ImportError:
    camerapresets = None


# ── Default OCIO config for new scripts ──────────────────────────────────────
# STUDIO_COLOR_DEFAULTS_APPLIED_2026_09_17
# knobDefault only changes what a NEW Root node gets when created -- it does
# not touch scripts that already have these knobs saved, so opening an older
# script keeps whatever config it was authored with. The onScriptLoad
# callback below (STUDIO COLOR DEFAULTS section) additionally forces these
# same values onto every script as it opens, new or old, per Mark's request
# 2026-09-16/17.
#
# STUDIO_OCIO_PINNED_ACES13_2026_09_17
# UPDATE 2026-09-17: the "-latest" alias below turned out to not even load
# in the real, on-disk Nuke -- Nuke 15.1v6 bundles PyOpenColorIO 2.2.1, and
# the "-latest" shortcut aliases were only added to OCIO's built-in config
# registry in 2.3. Every real Nuke session was hitting "Error loading
# custom OCIO config" on open and silently falling back to nuke-default
# the whole time this pointed at "ocio://studio-config-latest".
#
# Confirmed via a live PyOpenColorIO probe run inside the real Nuke
# (Windows, 15.1v6, PyOpenColorIO 2.2.1), 2026-09-17:
#   OK   ocio://studio-config-v1.0.0_aces-v1.3_ocio-v2.1  -> loads fine
#   FAIL ocio://studio-config-latest -> "Could not find
#        'studio-config-latest' in the built-in configurations."
#
# So this is now pinned to that exact, genuinely-ACES-1.3 build instead --
# which is also just what Mark asked for originally (ACES 1.3), rather
# than whatever "-latest" drifts to next. A versioned built-in name like
# this one is meant to stay resolvable across future OCIO releases for
# backward compatibility, so this should not need to move again the way
# "-latest" already did once (see the superseded note below, kept for
# history). If the show ever deliberately moves to ACES 2.0, change this
# one line -- and audit every other consumer still pointed at the live
# "-latest" alias (qt_bake_oiio.py, plate_reads.py, generate_turntable.py,
# staging.py -- see pipeline-folder-audit-2026-09-13.md, finding F-05),
# since they would otherwise disagree with Nuke about which ACES version
# is live.
#
# SUPERSEDED NOTE, kept for history (accurate as of 2026-09-17, before the
# above): this comment previously said "ocio://studio-config-latest is the
# same built-in ACES 1.3 Studio config reference qt_bake_oiio.py already
# uses" -- true when first written, no longer true once the built-in
# registry moved "-latest" forward to studio-config-v4.0.0_aces-v2.0_ocio-
# v2.5 (ACES 2.0) -- and it turned out Nuke's own bundled OCIO (2.2.1)
# didn't even recognize the alias at all.
# STUDIO_OCIO_FILE_2026_10_13
# Nuke 17.1 (the Deadline farm) rejects an ocio:// URI in
# Root.customOCIOConfigPath ("invalid OpenColorIO config file ... Config will
# not be loaded") although its own OCIO 2.4.2 resolves the URI fine -- every
# ACES name in a farm render was then unknown and the job died. So Nuke now
# points at a FILE: the same built-in config, serialised with PyOpenColorIO
# 2.4.2, kept next to this file in ocio/. A plain path loads in 17.0 and 17.1,
# and Deadline path mapping translates it for the Linux workers.
_STUDIO_OCIO_BUILTIN = "studio-config-v1.0.0_aces-v1.3_ocio-v2.1"


def _studio_ocio_config_path():
    import os
    dirs = []
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    try:
        dirs.extend(nuke.pluginPath())
    except Exception:
        pass
    for folder in dirs:
        path = os.path.join(folder, "ocio", _STUDIO_OCIO_BUILTIN + ".ocio")
        if os.path.isfile(path):
            path = path.replace("\\", "/")
            nuke.tprint("[studio-color-defaults] OCIO config file: %s" % path)
            return path
    nuke.tprint(
        "[studio-color-defaults] WARNING: ocio/%s.ocio not found next to "
        "init.py (searched %s). Using ocio://%s -- Deadline renders on Nuke "
        "17.1 WILL fail to load colour." % (
            _STUDIO_OCIO_BUILTIN, dirs, _STUDIO_OCIO_BUILTIN))
    return "ocio://" + _STUDIO_OCIO_BUILTIN


_STUDIO_OCIO_CONFIG_PATH = _studio_ocio_config_path()

nuke.knobDefault("Root.colorManagement", "OCIO")
nuke.knobDefault("Root.OCIO_config", "custom")
nuke.knobDefault("Root.customOCIOConfigPath", _STUDIO_OCIO_CONFIG_PATH)


# ── Studio format + frame rate for new scripts ──────────────────────────────
# STUDIO_BUFFALO_MENU_2026_09_29 -- Mark's request 2026-09-29: every new Nuke
# script starts at 23.976 fps on a 4608x2592 square-pixel working format
# (the ALEXA 35 4.6K 16:9 plate size, e.g. plates/<element>/4608x2592/).
# knobDefault only reaches NEW Root nodes -- a fresh Nuke, File > New, and
# Workfiles2 New File (scene_operation_tk-nuke.py scriptClear()s first) --
# so already-saved scripts keep the format and fps they were authored with,
# unlike the OCIO values above, which are forced on every open.
_STUDIO_FORMAT_NAME = "BUF_4608x2592"
_STUDIO_FORMAT = "4608 2592 1.0 %s" % _STUDIO_FORMAT_NAME
_STUDIO_FPS = "23.976"

if _STUDIO_FORMAT_NAME not in [f.name() for f in nuke.formats()]:
    nuke.addFormat(_STUDIO_FORMAT)
nuke.knobDefault("Root.format", _STUDIO_FORMAT_NAME)
nuke.knobDefault("Root.fps", _STUDIO_FPS)


# ── Studio color defaults enforced on every script open (new or old) ────────
# Mark's request 2026-09-16: every Nuke instance should open on the studio
# OCIO config with the Viewer already showing the Rec.709 ACES Output
# Transform, so the viewer shows the final display-referred look while the
# node graph itself stays in scene-linear ACES -- not just for brand-new
# scripts (knobDefault's normal limit), but for every script as it opens.
#
# This deliberately does NOT use the Viewer's "Input Process" feature.
# Input Process is a separate, optional stage layered on top of whatever
# the Viewer Process (this section) already shows -- it is the right tool
# for previewing a per-shot CDL/show LUT match to dailies (the same grade
# qt_bake_oiio.py bakes into review QTs), not for this base "show me the
# final color" default, which is exactly what the Viewer's own
# viewerProcess knob is for. The per-shot CDL/LUT-in-Nuke piece is still
# open work -- see CLAUDE_INSTRUCTIONS.md's WIP list: ep_shot_cdl /
# ep_shot_lut templates are defined but nothing resolves them yet. Once
# that lands, an Input Process node fed by that lookup is the natural way
# to get a true dailies-matching viewer preview; this section does not
# attempt that.
#
# The exact OCIO display/view names -- and how Nuke concatenates them into
# one viewerProcess knob value -- depend on whatever
# _STUDIO_OCIO_CONFIG_PATH resolves to. That is now pinned (see
# STUDIO_OCIO_PINNED_ACES13_2026_09_17 above) rather than a floating alias, but
# this still reads the live choices off a real Viewer node's knob instead
# of hardcoding a guessed string -- cheap insurance if the pin is ever
# deliberately changed later, and it is exactly what caught the earlier
# drift in the first place. Logs loudly (nuke.tprint, visible in the
# Script Editor / terminal) if nothing matches instead of silently
# leaving the default wrong.

# STUDIO_VIEWER_RAW_2026_10_27
# Mark's request 2026-10-27: the Viewer defaults to the Raw view on the
# Rec.1886 Rec.709 display (no ACES output transform) instead of
# ACES 1.0 - SDR Video. Same enforcement as before, different target.
# Change these two names, not the matching code, to move it again.
_STUDIO_VIEWER_DISPLAY = "Rec.1886 Rec.709 - Display"
_STUDIO_VIEWER_VIEW = "Raw"

# STUDIO_VIEWER_DEFAULT_FIX_2026_10_28
# Why Raw did not stick (found 2026-10-28): the old code resolved the
# viewerProcess name once per session, from a temporary Viewer made inside
# the Root's creation at Nuke start-up - before the studio OCIO config had
# loaded - and CACHED the miss, so it never retried and never set anything.
# Now the name is read off the Viewer being set, only a match is cached, and
# every Viewer is set as it is created (onCreate) as well as on script open.
_VIEWER_PROCESS_CACHE = {}


def _match_studio_viewer_process(choices):
    """Values in choices that are exactly _STUDIO_VIEWER_VIEW on
    _STUDIO_VIEWER_DISPLAY. Nuke spells them either
    "Raw (Rec.1886 Rec.709 - Display)" or "Rec.1886 Rec.709 - Display/Raw";
    stripping the display name and the punctuation leaves the view."""
    return [
        c for c in choices
        if _STUDIO_VIEWER_DISPLAY in c
        and c.replace(_STUDIO_VIEWER_DISPLAY, "").strip(" ()/") == _STUDIO_VIEWER_VIEW
    ]


def _studio_viewer_process(viewer):
    """The viewerProcess value to use, read off this Viewer's own knob.
    None (logged, NOT cached - retried on the next Viewer) if no match."""
    cached = _VIEWER_PROCESS_CACHE.get(_STUDIO_OCIO_CONFIG_PATH)
    if cached:
        return cached
    try:
        choices = list(viewer["viewerProcess"].values())
    except Exception as exc:
        nuke.tprint(
            "[studio-color-defaults] Could not read viewerProcess choices "
            "on %s: %s" % (viewer.name(), exc))
        return None
    candidates = _match_studio_viewer_process(choices)
    if not candidates:
        nuke.tprint(
            "[studio-color-defaults] WARNING: no viewerProcess choice on %s "
            "matched %s (%s) - will retry on the next Viewer. Available "
            "choices: %s" % (viewer.name(), _STUDIO_VIEWER_VIEW,
                             _STUDIO_VIEWER_DISPLAY, choices))
        return None
    if len(candidates) > 1:
        nuke.tprint(
            "[studio-color-defaults] WARNING: %d viewerProcess choices "
            "matched %s (%s), using the first: %s"
            % (len(candidates), _STUDIO_VIEWER_VIEW,
               _STUDIO_VIEWER_DISPLAY, candidates))
    result = candidates[0]
    _VIEWER_PROCESS_CACHE[_STUDIO_OCIO_CONFIG_PATH] = result
    nuke.knobDefault("Viewer.viewerProcess", result)
    nuke.tprint("[studio-color-defaults] Viewer default -> %r" % (result,))
    return result


def _set_studio_viewer(viewer):
    value = _studio_viewer_process(viewer)
    if value is None:
        return
    knob = viewer["viewerProcess"]
    if knob.value() == value:
        return
    try:
        nuke.tprint(
            "[studio-color-defaults] %s.viewerProcess: %r -> %r"
            % (viewer.name(), knob.value(), value))
        knob.setValue(value)
    except Exception as exc:
        nuke.tprint(
            "[studio-color-defaults] Could not set %s.viewerProcess to %r: %s"
            % (viewer.name(), value, exc))


def _on_viewer_created():
    try:
        _set_studio_viewer(nuke.thisNode())
    except Exception as exc:
        nuke.tprint("[studio-color-defaults] Viewer onCreate failed: %s" % exc)


def _apply_studio_color_defaults():
    root = nuke.root()

    for knob_name, target_value in (
        ("colorManagement", "OCIO"),
        ("OCIO_config", "custom"),
        ("customOCIOConfigPath", _STUDIO_OCIO_CONFIG_PATH),
    ):
        knob = root[knob_name]
        current_value = knob.value()
        if current_value != target_value:
            nuke.tprint(
                "[studio-color-defaults] Root.%s: %r -> %r"
                % (knob_name, current_value, target_value)
            )
            knob.setValue(target_value)

    for viewer in nuke.allNodes("Viewer"):
        _set_studio_viewer(viewer)


nuke.addOnScriptLoad(_apply_studio_color_defaults)
nuke.addOnCreate(_on_viewer_created, nodeClass="Viewer")

# (label, haperture_mm, vaperture_mm)
# Labels follow Nuke's built-in "Vendor/Model …" naming.
_STUDIO_FILM_BACKS = (
    # ARRI ALEXA 35 — sensor / recorded formats (mm)
    # Open Gate 4.6K 3:2: 27.99 x 19.22 (ARRI)
    ("Arri/Alexa 35 Open Gate", 27.99, 19.22),
    # 4.6K 16:9: 27.99 x 15.75 (ARRI)
    ("Arri/Alexa 35 16:9", 27.99, 15.75),
    # Nikon ZR — full-frame FX CMOS 35.9 x 23.9 (Nikon)
    ("Nikon/ZR", 35.9, 23.9),
)


def _register_studio_film_backs():
    if camerapresets is None:
        return
    existing = set(camerapresets.getLabels())
    for label, haperture, vaperture in _STUDIO_FILM_BACKS:
        if label in existing:
            continue
        camerapresets.addPreset(label, haperture, vaperture)
        existing.add(label)


_register_studio_film_backs()
