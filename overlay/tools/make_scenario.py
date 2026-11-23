"""Write a *copy* of the offline save with custom pending attacks (training-data scenarios).

SAFETY: this never modifies an existing save. It reads the newest save in
``%APPDATA%\\axisandalliesonline-desktop\\local-games``, edits an in-memory copy and writes it
under a NEW game id, so the original game is untouched. Offline games only. Delete the new
file to remove the scenario. A backup of the source is also kept in ``%LOCALAPPDATA%\\aaoverlay\\backup_saves`` (outside the project folder: saves contain account ids).

Prototype usage:
    python tools\\make_scenario.py karelia     # German stack in West Russia attacks Karelia
"""
import copy
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import aaoverlay  # noqa: E402,F401
from aaoverlay import savetruth as T  # noqa: E402


def add_attack(state, target, attacker_ids, template_encounter):
    """Add a pending attack on ``target`` by existing units; returns the defender ids."""
    units = state["units"]
    power = state["phaseStorage"]["power"]
    alliance = state["powers"][power]["alliance"]
    attackers = list(attacker_ids)
    for uid in attackers:
        assert units[uid]["owner"] == power, f"{uid} is not owned by {power}"
        state["phaseStorage"]["delta"].append(
            {"func": "moveUnit", "args": [uid, [target], {target: True}]})
    defenders = [uid for uid, u in units.items()
                 if u["location"] == target and state["powers"][u["owner"]]["alliance"] != alliance]
    assert defenders, f"nothing to attack in {target}"
    enc = copy.deepcopy(template_encounter)
    enc["location"] = target
    enc["initialAttackers"] = enc["currentAttackers"] = attackers
    enc["initialDefenders"] = enc["currentDefenders"] = defenders
    enc["retreatableEntryLocations"] = {uid: units[uid]["startOfTurnLocation"] or units[uid]["location"]
                                        for uid in attackers}
    state["pendingEncounters"][target] = enc
    return defenders


def write_copy(data, save_dir=T.SAVE_DIR, name="Overlay Training Scenario"):
    new_id = str(int(time.time() * 1000))
    data["state"]["phaseStorage"]["gameId"] = new_id
    # The game tells campaigns apart by more than the file name (a copy that kept the original's
    # timestamps opened the original), so give the copy its own creation timestamps too.
    iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(new_id) / 1000)) + f".{int(new_id) % 1000:03d}Z"
    for key in ("createdAt", "startDate", "started", "lastTurn"):
        data[key] = iso
    # The campaign list identifies a game by the top-level "id" field, not the file name: a copy
    # that kept the original's id simply opened the original. A distinct name also makes the
    # copy recognisable in the list. (ownerUUID and the player fields are left untouched.)
    data["id"] = int(new_id)
    data["name"] = name
    path = os.path.join(save_dir, new_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    json.load(open(path, encoding="utf-8"))  # verify it parses
    return path


def main(which):
    src = T.latest_save()
    backup_dir = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "aaoverlay", "backup_saves")  # outside the synced project folder: saves hold account ids
    os.makedirs(backup_dir, exist_ok=True)
    shutil.copy2(src, os.path.join(backup_dir, os.path.basename(src).replace(".json", f"_pre_{int(time.time())}.json")))
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    st = data["state"]
    template = next(iter(st["pendingEncounters"].values()))
    if which == "karelia":
        defenders = add_attack(st, "karelia_ssr", ["in79", "in80", "in81", "ar82", "ta83"], template)
        print("added attack on karelia_ssr; defenders:", defenders)
    else:
        sys.exit("unknown scenario")
    path = write_copy(data)
    print("wrote copy:", path)
    print("regions under attack in the copy:", T.regions_under_attack(st))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "karelia")
