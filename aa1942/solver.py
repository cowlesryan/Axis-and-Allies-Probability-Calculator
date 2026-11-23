"""Exact battle solver for Axis & Allies 1942 (2nd Edition): land, air and sea.

Method
------
A battle is a Markov chain over states ``(attacker army, defender army)``.
One combat round is enumerated exactly:

1. **Surprise strike** (sea): submarines whose enemy has no destroyer roll
   first (attack 2, defend 1). Their hits are applied *before* anyone else
   fires, so struck units do not fire back.
2. **General fire**: every other rolling unit fires simultaneously. Hits are
   split into pools (see casualties.py) because submarines cannot hit aircraft
   and aircraft cannot hit submarines without a destroyer.
3. **Casualties** are removed per the side's policy, giving the next state.

States are solved recursively with memoization. Rounds that change nothing are
removed and the rest rescaled by ``1 / (1 - P(stay))``. If *no* hit is possible
for either side (e.g. fighters versus a lone submarine) the battle is a
**stalemate** and ends as such.

Before the first round, defending AA guns fire once at attacking aircraft
(``_with_aa``); amphibious assaults add a bombardment step
(``solve_land_assault``).

Fighting to the end
-------------------
This release never retreats or submerges: every battle runs until one side has
no combatants left (or a stalemate).

End conditions
--------------
A side is *defeated* when it has no combatants (anything except AA guns and
transports). Its leftover AA guns are captured and its transports are
"defenseless" (removed once the enemy can still roll dice).

Army tuples follow ``units.ORDER``.
"""
import threading
import time
from functools import lru_cache

from .casualties import apply_hits, candidates, custom_order, default_order
from .combat import binom, convolve, roll_dist
from .units import (AA, BMB, DES, FTR, LAND_IDX, N, SEA_IDX, SUB, ZERO,
                    combatants, resolve_unit)

EPS = 1e-12
STANDARD = ("standard",)
OPTIMAL = ("optimal",)


# ---------------------------------------------------------------------------
# Time budget (large-battle protection)
# ---------------------------------------------------------------------------
class SolveBudgetExceeded(Exception):
    """Raised when an exact solve runs past its ``time_limit``.

    Callers (the CLI) catch it and fall back to Monte Carlo simulation. States
    already solved stay cached, so nothing is lost.
    """


class SolveCancelled(Exception):
    """Raised when ``cancel_event`` is set while an exact solve is running."""


_deadline = [None]  # monotonic-clock deadline for the solve in progress, or None
cancel_event = threading.Event()  # set from another thread to abort the running solve


def _check_budget():
    if cancel_event.is_set():
        raise SolveCancelled("solve cancelled")
    if _deadline[0] is not None and time.monotonic() > _deadline[0]:
        raise SolveBudgetExceeded("exact solve exceeded its time limit")


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------
def parse_policy(text):
    """Parse ``standard`` | ``optimal`` | ``order:inf,art,tank`` into a policy tuple."""
    t = text.strip().lower()
    if t in ("standard", "optimal"):
        return (t,)
    if t.startswith("order:"):
        names = [x for x in t[6:].split(",") if x.strip()]
        return ("order", tuple(resolve_unit(x) for x in names))
    raise ValueError(f"Unknown casualty policy: {text!r}")


def _order(policy, attacking):
    if policy[0] == "order":
        return custom_order(policy[1], attacking)
    return default_order(attacking)


def _check_policies(pa, pd):
    if pa[0] == "optimal" and pd[0] == "optimal":
        raise ValueError("At most one side may use the 'optimal' policy "
                         "(it is a best response to the other side's fixed policy).")


# ---------------------------------------------------------------------------
# Validation and classification
# ---------------------------------------------------------------------------
def validate(att, dfn):
    """Reject armies this solver cannot fight as one battle."""
    for name, army in (("attacker", att), ("defender", dfn)):
        if len(army) != N or min(army) < 0:
            raise ValueError(f"Bad {name} army: {army!r}")
    if att[AA]:
        raise ValueError("The attacker cannot bring AA guns into combat.")
    has_sea = any(a[i] for a in (att, dfn) for i in SEA_IDX)
    has_land = any(a[i] for a in (att, dfn) for i in LAND_IDX)
    if has_sea and has_land:
        raise ValueError("Cannot mix land and sea units in one battle; "
                         "use the amphibious assault solver for combined attacks.")


def classify(a, d):
    """Outcome label of a final state."""
    ao, do = combatants(a) > 0, combatants(d) > 0
    if ao and not do:
        return "attacker_win"
    if do and not ao:
        return "defender_win"
    if not ao and not do:
        return "draw"
    return "stalemate"


def summarize(dist):
    """Collapse a final-state distribution into outcome probabilities."""
    out = {"attacker_win": 0.0, "defender_win": 0.0, "draw": 0.0, "stalemate": 0.0}
    for (a, d), p in dist.items():
        out[classify(a, d)] += p
    out["final_states"] = dist
    return out


def _att_win(a, d, pa, pd):
    return sum(p for (x, y), p in _solve(a, d, pa, pd).items()
               if combatants(x) > 0 and combatants(y) == 0)


def _best(cands, other, attacker, pa, pd):
    """Pick the candidate army that is best for its owner (ties: first)."""
    best, best_score = None, None
    for c in cands:
        a, d = (c, other) if attacker else (other, c)
        w = _att_win(a, d, pa, pd)
        score = w if attacker else -w
        if best_score is None or score > best_score + 1e-12:
            best, best_score = c, score
    return best


# ---------------------------------------------------------------------------
# One round
# ---------------------------------------------------------------------------
def _casualties(a1, d1, dh, ah, pa, pd, oa, od):
    """Apply defender hits ``dh`` to the attacker and attacker hits ``ah`` to the defender."""
    if pa[0] == "optimal":
        d2 = apply_hits(d1, ah, od)
        cands = candidates(a1, dh, oa)
        a2 = cands[0] if len(cands) == 1 else _best(cands, d2, True, pa, pd)
    elif pd[0] == "optimal":
        a2 = apply_hits(a1, dh, oa)
        cands = candidates(d1, ah, od)
        d2 = cands[0] if len(cands) == 1 else _best(cands, a2, False, pa, pd)
    else:
        a2 = apply_hits(a1, dh, oa)
        d2 = apply_hits(d1, ah, od)
    return a2, d2


def _round(att, dfn, pa, pd, def_fire=None):
    """Enumerate one combat round: ``{next_state: probability}``.

    ``def_fire`` lets the defender roll with a different (larger) army than the
    one that takes casualties; used for units killed by bombardment, which still
    fire back in the first round.
    """
    oa, od = _order(pa, True), _order(pd, False)
    a_sur = att[SUB] if dfn[DES] == 0 else 0  # surprise-striking subs
    d_sur = dfn[SUB] if att[DES] == 0 else 0

    stage1 = []
    if not a_sur and not d_sur:
        stage1.append((1.0, att, dfn))
    else:
        # Surprise strike: standard casualties, applied before general fire.
        for ha, p_a in enumerate(binom(a_sur, 2 / 6)):
            for hd, p_d in enumerate(binom(d_sur, 1 / 6)):
                w = p_a * p_d
                if w == 0:
                    continue
                a1 = apply_hits(att, (0, hd, 0, 0), oa) if hd else att
                d1 = apply_hits(dfn, (0, ha, 0, 0), od) if ha else dfn
                stage1.append((w, a1, d1))

    out = {}
    for w1, a1, d1 in stage1:
        adist = roll_dist(a1, True, a_sur > 0)
        ddist = roll_dist(d1 if def_fire is None else def_fire, False, d_sur > 0)
        for ah, p_a in adist.items():
            for dh, p_d in ddist.items():
                _check_budget()  # one round between huge armies is itself a very long loop (millions of
                #                  casualty applications); honour the time limit and cancellation inside it
                a2, d2 = _casualties(a1, d1, dh, ah, pa, pd, oa, od)
                key = (a2, d2)
                out[key] = out.get(key, 0.0) + w1 * p_a * p_d
    return out


# ---------------------------------------------------------------------------
# Whole battle
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _solve(att, dfn, pa, pd):
    """Final-state distribution ``{(att, dfn): probability}`` for a battle state."""
    a_ok, d_ok = combatants(att) > 0, combatants(dfn) > 0
    if not a_ok or not d_ok:
        # Defeated side's leftovers (AA guns / transports) are lost.
        return {(att if a_ok else ZERO, dfn if d_ok else ZERO): 1.0}

    _check_budget()
    out = _round(att, dfn, pa, pd)
    stay = out.pop((att, dfn), 0.0)
    if 1.0 - stay < EPS:  # nobody can hit anybody: stalemate
        return {(att, dfn): 1.0}
    scale = 1.0 / (1.0 - stay)  # condition on the round changing something
    result = {}
    for state, p in out.items():
        for final, q in _solve(*state, pa, pd).items():
            result[final] = result.get(final, 0.0) + p * scale * q
    return result


def _with_aa(att, dfn, pa, pd):
    """Resolve the one-time AA fire against attacking aircraft, then fight."""
    planes = att[FTR] + att[BMB]
    shots = min(3 * dfn[AA], planes)  # each gun: up to 3 shots, one per plane
    if not shots:
        return _solve(att, dfn, pa, pd)
    oa = _order(pa, True)
    result = {}
    for h, p in enumerate(binom(shots, 1 / 6)):  # AA hits on a roll of 1
        if p == 0:
            continue
        hits = (0, 0, 0, h)
        if h == 0:
            a1 = att
        elif pa[0] == "optimal":
            cands = candidates(att, hits, oa)
            a1 = cands[0] if len(cands) == 1 else _best(cands, dfn, True, pa, pd)
        else:
            a1 = apply_hits(att, hits, oa)
        for final, q in _solve(a1, dfn, pa, pd).items():
            result[final] = result.get(final, 0.0) + p * q
    return result


def solve(att, dfn, att_policy=STANDARD, def_policy=STANDARD, time_limit=None):
    """Exact outcome of a land, air or sea battle fought to the end.

    Args:
        att, dfn: army tuples (see ``units.parse_army``).
        att_policy, def_policy: casualty policies (see casualties.py). At most
            one may be ``optimal``.
        time_limit: optional seconds budget. Exact solving cost grows quickly
            with the number of dice and distinct unit types (roughly a minute
            for ~85 mixed units), so callers that can fall back to simulation
            pass a limit; ``SolveBudgetExceeded`` is raised when it runs out.

    Returns:
        ``attacker_win``, ``defender_win``, ``draw`` (both wiped out),
        ``stalemate`` (neither side can hit), and ``final_states``.
    """
    att, dfn = tuple(att), tuple(dfn)
    validate(att, dfn)
    _check_policies(att_policy, def_policy)
    _deadline[0] = None if time_limit is None else time.monotonic() + time_limit
    try:
        return summarize(_with_aa(att, dfn, att_policy, def_policy))
    finally:
        _deadline[0] = None


def solve_land_assault(att, d0, n_bb, n_cru, att_policy=STANDARD, def_policy=STANDARD):
    """Land battle preceded by offshore bombardment (amphibious assault, 1942 rules).

    Bombarding battleships (hit on <= 4) and cruisers (<= 3) fire first; the
    defender's bombardment casualties are removed but, per the 1942 rules
    comparison, *still fire back* in the first round. AA guns fire at attacking
    aircraft from the same pre-casualty army. Casualty choice in the bombard and
    AA steps always follows the standard order.

    Returns the same dictionary as ``solve``.
    """
    att, d0 = tuple(att), tuple(d0)
    validate(att, d0)
    _check_policies(att_policy, def_policy)
    pa, pd = att_policy, def_policy
    oa, od = _order(pa, True), _order(pd, False)
    bomb = convolve(binom(n_bb, 4 / 6), binom(n_cru, 3 / 6))
    shots = min(3 * d0[AA], att[FTR] + att[BMB])
    result = {}
    for hb, pb in enumerate(bomb):
        d1 = apply_hits(d0, (hb, 0, 0, 0), od) if hb else d0
        for h, ph in enumerate(binom(shots, 1 / 6)):
            a1 = apply_hits(att, (0, 0, 0, h), oa) if h else att
            if combatants(a1) == 0:
                nxt = {(a1, d1): 1.0}
            else:
                nxt = _round(a1, d1, pa, pd, def_fire=d0)
            for state, pr in nxt.items():
                for final, q in _solve(*state, pa, pd).items():
                    result[final] = result.get(final, 0.0) + pb * ph * pr * q
    return summarize(result)


