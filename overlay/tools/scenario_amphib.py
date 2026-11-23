"""Create an offline scenario for playing an amphibious assault by hand (Phase 1 of the amphibious plan).

UK: units on the coast of the United Kingdom, two transports + a battleship + a cruiser in sea_zone_8, and
German-held France as the target. No battle is pre-built: the assault is made through the game's own screens so
the save records the real transport/bombard structures.
Usage: python tools\scenario_amphib.py        (restart the game afterwards)
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import scenario_gen as G  # noqa: E402
import make_scenario as M  # noqa: E402

POWER, HOME, SEA, TARGET = "united_kingdom", "united_kingdom", "sea_zone_8", "france"


def main():
    with open(G.find_base(), encoding="utf-8") as fh:
        data = json.load(fh)
    s = data["state"]
    b = G.Builder(s)
    ps = s["phaseStorage"]
    ps["power"], ps["phase"], ps["delta"] = POWER, "combatMove", []
    s["currentPower"], s["currentPhase"] = POWER, "combatMove"
    s["pendingEncounters"] = {}
    for kind, n, where in [("inf", 5, HOME), ("art", 2, HOME), ("tank", 1, HOME),
                           ("trn", 2, SEA), ("bb", 1, SEA), ("cru", 1, SEA)]:
        for _ in range(n):
            b.new_unit(kind, POWER, where)
    for u in s["units"].values():
        if u["owner"] == POWER and u.get("startOfTurnLocation") is None:
            u["startOfTurnLocation"] = u["location"]
    path = M.write_copy(data, name="Overlay amphibious")
    print("created", path)


if __name__ == "__main__":
    main()
