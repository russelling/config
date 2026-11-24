#!/usr/bin/env python3
"""
Test qt_frame_preview WITHOUT Nuke -- including a real end-to-end bake.

The payload shaping is pure and tested directly. The bake itself is then run
for real against a synthetic ACEScg sequence, with oiiotool/ffmpeg pointed at
whatever this machine has, so the whole path is exercised rather than mocked.

    python3 test_qt_frame_preview.py <qt_bake_oiio.py> [oiiotool] [ffmpeg]
"""
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_bake_bridge as bridge
import qt_frame_preview as qfp

# numpy/OpenImageIO are only needed to SYNTHESISE a test sequence. The payload
# logic -- which is most of what can go wrong -- must be runnable on any
# machine, including the Mac Studio's Flow desktop Python, so they are
# imported lazily and the end-to-end section skips itself without them.
try:
    import numpy as np
    import OpenImageIO as oiio
    CAN_SYNTHESISE = True
except ImportError:
    CAN_SYNTHESISE = False

FAILS = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("  ok  " if cond else " FAIL ", label,
                       ("  -- %s" % detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


FLAG = {
    "type": "shot",
    "shot_code": "301_999_0010",
    "step": "comp",
    "version": 7,
    "artist": "Mark",
    "date": "2026-09-15",
    "frame_first": 1001,
    "frame_last": 1010,
    "start_timecode": "01:00:00:00",
    "project_id": 1343, "entity_id": 1, "entity_type": "Shot",
}


def make_seq(folder, first, last, w=2048, h=858):
    os.makedirs(folder, exist_ok=True)
    pattern = os.path.join(folder, "plate.%04d.exr")
    for f in range(first, last + 1):
        img = np.zeros((h, w, 3), np.float32)
        img[:, :, 0] = np.linspace(0, 0.8, w, dtype=np.float32)[None, :]
        img[:, :, 1] = np.linspace(0, 0.8, h, dtype=np.float32)[:, None]
        img[:, :, 2] = 0.18 + 0.01 * (f - first)
        out = oiio.ImageOutput.create(pattern % f)
        out.open(pattern % f, oiio.ImageSpec(w, h, 3, "half"))
        out.write_image(img)
        out.close()
    return pattern


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    bake_path = sys.argv[1]
    oiiotool = sys.argv[2] if len(sys.argv) > 2 else "/usr/local/bin/oiiotool"
    ffmpeg = sys.argv[3] if len(sys.argv) > 3 else "/usr/bin/ffmpeg"

    m = bridge.load(bake_path, force=True)
    m.OIIOTOOL = oiiotool
    m.FFMPEG = ffmpeg
    print("bake: %s\n  oiiotool=%s\n  ffmpeg=%s\n" % (bake_path, oiiotool, ffmpeg))

    # ---------------- payload shaping ----------------
    print("--- payload ---")
    p, notes = qfp.build_payload(FLAG, mode="frame", frame=1005, module=m)
    check("frame mode trims the range to one frame",
          p["frame_first"] == 1005 and p["frame_last"] == 1005)
    check("frame mode turns the slate off", p["include_slate"] is False)
    check("review proxy skipped", p["review_proxy"] is False)
    check("timecode advanced by 4 frames (1005-1001)",
          p["start_timecode"] == "01:00:00:04", p["start_timecode"])
    check("the caller's dict is not mutated",
          FLAG["frame_first"] == 1001 and "include_slate" not in FLAG)
    check("every change is reported", len(notes) >= 3, str(notes))

    p0, _ = qfp.build_payload(FLAG, mode="frame", frame=1001, module=m)
    check("first frame leaves the timecode alone",
          p0["start_timecode"] == "01:00:00:00")

    pf, nf = qfp.build_payload(FLAG, mode="full", module=m)
    check("full mode keeps the real range",
          pf["frame_first"] == 1001 and pf["frame_last"] == 1010)
    check("full mode keeps the slate", "include_slate" not in pf
          or pf["include_slate"] is not False)

    no_tc = dict(FLAG); no_tc.pop("start_timecode")
    pn, _ = qfp.build_payload(no_tc, mode="frame", frame=1005, module=m)
    check("no flag timecode -> none invented",
          "start_timecode" not in pn or not pn["start_timecode"])

    for bad, label in ((9999, "frame outside the range is refused"),):
        try:
            qfp.build_payload(FLAG, mode="frame", frame=bad, module=m)
            check(label, False, "no error raised")
        except qfp.PreviewError:
            check(label, True)
    try:
        qfp.build_payload(FLAG, mode="sideways", module=m)
        check("unknown mode is refused", False)
    except qfp.PreviewError:
        check("unknown mode is refused", True)
    try:
        qfp.build_payload({"type": "shot"}, mode="frame", module=m)
        check("missing frame range is refused", False)
    except qfp.PreviewError:
        check("missing frame range is refused", True)

    # ---------------- naming ----------------
    print("\n--- naming ---")
    name = os.path.basename(qfp.output_path(FLAG, "frame", 1005))
    check("output is marked PREVIEW", "PREVIEW" in name, name)
    check("output names the frame", "f1005" in name, name)
    check("output is not written near the render",
          "/Volumes/" not in qfp.output_path(FLAG, "frame", 1005))

    # ---------------- a real bake ----------------
    print("\n--- real bake, end to end ---")
    if not CAN_SYNTHESISE:
        print("       skipped: needs numpy + OpenImageIO to build a test")
        print("       sequence. The payload checks above are the ones that")
        print("       catch real bugs; this section proves the plumbing.")
        print("\n%s" % ("ALL PASS (payload only)" if not FAILS
                        else "FAILED: %s" % ", ".join(FAILS)))
        return 0 if not FAILS else 1

    with tempfile.TemporaryDirectory() as td:
        pattern = make_seq(os.path.join(td, "exr"), 1001, 1010)
        data = dict(FLAG, exr_path_pattern=pattern)

        try:
            missing = qfp.render_preview(data=dict(data, exr_path_pattern=
                                                   os.path.join(td, "nope.%04d.exr")),
                                         mode="frame", frame=1005,
                                         out_dir=td, module=m)
            check("a missing EXR is caught before baking", False)
        except qfp.PreviewError as exc:
            check("a missing EXR is caught before baking",
                  "not on disk" in str(exc))

        try:
            res = qfp.render_preview(data=data, mode="frame", frame=1005,
                                     out_dir=os.path.join(td, "out"), module=m)
        except qfp.PreviewError as exc:
            check("the real bake ran", False, str(exc)[:400])
            res = None

        if res:
            check("the real bake ran", True)
            check("it wrote the preview", os.path.isfile(res["path"]))
            size = os.path.getsize(res["path"])
            check("the preview has content", size > 10000, "%d bytes" % size)
            r = subprocess.run(
                [ffmpeg.replace("ffmpeg", "ffprobe"), "-v", "error",
                 "-show_entries", "stream=width,height,nb_frames",
                 "-of", "default=noprint_wrappers=1", res["path"]],
                capture_output=True, text=True)
            info = dict(l.split("=", 1) for l in r.stdout.strip().splitlines()
                        if "=" in l)
            check("delivery size 1920x1080",
                  info.get("width") == "1920" and info.get("height") == "1080",
                  str(info))
            check("exactly one frame, no slate",
                  info.get("nb_frames") in ("1", "N/A"), str(info))
            print("       %.1fs  %s" % (res["seconds"], os.path.basename(res["path"])))
            for n in res["notes"]:
                print("       - %s" % n)

    print("\n%s" % ("ALL PASS" if not FAILS else "FAILED: %s" % ", ".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
