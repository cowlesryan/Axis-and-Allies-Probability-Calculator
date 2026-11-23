"""Reads the game's attack panel from a screenshot (passive, image-only).

Panel layout (measured at 1440p; every screenshot is rescaled to 1440p first)
---------------------------------------------------------------------------
* A coloured vertical line runs down the middle of the panel (``cx``). Its colour
  is the game's own outlook label (red = bad odds, green = good, ...), so it is
  found by *shape* (a long, saturated vertical line), not by hue.
* Black ~59px bars, one per hit value. The left edge sits ``cx - 400``; the right
  edge varies with the content, so both edges are measured from the image.
* In each bar the attacker half (left) and defender half (right) read
  ``[icon][xN]`` pairs left to right. A white die showing the hit value sits at
  ``cx - 58 .. cx - 17`` (attacker) and ``cx + 17 .. cx + 58`` (defender).
* Counts are a short ``x`` followed by taller digits. The digit height varies
  between panels, so it is measured from the text; digits are found in the top
  band that the shorter ``x`` does not reach.
* The game hides non-combatants (transports, AA guns) behind a "VIEW NON
  COMBATANTS" button, so they are never on the panel.

The reader never guesses silently: unknown icons or digits are reported in
``Panel.warnings`` and the entry is marked ``unit=None`` / ``count=None``.
"""
from dataclasses import dataclass, field

import cv2
import numpy as np

from .templates import TemplateStore, colour_class

BASE_H = 1440
DIE_NEAR, DIE_FAR = 17, 58
MERGE_GAP = 14          # blank columns that still belong to one icon/label
ICON_MIN_SCORE = 0.55
DIGIT_MIN_SCORE = 0.72
DIGIT_MIN_MARGIN = 0.06

# Units that can roll each die value, by side (1942 2nd Edition stats). Used to
# rule candidates out when matching icons. 'inf' appears at 2 for attackers
# because artillery boosts it.
ATT_BY_DIE = {1: {"inf", "car"}, 2: {"inf", "art", "sub", "des"}, 3: {"tank", "ftr", "cru"}, 4: {"bmb", "bb"}}
DEF_BY_DIE = {1: {"bmb", "sub", "aa"}, 2: {"inf", "art", "des", "car"}, 3: {"tank", "cru"}, 4: {"ftr", "bb"}}


@dataclass
class Entry:
    unit: object            # registry key, or None if the icon was not recognised
    count: object           # int, or None if a digit was not recognised
    icon_score: float
    count_text: str
    colour: str = "other"
    box: tuple = (0, 0, 0, 0)   # x0, y0, x1, y1 of the icon in 1440p coordinates


@dataclass
class Row:
    die_att: object
    die_def: object
    att: list = field(default_factory=list)
    dfn: list = field(default_factory=list)
    y0: int = 0
    y1: int = 0


@dataclass
class Panel:
    cx: int
    rows: list
    warnings: list = field(default_factory=list)
    bounds: tuple = (0, 0, 0, 0)  # left, top, right, bottom of the bars in 1440p coordinates
    frame: object = field(default=None, repr=False, compare=False)  # the normalised 1440p frame it was read from

    def signature(self):
        """Hashable summary used to detect that the panel changed."""
        return tuple((r.die_att, r.die_def,
                      tuple((e.unit, e.count) for e in r.att),
                      tuple((e.unit, e.count) for e in r.dfn)) for r in self.rows)


@dataclass
class RowGeom:
    """Where one row's parts are, before anything is recognised."""
    y0: int
    y1: int
    left: int
    right: int
    die_att: object
    die_def: object
    att_segs: list
    def_segs: list
    att_extra: bool = False   # unparsed icon-like content after the last label (an entry that was dropped)
    def_extra: bool = False


def as_bgr(img):
    """A uint8 BGR image from whatever was passed (gray, BGRA, float), or ``None`` if it is unusable."""
    if img is None or not hasattr(img, "shape") or img.ndim not in (2, 3) or img.size == 0:
        return None
    if img.dtype != np.uint8:
        img = np.clip(img, 0, 255).astype(np.uint8)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 4:
        return np.ascontiguousarray(img[:, :, :3])
    return img if img.shape[2] == 3 else None


def normalize(img):
    """Rescale a screenshot so its height is 1440 (layout constants assume that)."""
    h, w = img.shape[:2]
    if h == BASE_H:
        return img
    return cv2.resize(img, (round(w * BASE_H / h), BASE_H), interpolation=cv2.INTER_AREA)


_FLANKS = np.array([[1, 1, 0, 0, 0, 0, 0, 1, 1]], np.float32)   # columns 4 and 3 to the left, 3 and 4 to the right


def vivid_mask(img):
    """``(mask, x0)``: pixels with saturation > 150 and value > 140 (0 / 255) in the strip of the frame where a
    centre line can be (columns 10%..90%, rows 100..1400); ``x0`` is the strip's first column. Both line finders
    need it, so ``locate_rows`` computes it once."""
    w = img.shape[1]
    x0, x1 = int(w * 0.1), int(w * 0.9)
    hsv = cv2.cvtColor(img[100:1400, x0:x1], cv2.COLOR_BGR2HSV)
    return cv2.inRange(hsv, (0, 151, 141), (180, 255, 255)), x0


def find_thin_lines(img, run=14, vivid=None):
    """Left columns of isolated 2-3px wide vivid vertical lines at least ``run`` px tall.

    For short centre lines (a one-row panel), where the clustering in ``find_dividers`` is thrown off by
    saturated map regions touching the line's columns: here each pixel must be flanked by non-vivid ones.
    """
    mask, x0 = vivid if vivid is not None else vivid_mask(img)
    # centre column c of a line 1-3 px wide: vivid, with the columns 3 and 4 px to either side clear. The flank test
    # is one filter (the mask is 0 / 255 and sums saturate, so a flank is 255 if any of its four pixels is vivid).
    flank = cv2.filter2D(mask, -1, _FLANKS, borderType=cv2.BORDER_CONSTANT)
    out = cv2.subtract(mask, flank)
    out[:, :4] = 0       # the test only applies where all four flank columns exist
    out[:, -4:] = 0
    line = cv2.erode(out, np.ones((run, 1), np.uint8))
    cols = [int(c) + x0 - 1 for c in np.where(line.any(axis=0))[0]]  # left column of a 3px line
    return [c for i, c in enumerate(cols) if i == 0 or c != cols[i - 1] + 1]  # one candidate per run of columns


def find_dividers(img, run=60, vivid=None):
    """Left columns of every thin, tall, vividly coloured vertical line (centre-line candidates).

    A two-row panel's line is cut in two by the "VS" label, so runs only need to be
    ~60px tall. Thin (<= 6px wide) clusters only, since map regions are wide. Each
    candidate is later checked against the bars and dice, so false candidates are harmless.
    """
    sat, x0 = vivid if vivid is not None else vivid_mask(img)
    line = cv2.erode(sat, np.ones((run, 1), np.uint8))  # keep only tall vertical runs
    cols = np.where(line.sum(axis=0) > 0)[0]
    clusters, start, prev = [], None, None
    for c in cols:
        if start is None:
            start = prev = c
        elif c == prev + 1:
            prev = c
        else:
            clusters.append((start, prev))
            start = prev = c
    if start is not None:
        clusters.append((start, prev))
    return [int(a) + x0 for a, b in clusters if b - a + 1 <= 6]


def find_bars(img, cx):
    """(y0, y1) of each black bar (title bar included).

    A bar is black on both sides of the centre line, so two thin strips are examined, one just left of the left
    die and one just right of the right die, and a row counts as bar if either strip is (nearly) black there. Using
    both sides and tolerating a little noise keeps a bar found when image compression smears light content
    (a die, an icon) into one of the strips.
    """
    h, w = img.shape[:2]
    ok = np.zeros(1300, bool)
    for x0, x1 in ((cx - 66, cx - 60), (cx + 60, cx + 66)):
        if x0 < 0 or x1 > w:
            continue
        strip = img[100:1400, x0:x1].max(axis=2) < 20
        ok |= strip.mean(axis=1) >= 0.83  # at least 5 of 6 pixels dark
    runs, s = [], None
    for i, v in enumerate(ok):
        if v and s is None:
            s = i
        if not v and s is not None:
            runs.append((s + 100, i + 100))
            s = None
    return [r for r in runs if r[1] - r[0] >= 45]


PANEL_GREY = np.array([57, 57, 61])  # the uniform margin around the bars (BGR)


def _margin_colour(img, cx, y0, y1):
    """The panel's margin grey, sampled from the gap above or below the bar.

    It is not constant: the panel is drawn a little brighter or darker depending on the game state
    (57,57,61 in some captures, 31,31,33 in others), so a fixed value would miss the bar edges.
    """
    best = PANEL_GREY
    for a, b in ((y0 - 12, y0 - 5), (y1 + 5, y1 + 12)):
        if a < 0 or b > img.shape[0]:
            continue
        m = np.median(img[a:b, max(0, cx - 300):cx - 80].reshape(-1, 3), axis=0)
        if 18 < m.max() < 110 and (m.max() - m.min()) < 14:  # plain dark grey, not black, not an icon
            return m
    return best


def bar_extents(img, cx, y0, y1):
    """(left, right) x extents of the bar spanning rows y0..y1, or None.

    Icons can touch the bar's top and bottom edges, so the edges are found from
    the panel's uniform grey margin instead: walk outward from the centre line
    until four columns in a row show that grey on both the top and bottom edge
    rows of the bar.
    """
    rows = img[[y0 + 1, y1 - 2]].astype(int)  # shape (2, W, 3)
    grey = _margin_colour(img, cx, y0, y1)
    margin = (np.abs(rows - grey).max(axis=2) <= 8).all(axis=0)  # per column

    def walk(step):
        x = cx
        while 4 <= x < len(margin) - 4:
            x += step
            window = margin[x:x + 4] if step > 0 else margin[x - 3:x + 1]
            if window.all():
                return x - step
        return None

    left, right = walk(-1), walk(+1)
    if left is None or right is None or right - left < 300:
        return None
    return left, right


def read_die(img, y0, y1, x0, x1):
    """Pip count (1-6) of the white die inside the box, or None if no die is there."""
    box = cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY)
    white = (box > 200).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(white, connectivity=8)
    if n <= 1:
        return None
    k = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[k]
    if area < 500 or w < 25 or h < 25:
        return None
    inner = box[y + 5:y + h - 5, x + 5:x + w - 5]
    dark = (inner < 110).astype(np.uint8)
    m, _, st, _ = cv2.connectedComponentsWithStats(dark, connectivity=8)
    pips = sum(1 for i in range(1, m) if st[i, cv2.CC_STAT_AREA] >= 8)
    return pips if 1 <= pips <= 6 else None


def split_segments(img, y0, y1, x0, x1, thresh=60, merge=MERGE_GAP, min_w=3):
    """Left-to-right runs of non-black columns inside a region, small gaps merged."""
    region = img[y0:y1, x0:x1].max(axis=2) > thresh
    cols = region.any(axis=0)
    segs, s = [], None
    for x, v in enumerate(cols):
        if v and s is None:
            s = x
        if not v and s is not None:
            if x - s >= min_w:
                segs.append([s + x0, x + x0])
            s = None
    if s is not None:
        segs.append([s + x0, x1])
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < merge:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def pair_segments(img, y0, y1, x0, x1):
    """Flat ``[icon, label, icon, label, ...]`` column ranges for one half of a bar.

    The count labels are *pure white* text, so they are located first; the icon belonging to a
    label is whatever non-black content lies between the previous label (or the region start) and
    this label. This stays correct when an icon sits very close to its label, which a plain
    "merge nearby blobs" split gets wrong. Falls back to ``split_segments`` if no label is found.
    """
    rows = img[y0 + 26:y1 - 3, x0:x1]
    mn = rows.min(axis=2)
    # Text is pure white; after rescaling a screenshot its core is a little dimmer, so the cut-off
    # follows the brightest pixels of the region (but never drops into pale-icon territory).
    thr = max(215, min(245, int(np.percentile(mn, 99.8)) - 10)) if mn.size else 245
    white = mn > thr
    cols = white.sum(axis=0)
    xs = np.where(cols > 0)[0]
    clusters = []
    for x in xs:
        # digits of one label (and its "x") can sit several pixels apart; separate labels are an icon apart
        if clusters and x - clusters[-1][1] <= 12:
            clusters[-1][1] = x
        else:
            clusters.append([x, x])
    height_band = white.shape[0]

    def looks_like_text(a, b):
        """Text is tall (digits span >= 15 rows), sits on the baseline (white in the bottom rows of the
        band) and has plenty of white; sparse highlights on pale icons fail at least one of these."""
        block = white[:, a:b + 1]
        if b - a + 1 < 10 or block.sum() < 90:
            return False
        rows_w = np.where(block.any(axis=1))[0]
        return bool((rows_w.max() - rows_w.min() + 1 >= 15) and rows_w.max() >= height_band - 6
                    and block.sum() / ((b - a + 1) * height_band) >= 0.15)

    soft = (rows.min(axis=2) > 120).sum(axis=0)  # anti-aliased glyph edges, dimmer than the core

    def grow(a, b):
        """Extend a label to include its faint anti-aliased edge columns (at most 3px each side)."""
        for _ in range(3):
            if a > 0 and soft[a - 1] > 0:
                a -= 1
        for _ in range(3):
            if b < len(soft) - 1 and soft[b + 1] > 0:
                b += 1
        return a, b

    labels = []
    for a, b in clusters:
        if looks_like_text(a, b):
            ga, gb = grow(a, b)
            labels.append((ga + x0, gb + x0 + 1))
    if not labels:
        return split_segments(img, y0, y1, x0, x1)
    out, prev = [], x0
    for la, lb in labels:
        content = img[y0:y1, prev:la].max(axis=2) > 60
        cols_c = np.where(content.any(axis=0))[0]
        if len(cols_c) == 0:
            return split_segments(img, y0, y1, x0, x1)  # a label with no icon: layout not understood
        # group the content into runs; a narrow trailing run is the label's blurred edge, not icon
        runs = [[cols_c[0], cols_c[0]]]
        for c in cols_c[1:]:
            if c - runs[-1][1] <= 2:
                runs[-1][1] = c
            else:
                runs.append([c, c])
        while len(runs) > 1 and runs[-1][1] - runs[-1][0] + 1 <= 6:
            runs.pop()
        out.append([prev + int(runs[0][0]), prev + int(runs[-1][1]) + 1])
        out.append([la, lb])
        prev = lb
    return out


def _leftover(img, y0, y1, segs, lo, hi):
    """True if icon-like content (>= 12px wide) remains after the last label of a half bar, or if the half
    has content but no label at all: an entry the segmenter dropped. Never ignore it silently."""
    start = segs[-1][1] if segs else lo
    if hi - start < 12:
        return False
    content = img[y0:y1, start + 2:hi].max(axis=2) > 60
    cols = np.where(content.any(axis=0))[0]
    return len(cols) >= 12


def _has_stubs(img, cx, y0, y1):
    """The centre line is drawn at the top and the bottom of a bar (the dice and "VS" interrupt it in the middle)."""
    def vivid(a, b):
        hsv = cv2.cvtColor(img[max(0, a):b, cx - 1:cx + 3], cv2.COLOR_BGR2HSV)
        return int(((hsv[..., 1] > 150) & (hsv[..., 2] > 140)).any(axis=1).sum())
    return vivid(y0 - 10, y0 + 22) >= 14 and vivid(y1 - 22, y1 + 10) >= 6


def locate_rows(img):
    """Find the panel and return ``(cx, [RowGeom])``; ``(None, [])`` if no panel."""
    # A one-row panel's centre line is only two stubs (above and below the row), so a second pass accepts
    # short runs. Candidates are validated against the bars and dice, so extra candidates are harmless.
    vivid = vivid_mask(img)
    cands = find_dividers(img, vivid=vivid)
    short = [c for c in find_thin_lines(img, vivid=vivid) if c not in cands]
    cands += short
    best_cx, best_rows = None, []
    for cx in cands:
        rows = []
        for y0, y1 in find_bars(img, cx):
            if cx in short and not _has_stubs(img, cx, y0, y1):
                continue
            da = read_die(img, y0, y1, cx - DIE_FAR, cx - DIE_NEAR)
            dd = read_die(img, y0, y1, cx + DIE_NEAR, cx + DIE_FAR)
            if da is None and dd is None:
                continue  # the title bar or a decorative bar, not a unit row
            ext = bar_extents(img, cx, y0, y1)
            if ext is None:
                continue
            left, right = ext
            a_lo, a_hi = left + 2, cx - DIE_FAR - 4
            d_lo, d_hi = cx + DIE_FAR + 4, right - 2
            a_segs = pair_segments(img, y0, y1, a_lo, a_hi)
            d_segs = pair_segments(img, y0, y1, d_lo, d_hi)
            rows.append(RowGeom(y0, y1, left, right, da, dd, a_segs, d_segs,
                                _leftover(img, y0, y1, a_segs, a_lo, a_hi),
                                _leftover(img, y0, y1, d_segs, d_lo, d_hi)))
        # a stray candidate can also pass the bar/dice checks on one row: take the one that explains the most rows
        if len(rows) > len(best_rows):
            best_cx, best_rows = cx, rows
        if len(best_rows) >= 3:
            break
    return best_cx, best_rows


def stray_dice(img, cx, geoms):
    """Number of die squares beside the centre line that lie outside every row that was read.

    Every row of the panel has a die (a white square with pips) on at least one side of the centre line. A
    die the row finder did not account for means a row was missed (a bar that image noise or compression hid),
    and the army read from the rest would be silently incomplete, so the caller warns.
    """
    n = 0
    for x0, x1 in ((cx - DIE_FAR, cx - DIE_NEAR), (cx + DIE_NEAR, cx + DIE_FAR)):
        if x0 < 0 or x1 > img.shape[1]:
            continue
        gray = cv2.cvtColor(img[100:1400, x0:x1], cv2.COLOR_BGR2GRAY)
        white = (gray > 200).astype(np.uint8)
        cnt, _, stats, cents = cv2.connectedComponentsWithStats(white, connectivity=8)
        for i in range(1, cnt):
            x, y, w, h, area = stats[i]
            if not (25 <= w <= 60 and 25 <= h <= 60) or abs(w - h) > 8 or area < 0.7 * w * h:
                continue  # not a die-shaped white square
            inner = gray[y + 5:y + h - 5, x + 5:x + w - 5]
            m, _, st, _ = cv2.connectedComponentsWithStats((inner < 110).astype(np.uint8), connectivity=8)
            if not 1 <= sum(1 for j in range(1, m) if st[j, cv2.CC_STAT_AREA] >= 8) <= 6:
                continue  # a white shape without pips
            yc = int(cents[i][1]) + 100
            if not any(g.y0 - 6 <= yc <= g.y1 + 6 for g in geoms):
                n += 1
    return n


def digit_glyphs(img, y0, y1, x0, x1):
    """Digit glyph masks (left to right) inside a count label's columns.

    The digit height is measured from the bright text itself. Digits are found in
    the top 18% of that height, where the shorter ``x`` is absent. A run much wider
    than one digit is split evenly (touching digits).
    """
    bright = img[y0:y1, x0:x1].max(axis=2) > 140
    rows = np.where(bright.any(axis=1))[0]
    if len(rows) == 0:
        return []
    top, bottom = int(rows.min()), int(rows.max())
    height = bottom - top + 1
    band = bright[top:top + max(2, int(height * 0.18))]  # the 'x' starts ~23% below the digit tops
    cols = band.any(axis=0)
    runs, s = [], None
    for x, v in enumerate(cols):
        if v and s is None:
            s = x
        if not v and s is not None:
            runs.append((s, x))
            s = None
    if s is not None:
        runs.append((s, len(cols)))
    digit_w = 0.58 * height
    glyphs = []
    for a, b in runs:
        # a run clearly wider than one digit is touching digits ("88"): split it evenly
        n = max(1, round((b - a) / digit_w)) if (b - a) > 1.25 * digit_w else 1
        step = (b - a) / n
        for i in range(n):
            ca, cb = a + int(i * step), a + int((i + 1) * step)
            glyphs.append(bright[top:bottom + 1, max(0, ca - 1):cb + 1])
    return glyphs


def read_count(img, y0, y1, x0, x1, store):
    """Return ``(count or None, text)`` for the ``xN`` label in the given columns."""
    text = ""
    for g in digit_glyphs(img, y0, y1, x0, x1):
        (d, score), runner = store.match_digit_ranked(g)
        # Refuse to guess: the best digit must score well AND clearly beat every other digit.
        ok = d is not None and score >= DIGIT_MIN_SCORE and score - runner >= DIGIT_MIN_MARGIN
        text += d if ok else "?"
    if not text or "?" in text:
        return None, text or "?"
    return int(text), text


def read_side(img, g, segs, die, by_die, store, warnings, side):
    """Parse one half of a bar into ``Entry`` objects (icon/count pairs)."""
    if len(segs) % 2:
        warnings.append(f"{side}: odd number of icon/label blobs ({len(segs)}) in row at y={g.y0}")
    entries = []
    for i in range(0, len(segs) - 1, 2):
        (ia, ib), (ca, cb) = segs[i], segs[i + 1]
        icon = img[g.y0:g.y1, ia:ib]
        cands = by_die.get(die) if die else None
        unit, colour, score = store.match_icon(icon, cands)
        if unit is None or score < ICON_MIN_SCORE:
            warnings.append(f"{side}: unrecognised icon at x={ia},y={g.y0} (best score {score:.2f})")
            unit = None
        count, text = read_count(img, g.y0, g.y1, ca - 3, cb + 3, store)
        if count is None:
            warnings.append(f"{side}: unreadable count '{text}' at x={ca},y={g.y0}")
        entries.append(Entry(unit, count, score, text, colour or colour_class(icon), (ia, g.y0, ib, g.y1)))
    return entries


def read_panel(img, store=None):
    """Read the attack panel from a BGR screenshot; ``None`` if no panel is showing."""
    store = store or TemplateStore()
    img = as_bgr(img)
    if img is None or img.shape[0] < 100 or img.shape[1] < 100:
        return None
    img = normalize(img)
    cx, geoms = locate_rows(img)
    if cx is None or not geoms:
        return None
    rows, warnings = [], []
    for g in geoms:
        row = Row(g.die_att, g.die_def, y0=g.y0, y1=g.y1)
        row.att = read_side(img, g, g.att_segs, g.die_att, ATT_BY_DIE, store, warnings, "attacker")
        row.dfn = read_side(img, g, g.def_segs, g.die_def, DEF_BY_DIE, store, warnings, "defender")
        for flag, side in ((g.att_extra, "attacker"), (g.def_extra, "defender")):
            if flag:
                warnings.append(f"{side}: unparsed content after the last entry in the row at y={g.y0} "
                                "(an entry may have been missed)")
        rows.append(row)
    missed = stray_dice(img, cx, geoms)
    if missed:
        warnings.append(f"a die was seen outside the rows that were read ({missed}): a row of the panel may be missing")
    bounds = (min(g.left for g in geoms), min(g.y0 for g in geoms),
              max(g.right for g in geoms), max(g.y1 for g in geoms))
    return Panel(cx, rows, warnings, bounds, img)


def panel_to_armies(panel):
    """Convert a ``Panel`` into ``(attacker_tuple, defender_tuple, warnings)``.

    Counts of the same unit across rows add up (an attacking infantry on die 2
    is an artillery-boosted infantry and is still an infantry). Entries with an
    unknown unit or count are skipped and reported, so the armies are only
    trustworthy when ``warnings`` is empty. AA guns and transports never roll, so
    the game hides them behind "VIEW NON COMBATANTS"; the caller adds them.
    """
    from aa1942.units import IDX, N  # local import keeps the reader usable without aa1942 on the path
    att, dfn = [0] * N, [0] * N
    warnings = list(panel.warnings)
    for row in panel.rows:
        for entries, army, side in ((row.att, att, "attacker"), (row.dfn, dfn, "defender")):
            for e in entries:
                if e.unit is None or e.count is None:
                    warnings.append(f"{side}: skipped an unreadable entry in the die-{row.die_att or row.die_def} row")
                    continue
                army[IDX[e.unit]] += e.count
    return tuple(att), tuple(dfn), warnings
