"""Capture a training sample of the game's attack panel, labelled from the offline save.

Usage (from the overlay folder, with the game showing an attack panel):
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\harvest.py snap --region sea_zone_17 --tag first_try
    ...\\python.exe tools\\harvest.py snap --auto --tag round1     # use the only region under attack
    ...\\python.exe tools\\harvest.py regions                       # list regions currently under attack

Each sample is a crop of the panel only (no other windows) saved to ``dataset/raw`` as
``NNNN_<tag>.png`` with a ``.json`` sidecar holding the ground truth from the save, the
reader's own guess, and whether the two agree. ``tools/coverage.py`` summarises what is
still missing.
"""
import argparse
import json
import os
import sys
import time

import cv2
import mss
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import aaoverlay  # noqa: E402,F401
from aaoverlay import reader as R  # noqa: E402
from aaoverlay import savetruth as T  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402

RAW = os.path.join(ROOT, "dataset", "raw")


def grab():
    with mss.mss() as sct:
        return np.array(sct.grab(sct.monitors[1]))[:, :, :3]


def stable_panel(tries=12, gap=0.35):
    """Wait for the panel to stop animating: same layout on three consecutive grabs."""
    store = TemplateStore()
    seen = []
    for _ in range(tries):
        img = R.normalize(grab())
        panel = R.read_panel(img, store)
        key = None if panel is None else (panel.bounds, panel.cx, panel.signature())
        seen.append((img, panel, key))
        if len(seen) >= 3 and key is not None and seen[-1][2] == seen[-2][2] == seen[-3][2]:
            return img, panel
        time.sleep(gap)
    return None, None


from aaoverlay.windows import crop_box, occluding_windows  # noqa: E402,F401  (moved into the package)


def next_index():
    os.makedirs(RAW, exist_ok=True)
    nums = [int(f.split("_")[0]) for f in os.listdir(RAW) if f.endswith(".png") and f.split("_")[0].isdigit()]
    return (max(nums) + 1) if nums else 1


def guess_counts(panel):
    att, dfn, warnings = R.panel_to_armies(panel)
    from aa1942.units import ORDER
    to = lambda a: {k: n for k, n in zip(ORDER, a) if n}
    return to(att), to(dfn), warnings


def normalise_truth(counts):
    """Truth counts with the internal damaged-battleship slot folded back into ``bb``."""
    out = dict(counts)
    if "bbd" in out:
        out["bb"] = out.get("bb", 0) + out.pop("bbd")
    return out


def panel_structure(img, store):
    """``{(side, die): [counts]}`` as read from the panel (None = unreadable count)."""
    from learn import expected_dice  # noqa: F401  (same package folder)
    cx, geoms = R.locate_rows(img)
    out = {}
    for g in geoms:
        for side, segs, die in (("att", g.att_segs, g.die_att), ("def", g.def_segs, g.die_def)):
            if die is None:
                continue
            counts = []
            for i in range(0, len(segs) - 1, 2):
                (ca, cb) = segs[i + 1]
                counts.append(R.read_count(img, g.y0, g.y1, ca - 3, cb + 3, store)[0])
            out[(side, die)] = counts
    return out


def truth_structure(truth):
    """The same shape from a truth dict (``attackers`` / ``defenders`` counts, plus panel AA guns)."""
    from learn import side_rows
    out = {}
    for side, attacking in (("att", True), ("def", False)):
        for unit, die, n in side_rows(truth, "attackers" if attacking else "defenders", attacking):
            out.setdefault((side, die), []).append(n)
    return out


def compatible(panel_s, truth_s):
    """Same rows and entry counts, and counts equal wherever the panel's were readable."""
    from itertools import permutations
    if set(panel_s) != set(truth_s):
        return False
    for key, got in panel_s.items():
        want = truth_s[key]
        if len(got) != len(want):
            return False
        if not any(all(g is None or g == w for g, w in zip(got, perm)) for perm in permutations(want)):
            return False
    return True


def scenario_truths(game_id):
    """``{target: truth}`` for every pending battle in the scenario's save (encounter-based)."""
    state = T.load_state(os.path.join(T.SAVE_DIR, f"{game_id}.json"))
    return {t: T.truth_for(state, t) for t in state["pendingEncounters"]}


def identify(truths, img, store, skip=()):
    """The ``(target, truth)`` battles whose layout and counts fit the panel on screen."""
    ps = panel_structure(img, store)
    return [(t, tr) for t, tr in truths.items() if t not in skip and compatible(ps, truth_structure(tr))]


def captured_targets(game_id):
    done = set()
    for f in os.listdir(RAW) if os.path.isdir(RAW) else []:
        if f.endswith(".json"):
            with open(os.path.join(RAW, f), encoding="utf-8") as fh:
                m = json.load(fh)
            if m.get("source", {}).get("game_id") == game_id:
                done.add(m["source"]["target"])
    return done


def snap(region, tag, auto, manifest_id=None, force=False):
    source = None
    if manifest_id:
        truths = scenario_truths(manifest_id)
        img, panel = stable_panel()
        if panel is None:
            sys.exit("no stable attack panel on screen")
        store = TemplateStore()
        done = set() if force else captured_targets(manifest_id)
        matches = identify(truths, img, store, skip=done)
        if not matches:
            sys.exit("panel does not match any battle not yet captured in this scenario "
                     "(already captured, or an unexpected layout)")
        if len(matches) > 1:
            sys.exit(f"ambiguous: panel fits {[t for t, _ in matches]}; capture it with --region instead")
        region, truth = matches[0]
        source = {"game_id": manifest_id, "target": region}
    else:
        state = T.load_state()
        if auto:
            regions = T.regions_under_attack(state)
            if len(regions) != 1:
                sys.exit(f"--auto needs exactly one region under attack; found {regions}")
            region = regions[0]
        img, panel = stable_panel()
        if panel is None:
            sys.exit("no stable attack panel on screen")
        state = T.load_state()  # re-read: the save autosaves continuously
        truth = T.truth_for(state, region)
    att_g, def_g, warnings = guess_counts(panel)
    truth_att, truth_def = normalise_truth(truth["attackers"]), normalise_truth(truth["defenders"])
    if truth.get("panel_aa"):
        truth_def["aa"] = truth["panel_aa"]  # defending AA guns are on the panel when attackers have aircraft
    match = (att_g == truth_att and def_g == truth_def and not warnings)
    box = crop_box(img, panel)
    x0, y0, x1, y1 = box
    scale = grab().shape[0] / R.BASE_H  # crop box is in 1440p units; windows are in screen pixels
    blockers = occluding_windows(tuple(int(v * scale) for v in box))
    if blockers:
        sys.exit(f"refusing to capture: another window overlaps the panel ({', '.join(blockers)}). "
                 "Pan the map so the panel sits clear of it, then retry.")
    idx = next_index()
    name = f"{idx:04d}_{tag}"
    cv2.imwrite(os.path.join(RAW, name + ".png"), img[y0:y1, x0:x1])
    meta = {
        "name": name, "tag": tag, "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
        "crop_box_1440p": list(box), "screen_height_1440p": img.shape[0],
        "panel": {"cx": panel.cx, "bounds": list(panel.bounds),
                  "dice": [[r.die_att, r.die_def] for r in panel.rows]},
        "truth": {**truth, "attackers": truth_att, "defenders": truth_def},
        "reader": {"attackers": att_g, "defenders": def_g, "warnings": warnings, "matches_truth": match},
    }
    if source:
        meta["source"] = source
    with open(os.path.join(RAW, name + ".json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    print(f"saved {name}.png  region={region}  power={truth['attacker_power']}")
    print(f"  truth    att={truth_att}  def={truth_def}  hidden={truth['hidden']}")
    print(f"  reader   att={att_g}  def={def_g}  warnings={len(warnings)}")
    print("  RESULT:", "reader MATCHES truth" if match else "reader differs from truth (sample needs templates)")
    return meta


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("snap")
    s.add_argument("--region")
    s.add_argument("--auto", action="store_true")
    s.add_argument("--tag", default="sample")
    s.add_argument("--manifest", help="scenario game id: identify the battle from the panel's layout/counts")
    s.add_argument("--force", action="store_true", help="capture even if this battle was already captured")
    sub.add_parser("regions")
    a = ap.parse_args()
    if a.cmd == "regions":
        st = T.load_state()
        ps = st["phaseStorage"]
        print(f"round {ps['round']} {ps['power']} {ps['phase']}: under attack -> {T.regions_under_attack(st)}")
    else:
        if not a.region and not a.auto and not a.manifest:
            sys.exit("give --region, --auto or --manifest")
        snap(a.region, a.tag, a.auto, a.manifest, a.force)


if __name__ == "__main__":
    main()
