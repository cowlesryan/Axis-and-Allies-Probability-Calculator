"""Generate offline-save copies full of custom attack panels for training the reader.

Each scenario is a COPY of a base offline save, with:
* the current power switched to the one under test (combat move phase),
* every existing pending battle removed,
* N land and M sea battles added, built from brand-new units (new ids): attackers owned by the
  power, placed in a neighbouring region and moved to the target; defenders owned by enemy powers
  sitting in the target.

Battles are chosen to be easy to capture and to fill gaps:
* targets are in the middle of the default map view (Europe, the Atlantic, the Mediterranean,
  the Middle East), so panels sit clear of the bottom bar and of other windows;
* a greedy planner picks, for each battle, the unit mix and the defender owners that fill the
  most still-missing (power, unit) icon cells and under-represented panel layouts;
* counts come from lists that together contain every digit, and each battle gets a different
  "prediction", which the game turns into its outlook label and centre-line colour.

Nothing existing is modified: the copy gets a new id/name (see make_scenario.write_copy) and the base is
backed up. Offline games only. A manifest of the battles is written to dataset/scenarios/<id>.json.

Usage:
    %USERPROFILE%\\aaoverlay-venv\\Scripts\\python.exe tools\\scenario_gen.py --power germany --land 9 --sea 9
    ...\\python.exe tools\\scenario_gen.py --plan        # the full capture plan (5 scenarios)
    ...\\python.exe tools\\scenario_gen.py --clean       # delete scenario copies this tool made
Restart the game afterwards (it reads the save folder only at startup).
"""
import argparse
import copy
import glob
import itertools
import json
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import aaoverlay  # noqa: E402,F401
from aaoverlay import savetruth as T  # noqa: E402
import make_scenario as M  # noqa: E402

POWERS = ["soviet_union", "germany", "united_kingdom", "japan", "united_states"]
KIND_PREFIX = {v: k for k, v in T.PREFIX.items()}
NAME_PREFIX = "Overlay "

LAND_KINDS = ["inf", "art", "tank", "ftr", "bmb"]
SEA_KINDS = ["sub", "des", "cru", "car", "bb", "ftr", "bmb"]
AIR = {"ftr", "bmb"}
# panel rows = distinct dice among the entries (1-4); the planner favours under-represented layouts
DICE_ATT = {"inf": (1, 2), "art": 2, "tank": 3, "ftr": 3, "bmb": 4, "sub": 2, "des": 2, "cru": 3, "car": 1, "bb": 4}
DICE_DEF = {"inf": 2, "art": 2, "tank": 3, "ftr": 4, "bmb": 1, "sub": 1, "des": 2, "cru": 3, "car": 2, "bb": 4}

# Targets in the middle of the default map view. Region ids that do not exist are ignored.
CENTRAL_LAND = ["norway", "finland", "karelia_ssr", "baltic_states", "belorussia", "ukraine_ssr", "west_russia",
                "caucasus", "bulgaria_romania", "poland", "germany", "france", "italy", "southern_europe",
                "spain_portugal", "libya", "egypt", "turkey", "trans-jordan", "algeria", "northwestern_europe",
                "sweden", "united_kingdom", "archangel", "persia", "morocco"]
CENTRAL_SEA = ["sea_zone_%d" % n for n in (3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 14, 15, 16, 17)]

ATT_COUNTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 17, 20, 24]
DEF_COUNTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 20, 23, 30, 31, 37, 38, 40, 48, 50, 57, 60, 67,
              70, 73, 77, 78, 80, 83, 88, 89, 90, 99]
PREDICTIONS = [50]  # outlook labels are not read or used, so no variety is needed
CELL_TARGET = 2  # appearances wanted per (power, unit) icon cell within one scenario


def alliance(state, power):
    return state["powers"][power]["alliance"]


class Builder:
    """Adds new units and battles to a save state (in memory)."""

    def __init__(self, state):
        self.state = state
        self.next_id = int(state["lastUnitId"])
        self.templates = {}
        for uid, u in state["units"].items():
            prefix = "".join(c for c in uid if c.isalpha())
            self.templates.setdefault(prefix, u)
        enc = next(iter(state["pendingEncounters"].values()), None)
        self.encounter_template = copy.deepcopy(enc) if enc else None

    def new_unit(self, kind, owner, location):
        prefix = KIND_PREFIX[kind]
        self.next_id += 1
        uid = f"{prefix}{self.next_id}"
        u = copy.deepcopy(self.templates[prefix])
        u.update(id=uid, owner=owner, location=location, startOfTurnLocation=location, damage=0,
                 combatMovePath=[], nonCombatMovePath=[], transporter=None)
        self.state["units"][uid] = u
        self.state["lastUnitId"] = self.next_id
        return uid

    def add_battle(self, power, target, attackers, defenders, origin, prediction):
        """``attackers`` {kind: n}; ``defenders`` [(owner, kind, n)]; returns the battle's ids."""
        st = self.state
        att_ids, def_ids = [], []
        for kind, n in attackers.items():
            for _ in range(n):
                uid = self.new_unit(kind, power, origin)
                att_ids.append(uid)
                st["phaseStorage"]["delta"].append({"func": "moveUnit", "args": [uid, [target], {target: True}]})
        # the game lists every enemy unit already in the target as a defender too
        for uid, u in st["units"].items():
            if u["location"] == target and alliance(st, u["owner"]) != alliance(st, power) and uid not in def_ids:
                def_ids.append(uid)
        for owner, kind, n in defenders:
            for _ in range(n):
                def_ids.append(self.new_unit(kind, owner, target))
        enc = copy.deepcopy(self.encounter_template)
        enc["location"] = target
        enc["initialAttackers"] = enc["currentAttackers"] = list(att_ids)
        enc["initialDefenders"] = enc["currentDefenders"] = list(def_ids)
        enc["retreatableEntryLocations"] = {uid: origin for uid in att_ids}
        enc["prediction"] = prediction
        st["pendingEncounters"][target] = enc
        return att_ids, def_ids


def pick_spread(items, n):
    """``n`` items spread evenly across a list (deterministic)."""
    if len(items) <= n:
        return list(items)
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def land_targets(state, power):
    """Legal land targets for ``power``: enemy-held regions that have a land neighbour owned by the
    attacker's alliance (the attackers start there). Central-view regions come first.

    Starting attackers anywhere else (e.g. an enemy-held neighbour) makes the game crash on load
    ("getCommittedUnit of undefined"), so legality is required.
    """
    my = alliance(state, power)
    ordered = CENTRAL_LAND + sorted(r for r, v in state["regions"].items()
                                    if v["type"] == "land" and r not in CENTRAL_LAND)
    out = []
    for rid in ordered:
        r = state["regions"].get(rid)
        if not r or not r.get("owner") or alliance(state, r["owner"]) == my:
            continue
        for adj in r["adjacentRegions"]:
            a = state["regions"][adj]
            if a["type"] == "land" and a.get("owner") and alliance(state, a["owner"]) == my:
                out.append((rid, adj))
                break
    return out


def central_targets(state, domain):
    """``[(target, origin)]``: central-view regions with a neighbour of the same domain as origin."""
    wanted = CENTRAL_LAND if domain == "land" else CENTRAL_SEA
    out = []
    for rid in wanted:
        r = state["regions"].get(rid)
        if not r:
            continue
        same = [a for a in r["adjacentRegions"]
                if (state["regions"][a]["type"] == "land") == (domain == "land")]
        if same:
            out.append((rid, same[0]))
    return out


class Planner:
    """Greedy unit-mix chooser that fills missing (power, unit) icon cells and panel layouts."""

    def __init__(self, power, enemies, seed=1):
        self.power, self.enemies = power, enemies
        self.rng = random.Random(seed)
        self.att = {}   # kind -> appearances as attacker
        self.dfn = {}   # (owner, kind) -> appearances as defender
        self.layouts = {}

    def _rows(self, att_kinds, def_kinds, aa):
        dice = set()
        for k in att_kinds:
            d = DICE_ATT[k]
            dice.update(d if isinstance(d, tuple) else (d,))
        for k in def_kinds:
            dice.add(DICE_DEF[k])
        if aa:
            dice.add(1)
        return len(dice)

    def choose(self, domain):
        kinds = LAND_KINDS if domain == "land" else SEA_KINDS
        best, best_score = None, -1
        for _ in range(60):
            a = self.rng.sample(kinds, self.rng.randint(1, min(4, len(kinds))))
            d = self.rng.sample(kinds, self.rng.randint(1, min(4, len(kinds))))
            owners = {}
            gain = 0
            for k in a:
                if self.att.get(k, 0) < CELL_TARGET:
                    gain += 1
            for k in d:
                # defender owner = the enemy whose (owner, kind) cell is least filled
                o = min(self.enemies, key=lambda e: (self.dfn.get((e, k), 0), self.rng.random()))
                owners[k] = o
                if self.dfn.get((o, k), 0) < CELL_TARGET:
                    gain += 1
            aa = bool(set(a) & AIR)
            rows = self._rows(a, d, aa)
            score = gain + (1.5 if self.layouts.get(rows, 0) < 3 else 0) + self.rng.random() * 0.1
            if score > best_score:
                best, best_score = (a, d, owners, rows), score
        a, d, owners, rows = best
        for k in a:
            self.att[k] = self.att.get(k, 0) + 1
        for k in d:
            self.dfn[(owners[k], k)] = self.dfn.get((owners[k], k), 0) + 1
        self.layouts[rows] = self.layouts.get(rows, 0) + 1
        return a, d, owners

    def coverage(self):
        return {"attacker_kinds": dict(sorted(self.att.items())),
                "defender_cells": {f"{o}:{k}": n for (o, k), n in sorted(self.dfn.items())},
                "layouts": dict(sorted(self.layouts.items()))}


def make_manifest_entry(kind, target, origin, power, attackers, defenders, hidden, prediction, state):
    truth = T.truth_for(state, target)
    return {"type": kind, "target": target, "origin": origin, "attacker_power": power,
            "attackers": attackers, "defenders": [list(d) for d in defenders], "hidden_defenders": hidden,
            "prediction": prediction, "truth": {"attackers": truth["attackers"], "defenders": truth["defenders"],
                                                 "hidden": truth["hidden"], "panel_aa": truth["panel_aa"]}}


def generate(data, power, n_land=8, n_sea=5, offset=0):
    """Turn ``data`` (a loaded save) into a scenario for ``power``. Returns ``(manifest, coverage)``."""
    state = data["state"]
    builder = Builder(state)  # captures the encounter template before it is cleared
    ps = state["phaseStorage"]
    ps["power"], ps["phase"] = power, "combatMove"
    state["currentPower"], state["currentPhase"] = power, "combatMove"
    ps["delta"] = []
    state["pendingEncounters"] = {}
    for u in state["units"].values():
        if u["owner"] == power and u.get("startOfTurnLocation") is None:
            u["startOfTurnLocation"] = u["location"]

    enemies = [p for p in POWERS if alliance(state, p) != alliance(state, power)]
    planner = Planner(power, enemies, seed=11 + offset)
    att_counts = itertools.cycle(ATT_COUNTS[offset % len(ATT_COUNTS):] + ATT_COUNTS[:offset % len(ATT_COUNTS)])
    def_counts = itertools.cycle(DEF_COUNTS[offset % len(DEF_COUNTS):] + DEF_COUNTS[:offset % len(DEF_COUNTS)])
    preds = itertools.cycle(PREDICTIONS[offset % len(PREDICTIONS):] + PREDICTIONS[:offset % len(PREDICTIONS)])
    manifest = []

    def run(kind, n):
        targets = (land_targets(state, power)[:n] if kind == "land"
                   else pick_spread(central_targets(state, "sea"), n))
        for i, (target, origin) in enumerate(targets):
            a_kinds, d_kinds, owners = planner.choose(kind)
            attackers = {k: next(att_counts) for k in a_kinds}
            defenders = [(owners[k], k, next(def_counts)) for k in d_kinds]
            hidden = {}
            if kind == "land" and set(a_kinds) & AIR:
                hidden["aa"] = 1 + (i % 3)  # shown on the panel (die 1) because the attackers have aircraft
            elif kind == "land" and i % 3 == 0:
                hidden["aa"] = 2            # hidden behind "VIEW NON COMBATANTS"
            if kind == "sea" and i % 2 == 0:
                hidden["trn"] = 1 + (i % 3)
            for hk, hn in hidden.items():
                defenders.append((defenders[0][0], hk, hn))
            prediction = next(preds)
            builder.add_battle(power, target, attackers, defenders, origin, prediction)
            manifest.append(make_manifest_entry(kind, target, origin, power, attackers,
                                                [d for d in defenders if d[1] not in T.HIDDEN],
                                                hidden, prediction, state))

    run("land", n_land)
    run("sea", n_sea)
    return manifest, planner.coverage()


# Small-battle plan: (domain, attackers {kind: n}, [defender kinds]). The first group has a single panel row (the
# attacker and defender roll the same die value); the second group has two rows.
SMALL_PLAN = [
    ("land", {"inf": 0}, ["bmb"]), ("land", {"art": 0}, ["inf"]), ("land", {"tank": 0}, ["tank"]),
    ("land", {"ftr": 0}, ["tank"]), ("land", {"bmb": 0}, ["ftr"]),
    ("land", {"inf": 0, "art": 0}, ["inf"]), ("land", {"tank": 0, "inf": 0}, ["inf"]),
    ("sea", {"car": 0}, ["sub"]), ("sea", {"sub": 0}, ["des"]), ("sea", {"des": 0}, ["car"]),
    ("sea", {"cru": 0}, ["cru"]), ("sea", {"bb": 0}, ["bb"]), ("sea", {"bb": 0}, ["ftr"]),
    ("sea", {"sub": 0, "des": 0}, ["sub"]),
]
SMALL_COUNTS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 12]


def strip_enemy_units(state, region, power):
    """Remove the units of the attacker's enemies from ``region`` (scenario copy only) so the panel is small."""
    for uid in [u for u, v in state["units"].items()
                if v["location"] == region and alliance(state, v["owner"]) != alliance(state, power)]:
        del state["units"][uid]


def generate_small(data, power, offset=0):
    """Small battles (1-9 units, one or two panel rows) for ``power``. Returns ``(manifest, coverage)``."""
    state = data["state"]
    builder = Builder(state)
    ps = state["phaseStorage"]
    ps["power"], ps["phase"] = power, "combatMove"
    state["currentPower"], state["currentPhase"] = power, "combatMove"
    ps["delta"] = []
    state["pendingEncounters"] = {}
    for u in state["units"].values():
        if u["owner"] == power and u.get("startOfTurnLocation") is None:
            u["startOfTurnLocation"] = u["location"]
    enemies = [p for p in POWERS if alliance(state, p) != alliance(state, power)]
    counts = itertools.cycle(SMALL_COUNTS[offset % len(SMALL_COUNTS):] + SMALL_COUNTS[:offset % len(SMALL_COUNTS)])
    occupied = {}
    for u in state["units"].values():
        occupied.setdefault(u["location"], []).append(u["owner"])
    my = alliance(state, power)
    sea = [(t, o) for t, o in central_targets(state, "sea")
           if not any(alliance(state, w) != my for w in occupied.get(t, []))]
    land = land_targets(state, power)
    manifest, used = [], set()
    li = si = 0
    for i, (domain, att, dfn) in enumerate(SMALL_PLAN):
        pool = land if domain == "land" else sea
        idx = li if domain == "land" else si
        while idx < len(pool) and pool[idx][0] in used:
            idx += 1
        if idx >= len(pool):
            continue
        target, origin = pool[idx]
        used.add(target)
        if domain == "land":
            li = idx + 1
            strip_enemy_units(state, target, power)
        else:
            si = idx + 1
        attackers = {k: next(counts) for k in att}
        defenders = [(enemies[(i + j + offset) % len(enemies)], k, next(counts)) for j, k in enumerate(dfn)]
        builder.add_battle(power, target, attackers, defenders, origin, 50)
        manifest.append(make_manifest_entry(domain, target, origin, power, attackers, defenders, {}, 50, state))
    return manifest, {"attacker_kinds": {}, "defender_cells": {}, "layouts": {}}


def validate(data, manifest):
    """Check every battle's truth (read back from the state) against what was asked for."""
    state = data["state"]
    for m in manifest:
        t = T.truth_for(state, m["target"])
        assert t["attackers"] == m["attackers"], (m["target"], t["attackers"], m["attackers"])
        want_def = {}
        for owner, kind, n in m["defenders"]:
            want_def[kind] = want_def.get(kind, 0) + n
        assert all(t["defenders"].get(k, 0) >= n for k, n in want_def.items()), (m["target"], t["defenders"], want_def)
        assert m["truth"]["defenders"] == t["defenders"] and m["truth"]["attackers"] == t["attackers"]
    assert sorted(T.regions_under_attack(state)) == sorted(m["target"] for m in manifest)


def find_base(save_dir=T.SAVE_DIR):
    """The oldest save whose name does not start with the scenario prefix (the user's own game)."""
    best = None
    for f in glob.glob(os.path.join(save_dir, "*.json")):
        with open(f, encoding="utf-8") as fh:
            name = json.load(fh).get("name", "")
        if not name.startswith(NAME_PREFIX):
            if best is None or os.path.getmtime(f) < os.path.getmtime(best):
                best = f
    if best is None:
        raise FileNotFoundError("no base save found")
    return best


def clean(save_dir=T.SAVE_DIR):
    removed = 0
    for f in glob.glob(os.path.join(save_dir, "*.json")):
        with open(f, encoding="utf-8") as fh:
            name = json.load(fh).get("name", "")
        if name.startswith(NAME_PREFIX):
            os.remove(f)
            removed += 1
    print(f"removed {removed} scenario copies")


def make_one(power, n_land, n_sea, offset=0, small=False, amphibious=False):
    base = find_base()
    backup_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "aaoverlay", "backup_saves")  # outside the synced project folder: saves hold account ids
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(base, os.path.join(backup_dir, os.path.basename(base).replace(".json", f"_pre_{int(time.time())}.json")))
    with open(base, encoding="utf-8") as fh:
        data = json.load(fh)
    if amphibious:
        import amphibious_gen
        manifest, coverage = amphibious_gen.generate(data, power, offset)
    else:
        manifest, coverage = (generate_small(data, power, offset) if small else generate(data, power, n_land, n_sea, offset))
        validate(data, manifest)
    name = f"{NAME_PREFIX}{power}"
    path = M.write_copy(data, name=name)
    sdir = os.path.join(ROOT, "dataset", "scenarios")
    os.makedirs(sdir, exist_ok=True)
    with open(os.path.join(sdir, os.path.basename(path)), "w", encoding="utf-8") as fh:
        json.dump({"game_id": os.path.basename(path)[:-5], "name": name, "power": power,
                   "battles": manifest, "planned_coverage": coverage}, fh, indent=1)
    print(f"{power}: {os.path.basename(path)} with {len(manifest)} battles; layouts {coverage['layouts']}")
    return path, manifest


# The full capture plan: (power, land battles, sea battles). Order = the order campaigns are created,
# which is the order they appear in the Single Player list after the user's own game.
PLAN = [("germany", 9, 9), ("soviet_union", 7, 7), ("united_kingdom", 3, 3), ("japan", 3, 3), ("united_states", 3, 3)]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--power", choices=POWERS)
    ap.add_argument("--plan", action="store_true", help="generate the full five-scenario capture plan")
    ap.add_argument("--land", type=int, default=8)
    ap.add_argument("--sea", type=int, default=5)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--small", action="store_true", help="small battles: one/two-row panels, counts 1-9")
    ap.add_argument("--amphibious", action="store_true", help="amphibious assaults (transports, bombardment)")
    ap.add_argument("--clean", action="store_true")
    a = ap.parse_args()
    if a.clean:
        clean()
        return
    if a.plan:
        for i, (p, nl, ns) in enumerate(PLAN):
            make_one(p, nl, ns, a.offset + i * 5)
            time.sleep(1.1)  # distinct ids, and creation order = list order
        return
    if not a.power:
        sys.exit("give --power, --plan or --clean")
    make_one(a.power, a.land, a.sea, a.offset, a.small, a.amphibious)


if __name__ == "__main__":
    main()
