"""Regression test over the harvested, ground-truth-labelled samples.

The key safety property: the reader may flag a panel it cannot read, but it must NEVER return a
wrong army without a warning. Samples live in dataset/raw (written by tools/harvest.py).
"""
import os

import pytest

from aaoverlay import dataset as D
from aaoverlay import reader as R
from aaoverlay.templates import TemplateStore
from aa1942.units import ORDER

SAMPLES = list(D.samples())

pytestmark = pytest.mark.skipif(not SAMPLES, reason="no harvested samples present")


@pytest.fixture(scope="module")
def store():
    return TemplateStore()


def read(name, meta, store):
    panel = R.read_panel(D.load_canvas(name, meta), store)
    assert panel is not None, f"{name}: panel not found"
    att, dfn, warnings = R.panel_to_armies(panel)
    return ({k: v for k, v in zip(ORDER, att) if v}, {k: v for k, v in zip(ORDER, dfn) if v}, warnings)


def wanted(meta):
    t = meta["truth"]
    want_d = dict(t["defenders"])
    if t.get("panel_aa"):
        want_d["aa"] = t["panel_aa"]  # defending AA guns are on the panel when attackers have aircraft
    return dict(t["attackers"]), want_d


@pytest.mark.parametrize("name,meta", SAMPLES, ids=[n for n, _ in SAMPLES])
def test_never_wrong_without_a_warning(store, name, meta):
    got_a, got_d, warnings = read(name, meta, store)
    want_a, want_d = wanted(meta)
    if not warnings:
        assert (got_a, got_d) == (want_a, want_d), f"{name}: silently wrong reading"


def test_most_samples_read_exactly(store):
    exact = 0
    for name, meta in SAMPLES:
        got_a, got_d, warnings = read(name, meta, store)
        if not warnings and (got_a, got_d) == wanted(meta):
            exact += 1
    assert exact >= 0.6 * len(SAMPLES), f"only {exact}/{len(SAMPLES)} samples read exactly"


def test_all_digits_have_templates(store):
    assert {d for d, _, _ in store.digits} >= set("0123456789")


def test_samples_never_contain_other_windows():
    """Each sample is a crop of the panel only and carries no extra frame (guard against leaks)."""
    for name, meta in SAMPLES:
        assert os.path.exists(os.path.join(D.RAW, name + ".png"))
        x0, y0, x1, y1 = meta["crop_box_1440p"]
        assert x1 - x0 < 1600 and y1 - y0 < 1250  # a (possibly wide) panel crop, never a full 2560x1440 screenshot


NEG_DIR = os.path.join(D.ROOT, "negatives")


@pytest.mark.skipif(not os.path.isdir(NEG_DIR), reason="no negative samples")
def test_no_panel_is_never_invented_on_negative_frames(store):
    """Game frames with no attack panel (dataset/negatives) must read as 'no panel'."""
    import cv2
    files = [f for f in sorted(os.listdir(NEG_DIR)) if f.endswith(".png")]
    assert files
    for f in files:
        assert R.read_panel(cv2.imread(os.path.join(NEG_DIR, f)), store) is None, f"{f}: phantom panel"


def test_one_row_panels_are_found_and_read(store):
    """A one-row panel's centre line is only short stubs; they must still be located and read exactly."""
    one = [(n, m) for n, m in SAMPLES if len(m["panel"]["dice"]) == 1]
    assert len(one) >= 20
    exact = sum(1 for n, m in one if not read(n, m, store)[2] and read(n, m, store)[:2] == wanted(m))
    assert exact >= 0.8 * len(one)


def test_amphibious_panels_read_with_bombarding_ships(store):
    """Amphibious landings (offloaded units + bombarding ships as attackers) read like any other panel."""
    amph = [(n, m) for n, m in SAMPLES if m["truth"].get("amphibious") or n.endswith("_amph")]
    assert len(amph) >= 10
    with_ships = [(n, m) for n, m in amph if {"bb", "cru"} & set(m["truth"]["attackers"])]
    assert len(with_ships) >= 5
    exact = sum(1 for n, m in amph if not read(n, m, store)[2] and read(n, m, store)[:2] == wanted(m))
    assert exact >= 0.8 * len(amph)
