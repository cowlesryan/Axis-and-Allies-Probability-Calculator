"""Template store for the panel reader.

Two kinds of templates live under ``aaoverlay/templates/``:

* ``icons/<unit>__<colour>__<n>.png`` - binary silhouette of a unit icon
  (``unit`` is a registry key such as ``inf`` or ``tank``; ``colour`` is the
  power colour class the icon was seen in, e.g. ``red`` or ``grey``).
* ``digits/<d>__<n>.png`` - binary glyph of one count digit.

Icons are matched by silhouette overlap (IoU) after cropping to the bounding
box and resizing to a fixed canvas, so they are independent of screen scale.
"""
import os
import shutil

import cv2
import numpy as np

from .paths import FROZEN, data_dir

BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")


def _working_copy():
    """The packaged app cannot write beside its own files (``Program Files``, updates), so Teach works on a copy in
    the user's data folder. Seed templates missing from the copy are added; icons the user taught are never touched."""
    root = os.path.join(data_dir(), "templates")
    for sub in ("icons", "digits"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
        src = os.path.join(BUNDLED, sub)
        for name in os.listdir(src) if os.path.isdir(src) else []:
            dst = os.path.join(root, sub, name)
            if not os.path.exists(dst):
                shutil.copyfile(os.path.join(src, name), dst)
    return root


ROOT = _working_copy() if FROZEN else BUNDLED
ICON_SIZE = 48
DIGIT_W, DIGIT_H = 20, 32


def _canvas(mask, size_w, size_h, keep_aspect):
    """Crop ``mask`` (bool) to its bounding box and fit it into a fixed canvas."""
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None
    crop = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    h, w = crop.shape
    if keep_aspect:
        s = min((size_w - 2) / w, (size_h - 2) / h)
        nw, nh = max(1, round(w * s)), max(1, round(h * s))
        crop = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA)
        out = np.zeros((size_h, size_w), np.uint8)
        y0, x0 = (size_h - nh) // 2, (size_w - nw) // 2
        out[y0:y0 + nh, x0:x0 + nw] = crop
        return out > 127
    return cv2.resize(crop, (size_w, size_h), interpolation=cv2.INTER_AREA) > 127


def icon_signature(crop_bgr, thresh=60):
    """Binary, scale-independent signature of an icon crop (or None if empty).

    The cut-off is relative to the icon's own brightest pixels, so a blurry (rescaled) screenshot,
    whose dim halo would otherwise fatten the silhouette, still gives the same shape.
    """
    mx = crop_bgr.max(axis=2)
    cut = max(thresh, int(0.35 * mx.max()))
    return _canvas(mx > cut, ICON_SIZE, ICON_SIZE, True)


def digit_signature(mask):
    """Signature of one digit glyph mask: stretched to a fixed canvas.

    The glyph's aspect ratio is returned separately because stretching hides
    the difference between a narrow ``1`` and a wide ``0``.
    """
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return None, 0.0
    aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
    return _canvas(mask, DIGIT_W, DIGIT_H, False), float(aspect)


def holes(mask):
    """Number of enclosed holes in a glyph mask (8 has two; 0, 4, 6, 9 one; 1, 2, 3, 5, 7 none)."""
    m = (np.asarray(mask).astype(np.uint8)) * 255
    contours, hier = cv2.findContours(m, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return 0
    return sum(1 for i, c in enumerate(contours) if hier[0][i][3] != -1 and cv2.contourArea(c) >= 6)


def iou(a, b):
    inter = np.logical_and(a, b).sum()
    union = np.logical_or(a, b).sum()
    return float(inter) / union if union else 0.0


def colour_class(crop_bgr, thresh=60):
    """Rough power-colour class of an icon: ``red``, ``grey`` or ``other``."""
    m = crop_bgr.max(axis=2) > thresh
    if not m.any():
        return "other"
    b, g, r = (float(crop_bgr[..., i][m].mean()) for i in range(3))
    if r - b > 60 and r - g > 50:
        return "red"
    if abs(r - b) < 45 and abs(r - g) < 45:
        return "grey"
    return "other"


class TemplateStore:
    """Loads templates from disk and matches signatures against them."""

    def __init__(self, root=ROOT):
        self.root = root
        self.icons = []    # (unit, colour, signature)
        self.digits = []   # (digit, signature, aspect)
        self.load()

    # -- loading / saving ----------------------------------------------------
    def load(self):
        self.icons, self.digits = [], []
        idir = os.path.join(self.root, "icons")
        ddir = os.path.join(self.root, "digits")
        for name in sorted(os.listdir(idir)) if os.path.isdir(idir) else []:
            if name.endswith(".png"):
                unit, colour = name.split("__")[:2]
                img = cv2.imread(os.path.join(idir, name), cv2.IMREAD_GRAYSCALE)
                self.icons.append((unit, colour, img > 127))
        for name in sorted(os.listdir(ddir)) if os.path.isdir(ddir) else []:
            if name.endswith(".png"):
                digit = name.split("__")[0]
                img = cv2.imread(os.path.join(ddir, name), cv2.IMREAD_GRAYSCALE)
                aspect = float(name.split("__")[2][:-4]) if name.count("__") >= 2 else 0.0
                self.digits.append((digit, img > 127, aspect))

    def _next_index(self, folder, prefix):
        os.makedirs(folder, exist_ok=True)
        n = 0
        while any(f.startswith(f"{prefix}__{n}") or f.startswith(f"{prefix}__{n}__") for f in os.listdir(folder)):
            n += 1
        return n

    def add_icon(self, unit, crop_bgr):
        sig = icon_signature(crop_bgr)
        if sig is None:
            return None
        colour = colour_class(crop_bgr)
        folder = os.path.join(self.root, "icons")
        os.makedirs(folder, exist_ok=True)
        n = len([f for f in os.listdir(folder) if f.startswith(f"{unit}__{colour}__")])
        path = os.path.join(folder, f"{unit}__{colour}__{n}.png")
        cv2.imwrite(path, sig.astype(np.uint8) * 255)
        self.icons.append((unit, colour, sig))
        return path

    def add_digit(self, digit, mask):
        sig, aspect = digit_signature(mask)
        if sig is None:
            return None
        folder = os.path.join(self.root, "digits")
        os.makedirs(folder, exist_ok=True)
        n = len([f for f in os.listdir(folder) if f.startswith(f"{digit}__")])
        path = os.path.join(folder, f"{digit}__{n}__{aspect:.3f}.png")
        cv2.imwrite(path, sig.astype(np.uint8) * 255)
        self.digits.append((digit, sig, aspect))
        return path

    # -- matching ------------------------------------------------------------
    def match_icon(self, crop_bgr, candidates=None):
        """Best ``(unit, colour, score)`` for an icon crop, or ``(None, None, 0.0)``.

        ``candidates`` optionally restricts the search to a set of unit keys
        (the die value on the row rules many units out).
        """
        sig = icon_signature(crop_bgr)
        if sig is None:
            return None, None, 0.0
        best = (None, None, 0.0)
        for unit, colour, t in self.icons:
            if candidates is not None and unit not in candidates:
                continue
            s = iou(sig, t)
            if s > best[2]:
                best = (unit, colour, s)
        return best

    def match_digit(self, mask):
        """Best ``(digit, score)`` for a glyph mask, or ``(None, 0.0)``."""
        best, _ = self.match_digit_ranked(mask)
        return best

    def match_digit_ranked(self, mask):
        """``((digit, score), runner_up_score)``: the best digit and the best score of any *other* digit.

        A glyph that is not a known digit (e.g. a 0 when only 6 and 9 were taught) tends to score
        about equally against several, so callers use the gap to refuse to guess.
        """
        sig, aspect = digit_signature(mask)
        if sig is None:
            return (None, 0.0), 0.0
        per_digit = {}
        q_holes = holes(sig)
        for d, t, a in self.digits:
            s = iou(sig, t)
            if a:  # penalise a wrong width (separates a narrow 1 from wide glyphs)
                s *= max(0.0, 1.0 - abs(aspect - a) * 0.8)
            if holes(t) != q_holes:  # 8 vs 0/6/9, 4 vs 7, ... : a different topology is a different digit
                s *= 0.55
            per_digit[d] = max(per_digit.get(d, 0.0), s)
        if not per_digit:
            return (None, 0.0), 0.0
        ranked = sorted(per_digit.items(), key=lambda kv: kv[1], reverse=True)
        runner = ranked[1][1] if len(ranked) > 1 else 0.0
        return ranked[0], runner
