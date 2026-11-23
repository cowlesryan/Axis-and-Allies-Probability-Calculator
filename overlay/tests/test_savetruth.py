"""Tests for the save-file ground truth and the scenario writer (synthetic saves, no game needed)."""
import copy
import json
import os
import sys

import pytest

from aaoverlay import savetruth as T

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import make_scenario as M  # noqa: E402
import learn as L  # noqa: E402


def unit(owner, location, damage=0):
    return {"owner": owner, "location": location, "startOfTurnLocation": location, "damage": damage,
            "combatMovePath": []}


def make_state():
    return {
        "phaseStorage": {"round": 1, "power": "germany", "phase": "combatMove", "gameId": "1", "delta": []},
        "powers": {"germany": {"alliance": "axis"}, "soviet_union": {"alliance": "allies"},
                   "united_kingdom": {"alliance": "allies"}},
        "units": {
            # German attackers start in west_russia; Soviet defenders sit in karelia_ssr
            "in1": unit("germany", "west_russia"), "in2": unit("germany", "west_russia"),
            "ar3": unit("germany", "west_russia"), "ta4": unit("germany", "west_russia"),
            "in10": unit("soviet_union", "karelia_ssr"), "in11": unit("soviet_union", "karelia_ssr"),
            "aa12": unit("soviet_union", "karelia_ssr"), "ft13": unit("soviet_union", "karelia_ssr"),
            # a German battleship attacking a sea zone holding a damaged UK battleship and a transport
            "bs20": unit("germany", "sea_zone_15"), "bs21": unit("united_kingdom", "sea_zone_17", damage=1),
            "tr22": unit("united_kingdom", "sea_zone_17"),
            # an unrelated German unit that is not moving
            "in30": unit("germany", "germany"),
        },
        "regions": {}, "pendingEncounters": {"seed": {"location": "seed", "initialAttackers": []}},
    }


def move(uid, dest):
    return {"func": "moveUnit", "args": [uid, [dest], {dest: True}]}


def test_unit_key_mapping():
    assert [T.unit_key(x) for x in ("in1", "ar2", "aa3", "ta4", "ft5", "bm6", "sb7", "cr8", "bs9", "de10", "ac11", "tr12")] == \
        ["inf", "art", "aa", "tank", "ftr", "bmb", "sub", "cru", "bb", "des", "car", "trn"]


def test_attackers_come_from_pending_moves_not_location():
    st = make_state()
    st["phaseStorage"]["delta"] = [move("in1", "karelia_ssr"), move("ar3", "karelia_ssr"), move("ta4", "karelia_ssr")]
    t = T.truth_for(st, "karelia_ssr")
    assert t["attackers"] == {"inf": 1, "art": 1, "tank": 1}  # in2 did not move; in30 is elsewhere
    assert t["defenders"] == {"inf": 2, "ftr": 1}
    assert t["hidden"]["defenders"] == {"aa": 1}  # AA guns are behind "VIEW NON COMBATANTS"
    assert t["attacker_power"] == "germany" and t["defender_powers"] == ["soviet_union"]


def test_last_move_wins_and_damaged_battleship_is_bbd():
    st = make_state()
    st["phaseStorage"]["delta"] = [move("in1", "somewhere"), move("in1", "karelia_ssr"), move("bs20", "sea_zone_17")]
    assert T.pending_destinations(st)["in1"] == "karelia_ssr"
    t = T.truth_for(st, "sea_zone_17")
    assert t["attackers"] == {"bb": 1}
    assert t["defenders"] == {"bbd": 1} and t["hidden"]["defenders"] == {"trn": 1}


def test_regions_under_attack_requires_an_enemy_there():
    st = make_state()
    st["phaseStorage"]["delta"] = [move("in1", "karelia_ssr"), move("in2", "empty_land")]
    assert T.regions_under_attack(st) == ["karelia_ssr"]


def test_add_attack_writes_moves_and_encounter():
    st = make_state()
    template = next(iter(st["pendingEncounters"].values()))
    defenders = M.add_attack(st, "karelia_ssr", ["in1", "ar3"], template)
    assert sorted(defenders) == ["aa12", "ft13", "in10", "in11"]
    enc = st["pendingEncounters"]["karelia_ssr"]
    assert enc["initialAttackers"] == ["in1", "ar3"] and enc["retreatableEntryLocations"]["in1"] == "west_russia"
    assert T.truth_for(st, "karelia_ssr")["attackers"] == {"inf": 1, "art": 1}
    with pytest.raises(AssertionError):
        M.add_attack(st, "karelia_ssr", ["in10"], template)  # not owned by the moving power


def test_write_copy_gets_its_own_identity_and_never_touches_the_source(tmp_path):
    data = {"id": 111, "name": "orig", "createdAt": "x", "startDate": "x", "started": "x", "lastTurn": "x",
            "ownerUUID": "keep-me", "state": {"phaseStorage": {"gameId": "111"}}}
    before = copy.deepcopy(data)
    path = M.write_copy(copy.deepcopy(data), save_dir=str(tmp_path))
    out = json.load(open(path, encoding="utf-8"))
    assert out["id"] != 111 and out["id"] == int(os.path.basename(path)[:-5])
    assert out["name"] != "orig" and out["createdAt"] != "x"
    assert out["state"]["phaseStorage"]["gameId"] == str(out["id"])
    assert out["ownerUUID"] == "keep-me"  # personal fields are left exactly as they were
    assert data == before  # the source object was not modified


def test_learn_assignment_rules():
    E = lambda count: type("E", (), {"count": count})()
    rows = [("inf", 1, 1), ("art", 1, 1)]
    # equal counts: ambiguous without icon evidence
    assert L.assign([E(1), E(1)], rows) is None
    # elimination: the first icon clearly looks like inf
    got = L.assign([E(1), E(1)], rows, [[0.95, 0.2], [0.3, 0.4]])
    assert [(u) for _, u, _ in got] == ["inf", "art"]
    # no clear winner -> refuse to guess
    assert L.assign([E(1), E(1)], rows, [[0.5, 0.5], [0.5, 0.5]]) is None
    # distinct counts resolve directly
    got = L.assign([E(12), E(1)], [("art", 2, 1), ("inf", 2, 12)])
    assert sorted((u, c) for _, u, c in got) == [("art", 1), ("inf", 12)]
