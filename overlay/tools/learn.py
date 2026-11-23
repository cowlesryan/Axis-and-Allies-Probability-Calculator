"""Teach the reader new icons and digits from harvested, ground-truth-labelled samples.

Usage (from the overlay folder):
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\learn.py            # learn from every sample
    ...\\python.exe tools\\learn.py --dry-run                                   # report only

For each sample the panel's rows are matched to the save's truth. An entry is taught only
when the match is unambiguous: within a row (and side) the number of panel entries equals
the number of truth units rolling that die value, and either there is one of them or their
counts all differ and every count was read. Ambiguous rows are listed so they can be labelled
by hand (see ``--ambiguous``). New icon templates are added only when the best existing match
is weak, so the template set does not balloon.
"""
import argparse
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import aaoverlay  # noqa: E402,F401
from aaoverlay import dataset as D  # noqa: E402
from aaoverlay import reader as R  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402
from aa1942.units import IDX, UNIT_LIST  # noqa: E402

STRONG = 0.88  # an existing template this close means the icon is already known


def expected_dice(counts, attacking):
    """``[(unit, die_value, count)]`` for a side's truth counts (artillery-boosted infantry on die 2)."""
    out = []
    boosted = min(counts.get("inf", 0), counts.get("art", 0)) if attacking else 0
    for unit, n in counts.items():
        if unit in ("aa", "trn"):
            continue
        u = UNIT_LIST[IDX[unit]]
        value = u.attack if attacking else u.defend
        if unit == "inf" and attacking:
            if boosted:
                out.append((unit, 2, boosted))
            if n - boosted:
                out.append((unit, 1, n - boosted))
        else:
            out.append((unit, value, n))
    return out


def side_rows(truth, side, attacking):
    """Expected ``(unit, die, count)`` rows for a side, including the AA gun the panel shows at die 1
    on the defender side when the attackers include aircraft."""
    rows = expected_dice(truth[side], attacking)
    if not attacking and truth.get("defender_entries"):
        # one panel entry per (owner, unit): units of different powers are drawn separately
        rows = [(unit, UNIT_LIST[IDX[unit]].defend, n) for _, unit, n in truth["defender_entries"]
                if unit in IDX and unit not in ("aa", "trn")]
        rows = [(("bb" if u == "bbd" else u), d, n) for u, d, n in rows]
    if not attacking and truth.get("panel_aa_entries"):
        rows += [("aa", 1, n) for _, n in truth["panel_aa_entries"]]
    elif not attacking and truth.get("panel_aa"):
        rows.append(("aa", 1, truth["panel_aa"]))
    return rows


def assign(entries, truth_rows, icon_scores=None):
    """Match panel entries to truth ``(unit, die, count)`` rows; returns list of (entry, unit, count) or None.

    Order of preference: a single entry; distinct readable counts; finally elimination by icon
    similarity (``icon_scores[i][j]`` = how well entry i matches truth unit j with templates already
    learned), accepted only when one assignment clearly beats every other.
    """
    if len(entries) != len(truth_rows):
        return None
    if len(entries) == 1:
        return [(entries[0], truth_rows[0][0], truth_rows[0][2])]
    counts = [t[2] for t in truth_rows]
    if len(set(counts)) == len(counts) and all(e.count is not None for e in entries):
        by_count = {t[2]: t for t in truth_rows}
        if all(e.count in by_count for e in entries):
            return [(e, by_count[e.count][0], e.count) for e in entries]
    if icon_scores is not None and len(entries) <= 4:
        from itertools import permutations
        ranked = sorted(((sum(icon_scores[i][p[i]] for i in range(len(entries))), p)
                         for p in permutations(range(len(entries)))), reverse=True)
        best, second = ranked[0], (ranked[1] if len(ranked) > 1 else (0.0, None))
        # needs a clearly best assignment, anchored by at least one icon that already looks like its
        # unit (the rest follow by elimination)
        if best[0] - second[0] >= 0.25 and max(icon_scores[i][best[1][i]] for i in range(len(entries))) >= 0.85:
            return [(e, truth_rows[best[1][i]][0], truth_rows[best[1][i]][2]) for i, e in enumerate(entries)]
    return None


def main(dry_run, show_ambiguous):
    store = TemplateStore()
    taught_icons = taught_digits = 0
    ambiguous = []
    # Two passes: the second can resolve equal-count rows by elimination using icons the first learned.
    for pass_no in (1, 2):
        ambiguous = []
        t_i, t_d = learn_pass(store, dry_run, ambiguous)
        taught_icons += t_i
        taught_digits += t_d
        if dry_run or (t_i == 0 and t_d == 0):
            break
    verb = "would teach" if dry_run else "taught"
    print(f"{verb} {taught_icons} new icon templates and {taught_digits} digit glyphs; "
          f"templates now {len(store.icons)} icons / {len(store.digits)} digits")
    if ambiguous:
        print(f"{len(ambiguous)} ambiguous rows (label by hand if needed):")
        for line in ambiguous[:40 if show_ambiguous else 8]:
            print("  " + line)


def holdout_ids():
    """Scenario game ids listed in dataset/holdout.txt: never learned from, only used to test."""
    path = os.path.join(D.ROOT, "holdout.txt")
    return set(open(path).read().split()) if os.path.exists(path) else set()


def learn_pass(store, dry_run, ambiguous):
    taught_icons = taught_digits = 0
    held = holdout_ids()
    for name, meta in D.samples():
        if meta.get("source", {}).get("game_id") in held:
            continue
        img = D.load_canvas(name, meta)
        cx, geoms = R.locate_rows(img)
        if cx is None:
            print(f"{name}: no panel found in sample, skipped")
            continue
        truth = meta["truth"]
        for g in geoms:
            for side, attacking, segs, die in (("attackers", True, g.att_segs, g.die_att),
                                               ("defenders", False, g.def_segs, g.die_def)):
                if not segs and not die:
                    continue
                rows = [t for t in side_rows(truth, side, attacking) if t[1] == die]
                pairs = [(segs[i], segs[i + 1]) for i in range(0, len(segs) - 1, 2)]
                entries = []
                for (ia, ib), (ca, cb) in pairs:
                    count, text = R.read_count(img, g.y0, g.y1, ca - 3, cb + 3, store)
                    entries.append(type("E", (), dict(icon=(ia, ib), cnt=(ca, cb), count=count, text=text))())
                scores = [[store.match_icon(img[g.y0:g.y1, e.icon[0]:e.icon[1]], {t[0]})[2] for t in rows]
                          for e in entries] if len(entries) == len(rows) else None
                match = assign(entries, rows, scores)
                if match is None:
                    if pairs or rows:
                        ambiguous.append(f"{name}: {side} die {die} -> panel has {len(pairs)} entries, truth {rows}")
                    continue
                for e, unit, count in match:
                    icon = img[g.y0:g.y1, e.icon[0]:e.icon[1]]
                    best_unit, _, score = store.match_icon(icon, {unit})
                    if score < STRONG:
                        if not dry_run:
                            store.add_icon(unit, icon)
                        taught_icons += 1
                    glyphs = R.digit_glyphs(img, g.y0, g.y1, e.cnt[0] - 3, e.cnt[1] + 3)
                    if len(glyphs) == len(str(count)):
                        for ch, glyph in zip(str(count), glyphs):
                            d, s = store.match_digit(glyph)
                            if d != ch or s < 0.8:
                                if not dry_run:
                                    store.add_digit(ch, glyph)
                                taught_digits += 1
                    else:
                        ambiguous.append(f"{name}: {side} die {die} count '{count}' split into {len(glyphs)} glyphs")
    return taught_icons, taught_digits


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ambiguous", action="store_true", help="list more ambiguous rows")
    a = ap.parse_args()
    main(a.dry_run, a.ambiguous)
