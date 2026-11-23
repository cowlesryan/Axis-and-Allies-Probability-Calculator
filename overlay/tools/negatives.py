"""Capture negative samples: game frames with NO attack panel, to prove the reader does not invent one.

Pans the map of the open OFFLINE scenario with the restricted driver (drags on empty sea/land in the
middle of the map), hides any panel, and saves a 1280x720 frame of the game only. A frame is refused if
another window overlaps the screen. Output: dataset/negatives/NNNN.png.

Usage (a scenario game must be open): python tools\negatives.py [count]
"""
import os
import random
import sys
import time

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import capture_run as C  # noqa: E402
import harvest as H  # noqa: E402
import ui_driver as U  # noqa: E402

OUT = os.path.join(os.path.dirname(HERE), "dataset", "negatives")


def main(count):
    os.makedirs(OUT, exist_ok=True)
    d = U.Driver(mode="scenario")
    have = len([f for f in os.listdir(OUT) if f.endswith(".png")])
    C.close_panel(d)
    rnd = random.Random(7)
    saved = 0
    for i in range(count * 3):
        if saved >= count:
            break
        # drag in the middle band of the map (frame coordinates 1456x819), away from the bars and panel
        x = rnd.randint(500, 950)
        y = rnd.randint(250, 480)
        dx, dy = rnd.randint(-250, 250), rnd.randint(-120, 120)
        try:
            d.drag((x, y), (x + dx, y + dy))
        except U.Refused as e:
            print("skipped drag:", e)
            continue
        d.wait(1.0)
        if C.panel_visible():
            C.close_panel(d)
        if C.panel_visible():
            continue
        occ = H.occluding_windows((0, 0, 2560, 1440))
        if occ:
            print("refusing, window over the game:", occ)
            continue
        img = H.grab()
        cv2.imwrite(os.path.join(OUT, f"{have + saved + 1:04d}.png"), cv2.resize(img, (1280, 720), interpolation=cv2.INTER_AREA))
        saved += 1
        print("saved negative", have + saved)
    print(f"{saved} negatives saved")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
