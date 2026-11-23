"""Seed icon and digit templates from labelled reference captures.

Usage (from the overlay folder):
    .venv\\Scripts\\python.exe tools\\seed_templates.py            # all captures
    .venv\\Scripts\\python.exe tools\\seed_templates.py --reset    # wipe templates first

To teach the reader more units or digits: capture a new attack panel
(``tools/capture_screen.ps1``), add a labelled entry to ``LABELLED`` (one
``(attacker, defender)`` pair of ``[(unit, count), ...]`` lists per panel row,
top to bottom) and run this script. The script refuses to seed a capture whose
detected layout does not match the labels, so a wrong label cannot slip in.
"""
import os
import shutil
import sys

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from aaoverlay import reader as R  # noqa: E402
from aaoverlay.templates import ROOT as TEMPLATE_ROOT, TemplateStore  # noqa: E402

REF = os.path.join(ROOT, "reference")

LABELLED = {
    # Phase 0: Soviet attack on the Caucasus (land + air). Game label: IMPLAUSIBLE.
    "attack_setup_caucasus_2560x1440.png": [
        ([("inf", 26)], [("bmb", 1)]),
        ([("inf", 1), ("art", 1)], [("art", 4), ("inf", 11)]),
        ([("tank", 1), ("ftr", 2)], [("tank", 9)]),
        ([], [("ftr", 5)]),
    ],
    # German attack on Sea Zone 7 (sea + air). Game label: STRONG. The defender's single
    # die-4 ship is a battleship (die 4 + ship silhouette; confirm against the game if in doubt).
    "attack_setup_seazone7_2560x1440.png": [
        ([("sub", 4)], []),
        ([("ftr", 1), ("cru", 1)], []),
        ([], [("bb", 1)]),
    ],
}


def seed(path, truth, store):
    img = R.normalize(cv2.imread(path))
    cx, geoms = R.locate_rows(img)
    assert cx is not None, f"{os.path.basename(path)}: no panel found"
    assert len(geoms) == len(truth), f"{os.path.basename(path)}: found {len(geoms)} rows, labelled {len(truth)}"
    for g, (att_truth, def_truth) in zip(geoms, truth):
        for side, truth_list, segs in (("att", att_truth, g.att_segs), ("def", def_truth, g.def_segs)):
            assert len(segs) == 2 * len(truth_list), (
                f"{os.path.basename(path)} row y={g.y0} {side}: {len(segs)} blobs vs {len(truth_list)} labelled entries")
            for i, (unit, count) in enumerate(truth_list):
                (ia, ib), (ca, cb) = segs[2 * i], segs[2 * i + 1]
                store.add_icon(unit, img[g.y0:g.y1, ia:ib])
                glyphs = R.digit_glyphs(img, g.y0, g.y1, ca - 3, cb + 3)
                text = str(count)
                assert len(glyphs) == len(text), f"{side} y={g.y0}: {len(glyphs)} glyphs for '{text}'"
                for ch, glyph in zip(text, glyphs):
                    store.add_digit(ch, glyph)


def main(argv):
    if "--reset" in argv:
        shutil.rmtree(TEMPLATE_ROOT, ignore_errors=True)
    store = TemplateStore()
    for name, truth in LABELLED.items():
        seed(os.path.join(REF, name), truth, store)
        print(f"seeded from {name}")
    print(f"templates now: {len(store.icons)} icons, {len(store.digits)} digit glyphs")


if __name__ == "__main__":
    main(sys.argv[1:])
