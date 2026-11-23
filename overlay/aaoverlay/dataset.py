"""Reading the training dataset written by ``tools/harvest.py``.

A sample is ``dataset/raw/NNNN_tag.png`` (a crop of the attack panel only) plus a
``.json`` sidecar holding the ground truth from the offline save. The reader works on
whole 1440p frames, so ``load_canvas`` pastes the crop back at its original position on
a canvas filled with the panel's margin grey (not black, so no false bars appear).
"""
import json
import os

import cv2
import numpy as np

from .reader import BASE_H, PANEL_GREY

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset")
RAW = os.path.join(ROOT, "raw")
CANVAS_W = 2560


def samples(raw_dir=RAW):
    """Yield ``(name, meta_dict)`` for every sample, oldest first."""
    if not os.path.isdir(raw_dir):
        return
    for f in sorted(os.listdir(raw_dir)):
        if f.endswith(".json"):
            with open(os.path.join(raw_dir, f), encoding="utf-8") as fh:
                meta = json.load(fh)
            yield meta["name"], meta


def load_canvas(name, meta, raw_dir=RAW):
    """The sample as a 1440p frame (crop pasted in place on a margin-grey canvas)."""
    crop = cv2.imread(os.path.join(raw_dir, name + ".png"))
    x0, y0, x1, y1 = meta["crop_box_1440p"]
    canvas = np.empty((BASE_H, CANVAS_W, 3), np.uint8)
    canvas[:] = PANEL_GREY.astype(np.uint8)
    canvas[y0:y1, x0:x1] = crop
    return canvas
