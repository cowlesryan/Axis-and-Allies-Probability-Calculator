"""Casualty assignment: which units die when a side takes hits.

Hit pools
---------
Not every hit may be applied to every unit, so hits are tracked in four pools:

* ``ANY``     - ordinary hits; may be applied to any unit.
* ``NONAIR``  - hits scored by submarines; may never be applied to aircraft.
* ``NOSUB``   - hits scored by aircraft whose side has no destroyer in the
                battle; may never be applied to submarines.
* ``AIRONLY`` - AA-gun hits; may only be applied to aircraft.

A hit with no legal target is simply lost. All legal hits must be applied
("you must apply both hits if possible", FAQ), so restricted pools are applied
first and ``ANY`` last.

Transports are special: they are taken as casualties only when no other
legal target remains (FAQ: "they can't be used as cannon fodder").

Battleships take two hits: the first moves a ``bb`` to ``bbd`` (damaged), the
second removes the ``bbd``.

Policies
--------
The unit owner chooses casualties, so the calculator needs a rule:

* ``standard``: lowest combat strength first (ties: cheaper first). An
  undamaged battleship absorbs a hit before anything else (the first hit costs
  it nothing).
* ``optimal``: for each round and each dice outcome, pick the legal
  casualty set that maximises that side's chance to win (see solver.py).
* ``order:a,b,c``: user-supplied removal priority; unlisted units follow in
  standard order.
"""
from functools import lru_cache

from .units import AIR_IDX, BB, BBD, N, SUB, TRN, UNIT_LIST

ANY, NONAIR, NOSUB, AIRONLY = 0, 1, 2, 3
POOL_SEQUENCE = (AIRONLY, NONAIR, NOSUB, ANY)  # most restricted first
NO_HITS = (0, 0, 0, 0)


def _allowed(pool, idx):
    if pool == ANY:
        return True
    if pool == NONAIR:
        return idx not in AIR_IDX
    if pool == NOSUB:
        return idx != SUB
    return idx in AIR_IDX  # AIRONLY


ALLOWED = tuple(tuple(_allowed(p, i) for i in range(N)) for p in range(4))


def strength(idx, attacking):
    """Combat value of a unit in the given role (0 for non-rolling units)."""
    u = UNIT_LIST[idx]
    return u.attack if attacking else u.defend


@lru_cache(maxsize=None)
def default_order(attacking):
    """Standard casualty priority (tuple of unit indices, first = lost first)."""
    return tuple(sorted(
        range(N),
        key=lambda i: (0 if i == BB else 1, strength(i, attacking), UNIT_LIST[i].cost),
    ))


@lru_cache(maxsize=None)
def custom_order(front, attacking):
    """User priority ``front`` followed by the remaining units in standard order."""
    rest = tuple(i for i in default_order(attacking) if i not in front)
    return tuple(front) + rest


def targets(army, pool):
    """Unit indices that may legally absorb a hit from ``pool`` right now."""
    ok = ALLOWED[pool]
    ts = [i for i in range(N) if army[i] > 0 and ok[i] and i != TRN]
    if not ts and army[TRN] > 0 and ok[TRN]:
        ts = [TRN]  # transports only when nothing else can be hit
    return ts


def hit_unit(army_list, idx):
    """Apply one hit to a mutable army list (battleships absorb two)."""
    if idx == BB:
        army_list[BB] -= 1
        army_list[BBD] += 1
    else:
        army_list[idx] -= 1


@lru_cache(maxsize=None)
def apply_hits(army, hits, order):
    """Apply ``hits`` (4-tuple of pool counts) to ``army`` following ``order``.

    Returns the new army tuple. Hits with no legal target are dropped.
    """
    cur = list(army)
    for pool in POOL_SEQUENCE:
        for _ in range(hits[pool]):
            ts = targets(cur, pool)
            if not ts:
                break
            pick = next(i for i in order if i in ts)
            hit_unit(cur, pick)
    return tuple(cur)


def candidates(army, hits, order, limit=4000):
    """All legal casualty outcomes that apply the maximum possible number of hits.

    Used by the ``optimal`` policy. If the branching explodes past ``limit``
    distinct armies, falls back to the single standard outcome.
    """
    level = {army: 0}  # army -> number of hits applied so far
    for pool in POOL_SEQUENCE:
        for _ in range(hits[pool]):
            new = {}
            for st, applied in level.items():
                ts = targets(st, pool)
                if not ts:
                    new[st] = max(new.get(st, -1), applied)
                    continue
                for t in ts:
                    nxt = list(st)
                    hit_unit(nxt, t)
                    nxt = tuple(nxt)
                    new[nxt] = max(new.get(nxt, -1), applied + 1)
            level = new
            if len(level) > limit:
                return [apply_hits(army, hits, order)]
    best = max(level.values())
    return sorted(s for s, a in level.items() if a == best)
