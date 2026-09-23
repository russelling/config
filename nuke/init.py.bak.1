# Nuke startup (loaded via NUKE_PATH from ShotGrid Desktop before_app_launch).
# Registers studio film-back presets for Camera / CameraTracker, and sets the
# studio default OCIO config for brand-new scripts.

import nuke

try:
    import nukescripts.camerapresets as camerapresets
except ImportError:
    camerapresets = None


# ── Default OCIO config for new scripts ──────────────────────────────────────
# knobDefault only changes what a NEW Root node gets when created -- it does
# not touch scripts that already have these knobs saved, so opening an older
# script keeps whatever config it was authored with.
#
# "ocio://studio-config-latest" is the same built-in ACES 1.3 Studio config
# reference qt_bake_oiio.py already uses (see CLAUDE_INSTRUCTIONS.md /
# ingest_turntable's color-convert step) -- pointing Nuke at the same alias
# keeps the whole pipeline on one OCIO config with no local file to go stale.
nuke.knobDefault("Root.colorManagement", "OCIO")
nuke.knobDefault("Root.OCIO_config", "custom")
nuke.knobDefault("Root.customOCIOConfigPath", "ocio://studio-config-latest")

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
