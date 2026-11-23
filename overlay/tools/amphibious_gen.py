"""Amphibious assault scenarios: transports with cargo, shore bombardment, offloading onto an enemy coast.

Builds the battles exactly as the game records them (see DATASET.md, "Amphibious assaults"):
* ``boardTransport`` / ``transportDrop`` / ``addShipToBombardment`` actions in ``phaseStorage.delta``;
* an encounter whose ``amphibiousAttackers`` are the offloaded units, ``bombarderSlots`` = one per unit and
  ``bombardingUnits`` = the bombarding ships;
* cargo units keep their home ``location`` and get ``droppedFrom`` / ``transporter``; each transport gets
  ``pendingCargo`` and ``dropTarget``.

Used through ``scenario_gen.py --amphibious --power <power>``.
"""
import copy

import scenario_gen as G
from aaoverlay import savetruth as T

CARGO = [{"inf": 1}, {"art": 1}, {"tank": 1}, {"inf": 2}, {"inf": 1, "art": 1}, {"inf": 1, "tank": 1},
         {"inf": 2, "art": 1}, {"inf": 3, "tank": 1}, {"inf": 2, "art": 2}, {"art": 1, "tank": 1},
         {"inf": 4, "art": 1, "tank": 1}, {"inf": 3, "art": 1}]
SHIPS = [{}, {"bb": 1}, {"cru": 1}, {"bb": 1, "cru": 1}, {"bb": 2}, {}, {"cru": 2}]
AIR = [{}, {}, {"ftr": 1}, {}, {"bmb": 1}, {}, {"ftr": 2, "bmb": 1}]
DEFENDERS = [[("inf", 3)], [("art", 2), ("inf", 4)], [("tank", 2)], [("inf", 8), ("tank", 3)], [("ftr", 2)],
             [("inf", 1)], [("bmb", 1), ("inf", 2)], [("art", 5), ("tank", 1)], [("inf", 12)],
             [("tank", 6), ("ftr", 1)]]


def routes(state, power):
    """``[(home, sea, target)]``: a land region the power owns, a sea zone next to it holding no enemy units,
    and an enemy-held coastal territory next to that sea zone. One route per target, central ones first."""
    R = state["regions"]
    my = G.alliance(state, power)
    occupied = {}
    for u in state["units"].values():
        occupied.setdefault(u["location"], []).append(u["owner"])
    found = []
    for home, r in R.items():
        if r["type"] != "land" or r.get("owner") != power:
            continue
        for sea in r["adjacentRegions"]:
            if R[sea]["type"] == "land" or any(G.alliance(state, o) != my for o in occupied.get(sea, [])):
                continue
            for tgt in R[sea]["adjacentRegions"]:
                t = R[tgt]
                if t["type"] == "land" and t.get("owner") and G.alliance(state, t["owner"]) != my:
                    found.append((home, sea, tgt))
    rank = {r: i for i, r in enumerate(G.CENTRAL_LAND)}
    found.sort(key=lambda x: (rank.get(x[2], 99), x))
    seen, out = set(), []
    for h, sea, tgt in found:
        if tgt not in seen:
            seen.add(tgt)
            out.append((h, sea, tgt))
    return out


def add_battle(builder, power, home, sea, target, cargo, ships, air, defenders):
    st = builder.state
    ps = st["phaseStorage"]
    cargo_ids = []
    for kind, n in cargo.items():
        for _ in range(n):
            cargo_ids.append((kind, builder.new_unit(kind, power, home)))
    cargo_ids.sort(key=lambda x: x[0] == "inf")  # heavier units first: a transport takes one non-infantry unit
    transports, pending = [], list(cargo_ids)
    while pending:
        slot = {"inf": None, "main": None}
        first = pending.pop(0)
        load = [first]
        slot["main" if first[0] != "inf" else "inf"] = first[1]
        if pending and pending[-1][0] == "inf":
            second = pending.pop()
            slot["inf" if slot["inf"] is None else "main"] = second[1]
            load.append(second)
        tid = builder.new_unit("trn", power, sea)
        st["units"][tid]["pendingCargo"] = slot
        st["units"][tid]["dropTarget"] = target
        transports.append((tid, [u for _, u in load]))
    for tid, load in transports:
        for uid in load:
            st["units"][uid]["droppedFrom"] = sea
            st["units"][uid]["transporter"] = tid
            ps["delta"].append({"func": "boardTransport", "args": [tid, uid, power, sea, True]})
        ps["delta"].append({"func": "transportDrop", "args": [tid, target, list(load)]})
    amph = [u for _, u in cargo_ids]
    ship_ids = [builder.new_unit(k, power, sea) for k, n in ships.items() for _ in range(n)][:len(amph)]
    for sid in ship_ids:
        ps["delta"].append({"func": "addShipToBombardment", "args": [sid, [sea], target, {sea: False}]})
    air_ids = []
    for kind, n in air.items():
        for _ in range(n):
            uid = builder.new_unit(kind, power, home)
            air_ids.append(uid)
            ps["delta"].append({"func": "moveUnit", "args": [uid, [target], {target: True}]})
    def_ids = [uid for uid, u in st["units"].items()
               if u["location"] == target and G.alliance(st, u["owner"]) != G.alliance(st, power)]
    owner = st["regions"][target]["owner"]
    for kind, n in defenders:
        for _ in range(n):
            def_ids.append(builder.new_unit(kind, owner, target))
    enc = copy.deepcopy(builder.encounter_template)
    enc.update(location=target, preCombatOwnerId=owner, amphibiousAttackers=list(amph),
               bombarderSlots={sea: len(amph)}, bombardingUnits=({sea: ship_ids} if ship_ids else {}),
               initialAttackers=amph + air_ids, currentAttackers=amph + air_ids,
               initialDefenders=list(def_ids), currentDefenders=list(def_ids), retreatableEntryLocations={},
               prediction=50)
    st["pendingEncounters"][target] = enc


def generate(data, power, offset=0):
    """Amphibious assaults for ``power``, one per route. Returns ``(manifest, coverage)``."""
    state = data["state"]
    builder = G.Builder(state)
    ps = state["phaseStorage"]
    ps["power"], ps["phase"], ps["delta"] = power, "combatMove", []
    state["currentPower"], state["currentPhase"] = power, "combatMove"
    state["pendingEncounters"] = {}
    for u in state["units"].values():
        if u["owner"] == power and u.get("startOfTurnLocation") is None:
            u["startOfTurnLocation"] = u["location"]
    manifest = []
    for i, (home, sea, target) in enumerate(routes(state, power)):
        j = i + offset
        G.strip_enemy_units(state, target, power)
        add_battle(builder, power, home, sea, target, CARGO[j % len(CARGO)], SHIPS[j % len(SHIPS)],
                   AIR[(j * 3 + 1) % len(AIR)], DEFENDERS[(j * 2) % len(DEFENDERS)])
        t = T.truth_for(state, target)
        manifest.append({"type": "amphibious", "target": target, "origin": home, "sea": sea,
                         "attacker_power": power, "attackers": t["attackers"], "defenders": [],
                         "hidden_defenders": {}, "prediction": 50,
                         "truth": {k: t[k] for k in ("attackers", "defenders", "hidden", "panel_aa",
                                                     "amphibious", "bombarders", "bombard_slots")}})
    return manifest, {"attacker_kinds": {}, "defender_cells": {}, "layouts": {}}
