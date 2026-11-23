"""Save the current game screen as a negative sample (a frame with NO attack setup panel).

Usage: python tools\neg_snap.py [label]   -> dataset/negatives/NNNN_label.png (1280x720, game only).
Refused when another window overlaps the screen. Reports whether the reader (wrongly) sees a panel.
"""
import os
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import harvest as H  # noqa: E402
from aaoverlay import reader as R  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "dataset", "negatives")


def claude_rects():
    """Screen rectangles of the Claude chat window (never stored: blanked out of the frame)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if u.IsWindowVisible(h):
            n = ctypes.create_unicode_buffer(40)
            u.GetWindowTextW(h, n, 40)
            r = wintypes.RECT()
            u.GetWindowRect(h, ctypes.byref(r))
            if n.value == "Claude" and 0 < (r.right - r.left) < 2300 and r.left > -10000:
                found.append((max(0, r.left), max(0, r.top), r.right, r.bottom))
        return True
    u.EnumWindows(cb, 0)
    return found


def main(label):
    os.makedirs(OUT, exist_ok=True)
    occ = H.occluding_windows((0, 0, 2560, 1440))
    if set(occ) - {"claude.exe"}:
        sys.exit(f"refusing: another window overlaps the game: {occ}")
    img = H.grab()
    for x0, y0, x1, y1 in (claude_rects() if occ else []):
        img[y0:y1, x0:x1] = 40  # blank out the chat window; its content is not kept
    seen = R.read_panel(R.normalize(img), TemplateStore()) is not None
    n = len([f for f in os.listdir(OUT) if f.endswith(".png")]) + 1
    name = f"{n:04d}_{label}.png"
    cv2.imwrite(os.path.join(OUT, name), cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA))
    print(("PHANTOM PANEL SEEN " if seen else "ok (no panel) ") + name)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "screen")
