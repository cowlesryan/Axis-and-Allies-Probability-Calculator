"""Monte Carlo simulator: independent dice-level check and large-battle fallback.

This module rolls actual random dice round by round. It re-implements the dice
rules (AA fire, surprise strike, artillery boost, hit pools, stalemate
detection) separately from ``solver.py`` so agreement between the two is real
evidence of correctness. It shares only the casualty-removal helpers
(``casualties.py``), and always uses the *standard* casualty policy.

The standard error of a reported probability is about ``0.5 / sqrt(runs)``.
"""
import random
import time

from .casualties import ANY, NONAIR, NOSUB, apply_hits, default_order, targets
from .combat import roll_groups
from .solver import classify
from .units import (AA, AIR_IDX, ART, BB, BBD, BMB, DES, FTR, INF, N, SUB,
                    UNIT_LIST, combatants)


def _count(rng, n, value):
    """Number of ``n`` dice that roll <= ``value``."""
    return sum(rng.randint(1, 6) <= value for _ in range(n))


def _rolls(rng, army, attacking, exclude_subs):
    """Roll one general-fire step; return the 4-tuple of hits per pool."""
    cnt = list(army)
    cnt[BB] += cnt[BBD]
    cnt[BBD] = 0
    own_des = cnt[DES] > 0
    boosted = min(cnt[INF], cnt[ART]) if attacking else 0
    hits = [0, 0, 0, 0]
    for idx in range(N):
        n = cnt[idx]
        u = UNIT_LIST[idx]
        s = u.attack if attacking else u.defend
        if n == 0 or s == 0 or (idx == SUB and exclude_subs):
            continue
        if idx == SUB:
            pool = NONAIR
        elif idx in AIR_IDX and not own_des:
            pool = NOSUB
        else:
            pool = ANY
        if idx == INF and attacking:
            hits[pool] += _count(rng, boosted, 2) + _count(rng, n - boosted, 1)
        else:
            hits[pool] += _count(rng, n, s)
    return tuple(hits)


def _stuck(att, dfn, a_sur, d_sur):
    """True if neither side has any possible hit on a present, legal target."""
    for me, you, sur, attacking in ((att, dfn, a_sur, True), (dfn, att, d_sur, False)):
        pools = {pool for pool, _, _ in roll_groups(me, attacking, sur > 0)}
        if sur:
            pools.add(NONAIR)
        if any(targets(you, pool) for pool in pools):
            return False
    return True


def _one_battle(rng, att, dfn):
    oa, od = default_order(True), default_order(False)
    # One-time AA fire at attacking aircraft (hit on a 1).
    shots = min(3 * dfn[AA], att[FTR] + att[BMB])
    h = _count(rng, shots, 1)
    if h:
        att = apply_hits(att, (0, 0, 0, h), oa)
    while combatants(att) and combatants(dfn):
        a_sur = att[SUB] if dfn[DES] == 0 else 0
        d_sur = dfn[SUB] if att[DES] == 0 else 0
        if _stuck(att, dfn, a_sur, d_sur):
            break
        if a_sur or d_sur:  # surprise strike, simultaneous, casualties first
            ha = _count(rng, a_sur, 2)
            hd = _count(rng, d_sur, 1)
            att, dfn = (apply_hits(att, (0, hd, 0, 0), oa),
                        apply_hits(dfn, (0, ha, 0, 0), od))
        ah = _rolls(rng, att, True, a_sur > 0)
        dh = _rolls(rng, dfn, False, d_sur > 0)
        att, dfn = apply_hits(att, dh, oa), apply_hits(dfn, ah, od)
    return att, dfn


def simulate(att, dfn, runs=100_000, seed=None, max_seconds=None, should_stop=None):
    """Estimate outcome probabilities over up to ``runs`` simulated battles.

    Args:
        runs: number of battles to simulate.
        seed: optional RNG seed for reproducible results.
        max_seconds: optional wall-clock cap. Large battles are slow to
            simulate, so the loop stops early once the cap passes (after at least 64
            battles, or after three times the cap if battles are that slow) and the
            result reports how many runs actually completed.
        should_stop: optional callable polled after every battle; returning True
            ends the loop early (used to cancel from another thread).

    Returns ``attacker_win``, ``defender_win``, ``draw``, ``stalemate`` (fractions
    of completed runs), ``stderr`` (standard error of ``attacker_win``) and
    ``runs`` (battles actually simulated).
    """
    rng = random.Random(seed)
    tally = {"attacker_win": 0, "defender_win": 0, "draw": 0, "stalemate": 0}
    deadline = None if max_seconds is None else time.monotonic() + max_seconds
    done = 0
    while done < runs:
        a, d = _one_battle(rng, tuple(att), tuple(dfn))
        tally[classify(a, d)] += 1
        done += 1
        # Checked after every battle: one battle between two very large armies can itself take about a second,
        # so checking only every 64 battles let a "1 second" cap run for over a minute. The cap is honoured once
        # at least 64 battles ran (a meaningful sample) or, failing that, after three times the cap.
        if should_stop is not None and should_stop():
            break
        if deadline is not None:
            now = time.monotonic()
            if now > deadline and (done >= 64 or now > deadline + 2 * max_seconds):
                break
    res = {k: v / done for k, v in tally.items()}
    p = res["attacker_win"]
    res["stderr"] = (p * (1 - p) / done) ** 0.5
    res["runs"] = done
    return res
