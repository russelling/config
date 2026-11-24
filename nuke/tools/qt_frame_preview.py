"""
qt_frame_preview -- see the real QT frame, inside Nuke.

WHAT IT DOES
------------
Runs the ACTUAL bake -- `qt_bake_oiio.bake_sequence()`, the same function
qt_watcher calls -- on a trimmed payload, then drops the result into your
script as a Read node.

So this is not a reproduction of the QT. It is the QT, one frame of it.
Colour, framing, the 2.39 mask, burn-ins, the anamorphic blow-up, the slate:
all of it is whatever the bake does today, because the bake did it. There is
nothing here to keep in sync.

TWO MODES
---------
  "frame"  one picture frame, no slate. Seconds. Use this constantly.
  "full"   the real range, slate and all -- literally the QT that would be
           delivered. Minutes on a long sequence. Use it before a turnover.

Only the payload differs between them; both call the same bake.

WHAT IT DELIBERATELY CHANGES IN THE PAYLOAD
-------------------------------------------
Three things, all listed in the result so nothing is hidden:

  1. frame_first = frame_last = <your frame>   ("frame" mode only)
  2. include_slate = False                     ("frame" mode only -- a slate
     built from a one-frame range would show that frame three times, which
     would be a lie about the slate rather than a preview of it. Use "full"
     to see the real slate.)
  3. review_proxy = False  -- the h264 sidecar is delivery plumbing, not
     something you look at here. Skipping it does not touch the .mov pixels.
  4. camera = <your pick>  -- ONLY if you choose one in the panel's Camera
     pulldown, to see a shot through another camera's log space + LUT
     (e.g. dji). Left on "from_flag", the flag is baked as it is.
     CAMERA_LOG_SPACE_2026_10_27

TIMECODE, WHICH IS EASY TO GET WRONG
------------------------------------
The burn-in timecode advances per frame from the sequence start. Trim the
range to frame N and the bake would happily stamp the SEQUENCE start TC on
frame N -- right-looking, wrong. So when the flag carries an explicit
`start_timecode`, it is advanced by (N - original first) using the bake's own
tc_to_frames/frames_to_tc. When it does not, the bake reads the TC out of
frame N's own EXR, which is already correct.

The frame counter needs no such help: with no slate the bake sets
burnin_offset = first, and ffmpeg's n is 0, so the counter reads N.

WHERE THE FILE GOES
-------------------
A temp folder, named `..._PREVIEW.mov`. Never the review folder, never the
editorial drop, never a ShotGrid Version. qt_watcher stays the only thing
that brands review media; this only ever shows you what it will do.
"""

from __future__ import annotations

import json
import os
import tempfile
import time

try:
    from . import qt_bake_bridge as bridge
except (ImportError, ValueError):          # flat import, or run as a script
    import qt_bake_bridge as bridge


MODE_FRAME = "frame"
MODE_FULL = "full"


class PreviewError(RuntimeError):
    """Something the user needs to fix. Message is shown as-is."""


# ---------------------------------------------------------------------------
# Payload shaping -- pure, no Nuke, fully testable
# ---------------------------------------------------------------------------

def load_flag(path):
    if not path or not os.path.isfile(path):
        raise PreviewError("No flag file at:\n%s" % path)
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception as exc:
        raise PreviewError("Could not read %s:\n%s" % (path, exc))


def shift_timecode(tc, frames, module):
    """Advance a HH:MM:SS:FF timecode by N frames, using the bake's own maths."""
    if not tc:
        return tc
    try:
        return module.frames_to_tc(module.tc_to_frames(tc) + int(frames))
    except Exception:
        # A timecode the bake cannot parse is the bake's problem to report,
        # not something to paper over with a guess.
        return tc


def build_payload(data, mode=MODE_FRAME, frame=None, module=None,
                  camera=None):
    """
    The payload to hand to bake_sequence, plus the list of what was changed.

    Returns (payload, notes). Never mutates the caller's dict.
    """
    m = module or bridge.load()
    payload = dict(data)
    notes = []

    first = data.get("frame_first")
    last = data.get("frame_last")
    if first is None or last is None:
        raise PreviewError(
            "This flag has no frame_first/frame_last, so there is no frame to "
            "preview.")
    first, last = int(first), int(last)

    if mode == MODE_FULL:
        notes.append("full range %d-%d, exactly as it would be delivered"
                     % (first, last))
    elif mode == MODE_FRAME:
        if frame is None:
            frame = first
        frame = int(frame)
        if not (first <= frame <= last):
            raise PreviewError(
                "Frame %d is outside this render's range (%d-%d)."
                % (frame, first, last))
        payload["frame_first"] = frame
        payload["frame_last"] = frame
        payload["include_slate"] = False
        notes.append("single frame %d, no slate" % frame)

        if data.get("start_timecode"):
            shifted = shift_timecode(data["start_timecode"], frame - first, m)
            payload["start_timecode"] = shifted
            notes.append("timecode advanced %d frames to %s"
                         % (frame - first, shifted))
    else:
        raise PreviewError("Unknown mode %r (expected %r or %r)"
                           % (mode, MODE_FRAME, MODE_FULL))

    if camera:
        flag_camera = data.get("camera")
        payload["camera"] = camera
        notes.append("camera set to %r for this preview (flag has %r)"
                     % (camera, flag_camera))

    if payload.get("review_proxy") is not False:
        payload["review_proxy"] = False
        notes.append("review proxy skipped (does not affect the .mov)")

    return payload, notes


def output_path(data, mode, frame, out_dir=None):
    """A name that can never be mistaken for a delivery."""
    base = (data.get("shot_code") or data.get("entity_name") or "preview")
    step = data.get("step") or ""
    version = data.get("version")
    bits = [str(base)]
    if step:
        bits.append(str(step))
    if version is not None:
        try:
            bits.append("v%03d" % int(version))
        except (TypeError, ValueError):
            pass
    if mode == MODE_FRAME and frame is not None:
        bits.append("f%04d" % int(frame))
    bits.append("PREVIEW")
    name = "_".join(b for b in bits if b) + ".mov"

    folder = out_dir or os.path.join(tempfile.gettempdir(), "buf_qt_preview")
    return os.path.join(folder, name)


# ---------------------------------------------------------------------------
# Running the real bake
# ---------------------------------------------------------------------------

class Result(dict):
    pass


def render_preview(flag_path=None, data=None, mode=MODE_FRAME, frame=None,
                   out_dir=None, module=None, camera=None):
    """
    Bake it for real. Returns a Result with the path and what happened.

    Raises PreviewError with something a person can act on.
    """
    m = module or bridge.load()
    if data is None:
        data = load_flag(flag_path)

    payload, notes = build_payload(data, mode=mode, frame=frame, module=m,
                                   camera=camera)
    dst = output_path(data, mode, frame, out_dir)
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    pattern = payload.get("exr_path_pattern")
    if not pattern:
        raise PreviewError("This flag has no exr_path_pattern.")
    probe = m.frame_path(pattern, int(payload["frame_first"]))
    if not os.path.isfile(probe):
        raise PreviewError(
            "The EXR for that frame is not on disk:\n%s\n\n"
            "The flag may be older than the render, or the sequence may have "
            "moved." % probe)

    started = time.time()
    try:
        m.bake_sequence(payload, [dst])
    except Exception as exc:
        raise PreviewError("The bake failed:\n\n%s\n\nThis is the real bake, "
                           "so the same thing would happen to the delivered "
                           "QT." % exc)

    if not os.path.isfile(dst):
        raise PreviewError("The bake reported success but wrote nothing to\n%s"
                           % dst)

    return Result(
        path=dst,
        mode=mode,
        frame=frame,
        seconds=time.time() - started,
        notes=notes,
        bake=bridge.capabilities(m),
    )


# ---------------------------------------------------------------------------
# The Nuke side -- deliberately thin
# ---------------------------------------------------------------------------

def _nuke():
    try:
        import nuke
    except ImportError:
        raise PreviewError("This has to run inside Nuke.")
    return nuke


def create_read(result, label=None):
    """Drop the baked preview into the script as a Read node."""
    nuke = _nuke()
    path = result["path"].replace("\\", "/")

    read = nuke.createNode("Read", inpanel=False)
    read.knob("file").setValue(path)

    # The bake writes a Rec.709 display-referred QuickTime. Tell Nuke that
    # rather than letting it guess from the extension, or the preview will be
    # graded twice on screen and look nothing like the QT.
    for candidate in ("Output - Rec.709", "rec709", "Rec.709", "sRGB"):
        try:
            read.knob("colorspace").setValue(candidate)
            break
        except Exception:
            continue

    note = label or "QT PREVIEW  %s" % result["mode"]
    read.knob("label").setValue(note + "\\n" + os.path.basename(path))
    try:
        read.knob("tile_color").setValue(0xFF8000FF)   # orange: not a delivery
    except Exception:
        pass
    return read


def preview_and_read(flag_path=None, data=None, mode=MODE_FRAME, frame=None,
                     out_dir=None, camera=None):
    """One call: bake for real, then show it. Returns (read_node, result)."""
    result = render_preview(flag_path=flag_path, data=data, mode=mode,
                            frame=frame, out_dir=out_dir, camera=camera)
    return create_read(result), result


def summary(result):
    lines = ["Baked with the real qt_bake_oiio in %.1fs" % result["seconds"],
             result["path"], ""]
    lines += ["  - %s" % n for n in result["notes"]]
    caps = result.get("bake") or {}
    if caps.get("anamorphic_blowup"):
        lines.append("  - bake has the %.2fx anamorphic blow-up"
                     % caps["anamorphic_blowup"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Menu entry
# ---------------------------------------------------------------------------

def build_qt_frame_preview():
    """Entry point registered in menu.py."""
    nuke = _nuke()
    try:
        bridge.load()
    except bridge.BakeUnavailable as exc:
        nuke.message("Cannot reach the QT bake.\n\n%s" % exc)
        return None

    flag = nuke.getFilename("Render-complete flag (.json)", "*.json")
    if not flag:
        return None

    try:
        data = load_flag(flag)
        first = int(data.get("frame_first"))
        last = int(data.get("frame_last"))
    except Exception as exc:
        nuke.message(str(exc))
        return None

    panel = nuke.Panel("QT Frame Preview")
    panel.addSingleLineInput("Frame (%d-%d)" % (first, last), str(first))
    panel.addBooleanCheckBox("Full range (slow, includes slate)", False)
    # One canonical name per camera the bake knows. Space-separated: Nuke's
    # pulldown splits on spaces, so these must not contain any.
    camera_label = "Camera (flag: %s)" % (data.get("camera") or "none = ARRI")
    panel.addEnumerationPulldown(camera_label, "from_flag arri red sony dji")
    if not panel.show():
        return None
    camera = panel.value(camera_label)
    if not camera or camera == "from_flag":
        camera = None

    full = panel.value("Full range (slow, includes slate)")
    mode = MODE_FULL if full else MODE_FRAME
    try:
        frame = int(panel.value("Frame (%d-%d)" % (first, last)))
    except (TypeError, ValueError):
        frame = first

    if mode == MODE_FULL and (last - first) > 200:
        if not nuke.ask("That is %d frames through the real bake, which will "
                        "take a while.\n\nCarry on?" % (last - first + 1)):
            return None

    try:
        read, result = preview_and_read(data=data, mode=mode, frame=frame,
                                        camera=camera)
    except PreviewError as exc:
        nuke.message(str(exc))
        return None

    print("[qt_frame_preview] %s" % summary(result))
    return read
