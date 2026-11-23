"""Command-line interface for the A&A 1942 (2nd Edition) battle calculator.

Commands
--------
``battle``  land, air or sea battle (the default; ``battle`` may be omitted)::

    python -m aa1942.cli --att "5 inf, 2 art, 3 tank" --def "6 inf, 2 tank, 1 aa"
    python -m aa1942.cli battle --att "2 des, 1 cru, 1 ftr" --def "1 bb, 2 sub"

``amph``    amphibious assault (sea battle -> bombardment -> land battle)::

    python -m aa1942.cli amph --fleet "2 trn, 1 des" --cargo "2 inf, 2 tank" \
        --def-fleet "1 sub" --def-land "3 inf, 1 aa" --bombard "1 bb, 1 cru"

``sbr``     strategic bombing raid::

    python -m aa1942.cli sbr --bombers 3 --ipc 4

``rules``   print every rule used with its source and verification status.

Common battle options: ``--att-policy`` / ``--def-policy`` (``standard``,
``optimal`` or ``order:inf,art``), ``--method`` (``auto``, ``exact``, ``sim``),
``--sim N`` and ``--no-spread``.
"""
import argparse
import sys
import time

from .amphibious import solve_amphibious
from .bombing import solve_sbr
from .rules import rule_lines
from .simulate import simulate
from .solver import (OPTIMAL, STANDARD, SolveBudgetExceeded, parse_policy,
                     solve, validate)
from .units import army_cost, fmt_army, parse_army

DEFAULT_EXACT_SECONDS = 20.0  # ``--method auto``: exact-solve budget before simulating
SPREAD_SECONDS = 1.0          # the policy comparison is skipped if the exact solve took longer
SPREAD_BUDGET_SECONDS = 5.0   # time allowed for each optimal-policy comparison solve
DEFAULT_SIM_RUNS = 200_000
DEFAULT_SIM_SECONDS = 30.0    # simulation stops here when the run count was not requested


def _pct(x):
    return f"{x:7.2%}"


def _expected_losses(att, dfn, final_states):
    """Expected IPC lost by each side: start cost minus probability-weighted survivor cost."""
    exp_a = sum(p * army_cost(a) for (a, _), p in final_states.items())
    exp_d = sum(p * army_cost(d) for (_, d), p in final_states.items())
    return army_cost(att) - exp_a, army_cost(dfn) - exp_d


def _print_outcomes(res):
    print(f"Attacker wins : {_pct(res['attacker_win'])}")
    print(f"Defender wins : {_pct(res['defender_win'])}")
    print(f"Mutual wipeout: {_pct(res['draw'])}")
    if res.get("stalemate", 0) > 1e-12:
        print(f"Stalemate     : {_pct(res['stalemate'])}  (neither side can hit the other)")


def _cmd_battle(args):
    att, dfn = parse_army(args.att), parse_army(args.dfn)
    validate(att, dfn)
    pa, pd = parse_policy(args.att_policy), parse_policy(args.def_policy)
    print(f"Attacker: {fmt_army(att)}  ({army_cost(att)} IPC)")
    print(f"Defender: {fmt_army(dfn)}  ({army_cost(dfn)} IPC)")
    print(f"Casualty policy: attacker={pa[0]}, defender={pd[0]}\n")

    res = None
    started = time.monotonic()
    if args.method != "sim":
        # "auto" tries the exact solver under a time budget and falls back to
        # simulation; "exact" has no budget.
        limit = args.exact_seconds if args.method == "auto" else None
        try:
            res = solve(att, dfn, pa, pd, time_limit=limit)
        except SolveBudgetExceeded:
            print(f"Exact solve exceeded {args.exact_seconds:g}s (battle too large); "
                  "falling back to Monte Carlo. Results are ESTIMATES.\n")
    exact_seconds = time.monotonic() - started

    if res is None:
        runs = args.sim or DEFAULT_SIM_RUNS
        if "optimal" in (pa[0], pd[0]):
            print("Note: simulation only supports the standard policy; using standard.\n")
        # An explicit --sim N runs exactly N battles; otherwise stop at the time cap.
        cap = None if args.sim else DEFAULT_SIM_SECONDS
        s = simulate(att, dfn, runs, seed=args.seed, max_seconds=cap)
        _print_outcomes(s)
        print(f"\nMonte Carlo, {s['runs']:,} runs (attacker-win standard error +/-{s['stderr']:.2%})")
        return

    _print_outcomes(res)
    la, ld = _expected_losses(att, dfn, res["final_states"])
    print(f"\nExpected IPC lost - attacker: {la:.1f}, defender: {ld:.1f}")

    # The policy comparison costs two more exact solves, so only offer it when
    # the first one was quick.
    # The ``optimal`` policy is far slower than ``standard`` (it evaluates every
    # legal casualty set), so each comparison solve gets its own time budget and
    # is skipped, not waited for, if it runs out.
    if (not args.no_spread and exact_seconds <= SPREAD_SECONDS
            and pa == STANDARD and pd == STANDARD):
        sys.stdout.flush()
        print("\nCasualty-choice sensitivity (attacker win %):")
        print(f"  both standard             : {_pct(res['attacker_win'])}")
        for label, pol_a, pol_d in (("attacker chooses optimally", OPTIMAL, STANDARD),
                                    ("defender chooses optimally", STANDARD, OPTIMAL)):
            try:
                w = solve(att, dfn, pol_a, pol_d, time_limit=SPREAD_BUDGET_SECONDS)["attacker_win"]
                print(f"  {label}: {_pct(w)}")
            except SolveBudgetExceeded:
                print(f"  {label}: skipped (over {SPREAD_BUDGET_SECONDS:g}s; "
                      "run with --att-policy/--def-policy optimal to wait for it)")
            sys.stdout.flush()

    if args.sim:
        s = simulate(att, dfn, args.sim, seed=args.seed)
        print(f"\nMonte Carlo check ({args.sim:,} runs, standard policy): "
              f"att {_pct(s['attacker_win']).strip()}, def {_pct(s['defender_win']).strip()}, "
              f"draw {_pct(s['draw']).strip()}")
    kind = "sea" if any(att[i] or dfn[i] for i in range(6, 13)) else "land"
    print("\nAssumptions in effect (unverified):")
    for line in rule_lines(kind):
        if line.startswith("[assumption]"):
            print("  " + line)


def _cmd_amph(args):
    fleet, cargo = parse_army(args.fleet), parse_army(args.cargo)
    air, dfl = parse_army(args.air), parse_army(args.def_fleet)
    dland, bomb = parse_army(args.def_land), parse_army(args.bombard)
    pa, pd = parse_policy(args.att_policy), parse_policy(args.def_policy)
    res = solve_amphibious(fleet, cargo, air, dfl, dland, bomb, pa, pd)
    print(f"Attacking fleet : {fmt_army(fleet)}")
    print(f"Cargo           : {fmt_army(cargo)}")
    print(f"Attacking air   : {fmt_army(air)}")
    print(f"Bombardment     : {fmt_army(bomb)}")
    print(f"Defending fleet : {fmt_army(dfl)}")
    print(f"Defending land  : {fmt_army(dland)}\n")
    print(f"Sea zone cleared and landing made: {_pct(res['p_landing'])}")
    print(f"Territory cleared (attacker wins): {_pct(res['attacker_win'])}")
    print(f"Defender holds                   : {_pct(res['defender_win'])}")
    print(f"Mutual wipeout                   : {_pct(res['draw'])}")
    if res["stalemate"] > 1e-12:
        print(f"Stalemate                        : {_pct(res['stalemate'])}")
    print(f"Failed before landing (at sea)   : {_pct(res['sea_failure'])}")
    print("\nAssumptions in effect (unverified):")
    for line in rule_lines("amph"):
        if line.startswith("[assumption]"):
            print("  " + line)


def _cmd_sbr(args):
    r = solve_sbr(args.bombers, args.ipc, args.damage)
    print(f"{args.bombers} bomber(s) vs complex in a {args.ipc}-IPC territory "
          f"(existing damage {args.damage}, cap {r['cap']})\n")
    print(f"Expected damage          : {r['expected_damage']:.2f} IPC")
    print(f"Chance of no damage      : {_pct(r['p_no_damage'])}")
    print(f"Chance damage cap reached: {_pct(r['p_cap_reached'])}")
    print(f"Expected bombers lost    : {r['expected_bombers_lost']:.2f} "
          f"(~{r['expected_bomber_cost_lost']:.1f} IPC)\n")
    print("Damage distribution:")
    for dmg, p in r["damage_distribution"].items():
        if p >= 0.0005:
            print(f"  {dmg:3d}: {_pct(p)}")


def _cmd_rules(_args):
    print("\n".join(rule_lines()))


def build_parser():
    ap = argparse.ArgumentParser(description="A&A 1942 2nd Edition battle calculator")
    sub = ap.add_subparsers(dest="cmd")

    b = sub.add_parser("battle", help="land, air or sea battle")
    b.add_argument("--att", required=True, help='attacker, e.g. "5 inf, 2 art, 3 tank"')
    b.add_argument("--def", dest="dfn", required=True, help='defender, e.g. "6 inf, 2 tank, 1 aa"')
    b.add_argument("--att-policy", default="standard", help="standard | optimal | order:inf,art,...")
    b.add_argument("--def-policy", default="standard", help="standard | optimal | order:inf,art,...")
    b.add_argument("--method", choices=("auto", "exact", "sim"), default="auto")
    b.add_argument("--sim", type=int, default=0, help="Monte Carlo runs (cross-check, or run count with --method sim)")
    b.add_argument("--exact-seconds", type=float, default=DEFAULT_EXACT_SECONDS,
                   help="auto mode: seconds the exact solver may run before falling back to simulation")
    b.add_argument("--seed", type=int, default=None, help="seed for reproducible simulation")
    b.add_argument("--no-spread", action="store_true", help="skip the casualty-policy comparison")
    b.set_defaults(func=_cmd_battle)

    a = sub.add_parser("amph", help="amphibious assault")
    a.add_argument("--fleet", default="", help="attacking ships incl. loaded transports")
    a.add_argument("--cargo", default="", help="land units on the transports (2 per transport)")
    a.add_argument("--air", default="", help="attacking aircraft joining the land battle")
    a.add_argument("--def-fleet", default="", help="defending ships in the sea zone")
    a.add_argument("--def-land", required=True, help="defenders in the coastal territory")
    a.add_argument("--bombard", default="", help='bombarding ships, e.g. "1 bb, 1 cru"')
    a.add_argument("--att-policy", default="standard")
    a.add_argument("--def-policy", default="standard")
    a.set_defaults(func=_cmd_amph)

    s = sub.add_parser("sbr", help="strategic bombing raid")
    s.add_argument("--bombers", type=int, required=True)
    s.add_argument("--ipc", type=int, required=True, help="IPC value of the territory")
    s.add_argument("--damage", type=int, default=0, help="damage already on the complex")
    s.set_defaults(func=_cmd_sbr)

    r = sub.add_parser("rules", help="print the rule register")
    r.set_defaults(func=_cmd_rules)
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv = ["battle"] + argv  # backwards compatible: no subcommand means "battle"
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return
    try:
        args.func(args)
    except ValueError as exc:  # bad user input (unknown unit, AA in the attack, overloaded transports, ...)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
