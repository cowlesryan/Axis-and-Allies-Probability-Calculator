"""Amphibious assault: a chain of sea battle -> bombardment -> land battle.

Steps (1942 2nd Edition, as modelled)
-------------------------------------
1. **Sea battle** (only if the defender has ships or transports in the sea
   zone). The attacking fleet includes the loaded transports. It is fought to
   the end. If the attacker is not left holding a cleared sea zone the landing
   never happens (counts as an attacker failure).
2. **Cargo loss**: each transport carries 2 land units. If ``t`` transports
   survive, at most ``2*t`` cargo units can land; the surplus is lost, weakest
   units first (standard attacker order).
3. **Bombardment + land battle**: bombarding ships (battleship hits on <= 4,
   cruiser <= 3, at most one ship per landing unit) fire first; the defender's
   bombard casualties still fire back (1942 rules comparison). Defending AA
   guns fire at any attacking aircraft. Then the land battle runs to the end.

Modelling assumptions (unverified items are listed in RULES.md)
---------------------------------------------------------------
* Bombarding ships are listed separately and do not take part in the sea battle.
* Attacking aircraft fly straight to the land battle (they do not join the
  sea battle).
* The defender's sea fleet and land army fight separately; defending aircraft
  in the territory are part of ``def_land``.
"""
from .casualties import apply_hits, default_order
from .solver import (STANDARD, _check_policies, _solve, classify,
                     solve_land_assault, validate)
from .units import (BB, BBD, CRU, INF, ART, TANK, N, SEA_IDX, AIR_IDX, TRN,
                    combatants)

LAND_CARGO = (INF, ART, TANK)


def _check_inputs(att_fleet, cargo, att_air, def_fleet, def_land, bombard):
    for name, army in (("att_fleet", att_fleet), ("cargo", cargo), ("att_air", att_air),
                       ("def_fleet", def_fleet), ("def_land", def_land), ("bombard", bombard)):
        if len(army) != N or min(army) < 0:
            raise ValueError(f"Bad {name} army: {army!r}")
    if any(att_fleet[i] for i in range(N) if i not in SEA_IDX):
        raise ValueError("att_fleet may contain sea units only")
    if any(cargo[i] for i in range(N) if i not in LAND_CARGO):
        raise ValueError("cargo may contain only inf, art and tank")
    if any(att_air[i] for i in range(N) if i not in AIR_IDX):
        raise ValueError("att_air may contain aircraft only")
    if any(def_fleet[i] for i in range(N) if i not in SEA_IDX):
        raise ValueError("def_fleet may contain sea units only")
    if any(bombard[i] for i in range(N) if i not in (CRU, BB, BBD)):
        raise ValueError("bombard may contain only cru and bb")
    if sum(cargo) > 2 * att_fleet[TRN]:
        raise ValueError("Each transport carries at most 2 land units")


def solve_amphibious(att_fleet, cargo, att_air, def_fleet, def_land, bombard,
                     att_policy=STANDARD, def_policy=STANDARD):
    """Probability of each outcome of a full amphibious assault.

    Returns a dict:
        ``attacker_win``: the attacker clears the defenders from the territory.
        ``defender_win``, ``draw``, ``stalemate``: the other end states, each
            including failures at sea (``sea_failure`` is the share of the
            total that never reached the land battle).
        ``p_landing``: probability the sea zone was cleared and the land battle happened.
    """
    args = [tuple(x) for x in (att_fleet, cargo, att_air, def_fleet, def_land, bombard)]
    att_fleet, cargo, att_air, def_fleet, def_land, bombard = args
    _check_inputs(*args)
    _check_policies(att_policy, def_policy)
    validate(tuple(a + b for a, b in zip(cargo, att_air)), def_land)

    sea_battle = combatants(def_fleet) + def_fleet[TRN] > 0
    if sea_battle:
        sea_dist = _solve(att_fleet, def_fleet, att_policy, def_policy)
    else:
        sea_dist = {(att_fleet, def_fleet): 1.0}

    out = {"attacker_win": 0.0, "defender_win": 0.0, "draw": 0.0, "stalemate": 0.0}
    p_landing = 0.0
    for (af, df), p in sea_dist.items():
        if sea_battle:
            label = classify(af, df)
            if label != "attacker_win":
                out[label] += p  # never landed
                continue
        p_landing += p
        lost = max(0, sum(cargo) - 2 * af[TRN])
        landed = apply_hits(cargo, (lost, 0, 0, 0), default_order(True)) if lost else cargo
        n_land = sum(landed)
        n_bb = min(bombard[BB] + bombard[BBD], n_land)
        n_cru = min(bombard[CRU], n_land - n_bb)
        att_land = tuple(a + b for a, b in zip(landed, att_air))
        res = solve_land_assault(att_land, def_land, n_bb, n_cru, att_policy, def_policy)
        for key in out:
            out[key] += p * res[key]
    out["p_landing"] = p_landing
    out["sea_failure"] = 1.0 - p_landing
    return out
