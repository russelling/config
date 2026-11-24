# CG_Element_Relight — Nuke relight tool for Blender CG elements

## What it is

A single Nuke Group node with five sliders — Diffuse Direct, Diffuse
Indirect, Glossy Direct, Glossy Indirect, Emission — that recombines the
lighting passes from a Blender multi-layer EXR (written by the "Flow - Set
Element Render Output" add-on) into one relit image. Push bounce light up,
kill reflections, dial back a hot emissive glow — all after the render,
no re-rendering the Blender element.

## How to install it

This is now wired into Nuke's toolbar automatically — no paste-and-run
needed. Every Toolkit-launched Nuke session gets a **Buffalo VFX** menu
(with the studio teardrop icon) containing **CG Element Relight**; click
it and a new `CG_Element_Relight` node appears in your current Node Graph.
Click it again any time you need another instance (Nuke auto-numbers it
`CG_Element_Relight1`, etc.). See `../README.md` in the `nuke/` tools repo
for how the menu itself is built.

The paste-and-run route (below) still works if you ever need to run it
somewhere that isn't Toolkit-launched:

1. Open Nuke.
2. Open the **Script Editor** (Windows menu → Script Editor).
3. Paste in the entire contents of `cg_element_relight_tool.py`.
4. Press **Ctrl+Return** (Cmd+Return on Mac) to run it.
5. A node named **CG_Element_Relight** appears in your current Node Graph.

## How to use it

1. Connect your multi-layer EXR **Read** node into CG_Element_Relight's
   single input.
2. Open its properties panel — the **Relight** tab has the five sliders,
   all defaulting to 1.0 (i.e. matching the original render).
3. Adjust to taste. Its output feeds the rest of your comp like any other
   node.

## Channel names — check this first

The tool is pre-wired to this studio's actual View Layer naming (confirmed
2026-09-13 from a real Read node): `ViewLayer_Diffuse_Direct`,
`ViewLayer_Diffuse_Indirect`, `ViewLayer_Diffuse_Color`,
`ViewLayer_Glossy_Direct`, `ViewLayer_Glossy_Indirect`,
`ViewLayer_Glossy_Color`, `ViewLayer_Emission`, `ViewLayer_Depth`.
Depending on Blender version and View Layer naming, your Read node might
show slightly different layer names — if a different show or a renamed
View Layer changes these, the node will tell you: any pass whose name
doesn't match anything on the input turns the node's tile red/orange and
lists exactly which passes on its Relight tab, so you don't have to guess
from a black output alone.

To check: select the Read node, look at its Channels list. If a pass is
flagged as mismatched (or looks black even though the node isn't flagged
anything), double-click into the group, select the matching `Shuffle_*`
node, and repoint its `in1` dropdown to the correct layer name from the
Read node's channel list. Nothing else in the group needs to change.

## What this does *not* do

- **Not a perfect recombination of the beauty pass.** Transmission,
  volume, and environment/AO passes aren't part of the set this add-on
  currently writes, so the five-pass sum won't sum back to an exact match
  of Combined/beauty — it's built for relighting adjustments, not lossless
  reconstruction.
- **Doesn't touch Normal or Depth/Z.** Those are utility passes (feed a
  ZDefocus, a relight-normal setup, etc.), not something merged back into
  the beauty here.
- **Doesn't do Cryptomatte isolation.** Use Nuke's own Cryptomatte node
  directly on the Read's `CryptoObject`/`CryptoMaterial` layers for
  object/material isolation — that's already a turnkey Nuke feature and
  this tool doesn't duplicate it.
- **Diffuse/Glossy Color passes aren't independently adjustable** — they
  multiply the direct+indirect sum as albedo, same as they do in the
  original render. If you want to grade albedo separately too, that's a
  small addition — just ask.

## Fix history

- **2026-09-13** — several rounds of real-testing fixes: the pre-wired
  layer names were Blender's generic short-form pass abbreviations
  (`DiffDir`, `GlossInd`, ...), which never matched this studio's actual
  `ViewLayer_<Full_Pass_Name>` naming and meant every Shuffle came up
  empty regardless of anything else; a Shuffle2 connection-order bug
  meant even correct layer names wouldn't have stuck; a wrong knob name
  (`output` instead of `out1`) meant no pass was ever routed to `rgba`;
  and the Group/Shuffle2 nodes' `autolabel` knob crashed with "expecting
  a knob" (two fix attempts — see `../README.md`'s Fix history for why
  the first one didn't work); the "Map Depth to depth Channel" checkbox,
  once added, initially overrode the ENTIRE image with near-black instead
  of just adding a depth channel (a Merge2 node was being told to grab
  channels via a knob, `"channels"`, that doesn't actually exist on
  Merge2 — that call silently failed, so the node fell back to its
  "rgba" default and effectively replaced the whole image; rebuilt as a
  single Shuffle2 instead, which has no such ambiguity). All fixed as of
  2026-09-13, plus a visible red/orange tile+warning on the node itself
  if a pass name ever fails to match again. If you last pulled this tool
  before 2026-09-13, update it — see `../README.md`'s Fix history for the
  full writeup of each issue.
