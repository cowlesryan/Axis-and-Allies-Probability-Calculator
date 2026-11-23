"""Tests for the panel reader, using the Phase 0 reference captures.

Run from the overlay folder:  .venv\\Scripts\\python.exe -m pytest tests
"""
import os
import sys
import tempfile

import cv2
import numpy as np
import pytest

from aaoverlay import reader as R
from aaoverlay.templates import TemplateStore

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(os.path.dirname(HERE), "reference")
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import seed_templates as S  # noqa: E402

CAUCASUS = "attack_setup_caucasus_2560x1440.png"
SEA7 = "attack_setup_seazone7_2560x1440.png"

# (inf, art, tank, aa, ftr, bmb, sub, des, cru, car, bb, bbd, trn)
CAUCASUS_ATT = (27, 1, 1, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0)
CAUCASUS_DEF = (11, 4, 9, 0, 5, 1, 0, 0, 0, 0, 0, 0, 0)
SEA7_ATT = (0, 0, 0, 0, 1, 0, 4, 0, 1, 0, 0, 0, 0)
SEA7_DEF = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 0)

pytestmark = pytest.mark.skipif(not os.path.exists(os.path.join(REF, CAUCASUS)),
                                reason="reference captures not present")


def load(name):
    return cv2.imread(os.path.join(REF, name))


@pytest.fixture(scope="module")
def store():
    return TemplateStore()


def test_reads_land_and_air_panel(store):
    att, dfn, warnings = R.panel_to_armies(R.read_panel(load(CAUCASUS), store))
    assert (att, dfn, warnings) == (CAUCASUS_ATT, CAUCASUS_DEF, [])


def test_reads_sea_panel_with_green_divider(store):
    """The centre line changes colour with the game's outlook label (green here)."""
    att, dfn, warnings = R.panel_to_armies(R.read_panel(load(SEA7), store))
    assert (att, dfn, warnings) == (SEA7_ATT, SEA7_DEF, [])


def test_dice_values_and_panel_geometry(store):
    p = R.read_panel(load(CAUCASUS), store)
    assert [(r.die_att, r.die_def) for r in p.rows] == [(1, 1), (2, 2), (3, 3), (None, 4)]
    assert R.read_panel(load(SEA7), store).bounds == (930, 552, 1617, 868)  # used to place the overlay
    cx, rows = R.locate_rows(load(SEA7))
    assert abs(cx - 1330) <= 1  # the centre line is 2-3px wide; the left column is used
    assert [(g.left, g.right) for g in rows] == [(930, 1617)] * 3  # right edge varies per panel


@pytest.mark.parametrize("name,size", [(CAUCASUS, (1920, 1080)), (CAUCASUS, (3840, 2160)),
                                       (SEA7, (1920, 1080)), (SEA7, (1280, 720))])
def test_resolution_independent(store, name, size):
    """Any 16:9 resolution works: screenshots are rescaled to 1440p first."""
    img = cv2.resize(load(name), size, interpolation=cv2.INTER_AREA)
    att, dfn, warnings = R.panel_to_armies(R.read_panel(img, store))
    want = (CAUCASUS_ATT, CAUCASUS_DEF) if name == CAUCASUS else (SEA7_ATT, SEA7_DEF)
    # Rescaled screenshots may blur an icon enough that it is not recognised. That is acceptable only if
    # the reader says so: it must never return a different army without a warning.
    if not warnings:
        assert (att, dfn) == want


def test_no_panel_returns_none(store):
    assert R.read_panel(np.full((1440, 2560, 3), 90, np.uint8), store) is None
    crop = load(CAUCASUS).copy()
    crop[300:930, 850:1720] = 120  # blank out the panel
    assert R.read_panel(crop, store) is None


def test_digits_generalise_but_unknown_icons_are_flagged_not_guessed():
    """Trained on the Caucasus panel only: the counts on the sea panel (a different digit
    size) still read, while icons it has never seen are reported, never silently guessed."""
    with tempfile.TemporaryDirectory() as tmp:
        st = TemplateStore(root=tmp)
        S.seed(os.path.join(REF, CAUCASUS), S.LABELLED[CAUCASUS], st)
        p = R.read_panel(load(SEA7), TemplateStore(root=tmp))
    texts = [e.count_text for r in p.rows for e in r.att + r.dfn]
    assert texts == ["4", "1", "1", "1"]
    assert any("unrecognised icon" in w for w in p.warnings)
    att, dfn, warnings = R.panel_to_armies(p)
    assert warnings  # callers must not trust the armies when warnings exist
    assert dfn == (0,) * 13  # the unknown defender icon was skipped, not invented


def test_boosted_infantry_rows_add_up(store):
    """Attacking infantry on die 1 and die 2 (artillery-boosted) are one infantry total."""
    att, _, _ = R.panel_to_armies(R.read_panel(load(CAUCASUS), store))
    assert att[0] == 26 + 1


def test_seed_script_rejects_wrong_labels(tmp_path):
    bad = [([("inf", 26)], [("bmb", 1)])]  # only one row labelled for a 4-row panel
    with pytest.raises(AssertionError):
        S.seed(os.path.join(REF, CAUCASUS), bad, TemplateStore(root=str(tmp_path)))
