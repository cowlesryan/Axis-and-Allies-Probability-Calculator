"""In-game battle-odds overlay for Axis & Allies 1942 Online.

Modules:
    reader     -- finds and reads the game's attack panel from a screenshot.
    templates  -- on-disk icon and digit templates the reader matches against.
    service    -- runs the aa1942 solver (exact, with Monte Carlo fallback).
    app        -- the transparent click-through Qt overlay window.

The reader is purely passive: it only looks at screenshots of the screen. It
never touches the game's files or process.
"""
import os
import sys

__version__ = "0.1.0"

# The calculator package (aa1942) lives one folder above this project (the packaged app bundles it instead).
if not getattr(sys, "frozen", False):
    _PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if _PARENT not in sys.path:
        sys.path.insert(0, _PARENT)
