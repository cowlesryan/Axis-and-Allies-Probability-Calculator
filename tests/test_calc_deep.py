"""Deep checks of the calculator: an independent brute-force reference, invariants over random armies,
exact-vs-simulation agreement, amphibious/bombing identities, parsing and the command line.

The reference solver below deliberately shares no code with ``solver.py``/``combat.py``: it enumerates which
individual units hit (2^n subsets), applies hits unit by unit, and recurses on the battle state.
"""
import itertools
import random
import subprocess
import sys
from functools import lru_cache

import pytest

from aa1942 import units as U
from aa1942.amphibious import solve_amphibious
from aa1942.bombing import solve_sbr
from aa1942.simulate import simulate
from aa1942.solver import OPTIMAL, STANDARD, parse_policy, solve, solve_land_assault, validate
from aa1942.units import (AA, AIR_IDX, ART, BB, BBD, BMB, CAR, CRU, DES, FTR, INF, N, ORDER, SUB, TANK, TRN,
                          UNIT_LIST, army_cost, combatants, fmt_army, parse_army)

A = parse_army


# ----------------------------------------------------------------------------------------------------------
# independent reference (standard casualty policy)
# ----------------------------------------------------------------------------------------------------------
def _standard_order(attacking):
    def key(i):
        u = UNIT_LIST[i]
        s = u.attack if attacking else u.defend
        return (0 if i == BB else 1, s, u.cost)
    return sorted(range(N), key=key)


def _units(army):
    """Expand an army to a list of unit indices (bbd is a damaged bb)."""
    out = []
    for i, n in enumerate(army):
        out += [i] * n
    return out


def _apply(army, hits, attacking):
    """Apply a list of hit restrictions ('air' | 'nonair' | 'nosub' | 'any') following the standard order."""
    cur = list(army)
    order = _standard_order(attacking)
    for restr in ("air", "nonair", "nosub", "any"):
        for _ in range(hits.count(restr)):
            ok = [i for i in order if cur[i] > 0 and (
                restr == "any" or (restr == "air" and i in AIR_IDX) or
                (restr == "nonair" and i not in AIR_IDX) or (restr == "nosub" and i != SUB))]
            non_trn = [i for i in ok if i != TRN]
            pool = non_trn or ok
            if not pool:
                continue
            pick = pool[0]
            if pick == BB:
                cur[BB] -= 1
                cur[BBD] += 1
            else:
                cur[pick] -= 1
    return tuple(cur)


def _fire(army, attacking, no_subs):
    """Distribution ``{tuple(sorted hit restrictions): p}`` of one general-fire step, subset by subset."""
    n = list(army)
    n[BB] += n[BBD]
    n[BBD] = 0
    own_des = n[DES] > 0
    shooters = []  # (p_hit, restriction)
    boosted = min(n[INF], n[ART]) if attacking else 0
    inf_seen = 0
    for i in range(N):
        for _ in range(n[i]):
            u = UNIT_LIST[i]
            s = u.attack if attacking else u.defend
            if i == INF and attacking:
                s = 2 if inf_seen < boosted else 1
                inf_seen += 1
            if s <= 0 or (i == SUB and no_subs):
                continue
            restr = "nonair" if i == SUB else ("nosub" if (i in AIR_IDX and not own_des) else "any")
            shooters.append((s / 6, restr))
    dist = {}
    for mask in itertools.product((0, 1), repeat=len(shooters)):
        p, hits = 1.0, []
        for hit, (ph, restr) in zip(mask, shooters):
            p *= ph if hit else 1 - ph
            if hit:
                hits.append(restr)
        key = tuple(sorted(hits))
        dist[key] = dist.get(key, 0.0) + p
    return dist


@lru_cache(maxsize=None)
def ref_final(att, dfn):
    """``{(att, dfn): p}`` for the whole battle, reference implementation."""
    a_ok, d_ok = combatants(att) > 0, combatants(dfn) > 0
    if not (a_ok and d_ok):
        return {(att if a_ok else (0,) * N, dfn if d_ok else (0,) * N): 1.0}
    a_sur = att[SUB] if dfn[DES] == 0 else 0
    d_sur = dfn[SUB] if att[DES] == 0 else 0
    out = {}
    stage = []
    if a_sur or d_sur:
        for ha in range(a_sur + 1):
            for hd in range(d_sur + 1):
                from math import comb
                p = (comb(a_sur, ha) * (2 / 6) ** ha * (4 / 6) ** (a_sur - ha) *
                     comb(d_sur, hd) * (1 / 6) ** hd * (5 / 6) ** (d_sur - hd))
                stage.append((p, _apply(att, ["nonair"] * hd, True), _apply(dfn, ["nonair"] * ha, False)))
    else:
        stage.append((1.0, att, dfn))
    for p1, a1, d1 in stage:
        for ka, pa in _fire(a1, True, a_sur > 0).items():
            for kd, pd in _fire(d1, False, d_sur > 0).items():
                key = (_apply(a1, list(kd), True), _apply(d1, list(ka), False))
                out[key] = out.get(key, 0.0) + p1 * pa * pd
    stay = out.pop((att, dfn), 0.0)
    if 1 - stay < 1e-12:
        return {(att, dfn): 1.0}
    res = {}
    for st, p in out.items():
        for fin, q in ref_final(*st).items():
            res[fin] = res.get(fin, 0.0) + p / (1 - stay) * q
    return res


def ref_outcomes(att, dfn):
    from aa1942.solver import classify
    # one-time AA fire
    shots = min(3 * dfn[AA], att[FTR] + att[BMB])
    from math import comb
    total = {"attacker_win": 0.0, "defender_win": 0.0, "draw": 0.0, "stalemate": 0.0}
    for h in range(shots + 1):
        p = comb(shots, h) * (1 / 6) ** h * (5 / 6) ** (shots - h)
        a1 = _apply(att, ["air"] * h, True)
        for (a, d), q in ref_final(a1, dfn).items():
            total[classify(a, d)] += p * q
    return total


def small_armies(rng, domain):
    pools = {"land": [INF, ART, TANK, FTR, BMB], "air": [FTR, BMB, INF], "sea": [SUB, DES, CRU, CAR, BB, FTR, BMB, TRN]}
    def one(defending):
        idxs = pools[domain]
        a = [0] * N
        for _ in range(rng.randint(1, 4)):
            a[rng.choice(idxs)] += 1
        if domain == "land" and defending and rng.random() < 0.4:
            a[AA] += rng.randint(1, 2)
        return tuple(a)
    return one(False), one(True)


@pytest.mark.parametrize("domain", ["land", "air", "sea"])
def test_exact_solver_matches_independent_reference(domain):
    rng = random.Random(1234 + len(domain))
    checked = 0
    while checked < 25:
        att, dfn = small_armies(rng, domain)
        if sum(att) + sum(dfn) > 9:
            continue
        try:
            validate(att, dfn)
        except ValueError:
            continue
        got = solve(att, dfn)
        want = ref_outcomes(att, dfn)
        for k in want:
            assert got[k] == pytest.approx(want[k], abs=1e-9), (fmt_army(att), fmt_army(dfn), k)
        checked += 1


# ----------------------------------------------------------------------------------------------------------
# invariants
# ----------------------------------------------------------------------------------------------------------
def random_army(rng, idxs, total):
    a = [0] * N
    for _ in range(rng.randint(1, total)):
        a[rng.choice(idxs)] += 1
    return tuple(a)


def test_probabilities_sum_to_one_and_losses_are_sane():
    rng = random.Random(7)
    for _ in range(40):
        dom = rng.choice(["land", "sea"])
        idxs = [INF, ART, TANK, FTR, BMB] if dom == "land" else [SUB, DES, CRU, CAR, BB, FTR, BMB, TRN]
        att, dfn = random_army(rng, idxs, 6), random_army(rng, idxs, 6)
        if dom == "land" and rng.random() < 0.5:
            dfn = tuple(x + (2 if i == AA else 0) for i, x in enumerate(dfn))
        res = solve(att, dfn)
        assert sum(res[k] for k in ("attacker_win", "defender_win", "draw", "stalemate")) == pytest.approx(1.0, abs=1e-9)
        assert all(res[k] >= -1e-12 for k in ("attacker_win", "defender_win", "draw", "stalemate"))
        assert sum(res["final_states"].values()) == pytest.approx(1.0, abs=1e-9)
        for (a, d), p in res["final_states"].items():
            assert min(a) >= 0 and min(d) >= 0
            assert army_cost(a) <= army_cost(att) + 20 and army_cost(d) <= army_cost(dfn) + 20  # damaged bb counts once


def test_symmetry_for_equal_attack_and_defence():
    for unit, n in (("tank", 3), ("cru", 2), ("art", 4)):
        for m in (1, 2, 4):
            a = solve(A(f"{n} {unit}"), A(f"{m} {unit}"))
            b = solve(A(f"{m} {unit}"), A(f"{n} {unit}"))
            assert a["attacker_win"] == pytest.approx(b["defender_win"], abs=1e-9)
            assert a["draw"] == pytest.approx(b["draw"], abs=1e-9)


def test_more_attackers_never_hurts_in_plain_land_battles():
    rng = random.Random(3)
    for _ in range(25):
        att = random_army(rng, [INF, ART, TANK], 5)
        dfn = random_army(rng, [INF, ART, TANK], 5)
        base = solve(att, dfn)["attacker_win"]
        for extra in (INF, ART, TANK):
            bigger = list(att)
            bigger[extra] += 1
            assert solve(tuple(bigger), dfn)["attacker_win"] >= base - 1e-9


def test_more_defenders_never_helps_the_attacker():
    rng = random.Random(4)
    for _ in range(25):
        att = random_army(rng, [INF, ART, TANK], 5)
        dfn = random_army(rng, [INF, ART, TANK], 5)
        base = solve(att, dfn)["attacker_win"]
        for extra in (INF, TANK):
            bigger = list(dfn)
            bigger[extra] += 1
            assert solve(att, tuple(bigger))["attacker_win"] <= base + 1e-9


def test_optimal_policy_is_never_worse_than_standard():
    rng = random.Random(11)
    for _ in range(12):
        att = random_army(rng, [INF, ART, TANK, FTR], 4)
        dfn = random_army(rng, [INF, ART, TANK, FTR], 4)
        std = solve(att, dfn)["attacker_win"]
        assert solve(att, dfn, OPTIMAL, STANDARD)["attacker_win"] >= std - 1e-9
        assert solve(att, dfn, STANDARD, OPTIMAL)["attacker_win"] <= std + 1e-9


def test_custom_order_that_equals_standard_changes_nothing():
    att, dfn = A("3 inf, 2 tank"), A("4 inf, 1 tank")
    std = solve(att, dfn)["attacker_win"]
    same = solve(att, dfn, parse_policy("order:inf,tank"), STANDARD)["attacker_win"]
    assert same == pytest.approx(std, abs=1e-12)


@pytest.mark.parametrize("domain", ["land", "sea"])
def test_exact_agrees_with_simulation_on_random_battles(domain):
    rng = random.Random(99)
    idxs = [INF, ART, TANK, FTR, BMB] if domain == "land" else [SUB, DES, CRU, CAR, BB, FTR]
    for _ in range(4):
        att, dfn = random_army(rng, idxs, 7), random_army(rng, idxs, 7)
        if domain == "land":
            dfn = tuple(x + (1 if i == AA else 0) for i, x in enumerate(dfn))
        want = solve(att, dfn)
        got = simulate(att, dfn, runs=6000, seed=5)
        for k in ("attacker_win", "defender_win", "draw", "stalemate"):
            assert abs(got[k] - want[k]) < 0.035, (fmt_army(att), fmt_army(dfn), k, got[k], want[k])


# ----------------------------------------------------------------------------------------------------------
# edge cases
# ----------------------------------------------------------------------------------------------------------
def test_empty_and_one_sided_battles():
    assert solve(A(""), A(""))["draw"] == 1.0
    assert solve(A("2 inf"), A(""))["attacker_win"] == 1.0
    assert solve(A(""), A("2 inf"))["defender_win"] == 1.0
    assert solve(A("2 inf"), A("3 aa"))["attacker_win"] == 1.0  # a lone AA gun is captured
    assert solve(A("1 ftr"), A("2 aa"))["attacker_win"] > 0.0


def test_stalemates():
    assert solve(A("1 sub"), A("1 bmb"))["stalemate"] == pytest.approx(1.0)  # sub cannot hit planes, bomber cannot hit sub
    res = solve(A("1 ftr"), A("1 sub"))
    assert res["stalemate"] == pytest.approx(1.0)
    assert 0 < solve(A("1 ftr, 1 des"), A("1 sub"))["stalemate"] < 0.2  # only if the destroyer dies first


def test_validation_errors():
    for att, dfn in ((A("1 aa"), A("1 inf")), (A("1 inf"), A("1 des")), (A("1 des"), A("1 inf"))):
        with pytest.raises(ValueError):
            solve(att, dfn)
    with pytest.raises(ValueError):
        solve((1,) * 12, A("1 inf"))
    with pytest.raises(ValueError):
        solve((-1,) + (0,) * 12, A("1 inf"))
    with pytest.raises(ValueError):
        solve(A("1 inf"), A("1 inf"), OPTIMAL, OPTIMAL)


def test_large_counts_do_not_crash_the_simulator():
    res = simulate(A("100 inf, 30 art, 30 tank"), A("120 inf, 20 tank"), runs=200, seed=1, max_seconds=5)
    assert 0 <= res["attacker_win"] <= 1 and res["runs"] >= 64


def test_simulation_cancel_and_cap():
    res = simulate(A("50 inf"), A("50 inf"), runs=10 ** 6, max_seconds=0.2)
    assert res["runs"] < 10 ** 6
    res = simulate(A("5 inf"), A("5 inf"), runs=10 ** 6, should_stop=lambda: True)
    assert res["runs"] == 1
    t0 = __import__("time").time()                      # one huge battle must not blow through a short cap
    res = simulate(A("300 inf, 150 art, 150 tank"), A("400 inf, 200 tank"), runs=10 ** 6, max_seconds=0.5)
    assert __import__("time").time() - t0 < 15 and res["runs"] >= 1


def test_damaged_battleship_slot_behaves_like_a_battleship_that_dies_in_one_hit():
    one_hit = solve(A("1 bbd"), A("1 des"))["defender_win"]
    two_hit = solve(A("1 bb"), A("1 des"))["defender_win"]
    assert one_hit > two_hit


def test_transports_defenceless_and_ordering():
    # 2 transports with a destroyer: the destroyer dies before the transports
    res = solve(A("3 des"), A("1 des, 2 trn"))
    assert res["attacker_win"] > 0.5
    for (a, d), p in res["final_states"].items():
        if combatants(a) > 0 and combatants(d) == 0:
            assert d[TRN] == 0  # defenceless transports never survive an attacker win


# ----------------------------------------------------------------------------------------------------------
# amphibious and bombing identities
# ----------------------------------------------------------------------------------------------------------
Z = (0,) * N


def test_amphibious_without_sea_battle_or_bombard_is_a_land_battle():
    cargo, dland = A("2 inf, 1 tank"), A("2 inf")
    amph = solve_amphibious(A("2 trn"), cargo, Z, Z, dland, Z)
    land = solve(cargo, dland)
    assert amph["attacker_win"] == pytest.approx(land["attacker_win"], abs=1e-9)
    assert amph["p_landing"] == pytest.approx(1.0)


def test_amphibious_outcomes_sum_to_one_and_bombard_is_monotone():
    args = (A("2 trn, 1 des"), A("2 inf, 2 tank"), A("1 ftr"), A("1 sub"), A("3 inf, 1 aa"))
    res0 = solve_amphibious(*args, Z)
    res1 = solve_amphibious(*args, A("1 bb"))
    res2 = solve_amphibious(*args, A("1 bb, 1 cru"))
    for r in (res0, res1, res2):
        assert sum(r[k] for k in ("attacker_win", "defender_win", "draw", "stalemate")) == pytest.approx(1.0, abs=1e-9)
        assert 0 <= r["p_landing"] <= 1
    assert res1["attacker_win"] >= res0["attacker_win"] - 1e-9
    assert res2["attacker_win"] >= res1["attacker_win"] - 1e-9


def test_amphibious_bombard_ships_beyond_landing_units_are_ignored():
    args = (A("1 trn"), A("1 inf"), Z, Z, A("2 inf"))
    a = solve_amphibious(*args, A("1 bb"))
    b = solve_amphibious(*args, A("3 bb"))
    assert a["attacker_win"] == pytest.approx(b["attacker_win"], abs=1e-12)


def test_amphibious_rejects_overloaded_transports_and_bad_armies():
    with pytest.raises(ValueError):
        solve_amphibious(A("1 trn"), A("3 inf"), Z, Z, A("1 inf"), Z)
    with pytest.raises(ValueError):
        solve_amphibious(A("1 inf"), A("1 inf"), Z, Z, A("1 inf"), Z)  # fleet may hold sea units only


def test_sbr_identities():
    r = solve_sbr(0, 3)
    assert r["damage_distribution"] == {0: 1.0} and r["expected_damage"] == 0
    big = solve_sbr(1, 50)  # the cap is out of reach: one bomber, hit 1/6, else d6
    assert big["expected_damage"] == pytest.approx((5 / 6) * 3.5, abs=1e-9)
    for b, ipc, dmg in ((1, 1, 0), (3, 2, 1), (6, 4, 7), (2, 3, 6), (4, 0, 0)):
        r = solve_sbr(b, ipc, dmg)
        assert sum(r["damage_distribution"].values()) == pytest.approx(1.0, abs=1e-9)
        assert max(r["damage_distribution"]) <= r["cap"]
    assert solve_sbr(3, 4, 8)["cap"] == 0 and solve_sbr(3, 4, 8)["p_cap_reached"] == 1.0
    with pytest.raises(ValueError):
        solve_sbr(-1, 3)


# ----------------------------------------------------------------------------------------------------------
# parsing
# ----------------------------------------------------------------------------------------------------------
def test_parse_and_format_roundtrip():
    rng = random.Random(5)
    for _ in range(50):
        army = tuple(rng.randint(0, 5) for _ in range(N))
        assert parse_army(fmt_army(army)) == army
    assert parse_army("") == Z and parse_army("  ,  ") == Z
    assert parse_army("2 inf, 3 inf")[INF] == 5


@pytest.mark.parametrize("bad", ["inf", "x inf", "-1 inf", "2", "2 dragon", "1.5 inf", "2inf"])
def test_parse_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_army(bad)


@pytest.mark.parametrize("bad", ["", "best", "order", "order:dragon", "optimal2"])
def test_parse_policy_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_policy(bad)


def test_unit_table_matches_the_rules():
    want = {"inf": (3, 1, 2), "art": (4, 2, 2), "tank": (6, 3, 3), "aa": (5, 0, 0), "ftr": (10, 3, 4),
            "bmb": (12, 4, 1), "sub": (6, 2, 1), "des": (8, 2, 2), "cru": (12, 3, 3), "car": (14, 1, 2),
            "bb": (20, 4, 4), "trn": (7, 0, 0)}
    for u in UNIT_LIST:
        if u.key in want:
            assert (u.cost, u.attack, u.defend) == want[u.key], u.key
    assert ORDER[-2:] == ("bbd", "trn")


# ----------------------------------------------------------------------------------------------------------
# command line
# ----------------------------------------------------------------------------------------------------------
def cli(*args):
    return subprocess.run([sys.executable, "-m", "aa1942.cli", *args], capture_output=True, text=True, timeout=120)


def test_cli_good_commands():
    r = cli("--att", "3 inf", "--def", "2 inf", "--no-spread")
    assert r.returncode == 0 and "Attacker wins" in r.stdout
    r = cli("battle", "--att", "2 des, 1 cru", "--def", "1 bb, 2 sub", "--method", "sim", "--sim", "500", "--seed", "1")
    assert r.returncode == 0 and "Monte Carlo" in r.stdout
    r = cli("amph", "--fleet", "1 trn", "--cargo", "2 inf", "--def-land", "1 inf", "--bombard", "1 bb")
    assert r.returncode == 0 and "Territory cleared" in r.stdout
    r = cli("sbr", "--bombers", "2", "--ipc", "3")
    assert r.returncode == 0 and "Expected damage" in r.stdout
    r = cli("rules")
    assert r.returncode == 0 and "[assumption]" in r.stdout


@pytest.mark.parametrize("args", [
    ["--att", "5 foo", "--def", "1 inf"],
    ["--att", "1 aa", "--def", "1 inf"],
    ["--att", "1 inf", "--def", "1 des"],
    ["--att", "1 inf", "--def", "1 inf", "--att-policy", "bogus"],
    ["--att", "1 inf", "--def", "1 inf", "--att-policy", "optimal", "--def-policy", "optimal"],
    ["amph", "--fleet", "1 trn", "--cargo", "3 inf", "--def-land", "1 inf"],
    ["sbr", "--bombers", "-1", "--ipc", "3"],
])
def test_cli_bad_input_is_a_clean_error_not_a_traceback(args):
    r = cli(*args)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
