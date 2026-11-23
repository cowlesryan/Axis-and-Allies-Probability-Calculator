"""Ground truth for attack panels, read from the game's *offline* save file.

The offline (local / hot-seat) game autosaves its full state to
``%APPDATA%\\axisandalliesonline-desktop\\local-games\\<id>.json``. That file lists
every unit (id, owner, location) and, under ``state.phaseStorage.delta``, every
``moveUnit`` action of the current phase. From it we can say exactly which units
are attacking and defending a region, which labels a screenshot of the attack
panel without any manual typing.

Only unit/region/phase fields are read. Account fields in the file (player names
and ids) are never touched. Multiplayer games have no such file; this is for the
offline training-data work only.
"""
import glob
import json
import os
from collections import Counter

SAVE_DIR = os.path.join(os.environ.get("APPDATA", ""), "axisandalliesonline-desktop", "local-games")

# Save-file unit id prefix -> aa1942 unit key.
PREFIX = {"in": "inf", "ar": "art", "aa": "aa", "ta": "tank", "ft": "ftr", "bm": "bmb",
          "sb": "sub", "cr": "cru", "bs": "bb", "de": "des", "ac": "car", "tr": "trn"}
HIDDEN = {"aa", "trn"}  # the game hides these behind "VIEW NON COMBATANTS"


def unit_key(unit_id):
    """Registry key for a save unit id such as ``ta101`` -> ``tank``."""
    prefix = "".join(c for c in unit_id if c.isalpha())
    if prefix not in PREFIX:
        raise ValueError(f"unknown unit id prefix {prefix!r} in {unit_id!r} (the game added a unit type?)")
    return PREFIX[prefix]


def latest_save(save_dir=SAVE_DIR):
    files = glob.glob(os.path.join(save_dir, "*.json"))
    if not files:
        raise FileNotFoundError(f"no offline saves in {save_dir}")
    return max(files, key=os.path.getmtime)


def load_state(path=None):
    """The ``state`` object of the newest offline save (or of ``path``)."""
    with open(path or latest_save(), encoding="utf-8") as fh:
        return json.load(fh)["state"]


def _damaged_key(key, unit):
    """A battleship that already took a hit is tracked as ``bbd`` (damaged)."""
    return "bbd" if key == "bb" and unit.get("damage", 0) >= 1 else key


def pending_destinations(state):
    """``{unit_id: destination}`` for the units moved this phase (last move wins)."""
    dest = {}
    for action in state["phaseStorage"].get("delta", []):
        if action.get("func") == "moveUnit":
            uid, path = action["args"][0], action["args"][1]
            if path:
                dest[uid] = path[-1]
    return dest


def regions_under_attack(state):
    """Regions where the current power has moved units into enemy-held units' space."""
    power = state["phaseStorage"]["power"]
    alliance = state["powers"][power]["alliance"]
    units = state["units"]
    dests = pending_destinations(state)
    found = set()
    for uid, region in dests.items():
        u = units.get(uid)
        if u is None or u["owner"] != power:
            continue
        if any(v["location"] == region and state["powers"][v["owner"]]["alliance"] != alliance
               for v in units.values()):
            found.add(region)
    return sorted(found)


def truth_for(state, region):
    """Everything an attack panel for ``region`` should show, plus what it hides.

    Returns a dict with ``round``, ``phase``, ``attacker_power``, ``attackers`` and
    ``defenders`` (``{unit_key: count}`` of units the panel lists), ``hidden``
    (aa guns / transports behind "VIEW NON COMBATANTS", per side) and the powers
    involved on each side.
    """
    ps = state["phaseStorage"]
    power = ps["power"]
    alliance = state["powers"][power]["alliance"]
    units = state["units"]
    att, dfn = Counter(), Counter()
    att_hidden, dfn_hidden = Counter(), Counter()
    def_powers = set()
    enc = state.get("pendingEncounters", {}).get(region)
    if enc:
        # The panel lists exactly the units named in the battle record.
        att_ids, def_ids = enc["initialAttackers"], enc["initialDefenders"]
    else:
        dests = pending_destinations(state)
        # A pending move does not change ``location`` until the battle resolves, so
        # attackers are identified from the pending-move list alone.
        att_ids = [uid for uid, u in units.items() if u["owner"] == power and dests.get(uid) == region]
        def_ids = [uid for uid, u in units.items()
                   if u["location"] == region and state["powers"][u["owner"]]["alliance"] != alliance]
    # Amphibious assaults: ships assigned to shore bombardment are listed on the panel as ordinary attackers
    # (their normal attack die), but they are not in ``initialAttackers``; they live in ``bombardingUnits``.
    bombard_ids = [uid for ids in (enc or {}).get("bombardingUnits", {}).values() for uid in ids]
    amph_ids = list((enc or {}).get("amphibiousAttackers", []))
    for uid in list(att_ids) + [b for b in bombard_ids if b not in att_ids]:
        key = _damaged_key(unit_key(uid), units[uid])
        (att_hidden if key in HIDDEN else att)[key] += 1
    def_entries = Counter()
    aa_entries = Counter()
    for uid in def_ids:
        key = _damaged_key(unit_key(uid), units[uid])
        (dfn_hidden if key in HIDDEN else dfn)[key] += 1
        def_powers.add(units[uid]["owner"])
        if key not in HIDDEN:
            def_entries[(units[uid]["owner"], key)] += 1
        elif key == "aa":
            aa_entries[units[uid]["owner"]] += 1
    # Defending AA guns show on the panel (die 1, defender side) when the attackers include aircraft;
    # everything else hidden (transports, AA against ground attackers) stays behind "VIEW NON COMBATANTS".
    air = att.get("ftr", 0) + att.get("bmb", 0) > 0
    panel_aa = dfn_hidden.get("aa", 0) if air else 0
    return {"round": ps["round"], "phase": ps["phase"], "region": region, "attacker_power": power,
            "amphibious": dict(Counter(unit_key(u) for u in amph_ids)),
            "bombarders": dict(Counter(unit_key(u) for u in bombard_ids)),
            "bombard_slots": sum((enc or {}).get("bombarderSlots", {}).values()),
            "defender_powers": sorted(def_powers), "attackers": dict(att), "defenders": dict(dfn),
            "hidden": {"attackers": dict(att_hidden), "defenders": dict(dfn_hidden)}, "panel_aa": panel_aa,
            # the panel draws one entry per (owner, unit): units of different powers are separate entries
            "defender_entries": [[o, k, n] for (o, k), n in sorted(def_entries.items())],
            # AA guns are drawn per owner as well (only when the attackers have aircraft)
            "panel_aa_entries": [[o, n] for o, n in sorted(aa_entries.items())] if panel_aa else []}
