"""
qt_bake_bridge -- ask qt_bake_oiio.py what it is going to draw.

WHY THIS EXISTS
---------------
The August 2026 Nuke tool (QT_SlateBurnin) was a hand port of the bake's
rules into Nuke nodes. Within a month it was wrong: the bake was rewritten
on 10 Sept (fill-width / centre-crop / letterbox, --croptofull, the
display-window trap), gained a 2.39 framing mask, per-camera LUT selection,
CDL-from-plates lookup, colour_pipe toggles and vendor_code -- and the Nuke
tool still described the world as it was in August. Nobody had touched it;
it went stale by standing still.

A second hand-written copy of a rule drifts. So this module does not
reimplement anything. It imports the live bake and asks it:

    "given this payload, what do you draw, where, and at what size?"

Every number the Nuke graph uses comes back from here. If the bake changes a
margin, adds a burn-in field, or moves the status dots, the preview follows
without anyone editing the Nuke side.

HOW THE BURN-INS ARE READ
-------------------------
`build_drawtext_filters()` returns ONE ffmpeg filter-chain string -- the
mask, every burn-in, the status dots, and the slate gate, all encoded. We
parse that string back into structured items rather than re-deriving the
fields from `data`. That is deliberate: parsing the bake's own output means
a field it adds tomorrow shows up in the preview tomorrow, with no matching
edit here.

WHAT CANNOT BE MATCHED, AND WHY
-------------------------------
Be honest about this up front; the preview is a guide, not a proof.

1. TEXT RASTERISATION. ffmpeg's drawtext and Nuke's Text node rasterise
   glyphs differently. Anti-aliasing will never be identical.

2. CENTRE- AND RIGHT-ANCHORED POSITIONS. ffmpeg positions those with `tw`
   and `th` -- the text width and height IT measured. Nuke measures with its
   own metrics. Left-anchored items (x=40) land exactly; centred and
   right-aligned ones land within the metric difference between the two
   renderers. `resolve_position()` returns the expression AND what it
   depends on, so the Nuke side knows which is which instead of guessing.

3. THE BURN-IN FONT IS NOT PINNED. Look at the filters: only the slate title
   passes `fontfile=`. The burn-ins pass none, so ffmpeg falls back to
   whatever fontconfig resolves for the default family on the watcher
   machine. That is unpinned in the bake itself -- it can change when the
   machine's fonts change -- and it is why the preview cannot promise the
   same glyphs. Worth fixing in the bake (pin a fontfile for burn-ins too),
   at which point this note can go away.

4. THE PRORES ENCODE. Not reproducible in a viewer, full stop.

Geometry, colour transforms, text CONTENT and the layout rules are all exact
by construction. Glyph pixels are not. Say so in the UI.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

# Names this module depends on. Checked at import time so a change to the
# bake produces one clear error here rather than a confusing failure deep
# inside a node build. (buf.bake learned this the hard way: a sys.path
# insertion resolved correctly once and then kept resolving to a stale copy.
# Load by explicit file path, verify what you got.)
REQUIRED_CALLABLES = (
    "framing_plan",
    "build_drawtext_filters",
    "read_resolution",
    "read_pixel_aspect",
    "resolve_start_timecode",
    "is_shot_context",
    "compute_indicator_statuses",
)
REQUIRED_CONSTANTS = (
    "DELIVERY_WIDTH", "DELIVERY_HEIGHT", "FPS",
    "BURNIN_FONT_SIZE", "TITLE_FONT_PATH", "TITLE_FONT_SIZE",
    "TITLE_X", "TITLE_Y", "LOGO_MARGIN",
    "SBS_WIDTH", "SBS_HEIGHT", "SBS_SIZE",
    "MASK_ENABLED_DEFAULT", "MASK_RATIO", "MASK_OPACITY",
    "STATUS_DOT_SIZE", "STATUS_DOT_GAP", "STATUS_DOT_COLORS",
)

# Present only after the anamorphic blow-up patch. Optional on purpose, so
# this bridge works either side of that change and can SAY which it found.
OPTIONAL_NAMES = ("ANAMORPHIC_BLOWUP", "delivery_source_crop", "_crop_args")


class BakeUnavailable(RuntimeError):
    """The bake module could not be found, imported, or is the wrong shape."""


_CACHE = {}


def default_search_paths():
    """Where qt_bake_oiio.py is expected to be, most specific first.

    config/nuke/ (where the tool lives) is a sibling of flow/ on the share,
    so the relative hop is stable on any machine that has the share mounted,
    whatever the mount point or platform.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = []
    env = os.environ.get("BUF_QT_BAKE")
    if env:
        candidates.append(env)
    for up in (2, 3, 4):
        base = here
        for _ in range(up):
            base = os.path.dirname(base)
        candidates.append(os.path.join(
            base, "flow", "alts", "BUF_Mac_watcher", "scripts", "qt_bake_oiio.py"))
        candidates.append(os.path.join(
            base, "config", "flow", "alts", "BUF_Mac_watcher", "scripts",
            "qt_bake_oiio.py"))
    return candidates


def load(path=None, force=False):
    """Import qt_bake_oiio.py by explicit file path. Raises, never guesses."""
    if not force and "module" in _CACHE and (path is None or _CACHE.get("path") == path):
        return _CACHE["module"]

    tried = []
    for candidate in ([path] if path else default_search_paths()):
        if candidate and os.path.isfile(candidate):
            path = candidate
            break
        tried.append(candidate)
    else:
        raise BakeUnavailable(
            "Could not find qt_bake_oiio.py. Looked for:\n  %s\n"
            "Set BUF_QT_BAKE to its full path, or check the share is mounted."
            % "\n  ".join(str(t) for t in tried if t))

    spec = importlib.util.spec_from_file_location("_buf_qt_bake_oiio", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_buf_qt_bake_oiio"] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise BakeUnavailable("Found %s but it would not import: %s" % (path, exc))

    missing = [n for n in REQUIRED_CALLABLES if not callable(getattr(module, n, None))]
    missing += [n for n in REQUIRED_CONSTANTS if not hasattr(module, n)]
    if missing:
        raise BakeUnavailable(
            "%s is not the bake this tool understands - missing: %s\n"
            "The bake has changed shape; this bridge needs updating to match."
            % (path, ", ".join(missing)))

    _CACHE["module"] = module
    _CACHE["path"] = path
    return module


def capabilities(module=None):
    """What this particular copy of the bake can do. No silent assumptions."""
    m = module or load()
    return {
        "path": _CACHE.get("path"),
        "anamorphic_blowup": getattr(m, "ANAMORPHIC_BLOWUP", None),
        "has_source_crop": callable(getattr(m, "delivery_source_crop", None)),
    }


# ---------------------------------------------------------------------------
# Parsing the filter chain back into structured items
# ---------------------------------------------------------------------------
#
# ffmpeg filter syntax, as this bake emits it:
#   filters joined by ","     (a literal comma inside a value is "\,")
#   each is  name=k=v:k=v     (a literal colon inside a value is "\:")
#   values may be wrapped in '...'
#
# Splitting naively on "," or ":" corrupts the gate expression
# (:enable='gte(n\,1)') and every escaped colon in a timecode. So both splits
# below are escape-aware.

def _split_unescaped(text, sep):
    out, buf, esc = [], [], False
    for ch in text:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            buf.append(ch)
            esc = True
        elif ch == sep:
            out.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    out.append("".join(buf))
    return out


def _unescape(value):
    return re.sub(r"\\(.)", r"\1", value)


def _parse_filter(text):
    """'drawtext=text='x':y=40' -> ('drawtext', {'text': 'x', 'y': '40'})"""
    text = text.strip()
    if "=" not in text:
        return text, {}
    name, _, rest = text.partition("=")
    opts = {}
    for chunk in _split_unescaped(rest, ":"):
        if "=" not in chunk:
            continue
        k, _, v = chunk.partition("=")
        v = v.strip()
        if len(v) >= 2 and v[0] == "'" and v[-1] == "'":
            v = v[1:-1]
        opts[k.strip()] = _unescape(v)
    return name.lower(), opts


def parse_filter_chain(chain):
    """The chain string -> [(name, opts), ...] in draw order."""
    if not chain:
        return []
    return [_parse_filter(f) for f in _split_unescaped(chain, ",") if f.strip()]


# ---------------------------------------------------------------------------
# Position expressions
# ---------------------------------------------------------------------------

_ANCHOR_RE = {
    "centre": re.compile(r"^\(\s*[wh]\s*-\s*t[wh]\s*\)\s*/\s*2$"),
    "from_right": re.compile(r"^w\s*-\s*(\d+)\s*-\s*tw$"),
    "from_bottom": re.compile(r"^h\s*-\s*(\d+)\s*-\s*th$"),
    "fixed": re.compile(r"^\d+$"),
}


def resolve_position(expr, axis, frame_w, frame_h):
    """
    Turn an ffmpeg x/y expression into something the Nuke side can place.

    Returns (anchor, value, exact) where:
      anchor : 'fixed' | 'centre' | 'from_right' | 'from_bottom' | 'expr'
      value  : the pixel offset that anchor needs
      exact  : True when the result does NOT depend on ffmpeg's measured text
               size, i.e. Nuke can land on the same pixel

    'fixed' is exact. Everything anchored to tw/th is not, because ffmpeg
    measured the text with its font and Nuke will measure with its own. The
    Nuke side should place those by the same RULE (centre it, right-align it
    to a 40px margin) rather than by a copied pixel value -- that way the
    layout reads correctly even though the glyph box differs.
    """
    expr = (expr or "").strip()
    if _ANCHOR_RE["fixed"].match(expr):
        return ("fixed", int(expr), True)
    if _ANCHOR_RE["centre"].match(expr):
        return ("centre", 0, False)
    m = _ANCHOR_RE["from_right"].match(expr)
    if m:
        return ("from_right", int(m.group(1)), False)
    m = _ANCHOR_RE["from_bottom"].match(expr)
    if m:
        return ("from_bottom", int(m.group(1)), False)
    return ("expr", expr, False)


# ---------------------------------------------------------------------------
# The public shape the Nuke side consumes
# ---------------------------------------------------------------------------

class Item(dict):
    """One thing to draw. dict so it stays trivially printable/serialisable."""


def overlay_items(data, frame_offset=0, start_tc="00:00:00:00",
                  cdl_status=None, lut_status=None, burnin_first_frame=0,
                  module=None):
    """
    Everything the bake draws over the picture, as structured items.

    Built by calling the bake's own build_drawtext_filters() and reading the
    result back, so this cannot disagree with what gets encoded.
    """
    m = module or load()
    chain = m.build_drawtext_filters(
        data, frame_offset, start_tc,
        cdl_status=cdl_status, lut_status=lut_status,
        burnin_first_frame=burnin_first_frame)

    W, H = m.DELIVERY_WIDTH, m.DELIVERY_HEIGHT
    items = []
    for name, opts in parse_filter_chain(chain):
        gate = opts.get("enable")
        if name == "drawbox":
            items.append(Item(
                kind="box",
                x=opts.get("x"), y=opts.get("y"),
                w=opts.get("w"), h=opts.get("h"),
                color=opts.get("color"),
                gate=gate,
                # A mask bar is the one whose width is the whole frame.
                is_mask=(opts.get("w") == "iw"),
            ))
        elif name == "drawtext":
            xa, xv, xe = resolve_position(opts.get("x"), "x", W, H)
            ya, yv, ye = resolve_position(opts.get("y"), "y", W, H)
            items.append(Item(
                kind="text",
                text=opts.get("text"),
                timecode=opts.get("timecode"),
                timecode_rate=opts.get("timecode_rate"),
                font_size=int(opts.get("fontsize") or m.BURNIN_FONT_SIZE),
                font_file=opts.get("fontfile"),
                color=opts.get("fontcolor", "white"),
                box=opts.get("box") == "1",
                box_color=opts.get("boxcolor"),
                box_border=int(opts.get("boxborderw") or 0),
                x_anchor=xa, x_value=xv, x_exact=xe,
                y_anchor=ya, y_value=yv, y_exact=ye,
                gate=gate,
            ))
    return items


def framing(src_w, src_h, blowup=None, module=None):
    """
    Delivery geometry for a source of this size, straight from the bake.

    Returns a dict with the oiiotool args, the human description the bake
    logs, and -- when the blow-up patch is present -- the source-space crop
    rectangle both the movie and the slate thumbnails use.
    """
    m = module or load()
    if blowup is None:
        blowup = 1.0
    out_w, out_h = m.DELIVERY_WIDTH, m.DELIVERY_HEIGHT
    try:
        args, note = m.framing_plan(src_w, src_h, out_w, out_h, blowup=blowup)
    except TypeError:
        # Pre-blow-up bake: framing_plan has no blowup parameter at all.
        args, note = m.framing_plan(src_w, src_h, out_w, out_h)
        blowup = 1.0

    crop = None
    fn = getattr(m, "delivery_source_crop", None)
    if callable(fn):
        crop = fn(src_w, src_h, out_w, out_h, blowup)

    return {
        "args": list(args),
        "note": note,
        "source_crop": crop,
        "blowup": blowup,
        "out_w": out_w,
        "out_h": out_h,
    }
