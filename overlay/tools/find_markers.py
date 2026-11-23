"""Find the crossed-swords battle markers on the game map and print where to click.

Usage (game in front, map showing):
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\find_markers.py

Prints one line per marker: native pixel position and the click position in the 1456x819
coordinate frame used by the screen-control tool. Detection is template matching against
``aaoverlay/templates/marker_swords.png`` (the red crossed swords on the dark diamond).
"""
import os
import sys

import cv2
import mss
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(ROOT, "aaoverlay", "assets", "marker_swords.png")
FRAME_W = 1456  # coordinate frame width of the screen-control tool


def grab():
    with mss.MSS() as sct:
        return np.array(sct.grab(sct.monitors[1]))[:, :, :3]


def find_markers(img, threshold=0.62):
    """``[(score, cx, cy)]`` of marker centres in native pixels, best first, de-duplicated."""
    tpl = cv2.imread(TEMPLATE, cv2.IMREAD_GRAYSCALE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= threshold)
    kept = []
    for s, x, y in sorted(zip(res[ys, xs], xs, ys), reverse=True):
        if all(abs(x - a) > 40 or abs(y - b) > 40 for _, a, b in kept):
            kept.append((float(s), int(x), int(y)))
    h, w = tpl.shape
    return [(s, x + w // 2, y + h // 2) for s, x, y in kept]


def to_frame(img, x, y):
    """Native pixel -> click position in the 1456-wide frame (marker centre is ~14px below the swords)."""
    k = FRAME_W / img.shape[1]
    return round(x * k), round((y + 14 * img.shape[1] / 2560) * k)


def main():
    img = grab()
    markers = find_markers(img)
    print(f"{len(markers)} markers")
    for s, x, y in sorted(markers, key=lambda m: (m[1], m[2])):
        fx, fy = to_frame(img, x, y)
        print(f"  score {s:.2f}  native ({x},{y})  click ({fx},{fy})")


if __name__ == "__main__":
    main()


def find_arrow(img, threshold=0.7):
    """Click position (1456 frame) of the collapsed bottom bar's expand arrow, or None.

    The arrow's x depends on the length of the battle name shown in the bar, so it has to be found.
    """
    tpl = cv2.imread(os.path.join(ROOT, "aaoverlay", "assets", "bar_arrow.png"), cv2.IMREAD_GRAYSCALE)
    top = int(img.shape[0] * 0.87)
    res = cv2.matchTemplate(cv2.cvtColor(img[top:], cv2.COLOR_BGR2GRAY), tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < threshold:
        return None
    h, w = tpl.shape
    k = FRAME_W / img.shape[1]
    return round((loc[0] + w / 2) * k), round((loc[1] + top + h / 2) * k)


if __name__ == "__main__" and "--arrow" in sys.argv:
    a = find_arrow(grab())
    print("arrow click:", a)
