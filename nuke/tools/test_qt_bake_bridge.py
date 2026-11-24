#!/usr/bin/env python3
"""
Prove qt_bake_bridge reports what the bake actually does.

Runs WITHOUT Nuke -- that is the point. The arithmetic and the strings are
where a preview goes wrong, and all of that is testable here. Only node
wiring needs a real Nuke session.

    python3 test_qt_bake_bridge.py <path-to-qt_bake_oiio.py> [<path2> ...]

Pass both the patched and unpatched bake to prove the bridge works either
side of the anamorphic blow-up change.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qt_bake_bridge as br

FAILS = []


def check(label, cond, detail=""):
    print("%s %s%s" % ("  ok  " if cond else " FAIL ", label,
                       ("  -- %s" % detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


SHOT = {"type": "shot", "shot_code": "301_999_0010", "step": "comp",
        "version": 3, "artist": "Mark", "date": "2026-09-15T12:00:00"}
ASSET = {"type": "asset_turntable", "entity_name": "HeroChair",
         "asset_type": "Prop", "step": "turntable", "version": 1,
         "date": "2026-09-15"}


def run(path):
    print("\n=== %s" % path)
    m = br.load(path, force=True)
    caps = br.capabilities(m)
    print("    blow-up: %s | source crop: %s"
          % (caps["anamorphic_blowup"], caps["has_source_crop"]))

    # --- the chain round-trips: every filter becomes exactly one item
    chain = m.build_drawtext_filters(SHOT, 1000, "00:00:41:17",
                                     cdl_status="ok", lut_status="warn",
                                     burnin_first_frame=1)
    parsed = br.parse_filter_chain(chain)
    items = br.overlay_items(SHOT, 1000, "00:00:41:17", cdl_status="ok",
                             lut_status="warn", burnin_first_frame=1, module=m)
    check("every filter becomes one item", len(parsed) == len(items),
          "%d filters, %d items" % (len(parsed), len(items)))

    # --- escaped separators survived
    texts = [i["text"] for i in items if i["kind"] == "text"]
    check("escaped colons unescaped in text",
          all("\\:" not in (t or "") for t in texts))
    gated = [i for i in items if i.get("gate")]
    check("slate gate parsed, not split on its escaped comma",
          gated and all("gte(n,1)" in i["gate"] for i in gated),
          str(gated[0]["gate"]) if gated else "no gated items")

    # --- content follows the bake's own branching
    check("shot burn-in uses shot_code + vendor fallback",
          "301_999_0010_INH_comp_v003" in texts, str(texts))
    check("date truncated to 10 chars by the bake", "2026-09-15" in texts,
          str(texts))
    a_texts = [i["text"] for i in br.overlay_items(ASSET, 0, "00:00:00:00",
                                                   module=m)
               if i["kind"] == "text"]
    check("asset burn-in uses asset_type - entity_name",
          "Prop - HeroChair" in a_texts, str(a_texts))
    check("asset lower-left has no vendor code",
          "HeroChair_turntable_v001" in a_texts, str(a_texts))

    # --- timecode is an option, not body text
    tc = [i for i in items if i["kind"] == "text" and i["timecode"]]
    check("timecode captured with its rate",
          len(tc) == 1 and tc[0]["timecode"] == "00:00:41:17"
          and tc[0]["timecode_rate"] == str(m.FPS))

    # --- per-flag overrides go THROUGH the bake, not around it
    n_default = sum(1 for i in items if i.get("is_mask"))
    n_off = sum(1 for i in br.overlay_items(dict(SHOT, aspect_mask=False), 0,
                                            "00:00:00:00", module=m)
                if i.get("is_mask"))
    check("aspect_mask default draws 2 bars", n_default == 2, str(n_default))
    check("aspect_mask=False draws none", n_off == 0, str(n_off))
    bars = [i["h"] for i in br.overlay_items(dict(SHOT, aspect_mask_ratio=1.85),
                                             0, "00:00:00:00", module=m)
            if i.get("is_mask")]
    check("aspect_mask_ratio override reaches the bar expression",
          bars and "1.85" in bars[0], str(bars[:1]))

    # --- which positions Nuke can match exactly, and which it cannot
    left = [i for i in items if i["kind"] == "text" and i["x_anchor"] == "fixed"]
    centred = [i for i in items if i["kind"] == "text" and i["x_anchor"] == "centre"]
    check("left-anchored positions reported exact",
          left and all(i["x_exact"] for i in left))
    check("centre-anchored positions reported NOT exact",
          centred and not any(i["x_exact"] for i in centred))

    # --- status dots land on absolute pixels (no text metrics involved)
    dots = [i for i in items if i["kind"] == "box" and not i["is_mask"]]
    check("status dots are absolute pixel boxes", len(dots) == 4
          and all(str(d["x"]).isdigit() for d in dots), str(len(dots)))

    # --- framing agrees with the bake, blow-up or not
    f = br.framing(4096, 1716, blowup=caps["anamorphic_blowup"] or 1.0, module=m)
    expect_args, expect_note = (
        m.framing_plan(4096, 1716, m.DELIVERY_WIDTH, m.DELIVERY_HEIGHT,
                       blowup=caps["anamorphic_blowup"])
        if caps["anamorphic_blowup"] else
        m.framing_plan(4096, 1716, m.DELIVERY_WIDTH, m.DELIVERY_HEIGHT))
    check("framing args come straight from the bake", f["args"] == list(expect_args))
    check("framing note comes straight from the bake", f["note"] == expect_note)
    if caps["has_source_crop"]:
        check("source crop present when the bake has it",
              f["source_crop"] is not None, str(f["source_crop"]))
        check("no crop when blow-up is 1.0",
              br.framing(4096, 1716, blowup=1.0, module=m)["source_crop"] is None)
    else:
        check("older bake: no crop offered, and none invented",
              f["source_crop"] is None)


def main():
    paths = sys.argv[1:]
    if not paths:
        print(__doc__)
        return 2
    for p in paths:
        run(p)
    print("\n%s" % ("ALL PASS" if not FAILS
                    else "FAILED: %s" % ", ".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
