"""Capture every battle panel of the scenario that is currently open in the game (closed loop).

Usage (a scenario "Overlay <power>" must be open in the game):
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\capture_run.py <scenario game id>

For each battle marker found on the map it: closes any open panel, clicks the marker, opens the panel
(expanding the bottom bar if needed), waits until the screen shows a stable panel, identifies which
battle it is from the layout and counts, and saves the labelled crop (tools/harvest.py). Every step
re-reads the screen instead of trusting timings. Press Esc at any time to stop.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import aaoverlay  # noqa: E402,F401
from aaoverlay import reader as R  # noqa: E402
import find_markers as FM  # noqa: E402
import harvest as H  # noqa: E402
import ui_driver as U  # noqa: E402

VIEW = (440, 643)        # the VIEW / HIDE toggle under the battle emblem (expanded bar)
COLLAPSE = (1157, 647)   # the collapse arrow of the expanded bar


def panel_visible():
    img = R.normalize(FM.grab())
    cx, rows = R.locate_rows(img)
    return cx is not None and len(rows) > 0


def wait_for_panel(d, seconds=4.0):
    end = time.time() + seconds
    while time.time() < end:
        d.check()
        if panel_visible():
            return True
        time.sleep(0.4)
    return False


def close_panel(d):
    """Make sure no panel is showing (HIDE toggle; expand the bar first if it is collapsed)."""
    for _ in range(3):
        if not panel_visible():
            return True
        d.click(*VIEW, note="HIDE")
        d.wait(1.0)
        if panel_visible():
            arrow = FM.find_arrow(FM.grab())
            if arrow:
                d.click(*arrow, note="expand bar")
                d.wait(1.2)
    return not panel_visible()


def open_marker(d, mx, my):
    """Click a marker and open its panel. Returns True if a panel is showing afterwards."""
    img = FM.grab()
    fx, fy = FM.to_frame(img, mx, my)
    d.click(fx, fy, note="marker")
    d.wait(1.5)
    arrow = FM.find_arrow(FM.grab())
    if arrow:
        d.click(*arrow, note="expand bar")
        d.wait(1.5)
    d.click(*VIEW, note="VIEW")
    return wait_for_panel(d, 4.0)


def try_snap(game_id, tag):
    """Capture the panel on screen; returns ``(meta or None, message)``."""
    try:
        return H.snap(None, tag, False, game_id), "ok"
    except SystemExit as e:
        return None, str(e)


def run(game_id, tag, log=print, pan=None):
    """Capture every battle of a scenario; ``pan=(dx, dy)`` first drags the map that far (frame px)."""
    d = U.Driver(mode="scenario", log=lambda m: None)
    truths = H.scenario_truths(game_id)
    log(f"scenario {game_id}: {len(truths)} battles, already captured: {len(H.captured_targets(game_id))}")
    tried = set()
    if pan and close_panel(d):
        d.drag((700, 330), (700 + pan[0], 330 + pan[1]))
        d.wait(1.5)
    for pass_no in range(3):
        done = H.captured_targets(game_id)
        if len(done) >= len(truths):
            break
        if not close_panel(d):
            log("could not close the open panel; stopping")
            break
        markers = sorted(FM.find_markers(FM.grab()), key=lambda m: (m[1], m[2]))
        fresh = [m for m in markers if (m[1] // 40, m[2] // 40) not in tried]
        log(f"pass {pass_no + 1}: {len(markers)} markers, {len(fresh)} not yet tried, "
            f"{len(done)}/{len(truths)} battles captured")
        if not fresh:
            break
        for score, mx, my in fresh:
            tried.add((mx // 40, my // 40))
            if not close_panel(d):
                log("could not close the open panel; skipping marker")
                continue
            if not open_marker(d, mx, my):
                log(f"  marker ({mx},{my}): no panel opened")
                continue
            meta, msg = try_snap(game_id, tag)
            if meta is None and "overlaps" not in msg:
                # the bottom bar may be covering the panel: collapse it and read again
                d.click(*COLLAPSE, note="collapse bar")
                d.wait(1.5)
                meta, msg = try_snap(game_id, tag)
            log(f"  marker ({mx},{my}): " + (f"captured {meta['source']['target']}" if meta else msg))
    done = H.captured_targets(game_id)
    missing = sorted(set(truths) - done)
    log(f"captured {len(done)}/{len(truths)}; missing: {missing or 'none'}")
    close_panel(d)
    return missing


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    pan = tuple(int(v) for v in sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    run(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "scn", pan=pan)
