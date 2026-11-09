"""Transitions the cutter knows how to build.

`cut` is a hard join. Every other name is an ffmpeg xfade transition.
"""

from __future__ import annotations

TRANSITION_NAMES = {
    "cut": None,
    "fade": "fade",
    "dissolve": "dissolve",
    "fadeblack": "fadeblack",
    "fadewhite": "fadewhite",
    "wipeleft": "wipeleft",
    "wiperight": "wiperight",
    "slideleft": "slideleft",
    "smoothleft": "smoothleft",
}

DEFAULT_TRANSITION = "fade"
DEFAULT_FADE = 0.25
