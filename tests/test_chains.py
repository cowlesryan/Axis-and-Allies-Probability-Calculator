"""Tests for amphibious assaults, strategic bombing and the CLI/fallback."""
import io
import unittest
from contextlib import redirect_stdout

from aa1942.amphibious import solve_amphibious
from aa1942.bombing import solve_sbr
from aa1942.cli import main
from aa1942.solver import SolveBudgetExceeded, solve
from aa1942.units import ZERO, parse_army as A


def total(r):
    return r["attacker_win"] + r["defender_win"] + r["draw"] + r["stalemate"]


class AmphibiousTests(unittest.TestCase):
    def test_no_sea_battle_no_bombard_equals_land(self):
        """With an empty sea zone and no bombardment the assault is the plain land battle."""
        cargo, dland = A("2 inf, 2 tank"), A("3 inf, 1 art")
        r = solve_amphibious(A("2 trn"), cargo, ZERO, ZERO, dland, ZERO)
        land = solve(cargo, dland)
        self.assertAlmostEqual(r["attacker_win"], land["attacker_win"])
        self.assertAlmostEqual(r["p_landing"], 1.0)

    def test_bombardment_helps_attacker(self):
        cargo, dland = A("2 inf, 2 tank"), A("3 inf, 1 art")
        base = solve_amphibious(A("2 trn"), cargo, ZERO, ZERO, dland, ZERO)
        bomb = solve_amphibious(A("2 trn, 1 bb"), cargo, ZERO, ZERO, dland, A("1 bb"))
        self.assertGreater(bomb["attacker_win"], base["attacker_win"])

    def test_bombard_limited_by_landing_units(self):
        """Only as many ships bombard as units land: 1 cargo unit -> 1 ship even if 2 are offered."""
        one = solve_amphibious(A("1 trn"), A("1 inf"), ZERO, ZERO, A("2 inf"), A("2 bb"))
        cap = solve_amphibious(A("1 trn"), A("1 inf"), ZERO, ZERO, A("2 inf"), A("1 bb"))
        self.assertAlmostEqual(one["attacker_win"], cap["attacker_win"])

    def test_sea_battle_gates_landing(self):
        r = solve_amphibious(A("2 trn, 1 des"), A("2 inf, 2 tank"), ZERO, A("2 des"), A("1 inf"), ZERO)
        self.assertAlmostEqual(total(r), 1.0)
        self.assertLess(r["p_landing"], 1.0)
        self.assertAlmostEqual(r["sea_failure"], 1.0 - r["p_landing"])
        self.assertLessEqual(r["attacker_win"], r["p_landing"] + 1e-12)

    def test_cargo_lost_with_transport(self):
        """A defender with strong ships can sink transports; cargo that can't fit is lost,
        so the win chance must fall below the 'no sea battle' case."""
        calm = solve_amphibious(A("2 trn, 1 cru"), A("4 inf"), ZERO, ZERO, A("3 inf"), ZERO)
        hot = solve_amphibious(A("2 trn, 1 cru"), A("4 inf"), ZERO, A("1 des"), A("3 inf"), ZERO)
        self.assertLess(hot["attacker_win"], calm["attacker_win"])

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            solve_amphibious(A("1 trn"), A("3 inf"), ZERO, ZERO, A("1 inf"), ZERO)  # over capacity
        with self.assertRaises(ValueError):
            solve_amphibious(A("1 trn"), A("1 ftr"), ZERO, ZERO, A("1 inf"), ZERO)  # not land cargo


class BombingTests(unittest.TestCase):
    def test_one_bomber_cap_two(self):
        """1 bomber, IPC 2 (cap 4): survives 5/6, damage min(d6,4): mean (1+2+3+4+4+4)/6 = 3."""
        r = solve_sbr(1, 2)
        self.assertAlmostEqual(r["expected_damage"], 5 / 6 * 3)
        self.assertAlmostEqual(r["p_no_damage"], 1 / 6)
        self.assertAlmostEqual(sum(r["damage_distribution"].values()), 1.0)

    def test_existing_damage_reduces_headroom(self):
        fresh = solve_sbr(3, 2)["expected_damage"]
        hurt = solve_sbr(3, 2, existing_damage=3)["expected_damage"]
        self.assertLess(hurt, fresh)
        self.assertEqual(solve_sbr(2, 2, existing_damage=4)["expected_damage"], 0.0)

    def test_bombers_lost(self):
        self.assertAlmostEqual(solve_sbr(6, 5)["expected_bombers_lost"], 1.0)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            main(list(argv))
        return buf.getvalue()

    def test_backwards_compatible_land_command(self):
        out = self.run_cli("--att", "5 inf, 2 art, 3 tank", "--def", "6 inf, 2 tank, 1 aa", "--no-spread")
        self.assertIn("71.74%", out)

    def test_sea_command(self):
        out = self.run_cli("battle", "--att", "2 des, 1 ftr", "--def", "2 sub", "--no-spread")
        self.assertIn("Attacker wins", out)

    def test_amph_and_sbr_and_rules(self):
        out = self.run_cli("amph", "--fleet", "2 trn, 1 des", "--cargo", "2 inf, 2 tank",
                           "--def-land", "2 inf", "--bombard", "1 bb")
        self.assertIn("landing made", out)
        self.assertIn("Expected damage", self.run_cli("sbr", "--bombers", "2", "--ipc", "3"))
        self.assertIn("[owner]", self.run_cli("rules"))

    def test_large_battle_falls_back_to_simulation(self):
        """A battle that can't finish inside the budget is simulated and labelled ESTIMATES."""
        att, dfn = "30 inf, 15 art, 15 tank, 8 ftr", "35 inf, 12 art, 12 tank, 6 ftr, 2 aa"
        out = self.run_cli("battle", "--att", att, "--def", dfn, "--exact-seconds", "0.2",
                           "--sim", "300", "--seed", "3")
        self.assertIn("ESTIMATES", out)
        self.assertIn("Monte Carlo", out)

    def test_budget_exception_and_recovery(self):
        """solve(time_limit=...) raises, and a later unlimited solve still works."""
        att, dfn = A("30 inf, 15 art, 15 tank, 8 ftr"), A("35 inf, 12 art, 12 tank, 6 ftr, 2 aa")
        with self.assertRaises(SolveBudgetExceeded):
            solve(att, dfn, time_limit=0.1)
        self.assertAlmostEqual(total(solve(A("2 inf"), A("2 inf"))), 1.0)

    def test_spread_never_hangs_after_result(self):
        """Regression: after printing the result, the optimal-policy comparison used to run
        without a limit and could take minutes. It must now give up and say so."""
        import aa1942.cli as cli
        old = cli.SPREAD_BUDGET_SECONDS, cli.SPREAD_SECONDS
        cli.SPREAD_BUDGET_SECONDS, cli.SPREAD_SECONDS = 0.2, 60.0
        try:
            out = self.run_cli("battle", "--att", "10 inf, 4 art, 5 tank, 3 ftr",
                               "--def", "12 inf, 3 art, 3 tank, 2 ftr, 1 aa")
        finally:
            cli.SPREAD_BUDGET_SECONDS, cli.SPREAD_SECONDS = old
        self.assertIn("skipped", out)
        self.assertIn("Attacker wins", out)

    def test_simulation_respects_time_cap(self):
        from aa1942.simulate import simulate
        r = simulate(A("30 inf, 15 art, 15 tank, 8 ftr"), A("35 inf, 12 art, 12 tank, 6 ftr, 2 aa"),
                     runs=10_000_000, seed=1, max_seconds=0.5)
        self.assertLess(r["runs"], 10_000_000)
        self.assertGreaterEqual(r["runs"], 64)

    def test_small_battle_stays_exact_in_auto_mode(self):
        out = self.run_cli("battle", "--att", "3 inf, 1 art", "--def", "3 inf", "--no-spread")
        self.assertNotIn("ESTIMATES", out)


if __name__ == "__main__":
    unittest.main()
