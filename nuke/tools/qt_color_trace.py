#!/usr/bin/env python3
"""
qt_color_trace.py -- what the QT bake actually did to one shot's colour.

RUN ON THE MAC STUDIO:

    "/Applications/Shotgun.app/Contents/Resources/Python3/bin/python3" \\
        qt_color_trace.py 301_017_0020_INH_v001

Give it a shot code, a version name, or the path to a flag .json. It finds the
flag, replays every colour decision through the BAKE'S OWN resolvers, and
prints:

  1. the decision chain, with the reason for each branch
  2. the exact oiiotool command the bake ran for one frame
  3. the equivalent Nuke graph, node by node, with the exact colorspace names

(3) is the point. If an in-Nuke recreation disagrees with the QT, the
difference is one of these nodes, and this prints the ones the bake really
used rather than the ones anyone assumed.

IT RUNS NOTHING AND CHANGES NOTHING. Every command is captured, not executed.

THE DIFFERENCE THAT CATCHES PEOPLE
----------------------------------
The bake converts ACEScg -> "arri_logc4", which is the FULL ARRI LogC4
colorspace: the LogC4 *curve* AND a gamut change from AP1 to ARRI Wide Gamut 4.

Nuke recreations usually reach for "Input - ARRI - Curve - LogC4 - EI800",
which is the curve ONLY -- it leaves the primaries in AP1.

Those are not the same transform. Same curve, different gamut, so the result
is close but wrong in saturated colour -- exactly the "slightly off" that is
hard to pin down by eye. qt_bake_oiio.py flags this in its own source
(see the NOTE above OCIO_LOGC4) and calls it unresolved.

Which one is correct depends on what the .cube expects. A LUT named
ARRILogC4_..._R709 conventionally expects LogC4 on AWG4, i.e. what the bake
does -- in which case the Nuke recreation needs the full colorspace, not the
curve. This script prints the LUT actually used so that can be settled.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_bake_bridge as bridge


def find_flag(needle, module, limit=20000):
    """Locate a flag by shot code / version name, or accept a path."""
    if os.path.isfile(needle):
        return needle
    roots = [module.SHOTS_ROOT]
    assets = getattr(module, "ASSETS_ROOT", None)
    if assets:
        roots.append(assets)
    hits, seen = [], 0
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                if not (fn.endswith(".json") and fn.startswith(
                        (".processed_", ".render_complete_", ".failed_"))):
                    continue
                seen += 1
                if seen > limit:
                    break
                if needle.lower() in fn.lower():
                    hits.append(os.path.join(dirpath, fn))
    if not hits:
        raise SystemExit(
            "No flag found matching %r under %s.\n"
            "Pass the flag's full path instead." % (needle, roots[0]))
    hits.sort(key=os.path.getmtime, reverse=True)
    if len(hits) > 1:
        print("%d flags match; using the most recent:" % len(hits))
        for h in hits[:6]:
            print("   %s" % h)
        print()
    return hits[0]


def section(title):
    print("\n" + title)
    print("-" * len(title))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("shot", help="shot code, version name, or flag .json path")
    ap.add_argument("--frame", type=int, help="which frame to trace (default: first)")
    ap.add_argument("--bake", help="path to qt_bake_oiio.py")
    args = ap.parse_args()

    m = bridge.load(args.bake) if args.bake else bridge.load()
    print("bake  : %s" % bridge.capabilities(m)["path"])

    flag_path = find_flag(args.shot, m)
    print("flag  : %s" % flag_path)
    with open(flag_path) as fh:
        data = json.load(fh)

    # ---------------------------------------------------------------- context
    section("1. Context")
    is_shot = m.is_shot_context(data)
    first, last = m.get_frame_range(data)
    frame = args.frame if args.frame is not None else first
    print("  type          : %s" % ("shot" if is_shot else data.get("type")))
    for key in ("shot_code", "entity_name", "episode", "sequence", "scene",
                "step", "version", "vendor_code", "camera"):
        if data.get(key) is not None:
            print("  %-14s: %r" % (key, data[key]))
    print("  frames        : %s-%s  (tracing %s)" % (first, last, frame))

    pipe = data.get("color_pipe")
    skip_color = bool(data.get("skip_color", False))
    if isinstance(pipe, dict):
        apply_log = bool(pipe.get("log_convert", True))
        apply_cdl_stage = bool(pipe.get("cdl", True))
        apply_lut_stage = bool(pipe.get("show_lut", True))
    else:
        apply_log = apply_cdl_stage = apply_lut_stage = not skip_color and pipe is not False
    print("  skip_color    : %s" % skip_color)
    print("  color_pipe    : log_convert=%s cdl=%s show_lut=%s"
          % (apply_log, apply_cdl_stage, apply_lut_stage))
    # CAMERA_LOG_SPACE_2026_10_27: the CDL + LUT run in the camera's log.
    src_space, src_why = m.source_colorspace(data)
    if hasattr(m, "resolve_log_space"):
        log_space, log_why = m.resolve_log_space(data)
    else:
        log_space, log_why = m.OCIO_LOGC4, "bake predates per-camera log spaces"
    print("  source space  : %s (%s)" % (src_space, src_why))
    print("  log space     : %s (%s)" % (log_space, log_why))

    # -------------------------------------------------------------------- CDL
    section("2. CDL  (shots only, applied in %s, before the LUT)" % log_space)
    plates_dir = m.shot_plates_dir(data)
    print("  plates folder : %s" % plates_dir)
    if plates_dir and not os.path.isdir(plates_dir):
        print("                  ^ DOES NOT EXIST")
    cdl_path = None
    if is_shot and not skip_color and apply_cdl_stage:
        cdl_path = m.find_shot_cdl(plates_dir, data.get("shot_code", ""))
        if cdl_path:
            print("  APPLIED       : %s" % cdl_path)
        else:
            print("  NOT APPLIED   : none found -- this QT is UNGRADED")
            print("                  If dailies are graded, that alone explains")
            print("                  a mismatch.")
    else:
        print("  NOT APPLIED   : %s" % (
            "skip_color" if skip_color else
            "color_pipe.cdl=false" if not apply_cdl_stage else
            "asset turntable"))

    # -------------------------------------------------------------------- LUT
    section("3. LUT  (%s -> Rec.709)" % log_space)
    # Is this camera even known to the bake? resolve_show_lut() prints a
    # warning and silently uses the ARRI LUT for anything it does not
    # recognise, which is a wrong look rather than a missing one.
    camera = str(data.get("camera") or "").strip().lower()
    known_cameras = sorted(a for aliases in m.SHOW_LUTS_BY_CAMERA for a in aliases)
    camera_known = (not camera) or any(camera in aliases
                                       for aliases in m.SHOW_LUTS_BY_CAMERA)
    if camera and not camera_known:
        print("  !! camera %r is NOT in the bake's SHOW_LUTS_BY_CAMERA." % camera)
        print("     It will silently use the ARRI LUT, which is the WRONG LOOK.")
        print("     Known: %s" % ", ".join(known_cameras))
        if camera.replace(" ", "") in ("nikonzr", "zr", "nikon"):
            print("     NOTE: the Nikon ZR records R3D with RED colour science")
            print("     (Nikon bought RED in 2024), so it should take the RED")
            print("     LUT. shows/lucid_s3.yml already maps it that way; the")
            print("     bake's own table does not. That is a real gap.")

    lut_path = None if skip_color else m.resolve_lut_path(data)
    used_display_fallback = False
    if lut_path:
        print("  per-shot LUT  : %s" % lut_path)
    elif not skip_color:
        show_lut, why = m.resolve_show_lut(data)
        print("  no per-shot LUT; show LUT %s" % why)
        print("  show LUT      : %s" % show_lut)
        if os.path.exists(show_lut):
            lut_path = show_lut
        else:
            used_display_fallback = True
            print("                  ^ MISSING")
            print("  FALLBACK      : generic display transform")
            print("                  '%s' / '%s'"
                  % (m.OCIO_REC709_DISPLAY, m.OCIO_REC709_VIEW))
            print("                  This is NOT the show look. It is the most")
            print("                  common cause of a QT that is close but wrong.")
    if not apply_lut_stage:
        print("  stage disabled: color_pipe.show_lut=false")

    # ------------------------------------------------------------- resolution
    section("4. Geometry")
    exr = m.frame_path(data.get("exr_path_pattern", ""), frame)
    print("  source frame  : %s" % exr)
    if not os.path.isfile(exr):
        print("                  ^ NOT ON DISK -- geometry cannot be traced")
        src_w = src_h = None
        par = 1.0
    else:
        src_w, src_h = m.read_resolution(exr)
        par = m.read_pixel_aspect(exr)
        print("  display window: %s" % ("%sx%s" % (src_w, src_h)
                                        if src_w and src_h else "could not read"))
        print("  pixel aspect  : %s%s" % (par, "" if abs(par - 1.0) <= 1e-3
                                          else "  -> anamorphic"))
    desqueeze_to = None
    blowup = 1.0
    if src_w and abs(par - 1.0) > 1e-3:
        desqueeze_to = (src_w, int(round(src_h / par)))
        blowup = getattr(m, "ANAMORPHIC_BLOWUP", 1.0)
        print("  de-squeeze to : %dx%d" % desqueeze_to)
        if blowup != 1.0:
            print("  blow-up       : %.2fx (anamorphic)" % blowup)
    fw, fh = (desqueeze_to if desqueeze_to else (src_w, src_h))
    frame_args = None
    if fw and fh:
        f = bridge.framing(fw, fh, blowup=blowup, module=m)
        frame_args = f["args"]
        print("  delivery      : %s" % f["note"])

    # ------------------------------------------------- the real oiiotool call
    section("5. The exact oiiotool command the bake runs for frame %s" % frame)
    captured = []
    real_run = m.run
    m.run = lambda cmd, label="": captured.append(list(cmd))
    try:
        m.bake_frame(
            exr, "/tmp/trace_frame.png", cdl_path=cdl_path, lut_path=lut_path,
            desqueeze_to=desqueeze_to, frame_args=frame_args,
            apply_log_convert=apply_log, apply_cdl=apply_cdl_stage,
            apply_lut_stage=apply_lut_stage,
            source_space=src_space, log_space=log_space,
        )
    except Exception as exc:
        print("  (could not build: %s)" % exc)
    finally:
        m.run = real_run
    for cmd in captured:
        print("  " + " \\\n    ".join(cmd))

    # ------------------------------------------------------- the Nuke version
    section("6. The same chain in Nuke")
    print("  Read                     %s" % os.path.basename(exr or "<source>"))
    print("    colorspace             ACEScg          (the EXRs are scene-linear ACEScg)")
    if apply_log and log_space == "dlog_dgamut":
        print("  OCIOFileTransform        nuke/ocio/ACEScg_to_DLog_DGamut.ctf")
        print("    working_space          ACEScg")
        print("    Nuke's ACES 1.3 config has no D-Log colourspace; the CTF is")
        print("    the bake's own dlog_dgamut transform, written out by OCIO.")
    elif apply_log and log_space != "arri_logc4":
        print("  OCIOColorSpace           ACEScg  ->  %s" % log_space)
    elif apply_log:
        print("  OCIOColorSpace           ACEScg  ->  ARRI LogC4")
        print("    !! NOT 'Input - ARRI - Curve - LogC4 - EI800'.")
        print("       The bake uses the FULL colorspace: LogC4 curve AND the")
        print("       AP1 -> ARRI Wide Gamut 4 gamut change. The curve-only")
        print("       option leaves the primaries in AP1 and is the usual")
        print("       cause of a close-but-wrong Nuke recreation.")
    else:
        print("  (no log convert -- color_pipe.log_convert=false)")
    if cdl_path and apply_cdl_stage:
        print("  OCIOCDLTransform         file: %s" % cdl_path)
        print("    (applied in %s, BEFORE the LUT)" % log_space)
    else:
        print("  (no CDL)")
    if lut_path and apply_lut_stage:
        print("  OCIOFileTransform        file: %s" % lut_path)
        print("    working space          %s in, Rec.709 out" % log_space)
        print("    interpolation          default (tetrahedral in OCIO)")
    elif used_display_fallback:
        print("  OCIODisplay              from %s" % log_space)
        print("    display                %s" % m.OCIO_REC709_DISPLAY)
        print("    view                   %s" % m.OCIO_REC709_VIEW)
    else:
        print("  (no LUT stage)")
    print("  Clamp                    0..1        <-- the bake clamps here")
    print("  (then 8-bit PNG, then ProRes 422 HQ -- so compare against the")
    print("   QT in a Rec.709 viewer, not against a float Nuke viewer)")

    section("7. Most likely causes of a mismatch, for this shot")
    causes = []
    if not cdl_path and is_shot and apply_cdl_stage and not skip_color:
        causes.append("No CDL was applied. Dailies almost certainly have one.")
    if used_display_fallback:
        causes.append("The show LUT was MISSING; a generic display transform "
                      "was used instead. This is not the show look.")
    if camera and not camera_known:
        causes.insert(0, "Camera %r is unknown to the bake, so it used the "
                         "ARRI LUT. If this is not an ARRI shot, that is the "
                         "mismatch." % camera)
    if lut_path and "Buffalo_V4" not in str(lut_path) and "cube" in str(lut_path):
        causes.append("A per-shot LUT was used (%s), not the show LUT -- check "
                      "dailies used the same one." % os.path.basename(lut_path))
    if log_space == "dlog_dgamut" and lut_path and "D-Log" in str(lut_path):
        causes.append("DJI: the LUT is DJI's own technical D-Log -> Rec.709, "
                      "not the show look. It will not match graded ARRI "
                      "dailies; set show_lut_path if it should.")
    if apply_log and log_space == "arri_logc4":
        causes.append("LogC4 flavour: the bake uses the full ARRI LogC4 "
                      "colorspace (curve + AWG4). A Nuke recreation using the "
                      "curve-only input transform will be close but wrong.")
    if not causes:
        causes.append("Nothing obviously wrong in the decision chain -- the "
                      "difference is likely in how it is being VIEWED "
                      "(Rec.709 vs float) or in the 8-bit/ProRes step.")
    for i, c in enumerate(causes, 1):
        print("  %d. %s" % (i, c))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
