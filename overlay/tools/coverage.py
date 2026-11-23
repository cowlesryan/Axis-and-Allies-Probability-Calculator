"""Report what the harvested dataset covers and how well the reader does on it.

Usage (from the overlay folder):
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\coverage.py

Accuracy is measured by re-reading every sample with the *current* templates and comparing with the
save's ground truth. The number that matters most is ``wrong without warning``: a reading that is
wrong but not flagged. It must be zero before the overlay can be trusted in a real game.
Note: samples are also what the templates were learned from, so this is a training-set figure;
hold out whole scenarios (``--holdout <game_id>``) for an honest test.
"""
import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import aaoverlay  # noqa: E402,F401
from aaoverlay import dataset as D  # noqa: E402
from aaoverlay import reader as R  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402
from aa1942.units import ORDER  # noqa: E402

UNITS = ["inf", "art", "tank", "ftr", "bmb", "sub", "des", "cru", "car", "bb"]
POWERS = ["soviet_union", "germany", "united_kingdom", "japan", "united_states"]


def main(holdout):
    store = TemplateStore()
    n = 0
    unit_seen = collections.defaultdict(set)      # unit -> {"att"/"def"}
    power_seen = collections.defaultdict(set)     # power -> units seen as attackers
    digits = collections.Counter()
    layouts = collections.Counter()
    outcome = collections.Counter()
    wrong = []
    for name, meta in D.samples():
        held = holdout and meta.get("source", {}).get("game_id") == holdout
        n += 1
        t = meta["truth"]
        for side in ("attackers", "defenders"):
            for unit, cnt in t[side].items():
                unit_seen[unit].add("att" if side == "attackers" else "def")
                digits.update(str(cnt))
                if side == "attackers":
                    power_seen[t["attacker_power"]].add(unit)
        layouts[len(meta["panel"]["dice"])] += 1
        img = D.load_canvas(name, meta)
        panel = R.read_panel(img, store)
        if panel is None:
            outcome["panel not found"] += 1
            continue
        att, dfn, warnings = R.panel_to_armies(panel)
        got_a = {k: v for k, v in zip(ORDER, att) if v}
        got_d = {k: v for k, v in zip(ORDER, dfn) if v}
        want_d = dict(t["defenders"])
        if t.get("panel_aa"):
            want_d["aa"] = t["panel_aa"]
        correct = got_a == t["attackers"] and got_d == want_d
        if held:
            outcome["HELD-OUT " + ("flagged" if warnings else "exact" if correct else "WRONG WITHOUT WARNING")] += 1
            if not warnings and not correct:
                wrong.append(name)
            continue
        if warnings:
            outcome["flagged"] += 1
        elif correct:
            outcome["exact"] += 1
        else:
            outcome["WRONG WITHOUT WARNING"] += 1
            wrong.append(name)
    print(f"samples: {n}")
    print("\nreader on these samples:")
    for k in ("exact", "flagged", "WRONG WITHOUT WARNING", "panel not found"):
        print(f"  {k:24s}{outcome.get(k, 0):4d}")
    if holdout:
        print("\nheld-out scenario (never learned from):")
        for k in ("exact", "flagged", "WRONG WITHOUT WARNING"):
            print(f"  {k:24s}{outcome.get('HELD-OUT ' + k, 0):4d}")
    if wrong:
        print("  wrong-without-warning samples:", ", ".join(wrong))
    print("\nunits seen (a = attacker, d = defender):")
    for u in UNITS:
        s = unit_seen.get(u, set())
        print(f"  {u:5s} {'a' if 'att' in s else '-'}{'d' if 'def' in s else '-'}")
    print("\nattacking powers seen:", {p: sorted(power_seen[p]) for p in POWERS if p in power_seen})
    missing_p = [p for p in POWERS if p not in power_seen]
    print("powers with no samples:", missing_p or "none")
    print("\ndigits in counts (sample counts):", dict(sorted(digits.items())))
    print("digits never seen:", [d for d in "0123456789" if d not in digits] or "none")
    print("digit glyph templates:", sorted({d for d, _, _ in store.digits}))
    print("\nrows per panel:", dict(sorted(layouts.items())))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holdout", help="scenario game id to treat as held out (reported separately)")
    main(ap.parse_args().holdout)
