"""Dice math: what a side rolls in one combat step and the hit distribution.

``roll_groups`` turns an army into groups of identical dice; ``roll_dist``
turns those into the exact probability of every combination of hits per pool.
"""
from functools import lru_cache
from math import comb

from .casualties import ANY, NONAIR, NOSUB
from .units import AIR_IDX, ART, BB, BBD, INF, N, SUB, UNIT_LIST, DES


def binom(n, p):
    """pmf list of Binomial(n, p): entry k = P(exactly k hits)."""
    return [comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(n + 1)]


def convolve(a, b):
    """pmf of the sum of two independent discrete variables."""
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return out


@lru_cache(maxsize=None)
def roll_groups(army, attacking, exclude_subs):
    """Describe one general-fire step as a tuple of ``(pool, count, p_hit)``.

    Rules applied here:
    * A die hits on <= the unit's attack (attacking) or defend (defending) value.
    * Each artillery lifts one infantry from attack 1 to 2 (attacker only).
    * A damaged battleship fires like an undamaged one.
    * Submarine hits go to the NONAIR pool (subs cannot hit aircraft).
    * Aircraft hits go to NOSUB unless the aircraft's own side has a destroyer
      in the battle (then aircraft may hit submarines).
    * ``exclude_subs`` removes submarines that already fired their surprise strike.
    """
    n = list(army)
    n[BB] += n[BBD]
    n[BBD] = 0
    own_des = n[DES] > 0
    boosted = min(n[INF], n[ART]) if attacking else 0
    groups = []
    for idx in range(N):
        cnt = n[idx]
        if cnt == 0 or (idx == SUB and exclude_subs):
            continue
        u = UNIT_LIST[idx]
        s = u.attack if attacking else u.defend
        if s <= 0:
            continue
        if idx == SUB:
            pool = NONAIR
        elif idx in AIR_IDX and not own_des:
            pool = NOSUB
        else:
            pool = ANY
        if idx == INF and attacking:
            if boosted:
                groups.append((pool, boosted, 2 / 6))
            if cnt - boosted:
                groups.append((pool, cnt - boosted, 1 / 6))
        else:
            groups.append((pool, cnt, s / 6))
    return tuple(groups)


@lru_cache(maxsize=None)
def roll_dist(army, attacking, exclude_subs):
    """Exact distribution of hits for one general-fire step.

    Returns ``{(any, nonair, nosub, 0): probability}`` over all hit combinations.
    Treat the returned dict as read-only (it is cached).
    """
    per = {ANY: [1.0], NONAIR: [1.0], NOSUB: [1.0]}
    for pool, cnt, p in roll_groups(army, attacking, exclude_subs):
        per[pool] = convolve(per[pool], binom(cnt, p))
    out = {}
    for a, pa in enumerate(per[ANY]):
        for b, pb in enumerate(per[NONAIR]):
            for c, pc in enumerate(per[NOSUB]):
                p = pa * pb * pc
                if p > 0:
                    out[(a, b, c, 0)] = p
    return out
