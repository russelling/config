# Copyright (c) Studio. All Rights Reserved.
"""
plate_lineup.py -- "Plate Lineup (Flow)" for Nuke.

STUDIO_PLATE_LINEUP_2026_11_02
STUDIO_PLATE_LINEUP_FFOA_2026_11_03
STUDIO_PLATE_LINEUP_PANE_2026_11_03
STUDIO_PLATE_LINEUP_SWITCH_2026_11_03

For every plate of a shot (BG, EL, FG ...), pulls the lineup data off
Flow Production Tracking and (on request) builds the nodes that line each
plate up against the shot:

    sg_plt_lineup_frame_start / _end     where the plate sits
    sg_plt_lineup_lfoa                   reported, checked against _end
    plt_timewarp_keyframe_position_list  retime curve (shot frames)
    plt_timewarp_keyframe_value_list     retime curve (plate frames)
    sg_plt_camera_format, every other *plt* field   reported

Which Flow entity carries these, and how it links to the Shot, is found
from the schema by hooks/plate_sg.py and printed at the top of every
report -- see that file to pin it.

WHAT IT DOES
------------
1. Report, in a dockable "Plate Lineup" pane (STUDIO_PLATE_LINEUP_PANE_2026_11_03,
   replaces the old pop-up): one table row per plate -- Flow lineup, where
   it actually LANDS in the shot, action, handles delivered vs Flow, frames
   on disk, offset, warnings -- rows tinted when something needs a look.
   Click a row for that plate's full block. Refresh / Build Lineup Nodes /
   Select Read / Copy Report buttons; follows context changes by itself.
   The full text report is also printed to the Script Editor. If the pane
   cannot be made, the old pop-up is used.
2. Build Lineup Nodes (button): for each BG plate found on disk, under the
   plate's Read (an existing Read of that sequence is reused; otherwise one
   is made by plate_reads.make_read, exactly as Load Shot Plates would):

       Read -> TimeOffset | TimeWarp -> FrameRange (shot sg_cut_in..out)

   TimeWarp when Flow has timewarp keyframes, TimeOffset otherwise. The
   nodes are tagged; running it again replaces this tool's nodes for that
   plate and never touches anything else.
3. Lineup Switch (STUDIO_PLATE_LINEUP_SWITCH_2026_11_03), built with the
   nodes: one Switch fed by the lined-up element plates (SWITCH_KINDS, "el"
   by default -- FG/BG plates that run the whole shot are the base, not part
   of the sequence), `which` keyed with CONSTANT (stepped) keys:

       each element is shown from its own FFOA until the next element's
       FFOA; the first is held before its FFOA, the last runs to shot end.

   Overlaps go to the newer element. Any switch window that reaches frames
   the chosen plate does not cover is reported. Tagged "__switch__";
   rebuilt on every Build. Nothing is wired downstream of it.

HOW THE NUMBERS ARE READ -- CONFIRMED 2026-11-03 (302_038_0040)
-----------------------------------------------------------------
  * sg_plt_lineup_frame_start / _end are SHOT frames: where the plate's
    first and last delivered frames land, assuming full handles.
  * sg_plt_lineup_ffoa / _lfoa = lineup start + head handle / lineup end -
    tail handle: the action, without handles.
  * A plate is NOT always delivered with its full head handle (EL03 on
    302_038_0040: source start TC == start TC, 0 frames of head). Its first
    frame on disk is then the first frame of ACTION, and lineup start is
    wrong by the missing handle. So a plate is PLACED BY ITS ACTION:

        first frame on disk -> shot frame  FFOA - delivered head handle

    delivered head = source start TC - start TC (sg_plt_*_timecode at
    sg_plt_fps). Without usable timecodes, sg_plt_handle_head is used;
    without FFOA, lineup start. The method is reported per plate, and any
    plate whose placement differs from lineup start is called out.
  * The result is cross-checked against lineup end and against the source
    TCs' action length (LFOA - FFOA + 1); disagreements are reported.
  * Shot range: Shot sg_cut_in/out (handles included). When those are
    empty it is DERIVED from the plates -- earliest FFOA - head handle to
    latest LFOA + tail handle -- checked against
    sg_plt_master_duration_frame_count, and labelled as derived.
  * Timewarp positions are SHOT frames, values are PLATE frames: Nuke's
    TimeWarp lookup(output frame) = input frame. Positions that all fall
    inside 0..shot length are taken as relative to the shot's first frame,
    values inside 0..plate length as relative to the first frame on disk
    -- and the report says when that happened.
  * LINEUP_FRAMES_ARE = "plate" / "auto" restore the 2026-11-02 readings
    (lineup start/end as plate frames). Known wrong on this show; kept only
    as a switch.

Nuke knob names used (TimeOffset.time_offset, TimeWarp.lookup,
FrameRange.first_frame/last_frame) are set inside try blocks; a failure is
reported in the result rather than aborting the build.
"""

import importlib.util
import os
import sys
import types

MENU_LABEL = "Plate Lineup (Flow)"

# Lineup Switch: element kinds (the letters of BG01 / EL02 / FG01, lower
# case) that take part. Empty tuple = every plate that is not BG/FG.
SWITCH_KINDS = ("el",)
SWITCH_TAG = "__switch__"

# "shot" (confirmed 2026-11-03) | "auto" | "plate" -- see
# "HOW THE NUMBERS ARE READ" above.
LINEUP_FRAMES_ARE = "shot"

TAG_KNOB = "buf_plate_lineup"
NODE_SPACING_Y = 70


# ---------------------------------------------------------------------------
# Sibling modules, loaded by path (hooks/ is not on sys.path)
# ---------------------------------------------------------------------------

def _load_sibling(name):
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, name + ".py")
    if not os.path.isfile(path):
        raise RuntimeError("%s.py not found next to plate_lineup.py (%s)"
                           % (name, path))
    module_name = "_buf_lineup_" + name
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Interpretation (no nuke -- testable)
# ---------------------------------------------------------------------------

def _within(values, lo, hi):
    return lo is not None and hi is not None and all(lo <= v <= hi for v in values)


def plan_plate(ps, plate, fields, shot_rng, disk_rng, mode=None):
    """What to build for one plate, and every assumption made.

    shot_rng / disk_rng: (first, last) or (None, None).
    Returns dict: lineup_start/end, lfoa, keys, problems, notes,
    kind ("timewarp"|"offset"|None), offset, tw_keys [(shot, plate)],
    frame_range (first, last) or None, lineup_as ("plate"|"shot"|None).
    """
    mode = mode or LINEUP_FRAMES_ARE
    sf, sl = shot_rng
    df, dl = disk_rng
    get = lambda logical: plate.get(fields[logical]) if fields.get(logical) else None
    ls, le, lfoa = (ps.to_number(get("lineup_start")),
                    ps.to_number(get("lineup_end")),
                    ps.to_number(get("lfoa")))
    pairs, kf_problem = ps.keyframes(plate, fields)

    plan = {"lineup_start": ls, "lineup_end": le, "lfoa": lfoa,
            "keys": pairs, "notes": [], "problems": [], "kind": None,
            "offset": None, "tw_keys": [], "lineup_as": None,
            "frame_range": (sf, sl) if sf is not None else None}
    notes, problems = plan["notes"], plan["problems"]
    if kf_problem:
        problems.append(kf_problem)
    if sf is None:
        notes.append("Shot has no %s/%s on Flow -- no FrameRange, and "
                     "relative keyframes cannot be placed."
                     % (ps.SG_FRAME_IN_FIELD, ps.SG_FRAME_OUT_FIELD))
    if df is None:
        notes.append("Not found on disk under plates/ -- report only.")

    # ---- where does the plate sit?
    if mode == "shot" and (ls is not None or get("ffoa") is not None):
        plan["lineup_as"] = "shot"
        _place_by_action(ps, plan, get, shot_rng, disk_rng)
    elif ls is not None:
        span = [v for v in (ls, le) if v is not None]
        in_shot = _within(span, sf, sl)
        in_disk = _within(span, df, dl)
        if mode in ("plate", "shot"):
            as_ = mode
            notes.append("Lineup read as %s frames (LINEUP_FRAMES_ARE)." % as_)
        elif in_shot and not in_disk:
            as_ = "shot"
            notes.append("Lineup %s read as SHOT frames: inside the shot "
                         "range, outside the plate's frames on disk." % _rng(ls, le))
        elif in_disk and not in_shot:
            as_ = "plate"
            notes.append("Lineup %s read as PLATE frames: inside the "
                         "plate's frames on disk, outside the shot range."
                         % _rng(ls, le))
        else:
            as_ = "plate"
            notes.append("Lineup %s fits %s -- AMBIGUOUS, read as PLATE "
                         "frames. Set LINEUP_FRAMES_ARE in plate_lineup.py "
                         "once confirmed." % (_rng(ls, le),
                                              "both ranges" if in_shot else
                                              "neither range"))
        plan["lineup_as"] = as_

        if le is not None and sf is not None and (le - ls) != (sl - sf):
            notes.append("Lineup length %g frames vs shot %d frames -- NOT "
                         "treated as a speed change; check it."
                         % (le - ls + 1, sl - sf + 1))
        if lfoa is not None and le is not None and lfoa != le:
            notes.append("LFOA %g differs from lineup end %g." % (lfoa, le))

        if as_ == "plate":
            if sf is not None:
                plan["offset"] = sf - ls
        else:
            if df is not None:
                plan["offset"] = ls - df
            else:
                notes.append("Lineup is in shot frames but the plate is "
                             "not on disk -- offset unknown.")
    elif not pairs:
        notes.append("No lineup start on Flow -- nothing to line up.")
    # ---- timewarp
    if pairs:
        positions = [p for p, _ in pairs]
        values = [v for _, v in pairs]
        pos_base = 0
        if sf is not None and not _within(positions, sf - 1000, sl + 1000) \
                and _within(positions, 0, sl - sf + 1):
            pos_base = sf
            notes.append("Timewarp positions %s look relative -- added the "
                         "shot's first frame %d." % (_rng(positions[0], positions[-1]), sf))
        val_base = 0
        if df is not None and not _within(values, df, dl) \
                and _within(values, 0, dl - df + 1):
            val_base = ls if (ls is not None and plan["lineup_as"] == "plate") else df
            notes.append("Timewarp values look relative -- added %g (%s)."
                         % (val_base, "lineup start" if val_base == ls
                            else "first frame on disk"))
        keys = [(p + pos_base, v + val_base) for p, v in pairs]
        if df is not None:
            outside = [v for _, v in keys if not df <= v <= dl]
            if outside:
                notes.append("%d timewarp value(s) fall outside the plate's "
                             "frames on disk (%d-%d) -- they will read "
                             "missing frames." % (len(outside), df, dl))
        plan["tw_keys"] = keys
        plan["kind"] = "timewarp"
        if plan["offset"] is not None and sf is not None:
            at_first = _lookup(keys, sf)
            expected = sf - plan["offset"]
            if at_first is not None and abs(at_first - expected) > 0.01:
                notes.append("Timewarp gives plate frame %g at shot frame %d; "
                             "the lineup start implies %g. The timewarp is "
                             "what gets built." % (at_first, sf, expected))
    elif plan["offset"] is not None:
        plan["kind"] = "offset"
    return plan


def _place_by_action(ps, plan, get, shot_rng, disk_rng):
    """Place the plate by its action (FFOA - delivered head handle) and
    cross-check everything Flow says about it. Fills plan in place:
    first_at / last_at (shot frames of the first / last frame on disk),
    placed_how, offset, notes. See "HOW THE NUMBERS ARE READ"."""
    notes = plan["notes"]
    sf, sl = shot_rng
    df, dl = disk_rng
    ls, le, lfoa = plan["lineup_start"], plan["lineup_end"], plan["lfoa"]
    num = ps.to_number
    ffoa = num(get("ffoa"))
    plan["ffoa"] = ffoa
    fps = get("fps")
    hh_flow, ht_flow = num(get("handle_head")), num(get("handle_tail"))
    tc = {k: ps.tc_to_frames(get(k), fps)
          for k in ("start_tc", "end_tc", "source_start_tc", "source_end_tc")}

    head = tail = None
    if tc["start_tc"] is not None and tc["source_start_tc"] is not None:
        head = tc["source_start_tc"] - tc["start_tc"]
    if tc["end_tc"] is not None and tc["source_end_tc"] is not None:
        tail = tc["end_tc"] - tc["source_end_tc"]
    if head is not None and head < 0:
        notes.append("Source start TC is BEFORE start TC (%d frames) -- "
                     "timecodes not used for the head handle." % head)
        head = None
    head_from = "timecodes" if head is not None else None
    if head is None and hh_flow is not None:
        head, head_from = hh_flow, "sg_plt_handle_head (no usable timecodes)"

    if ffoa is not None and head is not None:
        first = ffoa - head
        how = "FFOA %g - %g-frame head handle delivered (%s)" % (
            ffoa, head, head_from)
    elif ls is not None:
        first = ls
        how = "lineup start (%s)" % ("no FFOA on Flow" if ffoa is None
                                      else "head handle unknown")
    else:
        notes.append("No FFOA or lineup start on Flow -- cannot place.")
        return
    plan["first_at"], plan["placed_how"] = first, how
    plan["head_delivered"], plan["tail_delivered"] = head, tail

    if ls is not None and first != ls:
        assumed = (ffoa - ls) if ffoa is not None else hh_flow
        notes.append("PLACED BY ACTION: lineup start %g assumes a %s-frame "
                     "head handle, but %g %s delivered -- the first frame "
                     "goes to shot frame %g, not %g."
                     % (ls, "%g" % assumed if assumed is not None else "?",
                        head, "was" if head == 1 else "were", first, ls))
    if hh_flow is not None and head is not None and head != hh_flow \
            and head_from == "timecodes" and not (ls is not None and first != ls):
        notes.append("Head handle: %g delivered, sg_plt_handle_head says %g."
                     % (head, hh_flow))
    if ht_flow is not None and tail is not None and tail != ht_flow:
        notes.append("Tail handle: %g delivered, sg_plt_handle_tail says %g."
                     % (tail, ht_flow))

    tc_count = None
    if tc["start_tc"] is not None and tc["end_tc"] is not None:
        tc_count = tc["end_tc"] - tc["start_tc"] + 1
    if df is not None:
        count = dl - df + 1
        if tc_count is not None and tc_count != count:
            notes.append("Timecodes say %d frames delivered; %d on disk."
                         % (tc_count, count))
    else:
        count = tc_count if tc_count is not None else (
            le - ls + 1 if (le is not None and ls is not None) else None)
    if count is not None:
        last = first + count - 1
        plan["last_at"] = last
        if le is not None and last != le:
            notes.append("Ends at shot frame %g; lineup end on Flow says %g "
                         "-- check." % (last, le))
        if sf is not None and (first < sf or last > sl):
            notes.append("Reaches outside the shot range %d-%d." % (sf, sl))

    if ffoa is not None and lfoa is not None and \
            tc["source_start_tc"] is not None and tc["source_end_tc"] is not None:
        action = tc["source_end_tc"] - tc["source_start_tc"] + 1
        if lfoa - ffoa + 1 != action:
            notes.append("FFOA-LFOA is %g frames; source timecodes say %d."
                         % (lfoa - ffoa + 1, action))

    if df is not None:
        plan["offset"] = first - df
    else:
        notes.append("Not on disk -- offset unknown (would put the first "
                     "frame at shot frame %g)." % first)


def derive_shot_range(ps, plates, fields):
    """(first, last, how, notes) from the plates when the Shot has no cut
    range: earliest FFOA - head handle .. latest LFOA + tail handle,
    checked against sg_plt_master_duration_frame_count. (None, None, ...)
    if the plates do not carry enough to say."""
    num = ps.to_number
    get = lambda plate, logical: (plate.get(fields[logical])
                                  if fields.get(logical) else None)
    rows = []
    for plate in plates:
        ffoa, lfoa = num(get(plate, "ffoa")), num(get(plate, "lfoa"))
        if ffoa is None or lfoa is None:
            continue
        rows.append((ffoa, lfoa, num(get(plate, "handle_head")),
                     num(get(plate, "handle_tail")),
                     num(get(plate, "master_duration"))))
    if not rows:
        return None, None, "no FFOA/LFOA on any plate", []
    first_action = min(r[0] for r in rows)
    last_action = max(r[1] for r in rows)
    heads = set(r[2] for r in rows if r[2] is not None)
    tails = set(r[3] for r in rows if r[3] is not None)
    notes = []
    if len(heads) > 1 or len(tails) > 1:
        notes.append("Plates disagree on handles (head %s, tail %s) -- used "
                     "the largest." % (sorted(heads), sorted(tails)))
    head, tail = max(heads or [0]), max(tails or [0])
    masters = set(r[4] for r in rows if r[4] is not None)
    cut = last_action - first_action + 1
    if len(masters) == 1:
        master = masters.pop()
        notes.append("Cut %g-%g = %g frames; sg_plt_master_duration_frame_count"
                     " = %g -- %s." % (first_action, last_action, cut, master,
                                       "agrees" if master == cut else
                                       "DISAGREES, check the derived range"))
    elif masters:
        notes.append("Plates disagree on master duration (%s)." % sorted(masters))
    how = ("DERIVED from the plates (FFOA %g - %g head, LFOA %g + %g tail) "
           "-- sg_cut_in/out not set on Flow"
           % (first_action, head, last_action, tail))
    return int(first_action - head), int(last_action + tail), how, notes


def plan_switch(g):
    """The Lineup Switch, worked out from the plans (no nuke).

    Returns dict: steps [{code, item, frame, until, covered, warn}], kinds,
    skipped [(code, why)], text (one line). frame = first shot frame this
    plate is shown (its FFOA; the first step is held from shot start),
    until = last frame shown.
    """
    ps = g["ps"]
    sf, sl = g["shot_range"]
    out = {"steps": [], "kinds": SWITCH_KINDS, "skipped": [], "text": ""}
    cands = []
    for item in g["items"]:
        code = ps.plate_code(item["plate"])
        key = ps.element_key(code)
        kind = key[0] if key else None
        wanted = (kind in SWITCH_KINDS) if SWITCH_KINDS else \
            (kind not in ("bg", "fg"))
        if not wanted:
            continue
        plan = item["plan"]
        start = plan.get("ffoa")
        if start is None:
            start = plan.get("first_at")
        if start is None or plan["kind"] is None or item["record"] is None:
            out["skipped"].append((code, "no placement / not on disk"))
            continue
        cands.append((start, code, item))
    cands.sort(key=lambda c: (c[0], c[1]))
    for i, (start, code, item) in enumerate(cands):
        nxt = cands[i + 1][0] if i + 1 < len(cands) else None
        first = sf if (i == 0 and sf is not None) else start
        until = (nxt - 1) if nxt is not None else (sl if sl is not None else
                                                   item["plan"].get("last_at"))
        plan = item["plan"]
        lo, hi = plan.get("first_at"), plan.get("last_at")
        warn = []
        if until is not None and until < first:
            warn.append("never shown (next element starts at the same frame)")
        elif lo is not None and hi is not None and until is not None:
            if first < lo:
                warn.append("no picture %g-%g (plate starts %g)" % (first, lo - 1, lo))
            if until > hi:
                warn.append("no picture %g-%g (plate ends %g)" % (hi + 1, until, hi))
        out["steps"].append({"code": code, "item": item, "frame": start,
                             "first": first, "until": until, "warn": warn})
    shot = g["shot"].get("name") or ""
    short = lambda c: c[len(shot) + 1:] if shot and c.startswith(shot + "_") else c
    out["text"] = " -> ".join("%s %g" % (short(st["code"]), st["frame"])
                              for st in out["steps"])
    return out


def _lookup(keys, frame):
    """Linear interpolation of the key list at frame (None outside it)."""
    for (p0, v0), (p1, v1) in zip(keys, keys[1:]):
        if p0 <= frame <= p1:
            return v0 if p1 == p0 else v0 + (v1 - v0) * (frame - p0) / (p1 - p0)
    if len(keys) == 1 and keys[0][0] == frame:
        return keys[0][1]
    return None


def _rng(a, b):
    if b is None or a == b:
        return "%g" % a
    return "%g-%g" % (a, b)


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------

def gather(engine):
    """Everything the report and the build need. Raises RuntimeError with an
    artist-readable message when there is nothing to show."""
    ps = _load_sibling("plate_sg")
    pr = _load_sibling("plate_reads")
    context = engine.context
    if not (context and context.entity and context.entity.get("type") == "Shot"):
        raise RuntimeError("Plate Lineup works in a Shot context.\n\n"
                           "Current context: %s" % context)
    sg = engine.shotgun
    shot = context.entity
    try:
        data = ps.fetch_shot_plates(sg, shot)
    except ps.PlateSGError as exc:
        raise RuntimeError(str(exc))
    shot_rng = ps.shot_range(sg, shot["id"])
    range_how, range_notes = "sg_cut_in/out, handles incl.", []
    if shot_rng[0] is None:
        first, last, range_how, range_notes = derive_shot_range(
            ps, data["plates"], data["fields"])
        shot_rng = (first, last)
        if first is None:
            range_how = "not set on Flow (%s)" % range_how

    plates_dir = pr.plates_dir_for_context(engine.sgtk, context)
    records = []
    if plates_dir and os.path.isdir(plates_dir):
        records = pr.scan_plates(plates_dir, shot_code=shot.get("name"),
                                 latest_only=True)

    items = []
    for plate in data["plates"]:
        record, match_note = match_record(ps, plate, records)
        disk = (record["first"], record["last"]) if record else (None, None)
        plan = plan_plate(ps, plate, data["fields"], shot_rng, disk)
        items.append({"plate": plate, "record": record,
                      "match_note": match_note, "plan": plan,
                      "bg": ps.bg_number(plate) is not None})
    return {"ps": ps, "pr": pr, "shot": shot, "shot_range": shot_rng,
            "shot_range_how": range_how, "shot_range_notes": range_notes,
            "plates_dir": plates_dir, "data": data, "items": items}


def match_record(ps, plate, records):
    """The on-disk plate record (latest delivery) for a Flow plate."""
    code = ps.plate_code(plate)
    key = ps.element_key(code)
    if key is None:
        return None, "code %r names no element (bg01, el02 ...)" % code
    hits = [r for r in records if ps.element_key(r["element"]) == key]
    if not hits:
        return None, "no %s%02d folder under plates/" % key
    if len(hits) > 1:
        return hits[0], "%d sequences for %s%02d on disk; using %s" % (
            len(hits), key[0], key[1], hits[0]["relpath"])
    return hits[0], "on disk: %s" % hits[0]["relpath"]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report_header(g):
    """Header lines of the report (shot range, entity, folder, counts)."""
    ps, data = g["ps"], g["data"]
    sf, sl = g["shot_range"]
    lines = []
    bgs = [i for i in g["items"] if i["bg"]]
    lines.append("PLATE LINEUP -- %s" % g["shot"].get("name"))
    lines.append("=" * len(lines[0]))
    lines.append("Shot range   : %s" % ("%d-%d %s" % (sf, sl, g["shot_range_how"])
                                        if sf is not None else
                                        g["shot_range_how"]))
    for note in g["shot_range_notes"]:
        lines.append("               %s" % note)
    lines.append("Plate entity : %s (%s) -- %s" % (
        data["entity_name"], data["entity_type"], data["entity_how"]))
    lines.append("Shot link    : %s.%s" % (
        data["entity_type"] if data["link"][0] == "plate" else "Shot",
        data["link"][1]))
    lines.append("Plates folder: %s" % (g["plates_dir"] or "(not resolved)"))
    if data["missing"]:
        lines.append("NOT ON FLOW  : %s" % ", ".join(data["missing"]))
    lines.append("Plates       : %d on Flow, %d BG" % (len(g["items"]), len(bgs)))
    if not bgs:
        lines.append("NOTE: no BG plates on Flow -- Build lines up every plate.")
    sw = plan_switch(g)
    if len(sw["steps"]) >= 2:
        lines.append("Switch       : %s  (%s plates, stepped at each FFOA)"
                     % (sw["text"], "/".join(k.upper() for k in sw["kinds"])
                        or "non-BG/FG"))
        for st in sw["steps"]:
            for w in st["warn"]:
                lines.append("               %s: %s" % (st["code"], w))
    else:
        lines.append("Switch       : not built (%d element plate%s to switch)"
                     % (len(sw["steps"]), "" if len(sw["steps"]) == 1 else "s"))
    for code, why in sw["skipped"]:
        lines.append("               %s left out of the switch: %s" % (code, why))
    return lines


def item_lines(g, item):
    """The report block for one plate."""
    ps, data = g["ps"], g["data"]
    sf = g["shot_range"][0]
    shown = set(f for f in data["fields"].values() if f)
    lines = []
    plate, plan = item["plate"], item["plan"]
    head = "%s%s" % (ps.plate_code(plate), "" if item["bg"] else "   (not BG)")
    lines.append(head)
    lines.append("-" * len(head))
    for logical, given in ps.LINEUP_FIELDS.items():
        field = data["fields"].get(logical)
        value = ps.display_value(plate.get(field)) if field else "(no such field)"
        lines.append("  %-38s %s" % (field or given, value))
    for field in data["extra"]:
        if field in shown or field == "code":
            continue
        value = plate.get(field)
        if value in (None, "", []):
            continue
        lines.append("  %-38s %s" % (field, ps.display_value(value)))
    lines.append("  %-38s %s" % ("disk", item["match_note"]))
    if item["record"]:
        lines.append("  %-38s %d-%d" % ("frames on disk", item["record"]["first"],
                                        item["record"]["last"]))
    if plan["kind"] == "timewarp":
        lines.append("  BUILD: TimeWarp, %d key%s (shot -> plate): %s" % (
            len(plan["tw_keys"]), "" if len(plan["tw_keys"]) == 1 else "s",
            ", ".join("%g->%g" % k for k in plan["tw_keys"][:8])
            + (" ..." if len(plan["tw_keys"]) > 8 else "")))
    elif plan["kind"] == "offset" and plan.get("first_at") is not None:
        lines.append("  BUILD: TimeOffset %+d -- frame %d on disk lands on "
                     "shot frame %g%s" % (
                         plan["offset"], item["record"]["first"],
                         plan["first_at"],
                         "; covers %g-%g" % (plan["first_at"], plan["last_at"])
                         if plan.get("last_at") is not None else ""))
        lines.append("         placed by %s" % plan["placed_how"])
    elif plan["kind"] == "offset":
        lines.append("  BUILD: TimeOffset %+d (plate frame %g at shot frame %g)"
                     % (plan["offset"], _plate_at_shot_start(plan, g),
                        sf if sf is not None else float("nan")))
    else:
        lines.append("  BUILD: nothing (no usable lineup)")
    for problem in plan["problems"]:
        lines.append("  ! %s" % problem)
    for note in plan["notes"]:
        lines.append("  - %s" % note)
    return lines


def report_text(g):
    lines = report_header(g)
    for item in g["items"]:
        lines.append("")
        lines.extend(item_lines(g, item))
    return "\n".join(lines)


def _plate_at_shot_start(plan, g):
    sf = g["shot_range"][0]
    if sf is None or plan["offset"] is None:
        return float("nan")
    return sf - plan["offset"]


# ---------------------------------------------------------------------------
# Building (needs nuke)
# ---------------------------------------------------------------------------

def _tag(node, code, summary):
    import nuke
    try:
        if TAG_KNOB not in node.knobs():
            node.addKnob(nuke.Tab_Knob("buf_lineup_tab", "Lineup"))
            knob = nuke.String_Knob(TAG_KNOB, "Flow plate")
            node.addKnob(knob)
            node.addKnob(nuke.Text_Knob("buf_lineup_info", "", summary))
        node[TAG_KNOB].setValue(code)
    except Exception as exc:
        print("[plate_lineup] could not tag %s: %s" % (node.name(), exc))


def _set(node, knob, value, failures):
    try:
        node[knob].setValue(value)
        return True
    except Exception as exc:
        failures.append("%s.%s: %s" % (node.Class(), knob, exc))
        return False


def _existing_read(record):
    import nuke
    want = os.path.normpath(record["nuke_path"].replace("\\", "/"))
    for node in nuke.allNodes("Read"):
        try:
            if os.path.normpath(node["file"].value()) == want:
                return node
        except Exception:
            continue
    return None


def _remove_previous(code):
    import nuke
    removed = 0
    for node in nuke.allNodes(recurseGroups=False):
        try:
            if TAG_KNOB in node.knobs() and node[TAG_KNOB].value() == code \
                    and node.Class() != "Read":
                nuke.delete(node)
                removed += 1
        except Exception:
            continue
    return removed


def build_nodes(g, bg_only=None):
    """Build the lineup nodes. Returns a list of result lines.
    bg_only=None: BG plates only if the shot has any, else every plate."""
    import nuke
    ps, pr = g["ps"], g["pr"]
    if bg_only is None:
        bg_only = any(item["bg"] for item in g["items"])
    results = []
    g["_tails"] = {}
    for item in g["items"]:
        if bg_only and not item["bg"]:
            continue
        code, plan, record = ps.plate_code(item["plate"]), item["plan"], item["record"]
        if record is None:
            results.append("%s: skipped -- %s" % (code, item["match_note"]))
            continue
        if plan["kind"] is None:
            results.append("%s: skipped -- no usable lineup on Flow" % code)
            continue

        for selected in nuke.selectedNodes():
            selected.setSelected(False)
        read = _existing_read(record)
        read_note = "existing Read %s" % read.name() if read else None
        if read is None:
            read = pr.make_read(record, primary=False,
                                note="lineup: %s" % code)
            read_note = "new Read %s" % read.name()
        removed = _remove_previous(code)

        failures = []
        summary = report_line(plan)
        x, y = read.xpos(), read.ypos() + NODE_SPACING_Y + 30
        if plan["kind"] == "timewarp":
            retime = nuke.nodes.TimeWarp()
            try:
                lookup = retime["lookup"]
                lookup.setAnimated()
                for shot_frame, plate_frame in plan["tw_keys"]:
                    lookup.setValueAt(plate_frame, shot_frame)
                anim = lookup.animation(0)
                anim.changeInterpolation(anim.keys(), nuke.LINEAR)
            except Exception as exc:
                failures.append("TimeWarp.lookup: %s" % exc)
        else:
            retime = nuke.nodes.TimeOffset()
            _set(retime, "time_offset", plan["offset"], failures)
        retime.setInput(0, read)
        retime.setXYpos(x, y)
        retime["label"].setValue("LINEUP %s\n%s" % (code, summary))
        _tag(retime, code, summary)
        built = [retime.name()]

        if plan["frame_range"]:
            fr = nuke.nodes.FrameRange()
            _set(fr, "first_frame", plan["frame_range"][0], failures)
            _set(fr, "last_frame", plan["frame_range"][1], failures)
            fr.setInput(0, retime)
            fr.setXYpos(x, y + NODE_SPACING_Y)
            fr["label"].setValue("shot %d-%d" % plan["frame_range"])
            _tag(fr, code, summary)
            built.append(fr.name())
            g["_tails"][code] = fr
        else:
            g["_tails"][code] = retime

        line = "%s: %s -> %s%s" % (code, read_note, " -> ".join(built),
                                   " (replaced %d old)" % removed if removed else "")
        if failures:
            line += "\n    COULD NOT SET: %s" % "; ".join(failures)
        results.append(line)
    return results or ["No %splates to build." % ("BG " if bg_only else "")]


def report_line(plan):
    if plan["kind"] == "timewarp":
        return "TimeWarp %d keys" % len(plan["tw_keys"])
    if plan["kind"] == "offset" and plan.get("first_at") is not None:
        return "offset %+d (first frame -> shot %g, by %s)" % (
            plan["offset"], plan["first_at"],
            "FFOA" if plan["placed_how"].startswith("FFOA") else "lineup start")
    if plan["kind"] == "offset":
        return "offset %+d (lineup %s as %s frames)" % (
            plan["offset"], _rng(plan["lineup_start"], plan["lineup_end"]),
            plan["lineup_as"])
    return "no lineup"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _qt():
    try:
        from PySide6 import QtWidgets, QtGui
    except ImportError:
        from PySide2 import QtWidgets, QtGui
    return QtWidgets, QtGui


def _show_dialog(text, on_build):
    QtWidgets, QtGui = _qt()
    dialog = QtWidgets.QDialog()
    dialog.setWindowTitle(MENU_LABEL)
    dialog.resize(900, 640)
    layout = QtWidgets.QVBoxLayout(dialog)
    view = QtWidgets.QPlainTextEdit()
    view.setReadOnly(True)
    view.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
    font = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
    view.setFont(font)
    view.setPlainText(text)
    layout.addWidget(view)
    row = QtWidgets.QHBoxLayout()
    build = QtWidgets.QPushButton("Build Lineup Nodes")
    copy = QtWidgets.QPushButton("Copy Report")
    close = QtWidgets.QPushButton("Close")
    row.addWidget(build)
    row.addWidget(copy)
    row.addStretch(1)
    row.addWidget(close)
    layout.addLayout(row)

    def _build():
        result = on_build()
        view.appendPlainText("\n\nBUILD\n-----\n" + "\n".join(result))
        build.setEnabled(False)

    build.clicked.connect(_build)
    copy.clicked.connect(
        lambda: QtWidgets.QApplication.clipboard().setText(view.toPlainText()))
    close.clicked.connect(dialog.accept)
    dialog.exec_() if hasattr(dialog, "exec_") else dialog.exec()


def build_switch(g):
    """The Lineup Switch (see the docstring). Needs build_nodes(g) first.
    Returns result lines."""
    import nuke
    sw = plan_switch(g)
    tails = g.get("_tails") or {}
    steps = [st for st in sw["steps"] if st["code"] in tails]
    _remove_previous(SWITCH_TAG)
    if len(steps) < 2:
        return ["Lineup Switch: not built -- %d lined-up element plate%s"
                % (len(steps), "" if len(steps) == 1 else "s")]
    failures = []
    switch = nuke.nodes.Switch()
    for i, st in enumerate(steps):
        switch.setInput(i, tails[st["code"]])
    try:
        knob = switch["which"]
        knob.setAnimated()
        for i, st in enumerate(steps):
            knob.setValueAt(i, st["first"])
        anim = knob.animation(0)
        anim.changeInterpolation(anim.keys(), nuke.CONSTANT)
    except Exception as exc:
        failures.append("Switch.which keys: %s" % exc)
    xs = [tails[st["code"]].xpos() for st in steps]
    ys = [tails[st["code"]].ypos() for st in steps]
    switch.setXYpos(int(sum(xs) / len(xs)), max(ys) + NODE_SPACING_Y + 50)
    switch["label"].setValue("LINEUP SWITCH\n" + sw["text"].replace(" -> ", "\n"))
    _tag(switch, SWITCH_TAG, "stepped at each element's FFOA: " + sw["text"])
    lines = ["Lineup Switch %s: %s" % (switch.name(), "; ".join(
        "input %d %s from %g" % (i, st["code"], st["first"])
        for i, st in enumerate(steps)))]
    for st in steps:
        for w in st["warn"]:
            lines.append("    %s: %s" % (st["code"], w))
    left = [st["code"] for st in sw["steps"] if st["code"] not in tails]
    if left:
        lines.append("    not built, so not in the switch: %s" % ", ".join(left))
    if failures:
        lines.append("    COULD NOT SET: %s" % "; ".join(failures))
    return lines


def _do_build(g):
    """build_nodes + build_switch under one undo step; result lines printed
    and returned."""
    import nuke
    undo = nuke.Undo()
    undo.begin("Plate Lineup")
    try:
        result = build_nodes(g)
        result += build_switch(g)
    except Exception as exc:
        result = ["Build failed: %s" % exc]
    finally:
        undo.end()
    print("\n".join(result))
    return result


# ---------------------------------------------------------------------------
# Dockable pane (STUDIO_PLATE_LINEUP_PANE_2026_11_03)
# ---------------------------------------------------------------------------

PANE_TITLE = "Plate Lineup"
PANE_ID = "com.buffalovfx.plate_lineup"
# nukescripts evaluates the widget class from a string in Nuke's __main__,
# and this file is loaded by path, so the module is published under a fixed
# sys.modules name the string can always reach.
PANE_MODULE = "_buf_plate_lineup_pane"
CONTEXT_POLL_MS = 1500

COLUMNS = ("Plate", "Description", "Flow lineup", "Lands (shot)", "Action",
           "Handles", "On disk", "Build", "Warnings")


def _num(v):
    return "-" if v is None else "%g" % v


def pane_row(g, item):
    """Cell texts + severity ("ok" | "warn" | "bad") for one plate. No Qt."""
    ps = g["ps"]
    plate, plan, rec = item["plate"], item["plan"], item["record"]
    fields = g["data"]["fields"]
    get = lambda logical: (plate.get(fields[logical])
                           if fields.get(logical) else None)
    desc = ps.display_value(plate.get("sg_plt_plate_description"))
    flow = "%s-%s" % (_num(plan["lineup_start"]), _num(plan["lineup_end"]))
    if plan.get("first_at") is not None:
        lands = "%g-%s" % (plan["first_at"], _num(plan.get("last_at")))
    else:
        lands = "-"
    action = "%s-%s" % (_num(plan.get("ffoa", ps.to_number(get("ffoa")))),
                        _num(plan["lfoa"]))
    hh, ht = ps.to_number(get("handle_head")), ps.to_number(get("handle_tail"))
    dh, dt = plan.get("head_delivered"), plan.get("tail_delivered")
    handles = "%s/%s" % (_num(dh if dh is not None else hh),
                         _num(dt if dt is not None else ht))
    if (dh is not None and hh is not None and dh != hh) or \
            (dt is not None and ht is not None and dt != ht):
        handles += "  (Flow %s/%s)" % (_num(hh), _num(ht))
    disk = "%d-%d" % (rec["first"], rec["last"]) if rec else "NOT ON DISK"
    if plan["kind"] == "timewarp":
        build = "TimeWarp %d keys" % len(plan["tw_keys"])
    elif plan["kind"] == "offset":
        build = "offset %+d" % plan["offset"]
    else:
        build = "nothing"
    warnings = list(plan["problems"]) + list(plan["notes"])
    short = []
    for w in warnings:
        if w.startswith("PLACED BY ACTION"):
            short.append("short head handle -- placed by action")
        else:
            short.append(w.split(" -- ")[0].rstrip("."))
    severity = "bad" if (plan["kind"] is None or rec is None or plan["problems"]) \
        else ("warn" if warnings else "ok")
    code = ps.plate_code(plate)
    prefix = "%s_" % (g["shot"].get("name") or "")
    if prefix != "_" and code.startswith(prefix):
        code = code[len(prefix):]          # EL03_V001, not 302_038_0040_EL03_V001
    cells = (code, desc, flow, lands, action, handles, disk,
             build, "; ".join(short))
    return cells, severity


def _pane_class():
    """The QWidget class, built lazily so this file imports without Qt."""
    QtWidgets, QtGui = _qt()
    try:
        from PySide6 import QtCore
    except ImportError:
        from PySide2 import QtCore

    tints = {"warn": QtGui.QColor(95, 75, 25), "bad": QtGui.QColor(105, 35, 35)}

    class PlateLineupPane(QtWidgets.QWidget):

        def __init__(self, parent=None):
            super(PlateLineupPane, self).__init__(parent)
            self._g = None
            self._context_key = None
            layout = QtWidgets.QVBoxLayout(self)
            layout.setContentsMargins(4, 4, 4, 4)

            self.header = QtWidgets.QLabel("")
            self.header.setWordWrap(True)
            self.header.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
            layout.addWidget(self.header)

            split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
            self.table = QtWidgets.QTableWidget(0, len(COLUMNS))
            self.table.setHorizontalHeaderLabels(COLUMNS)
            self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
            self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
            self.table.verticalHeader().setVisible(False)
            self.table.horizontalHeader().setStretchLastSection(True)
            self.table.itemSelectionChanged.connect(self._show_detail)
            split.addWidget(self.table)

            self.detail = QtWidgets.QPlainTextEdit()
            self.detail.setReadOnly(True)
            self.detail.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
            self.detail.setFont(QtGui.QFontDatabase.systemFont(
                QtGui.QFontDatabase.FixedFont))
            split.addWidget(self.detail)
            split.setSizes([260, 220])
            layout.addWidget(split, 1)

            row = QtWidgets.QHBoxLayout()
            for label, slot in (("Refresh", self.refresh),
                                ("Build Lineup Nodes", self._build),
                                ("Select Read", self._select_read),
                                ("Copy Report", self._copy)):
                button = QtWidgets.QPushButton(label)
                button.clicked.connect(slot)
                row.addWidget(button)
            row.addStretch(1)
            layout.addLayout(row)

            self._timer = QtCore.QTimer(self)
            self._timer.timeout.connect(self._poll_context)
            self._timer.start(CONTEXT_POLL_MS)
            self.refresh()

        # -- data --------------------------------------------------------
        @staticmethod
        def _engine():
            try:
                import sgtk
                return sgtk.platform.current_engine()
            except Exception:
                return None

        def _key(self):
            engine = self._engine()
            ctx = engine.context if engine else None
            entity = ctx.entity if ctx else None
            return (entity or {}).get("type"), (entity or {}).get("id")

        def _poll_context(self):
            if self.isVisible() and self._key() != self._context_key:
                self.refresh()

        def refresh(self):
            self._context_key = self._key()
            self.table.setRowCount(0)
            self.detail.setPlainText("")
            engine = self._engine()
            if engine is None:
                self._g = None
                self.header.setText("Flow Production Tracking is not running.")
                return
            try:
                self._g = gather(engine)
            except Exception as exc:
                self._g = None
                self.header.setText(str(exc))
                return
            g = self._g
            print("\n" + report_text(g) + "\n")
            sf, sl = g["shot_range"]
            head = "<b>%s</b>   shot %s   <i>%s</i>" % (
                g["shot"].get("name"),
                "%d-%d" % (sf, sl) if sf is not None else "-",
                g["shot_range_how"])
            extra = list(g["shot_range_notes"])
            if not any(i["bg"] for i in g["items"]):
                extra.append("No BG plates -- Build lines up every plate.")
            sw = plan_switch(g)
            if len(sw["steps"]) >= 2:
                warns = ["%s: %s" % (st["code"], w)
                         for st in sw["steps"] for w in st["warn"]]
                extra.append("<b>Switch</b> (stepped at each FFOA): %s%s" % (
                    sw["text"], ("<br><span style='color:#e0b050'>%s</span>"
                                 % "<br>".join(warns)) if warns else ""))
            if extra:
                head += "<br>" + "<br>".join(extra)
            self.header.setText(head)
            self.table.setRowCount(len(g["items"]))
            for r, item in enumerate(g["items"]):
                cells, severity = pane_row(g, item)
                for c, text in enumerate(cells):
                    cell = QtWidgets.QTableWidgetItem(text)
                    if severity in tints:
                        cell.setBackground(tints[severity])
                    if c == len(cells) - 1:
                        cell.setToolTip("\n".join(
                            item["plan"]["problems"] + item["plan"]["notes"]))
                    self.table.setItem(r, c, cell)
            self.table.resizeColumnsToContents()
            if g["items"]:
                self.table.selectRow(0)

        # -- actions -----------------------------------------------------
        def _current(self):
            rows = self.table.selectionModel().selectedRows()
            if not self._g or not rows:
                return None
            return self._g["items"][rows[0].row()]

        def _show_detail(self):
            item = self._current()
            self.detail.setPlainText(
                "\n".join(item_lines(self._g, item)) if item else "")

        def _build(self):
            import nuke
            if not self._g:
                return
            result = _do_build(self._g)
            nuke.message("Plate Lineup -- build\n\n" + "\n".join(result))

        def _select_read(self):
            import nuke
            item = self._current()
            if not item:
                return
            if not item["record"]:
                nuke.message("%s is not on disk." % self._g["ps"].plate_code(item["plate"]))
                return
            read = _existing_read(item["record"])
            if read is None:
                nuke.message("No Read of this plate in the script yet -- "
                             "Build Lineup Nodes (or Load Shot Plates) makes one.")
                return
            for node in nuke.selectedNodes():
                node.setSelected(False)
            read.setSelected(True)
            try:
                nuke.zoom(nuke.zoom(), [read.xpos() + read.screenWidth() / 2,
                                        read.ypos() + read.screenHeight() / 2])
            except Exception:
                pass

        def _copy(self):
            if self._g:
                QtWidgets.QApplication.clipboard().setText(report_text(self._g))

    return PlateLineupPane


def _open_pane():
    """Show the dockable pane (docked beside Properties). Raises on failure."""
    import nuke
    import nukescripts
    module = sys.modules.get(PANE_MODULE)
    if module is None:
        module = types.ModuleType(PANE_MODULE)
        sys.modules[PANE_MODULE] = module
    # Always the class from THIS (freshly re-executed) file.
    module.PlateLineupPane = _pane_class()
    expr = "__import__('sys').modules[%r].PlateLineupPane" % PANE_MODULE
    panel = nukescripts.panels.registerWidgetAsPanel(
        expr, PANE_TITLE, PANE_ID, True)
    module.panel = panel
    dock = nuke.getPaneFor("Properties.1")
    if dock is not None:
        panel.addToPane(dock)
    else:
        panel.addToPane()


def _run():
    import nuke
    import sgtk

    try:
        _open_pane()
        return
    except Exception as exc:
        print("[plate_lineup] pane unavailable (%s); using the dialog" % exc)

    engine = sgtk.platform.current_engine()
    if engine is None:
        nuke.message("Flow Production Tracking is not running in this session.")
        return
    try:
        g = gather(engine)
    except Exception as exc:
        nuke.message("Plate Lineup:\n\n%s" % exc)
        return
    text = report_text(g)
    print("\n" + text + "\n")

    def on_build():
        return _do_build(g)

    try:
        _show_dialog(text, on_build)
    except Exception as exc:
        print("[plate_lineup] dialog unavailable (%s); using nuke.ask" % exc)
        if nuke.ask("%s\n\nBuild lineup nodes?"
                    % text[:3000]):
            nuke.message("\n".join(on_build()))


def _reload_self():
    """Re-exec this file from disk so a menu click always runs current code
    (same reason as dailies_viewer._reload_self)."""
    spec = importlib.util.spec_from_file_location("plate_lineup",
                                                  os.path.abspath(__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_plate_lineup():
    _reload_self()._run()


def register(engine):
    """Register with Flow Production Tracking > Buffalo VFX (filed there by
    hooks/buffalo_menu.py; position set by its _LAYOUT)."""
    engine.register_command(
        MENU_LABEL,
        cmd_plate_lineup,
        {"short_name": "plate_lineup",
         "description": "Show Flow's lineup / timewarp data for this shot's "
                        "plates and build TimeOffset/TimeWarp nodes that "
                        "line each plate up with the shot."},
    )
