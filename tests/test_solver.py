"""Tests for parsing, land/air/sea solving, policies and the simulator.

Run from the project root with:
    python -m unittest discover -s tests -t .

Hand-solved cases are derived in the docstrings so each test names the rule it
protects. Monte Carlo comparisons use fixed seeds and tolerances of ~5 standard
errors, so they are deterministic and still catch rule mismatches.
"""
import unittest

from aa1942.casualties import ANY, NONAIR, NOSUB, apply_hits, default_order, targets
from aa1942.simulate import simulate
from aa1942.solver import OPTIMAL, STANDARD, parse_policy, solve
from aa1942.units import (AA, BB, BBD, DES, FTR, N, SUB, TRN, ZERO, army_cost,
                          combatants, parse_army)


def A(text):
    return parse_army(text)


def total(r):
    return r["attacker_win"] + r["defender_win"] + r["draw"] + r["stalemate"]


class ParseTests(unittest.TestCase):
    def test_parse_all_units(self):
        a = A("1 inf, 2 art, 3 tank, 4 aa, 5 ftr, 6 bmb, 7 sub, 8 des, 9 cru, 1 car, 2 bb, 3 trn")
        self.assertEqual(a, (1, 2, 3, 4, 5, 6, 7, 8, 9, 1, 2, 0, 3))

    def test_aliases_and_plurals(self):
        self.assertEqual(A("2 infantry, 1 armor"), A("2 inf, 1 tank"))
        self.assertEqual(A("1 fighter, 2 destroyers"), A("1 ftr, 2 des"))

    def test_bad_input(self):
        for bad in ("x inf", "3 elephants", "inf"):
            with self.assertRaises(ValueError):
                A(bad)

    def test_costs(self):
        # AA gun cost 5 and carrier attack 1 were confirmed by the project owner.
        self.assertEqual(army_cost(A("1 aa")), 5)
        self.assertEqual(army_cost(A("1 inf, 1 art, 1 tank, 1 ftr, 1 bmb")), 3 + 4 + 6 + 10 + 12)
        self.assertEqual(army_cost(A("1 sub, 1 des, 1 cru, 1 car, 1 bb, 1 trn")), 6 + 8 + 12 + 14 + 20 + 7)


class LandTests(unittest.TestCase):
    def test_reference_battle(self):
        """Regression: the original land-only calculator's reference result."""
        r = solve(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"))
        self.assertAlmostEqual(r["attacker_win"], 0.7174, places=4)
        self.assertAlmostEqual(r["defender_win"], 0.2608, places=4)
        self.assertAlmostEqual(r["draw"], 0.0218, places=4)

    def test_inf_vs_inf(self):
        """Per round: att hits 1/6, def 2/6. Only-att 4/36, only-def 10/36, both 2/36;
        the 20/36 no-hit rounds repeat, so divide by 16/36."""
        r = solve(A("1 inf"), A("1 inf"))
        self.assertAlmostEqual(r["attacker_win"], 4 / 16)
        self.assertAlmostEqual(r["defender_win"], 10 / 16)
        self.assertAlmostEqual(r["draw"], 2 / 16)

    def test_artillery_boost(self):
        with_art = solve(A("1 inf, 1 art"), A("2 inf"))["attacker_win"]
        no_art = solve(A("2 inf"), A("2 inf"))["attacker_win"]
        self.assertGreater(with_art, no_art)

    def test_defender_only_aa_is_captured(self):
        self.assertEqual(solve(A("1 inf"), A("2 aa"))["attacker_win"], 1.0)

    def test_sums_to_one(self):
        self.assertAlmostEqual(total(solve(A("6 inf, 3 art, 4 tank"), A("8 inf, 2 art, 2 tank, 1 aa"))), 1.0)


class AirTests(unittest.TestCase):
    def test_aa_fire_only_defender(self):
        """1 fighter vs a lone AA gun: the gun shoots once (1/6 hit). Survivor wins
        outright; if the fighter dies both sides are out (draw)."""
        r = solve(A("1 ftr"), A("1 aa"))
        self.assertAlmostEqual(r["attacker_win"], 5 / 6)
        self.assertAlmostEqual(r["draw"], 1 / 6)

    def test_aa_shot_limit(self):
        """One AA gun shoots at most 3 planes, so 4 fighters can never all be shot down:
        against a lone gun the attacker always wins."""
        r = solve(A("4 ftr"), A("1 aa"))
        self.assertAlmostEqual(r["attacker_win"], 1.0)

    def test_two_guns_six_shots(self):
        r = solve(A("6 ftr"), A("2 aa"))
        self.assertAlmostEqual(r["attacker_win"], 1 - (1 / 6) ** 6)

    def test_bomber_weak_defense(self):
        """A defending bomber (defense 1) is a poor defender: 1 tank beats it more often than not."""
        r = solve(A("1 tank"), A("1 bmb"))
        self.assertGreater(r["attacker_win"], r["defender_win"])


class SeaTests(unittest.TestCase):
    def test_sub_vs_sub_surprise(self):
        """No destroyers: both subs surprise-strike simultaneously (att hits 1/3, def 1/6)
        and then neither fires again. Only-att 5/18, only-def 2/18, both 1/18; the
        10/18 no-hit rounds repeat, so divide by 8/18."""
        r = solve(A("1 sub"), A("1 sub"))
        self.assertAlmostEqual(r["attacker_win"], 5 / 8)
        self.assertAlmostEqual(r["defender_win"], 2 / 8)
        self.assertAlmostEqual(r["draw"], 1 / 8)

    def test_sub_vs_destroyer_symmetric(self):
        """The destroyer cancels the surprise strike: both roll 2 or less, so the
        battle is symmetric: only-att 2/9, only-def 2/9, both 1/9 -> 2/5, 2/5, 1/5."""
        r = solve(A("1 sub"), A("1 des"))
        self.assertAlmostEqual(r["attacker_win"], 2 / 5)
        self.assertAlmostEqual(r["defender_win"], 2 / 5)
        self.assertAlmostEqual(r["draw"], 1 / 5)

    def test_air_cannot_hit_sub_without_destroyer(self):
        """A lone fighter vs a lone sub: nobody can hit anybody -> stalemate."""
        r = solve(A("1 ftr"), A("1 sub"))
        self.assertAlmostEqual(r["stalemate"], 1.0)

    def test_air_hits_sub_with_destroyer(self):
        """With a destroyer the fighter can hit the sub, so the attacker usually wins.
        A small stalemate chance remains: if the sub kills the destroyer first, the
        fighter loses its ability to hit the sub and neither side can score."""
        r = solve(A("1 ftr, 1 des"), A("1 sub"))
        self.assertGreater(r["attacker_win"], 0.5)
        self.assertGreater(r["stalemate"], 0.0)
        self.assertLess(r["stalemate"], 0.2)
        self.assertAlmostEqual(total(r), 1.0)
        # stalemate states must be exactly "fighter alone vs sub"
        for (a, d), p in r["final_states"].items():
            if p > 0 and combatants(a) > 0 and combatants(d) > 0:
                self.assertEqual((a[FTR], a[DES], d[SUB]), (1, 0, 1))

    def test_sub_cannot_hit_aircraft(self):
        """Defending subs cannot hurt a fighter, so a fighter + destroyer can't lose its
        fighter to subs; the destroyer is the only possible casualty."""
        r = solve(A("1 ftr, 1 des"), A("2 sub"))
        for (a, _), p in r["final_states"].items():
            if p > 0 and combatants(a) > 0:
                self.assertEqual(a[FTR], 1)

    def test_defenseless_transports(self):
        self.assertEqual(solve(A("1 des"), A("2 trn"))["attacker_win"], 1.0)
        # Aircraft can hit the transport (subs are off-limits), then only a stalemate remains.
        r = solve(A("1 ftr"), A("1 sub, 1 trn"))
        self.assertAlmostEqual(r["stalemate"], 1.0)
        for (a, d), p in r["final_states"].items():
            self.assertEqual(d[TRN], 0)  # the transport never survives

    def test_transport_not_cannon_fodder(self):
        """With a destroyer and a transport defending, one hit must kill the destroyer first."""
        a = apply_hits(A("1 des, 1 trn"), (1, 0, 0, 0), default_order(False))
        self.assertEqual((a[DES], a[TRN]), (0, 1))

    def test_battleship_two_hits(self):
        a = apply_hits(A("1 bb"), (1, 0, 0, 0), default_order(False))
        self.assertEqual((a[BB], a[BBD]), (0, 1))
        a = apply_hits(a, (1, 0, 0, 0), default_order(False))
        self.assertEqual(sum(a), 0)

    def test_hit_pool_restrictions(self):
        """FAQ: sub hit must land on the destroyer, cruiser hit on the fighter."""
        army = A("1 des, 1 ftr")
        self.assertEqual(targets(army, NONAIR), [DES])
        out = apply_hits(army, (1, 1, 0, 0), default_order(False))
        self.assertEqual(sum(out), 0)
        self.assertNotIn(FTR, targets(army, NONAIR))
        self.assertNotIn(SUB, targets(A("1 sub, 1 des"), NOSUB))

    def test_sea_sums_to_one(self):
        r = solve(A("2 des, 1 cru, 1 ftr, 1 car, 1 sub"), A("1 bb, 2 sub, 1 trn, 1 ftr"))
        self.assertAlmostEqual(total(r), 1.0)

    def test_mixed_domains_rejected(self):
        with self.assertRaises(ValueError):
            solve(A("1 inf, 1 des"), A("1 inf"))


class PolicyTests(unittest.TestCase):
    def test_optimal_never_worse(self):
        att, dfn = A("2 des, 1 cru, 1 ftr, 1 car"), A("1 bb, 2 sub, 1 ftr")
        std = solve(att, dfn)["attacker_win"]
        self.assertGreaterEqual(solve(att, dfn, OPTIMAL, STANDARD)["attacker_win"], std - 1e-9)
        self.assertLessEqual(solve(att, dfn, STANDARD, OPTIMAL)["attacker_win"], std + 1e-9)

    def test_both_optimal_rejected(self):
        with self.assertRaises(ValueError):
            solve(A("1 inf"), A("1 inf"), OPTIMAL, OPTIMAL)

    def test_custom_order(self):
        """Losing tanks before infantry (custom order) must hurt the attacker."""
        att, dfn = A("3 inf, 2 tank"), A("4 inf, 1 tank")
        std = solve(att, dfn)["attacker_win"]
        bad = solve(att, dfn, parse_policy("order:tank,inf"), STANDARD)["attacker_win"]
        self.assertLess(bad, std)

    def test_parse_policy(self):
        self.assertEqual(parse_policy("standard"), STANDARD)
        with self.assertRaises(ValueError):
            parse_policy("random")


class SimulationTests(unittest.TestCase):
    CASES = [
        ("5 inf, 2 art, 3 tank", "7 inf, 1 art, 2 tank, 1 aa"),
        ("3 tank, 2 ftr, 1 bmb, 4 inf", "5 inf, 2 art, 2 aa, 1 ftr"),
        ("2 des, 1 cru, 1 ftr, 1 car, 1 sub", "1 bb, 2 sub, 1 trn, 1 ftr"),
        ("3 sub, 1 des", "2 des, 1 bb"),
        ("2 bb, 1 ftr, 1 trn", "3 sub, 1 des, 1 car, 1 ftr"),
    ]

    def test_exact_matches_simulation(self):
        for att_s, def_s in self.CASES:
            att, dfn = A(att_s), A(def_s)
            exact = solve(att, dfn)
            sim = simulate(att, dfn, runs=40_000, seed=7)
            for k in ("attacker_win", "defender_win", "draw", "stalemate"):
                self.assertAlmostEqual(exact[k], sim[k], delta=0.012, msg=f"{att_s} vs {def_s}: {k}")


if __name__ == "__main__":
    unittest.main()
