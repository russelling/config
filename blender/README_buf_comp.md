# BUF Comp — Nuke-style compositing for Blender 5.1

Two things, built to be used together:

1. **A node-group library** (`assets/buf_comp/`): 16 compositor groups that do what
   the Nuke node of the same name does, with Nuke's maths.
2. **An add-on** (`addons/buf_comp_nuke_tools.py`): Nuke hotkeys in the compositor,
   a **Shift+A › BUF** menu, and the library registered on the asset shelf.

Blender **5.1 or later** only. The compositor API changed in 5.0, so none of this
works on 4.x.

## Install

Copy this folder's contents into `repo/pipeline/config/blender/` on the share,
next to the existing `addons/` and `README.md`. Nothing existing is overwritten.

```
config/blender/
├── addons/buf_comp_nuke_tools.py        <- the add-on
├── assets/buf_comp/
│   ├── buf_comp_library.blend           <- the groups
│   ├── blender_assets.cats.txt          <- their catalogs
│   └── buf_comp_index.json              <- the add-on's menu
├── tools/
│   ├── build_buf_comp_library.py        <- rebuilds the library
│   ├── test_buf_comp_library.py         <- checks the maths
│   └── test_buf_comp_addon.py           <- checks the add-on
└── README_buf_comp.md
```

Then, once per machine: launch Blender through Flow, open
**Preferences › Add-ons**, enable **BUF Comp: Nuke-style tools**.

Toolkit launches set `BLENDER_SYSTEM_SCRIPTS` to `config/blender`, which is how
Blender finds the add-on. If it is not in the Add-ons list, that wiring is the
thing to check (see `claude/nuke-blender-tool-repos-2026-09-13.md`).

## The groups

| Catalog | Group | Nuke equivalent |
|---|---|---|
| Merge | `BUF_Merge` | Merge — Over, Under, Plus, Minus, From, Multiply, Screen, Max, Min, Difference, Divide, In, Out, Mask, Stencil, on one dropdown; Mask + Mix |
| Merge | `BUF_KeyMix` | KeyMix |
| Color | `BUF_Grade` | Grade (blackpoint, whitepoint, lift, gain, multiply, offset, gamma, black/white clamp, unpremult) |
| Color | `BUF_Grade_RGB` | Grade with per-channel knobs |
| Color | `BUF_Saturation` | Saturation (Rec.709 luma) |
| Color | `BUF_Clamp` | Clamp |
| Channel | `BUF_Premult` / `BUF_Unpremult` | Premult / Unpremult |
| Channel | `BUF_Copy` | Copy (alpha), optional premult |
| Channel | `BUF_Shuffle_AlphaToRGB` | Shuffle alpha → rgb |
| Channel | `BUF_Shuffle_SolidAlpha` | Shuffle white → alpha |
| Keyer | `BUF_Keyer_Luma` | Keyer (luminance) |
| Format | `BUF_AspectMask` | 2.39 letterbox guide, same default as the QT review bake |
| CG | `BUF_AOV_Rebuild_Cycles` | Cycles light-pass rebuild with a gain per lobe |
| QC | `BUF_QC_Difference` | Difference check |
| QC | `BUF_QC_Range` | Flags negatives (magenta) and values over a maximum (cyan) |

Inputs follow Nuke's order: **B first, then A**, so a node wired in after your
selection lands on B, as in Nuke.

## Hotkeys (compositor only)

| Key | Does | Blender's own key, if it had one |
|---|---|---|
| Tab | Node search | group edit → **Ctrl+Return** (exit stays Ctrl+Tab) |
| G | Grade | move → drag the node |
| M | Merge | mute → **D** |
| B | Blur | box select → drag on empty space |
| C | ColorCorrect (Blender's Color Correction node) | circle select |
| T | Transform | toolbar |
| R | Read (Image node) | rotate |
| W | Write (File Output) | context menu → right-click |
| F | Fit selected, or all | join in frame → Ctrl+J |
| 1 | View the active node | viewer slot 1 |
| D | Disable (mute) | — |
| K | Copy (alpha) | — |
| O | Roto (Mask node) | — |
| . | Dot (reroute) | — |
| Backspace | Delete | — |
| Shift+X | Swap A/B | — |

With a node selected, a new node is **wired in after it and spliced into its
downstream links**, as in Nuke. With nothing selected, it follows the mouse.

The first ten keys override Blender's, **in the compositor only**. Shader and
geometry node editors keep Blender's keys. Untick *Override Blender keys in the
compositor* in the add-on's preferences to keep Blender's everywhere, or
*Nuke hotkeys* to turn them all off.

## Things to know

- **Groups travel inside the .blend.** Hotkeys and Shift+A append a group
  (reusing it if the file already has it). The asset shelf uses Blender 5.1's
  default import method, *Pack*: the group is linked, packed into the file and
  locked like a Nuke gizmo. Use **Make Local** to edit one. Either way the file
  needs neither the library nor the share, so it is safe on the farm.
- **Merge evaluates all 15 operations every frame**, since Blender has no way to
  skip the unused ones. That costs about 0.25 s per HD frame on a 2-core CPU, and
  nothing noticeable on the GPU compositor.
- **`BUF_AOV_Rebuild_Cycles` is for Cycles passes only.** EEVEE's passes combine
  differently and are not covered.
- **C gives Blender's Color Correction node**, the closest built-in to Nuke's
  ColorCorrect. Its ranges and controls are not identical.

## Rebuilding and testing

On any Mac with Blender 5.1 and the share mounted, from `config/blender/tools/`:

```
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python build_buf_comp_library.py
```
```
/Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python test_buf_comp_library.py
```
```
BLENDER_SYSTEM_SCRIPTS="$(cd .. && pwd)" /Applications/Blender.app/Contents/MacOS/Blender -b --factory-startup --python test_buf_comp_addon.py
```

The library test renders every group in the real compositor, on pixels that
include zero alpha, negatives and values over 1. It compares each one with a
NumPy statement of Nuke's formula. To add a group, write a `build_*` function
in the build script, add it to `BUILDERS`, and add a check to the test.
