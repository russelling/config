# Buffalo VFX Nuke Tools

Custom studio Nuke tools, auto-loaded into every Toolkit-launched Nuke /
Nuke Studio session (via ShotGrid Desktop) as a "Buffalo VFX" top-level
menu.

## Why this loads automatically

`menu.py` in this folder is Nuke's standard auto-run entry point — any
folder on `NUKE_PATH` gets its `menu.py` executed at startup, and Nuke adds
that same folder to `sys.path`, which is why `menu.py` can `from tools
import ...` directly.

This folder ends up on `NUKE_PATH` via
`flow/current/config/hooks/tk-multi-launchapp/before_app_launch.py`, which
Toolkit runs before every Nuke launch. That hook also still handles the
pre-existing CameraTracker film-back presets (a *different* `nuke/` folder,
the one inside the Toolkit config itself at
`flow/current/config/nuke/`) — the two are unrelated, both just happen to
be named `nuke/` at different levels of the tree.

## Repo status

This is intentionally **not** part of `FlowTrackingConfig`'s own git
history — same pattern this pipeline already uses for `BUF_Mac_watcher`
and `ingest_pipeline`: a separate repo, checked out here as a sibling of
`flow/`, referenced (not duplicated) by a couple of lines in the main
config. Turn this folder into its own git repo (`git init`, add a GitHub
remote, first commit) whenever you're ready — nothing about how it loads
depends on it being git-tracked, that's purely for change history.

## Structure

```
nuke/
├── menu.py            <- Nuke auto-loads this; registers the "Buffalo VFX"
│                          menu AND the matching left-toolbar flyout
├── icons/
│   └── buf_icon.png    <- see "Menu icon" below
├── tools/
│   ├── __init__.py
│   └── cg_element_relight.py   <- one module per tool
└── README.md           <- this file
```

## Adding a new tool

1. Write it as a module under `tools/`, exposing one function that builds
   or runs the tool — follow `tools/cg_element_relight.py`'s shape: a
   `build_<name>()` function with no required arguments, since it's called
   directly as a Nuke menu command.
2. In `menu.py`, add one `("Display Name", module.build_thing)` tuple to
   the `_TOOLS` list — that's it, both the top menu and the left toolbar
   pick it up automatically (see "Left toolbar" below).
3. Push the change (once this is a proper git repo) — every artist picks it
   up next time they launch Nuke through ShotGrid Desktop, no manual
   install step.

## Left toolbar

As of 2026-09-13, every tool in `_TOOLS` shows up in two places: the top
**Buffalo VFX** menu (`nuke.menu("Nuke")`) and a matching **Buffalo VFX**
flyout on the left **Nodes** toolbar (`nuke.toolbar("Nodes")`). Both are
built from the same `_TOOLS` list, but **only the toolbar carries the
icon** — per Mark (2026-09-13), the top menu stays plain text on purpose.
`_register(parent_menu, icon)` in `menu.py` takes the icon as a parameter
so this asymmetry is explicit rather than accidental: `_register(nuke.menu("Nuke"), icon=None)`
vs `_register(nuke.toolbar("Nodes"), icon=_ICON)`.

## Current tools

- **CG Element Relight** (`tools/cg_element_relight.py`) — recombines the
  Diffuse/Glossy/Emission passes from a Blender CG-element multi-layer EXR
  into one relit image with five intensity sliders, plus a checkbox to
  pass the Depth/Z pass through as Nuke's native `depth` channel. Pass
  names in `LAYER_DEFAULTS` are pinned to this studio's real View Layer
  naming (`ViewLayer_Diffuse_Direct`, etc, confirmed 2026-09-13) — if any
  pass doesn't match on a given render, the node's tile turns red/orange
  and a warning on the Relight tab names exactly which ones. See the
  project doc `cg-element-relight-nuke-tool-2026-09-13.md` for the full
  design and known caveats.

## Menu icon

`icons/buf_icon.png` (with `buf_teardrop_48.png` / `buf_teardrop_master.png`
as larger masters for future use) is the studio teardrop mark with its
solid black backing removed, derived from
`shots/_globals/logo/teardrop_blk1.png`. `menu.py` passes it as `icon=`
only on the left-toolbar registration (see "Left toolbar" above), built as
an **absolute** path
(`os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", ...)`)
rather than the plain relative `"icons/buf_icon.png"` first tried — see
Fix history. Add a new icon the same way: source PNG in `icons/`, then
reuse `_ICON`, or build another absolute path the same way for a
tool-specific icon.

## Fix history

- **2026-09-13 — depth passthrough was overriding the whole image, not
  just adding a depth channel.** Mark reported: "The depth layer seemed to
  be overriding all the other channels in the previous version." The
  original depth passthrough was a second Shuffle2 (pulling just the depth
  layer) feeding a `Merge2(operation="copy")` meant to merge that one
  layer onto the recombined image. Root cause: **Merge2 has no `"channels"`
  knob at all** — only `Achannels`/`Bchannels`/`output` — so
  `depth_copy["channels"].setValue("depth")` silently did nothing (same
  class of mistake as the earlier Shuffle2 `"output"` vs `"out1"` bug),
  leaving all three at their `"rgba"` default. Per Nuke's own reference
  docs, the `"copy"` operation's formula is literally `= A` for every
  channel in `output` — and since the depth-only Shuffle2 sat in the "A"
  slot with its own rgba zeroed out (because remapping its `in1`/`out1` to
  the depth layer removed its rgba identity-passthrough), the entire
  result collapsed to that near-black rgba. That's exactly "depth
  overriding everything." **Fixed by removing Merge2/the Copy node from
  this entirely** — `_depth_passthrough()` in `tools/cg_element_relight.py`
  is now a single two-input Shuffle2: row 1 (`in1`/`out1`) is a plain
  identity passthrough of the recombined image's rgba (input 0), row 2
  (`in2`/`out2`) independently pulls the depth layer from the original
  Read input (input 1) — Shuffle2's two-input design needs no ambiguous
  channel-routing knobs to get this right. Disabling the node (the "Map
  Depth to depth Channel" checkbox) falls back to Nuke's ordinary
  disabled-node behavior (pass input 0 straight through), so unchecking it
  is a clean no-op.
- **2026-09-13 — the actual reason nothing was connecting: hardcoded
  layer names never matched this studio's View Layer naming.** Even after
  the connection-order fix just below, Mark reported it "still not
  connecting properly" and sent a screenshot of the Read node's real
  channel list: every pass is named `ViewLayer_<Full_Pass_Name>` (e.g.
  `ViewLayer_Diffuse_Direct`, `ViewLayer_Glossy_Indirect`,
  `ViewLayer_Depth`) — not Blender's generic short-form abbreviations
  (`DiffDir`, `GlossInd`, `Depth`) `LAYER_DEFAULTS` had been hardcoded to
  since the very first version of this tool. Those never existed on any
  real input, so every `in1` set silently failed the whole time — the
  connection-order bug was real too, but layer names being wrong outright
  was the dominant cause. Fixed: `LAYER_DEFAULTS` in
  `tools/cg_element_relight.py` now uses the real
  `ViewLayer_Diffuse_Direct` / `ViewLayer_Diffuse_Indirect` /
  `ViewLayer_Diffuse_Color` / `ViewLayer_Glossy_Direct` /
  `ViewLayer_Glossy_Indirect` / `ViewLayer_Glossy_Color` /
  `ViewLayer_Emission` / `ViewLayer_Depth` names. Also hardened
  `_shuffle_layer()` so this class of mismatch can't hide silently again:
  it now verifies the channel knob actually holds the value it just set
  (Channel_Knob.setValue doesn't reliably raise just because a channel is
  absent), and `build_cg_element_relight()` turns the Group's tile
  red/orange and adds a warning Text_Knob naming every mismatched pass
  directly on the node, instead of only printing to the Script Editor
  console where it's easy to miss.
- **2026-09-13 — autolabel fix from the same day didn't actually work; the
  real fix.** Mark rebuilt the node and reported it was still labeled
  "expecting a knob" with black output, seemingly unchanged. Digging into
  Nuke's own docs (`nuke.addAutolabel`) turned up why the earlier
  "clear the autolabel knob" fix (below) was incomplete: per Foundry,
  *"if the autolabel knob is not blank and returns anything other than
  None, the return value is displayed... [otherwise] the registered
  callback functions execute."* Blanking the knob to `""` doesn't disable
  autolabel at all — it just means "no per-node override," so Nuke falls
  through to the exact same broken global default that was crashing in
  the first place. Real fix: `_quiet_autolabel()` now sets `autolabel` to
  `"nuke.thisNode()['label'].value()"` -- a python expression that always
  evaluates to a real string (whatever we've put in the node's own
  `label` knob), so Nuke gets a valid non-None result straight from us
  and never falls through to the buggy global script.
  (Separately: if you see this same symptom again after this fix ships,
  first check whether Nuke was actually restarted — a long-running Nuke
  session keeps whatever version of this module it imported at startup in
  memory, so editing the .py files and clicking the menu again in the
  *same* session re-runs the stale cached code, not the new file. Fully
  quit and relaunch Nuke, don't just delete/recreate the node.)
- **2026-09-13 — added a "Map Depth to depth Channel" checkbox.** New
  `Shuffle_Depth` (source layer `LAYER_DEFAULTS["depth"]`, default
  `"Depth"`) feeds a `Merge2(operation="copy", channels="depth")` that
  copies just that layer onto the final recombined image, leaving rgba
  untouched. The checkbox (`map_depth`, on by default) drives that
  Merge2's own `disable` knob via expression (`!parent.map_depth`), so
  it's live and toggleable at any time, not a build-time choice.
- **2026-09-13 — Shuffle2 channels not sticking, icon not visible.** Two
  more real-testing findings:
  - **Shuffles connected but not routed correctly, even after the `out1`
    fix.** Mark reported that manually repointing each Shuffle2's `in1`
    dropdown by hand fixed everything, meaning the layer names themselves
    were always right — the *script's* wiring wasn't. Root cause: the old
    code set `in1`/`out1` on each Shuffle2 **before** connecting its
    input (the `Input` node was wired to all seven Shuffles in a separate
    loop afterward). Those knobs are Channel_Knobs whose dropdown is
    populated from whatever's arriving on input 0; setting them pre-connection
    let the value "take" on the unconnected node, but once Nuke rebuilt the
    channel menu after the real connection landed, it silently reset to
    default instead of keeping our value — exactly matching manual
    (post-connection) edits working while scripted (pre-connection) ones
    didn't. Fixed: `_shuffle_layer()` now takes the input node as a
    parameter and connects it first, before touching `in1`/`out1`.
  - **Icon not visible anywhere.** Switched `_ICON` in `menu.py` from the
    relative `"icons/buf_icon.png"` (relying on Nuke's plugin-path icon
    search) to an absolute path built from the module's own location, to
    remove that as a variable. Also worth knowing regardless: Nuke's top
    pulldown menu often doesn't render icons on plain menu items at all —
    only toolbar buttons do — so the left Nodes toolbar is the place to
    actually look for it.
- **2026-09-13 — Group node label also showed "expecting a knob".** After
  the `out1` fix below, the tool worked but the *Group* node itself (not
  just the internal Shuffles) displayed the literal text "expecting a
  knob" as its on-DAG label. Turns out every Nuke node has a hidden
  `autolabel` knob holding the script Nuke runs to compute that label
  automatically, and a bare scripted `Group` node hits the same broken
  default script the Shuffle2s did. Fixed generally: added
  `_quiet_autolabel()` in `tools/cg_element_relight.py`, which clears the
  `autolabel` knob (`node["autolabel"].setValue("")`) so Nuke never runs
  that script on our nodes at all — used on the Group and every Shuffle2,
  each then given a plain static `label` we set ourselves. This is the
  more robust fix the `out1` entry below was dancing around.
- **2026-09-13 — Shuffle2 "output" vs "out1" (functional bug, not just the
  earlier autolabel crash).** After the autolabel crash fix below, the tool
  ran but a compositor reported the Group's auto-generated node labels
  looked wrong and the relight wasn't actually doing anything. Root cause:
  Shuffle2's output-layer knob is named `out1`, not `output` — the code
  was calling `sh["output"].setValue("rgba")`, which doesn't exist on
  Shuffle2 and was silently swallowed by a try/except, so none of the
  seven Shuffle2 nodes were ever actually routed to `rgba`. Fixed in
  `tools/cg_element_relight.py`'s `_shuffle_layer()`. Because Shuffle2's
  own autolabel derives its on-node text from the real in1/out1 mapping,
  this single fix also resolved the "wrong name" symptom — both were the
  same root cause.
- **Autolabel crash (`ValueError: expecting a knob`).** Passing an explicit
  `label=` into the `Shuffle2` constructor conflicted with Nuke's own
  autolabel script for that class. Fixed by dropping the constructor
  `label=` and letting Nuke label the node itself.

## Not yet migrated in

`QT_SlateBurnin` (delivered 2026-08-17, see
`qt-slate-burnin-nuke-tool.md`) currently lives loose in this same parent
folder at `../qt_slate_burnin.py/qt_slate_burnin_nuke.py` (an odd nested
layout from before this repo had real structure). It hasn't been reviewed
or moved into `tools/` as part of this pass — worth folding in properly
once someone's confirmed it still works, rather than moving it blind.
