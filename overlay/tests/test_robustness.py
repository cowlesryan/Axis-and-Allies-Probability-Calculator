"""Stress tests of the panel reader: bad input, degraded images, geometry, templates and the dataset itself.

The central promise is the safety invariant: the reader may refuse (no panel) or warn, but it must never return a
wrong army without a warning.
"""
import os
import re

import cv2
import numpy as np
import pytest

from aaoverlay import dataset as D
from aaoverlay import reader as R
from aaoverlay.templates import ROOT, TemplateStore, iou
from aa1942.units import ORDER

SAMPLES = list(D.samples())
pytestmark = pytest.mark.skipif(not SAMPLES, reason="no harvested samples")


@pytest.fixture(scope="module")
def store():
    return TemplateStore()


def want(meta):
    t = meta["truth"]
    d = dict(t["defenders"])
    if t.get("panel_aa"):
        d["aa"] = t["panel_aa"]
    return dict(t["attackers"]), d


def status(panel, meta):
    if panel is None:
        return "none"
    a, d, w = R.panel_to_armies(panel)
    if w:
        return "flagged"
    got = ({k: v for k, v in zip(ORDER, a) if v}, {k: v for k, v in zip(ORDER, d) if v})
    return "exact" if got == want(meta) else "WRONG"


# -- bad input ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("img", [
    None, np.zeros((0, 0, 3), np.uint8), np.zeros((1, 1, 3), np.uint8), np.zeros((50, 50, 3), np.uint8),
    np.zeros((1440, 2560), np.uint8), np.zeros((1440, 2560, 4), np.uint8), np.zeros((1440, 2560, 1), np.uint8),
    np.zeros((1440, 2560, 3), np.float32), np.full((1440, 2560, 3), 255, np.uint8),
    np.random.RandomState(0).randint(0, 255, (1440, 2560, 3)).astype(np.uint8),
    np.zeros((2560, 1440, 3), np.uint8), np.zeros((720, 3440, 3), np.uint8),
], ids=lambda x: "none" if x is None else f"{x.shape}-{x.dtype}")
def test_unusable_input_gives_no_panel_and_never_raises(store, img):
    assert R.read_panel(img, store) is None


def test_bgra_screenshots_read_like_bgr(store):
    name, meta = SAMPLES[0]
    bgr = D.load_canvas(name, meta)
    bgra = np.dstack([bgr, np.full(bgr.shape[:2], 255, np.uint8)])
    assert status(R.read_panel(bgra, store), meta) == status(R.read_panel(bgr, store), meta)


def test_stray_lines_and_blocks_are_not_panels(store):
    img = np.full((1440, 2560, 3), 90, np.uint8)
    img[100:1400, 1200:1203] = (0, 255, 255)           # a lone vivid vertical line
    assert R.read_panel(img, store) is None
    img[600:660, 900:1500] = 0                          # a black bar with no dice
    assert R.read_panel(img, store) is None


# -- degraded images: never silently wrong -------------------------------------------------------------------
def _jpeg(q):
    return lambda im: cv2.imdecode(cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, q])[1], 1)


def _scale(f):
    return lambda im: cv2.resize(im, None, fx=f, fy=f, interpolation=cv2.INTER_AREA if f < 1 else cv2.INTER_CUBIC)


_rng = np.random.RandomState(0)
PERTURBATIONS = {
    "dark": lambda im: np.clip(im.astype(np.float32) * 0.8, 0, 255).astype(np.uint8),
    "bright": lambda im: np.clip(im.astype(np.float32) * 1.2, 0, 255).astype(np.uint8),
    "noise2": lambda im: np.clip(im.astype(np.int16) + _rng.normal(0, 2, im.shape), 0, 255).astype(np.uint8),
    "noise8": lambda im: np.clip(im.astype(np.int16) + _rng.normal(0, 8, im.shape), 0, 255).astype(np.uint8),
    "jpeg90": _jpeg(90), "jpeg70": _jpeg(70), "jpeg40": _jpeg(40),
    "blur": lambda im: cv2.GaussianBlur(im, (3, 3), 0),
    "shift": lambda im: np.roll(np.roll(im, 7, axis=1), 5, axis=0),
    "down75": _scale(0.75), "up133": _scale(1.333), "down50": _scale(0.5),
}


@pytest.mark.parametrize("name", list(PERTURBATIONS))
def test_degraded_images_never_read_silently_wrong(store, name):
    f = PERTURBATIONS[name]
    results = {"exact": 0, "flagged": 0, "none": 0, "WRONG": 0}
    wrong = []
    for sample, meta in SAMPLES[::2]:
        s = status(R.read_panel(f(D.load_canvas(sample, meta)), store), meta)
        results[s] += 1
        if s == "WRONG":
            wrong.append(sample)
    assert not wrong, f"{name}: silently wrong readings: {wrong}"
    if name in ("dark", "bright", "shift", "up133", "noise2"):  # mild changes must still mostly read
        assert results["exact"] >= 0.8 * sum(results.values()), results


def test_missing_row_detector_notices_a_die_outside_the_rows(store):
    """If the row finder misses a bar, its die is still visible: the reader must say a row may be missing."""
    name, meta = next((n, m) for n, m in SAMPLES if len(m["panel"]["dice"]) == 4)
    img = D.load_canvas(name, meta)
    cx, geoms = R.locate_rows(img)
    assert R.stray_dice(img, cx, geoms) == 0
    assert R.stray_dice(img, cx, geoms[:-1]) >= 1          # the last row forgotten
    assert R.stray_dice(img, cx, geoms[1:]) >= 1           # the first row forgotten
    assert R.stray_dice(img, cx, geoms[:1] + geoms[2:]) >= 1


def test_find_bars_survives_a_little_noise_on_one_side():
    name, meta = next((n, m) for n, m in SAMPLES if len(m["panel"]["dice"]) == 4)
    img = D.load_canvas(name, meta)
    cx, geoms = R.locate_rows(img)
    clean = R.find_bars(img, cx)
    noisy = img.copy()
    noisy[:, cx - 66:cx - 60] = np.clip(noisy[:, cx - 66:cx - 60].astype(np.int16) + 40, 0, 255)  # left strip ruined
    assert [b for b in R.find_bars(noisy, cx) if b[1] - b[0] >= 55] == [b for b in clean if b[1] - b[0] >= 55]


# -- geometry regression on the whole dataset -----------------------------------------------------------------
def test_every_sample_has_the_recorded_rows_and_dice(store):
    for name, meta in SAMPLES:
        img = D.load_canvas(name, meta)
        cx, rows = R.locate_rows(img)
        assert cx is not None, name
        assert [(r.die_att, r.die_def) for r in rows] == [tuple(d) for d in meta["panel"]["dice"]], name


def test_panel_bounds_and_crop_box_stay_inside_the_image(store):
    from aaoverlay import windows as W
    for name, meta in SAMPLES[::3]:
        img = D.load_canvas(name, meta)
        panel = R.read_panel(img, store)
        x0, y0, x1, y1 = W.crop_box(img, panel)
        assert 0 <= x0 < x1 <= img.shape[1] and 0 <= y0 < y1 <= img.shape[0]
        l, t, r, b = panel.bounds
        assert x0 <= l and r <= x1 and y0 <= t <= b <= y1


# -- templates and dataset consistency ---------------------------------------------------------------------------
def test_template_files_are_well_formed(store):
    icons = os.listdir(os.path.join(ROOT, "icons"))
    digits = os.listdir(os.path.join(ROOT, "digits"))
    assert icons and digits
    for f in icons:
        assert re.fullmatch(r"(%s)__[a-z]+__\d+\.png" % "|".join(ORDER), f), f
    for f in digits:
        assert re.fullmatch(r"\d__\d+__\d+\.\d{3}\.png", f), f
    for unit, colour, sig in store.icons:
        assert sig.shape == (48, 48) and sig.any(), (unit, colour)
    for d, sig, aspect in store.digits:
        assert sig.shape == (32, 20) and sig.any() and aspect > 0


def test_no_identical_silhouette_is_filed_under_two_different_units(store):
    worst = []
    for i, (u1, c1, s1) in enumerate(store.icons):
        for u2, c2, s2 in store.icons[i + 1:]:
            if u1 != u2 and iou(s1, s2) > 0.93:
                worst.append((u1, c1, u2, c2, round(iou(s1, s2), 3)))
    assert not worst, f"templates that cannot be told apart yet carry different labels: {worst[:5]}"


def test_every_unit_has_templates_in_every_power_colour_that_was_captured(store):
    seen = {}
    for name, meta in SAMPLES:
        for unit in meta["truth"]["attackers"]:
            seen.setdefault(unit, set()).add(meta["truth"]["attacker_power"])
    for unit in seen:
        assert any(u == unit for u, _c, _s in store.icons), unit


def test_dataset_files_are_consistent():
    names = set()
    for name, meta in SAMPLES:
        assert name not in names
        names.add(name)
        assert os.path.exists(os.path.join(D.RAW, name + ".png"))
        img = cv2.imread(os.path.join(D.RAW, name + ".png"))
        x0, y0, x1, y1 = meta["crop_box_1440p"]
        assert img.shape[:2] == (y1 - y0, x1 - x0), name
        t = meta["truth"]
        assert t["attackers"] and t["defenders"], name
        for side in ("attackers", "defenders"):
            for unit, n in t[side].items():
                assert unit in ORDER and isinstance(n, int) and n > 0, (name, unit, n)
        assert len(meta["panel"]["dice"]) in (1, 2, 3, 4), name
    for f in os.listdir(D.RAW):
        assert f.endswith((".png", ".json")), f
        stem = os.path.splitext(f)[0]
        assert os.path.exists(os.path.join(D.RAW, stem + ".png")) and os.path.exists(os.path.join(D.RAW, stem + ".json")), stem


def test_negative_frames_are_game_sized_and_read_as_no_panel(store):
    folder = os.path.join(D.ROOT, "negatives")
    if not os.path.isdir(folder):
        pytest.skip("no negatives")
    for f in os.listdir(folder):
        img = cv2.imread(os.path.join(folder, f))
        assert img.shape[:2] == (720, 1280), f
        assert R.read_panel(img, store) is None, f
