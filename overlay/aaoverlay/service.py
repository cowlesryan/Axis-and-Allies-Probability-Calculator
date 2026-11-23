"""Runs the aa1942 calculator for the overlay: fast estimate first, then exact.

``compute`` is a generator-style helper that calls ``emit(result_dict)`` as
better answers become available:

1. a quick Monte Carlo estimate (about a second) so something shows immediately;
2. the exact solution if it finishes inside the time budget;
3. the casualty-policy comparison, if the exact solve was fast.

Every emitted dict has the same keys so the UI can render any stage.
"""
import time

import aaoverlay  # noqa: F401  (puts the aa1942 package on sys.path)
from aa1942.rules import rule_lines
from aa1942.simulate import simulate
from aa1942 import solver as _solver
from aa1942.solver import (OPTIMAL, STANDARD, SolveBudgetExceeded, SolveCancelled,
                           cancel_event, solve, solve_land_assault)
from aa1942.units import (ART, BB, BBD, CRU, INF, SEA_IDX, TANK, army_cost, fmt_army)

QUICK_SIM_SECONDS = 1.0
SPREAD_AFTER_EXACT_SECONDS = 1.0
SPREAD_BUDGET_SECONDS = 4.0
SIM_FALLBACK_SECONDS = 2.0   # cap on the Monte Carlo fallback (the exact attempt has already used its budget)
# The live overlay shows the casualty-choice comparison only after the setup has been left alone for a moment, and
# with a short budget per solve: it is an extra, and it used to burn up to 8 s of CPU on every new battle.
LIVE_SPREAD_DELAY_SECONDS = 1.5
LIVE_SPREAD_BUDGET_SECONDS = 1.0
LIVE_QUICK_EXACT_SECONDS = 0.15


def request_cancel():
    """Ask any running solve to stop as soon as possible."""
    cancel_event.set()


def _base(att, dfn):
    return {
        "attacker": fmt_army(att), "defender": fmt_army(dfn),
        "att_ipc": army_cost(att), "def_ipc": army_cost(dfn),
        "stage": "start", "method": None, "outcomes": None, "stderr": None, "runs": None,
        "loss_att": None, "loss_def": None, "spread": None,
        "assumptions": [l[len("[assumption] "):] for l in rule_lines() if l.startswith("[assumption]")],
        "notes": [],
    }


def _losses(att, dfn, final_states):
    ea = sum(p * army_cost(a) for (a, _), p in final_states.items())
    ed = sum(p * army_cost(d) for (_, d), p in final_states.items())
    return army_cost(att) - ea, army_cost(dfn) - ed


def _outcomes(res):
    return {k: res[k] for k in ("attacker_win", "defender_win", "draw", "stalemate")}


GROUND = (INF, ART, TANK)


def split_amphibious(att, dfn):
    """``(att_land, n_bb, n_cru, ignored)`` if the panel is an amphibious land battle, else ``None``.

    In the game's attack panel, ships listed on the attacker's side of a *land* battle are the ships
    assigned to shore bombardment (they appear with their normal attack die). A battle with ships but
    no land units on either side is an ordinary sea battle. ``ignored`` counts ships that cannot bombard.
    """
    ships = [i for i in SEA_IDX if att[i]]
    if not ships or not (any(att[i] for i in GROUND) or any(dfn[i] for i in GROUND)):
        return None
    att_land = tuple(0 if i in SEA_IDX else n for i, n in enumerate(att))
    n_ground = sum(att[i] for i in GROUND)
    n_bb = min(att[BB] + att[BBD], n_ground)
    n_cru = min(att[CRU], n_ground - n_bb)
    ignored = sum(att[i] for i in ships) - att[BB] - att[BBD] - att[CRU]
    return att_land, n_bb, n_cru, ignored


def compute_amphibious(att, dfn, split, emit, exact_seconds=8.0, pa=STANDARD, pd=STANDARD):
    """Exact solution of the land battle with the offshore bombardment that precedes it."""
    att_land, n_bb, n_cru, ignored = split
    out = _base(att, dfn)
    out["notes"].append(f"Amphibious assault: {n_bb} battleship(s) and {n_cru} cruiser(s) bombard first "
                        "(one ship per landing unit); the land battle follows.")
    if ignored:
        out["notes"].append(f"{ignored} attacking ship(s) that cannot bombard were ignored.")
    if (att[BB] + att[BBD] + att[CRU]) > n_bb + n_cru:
        out["notes"].append("More ships than landing units: the extra ships cannot bombard.")
    emit(dict(out))
    _solver._deadline[0] = time.monotonic() + exact_seconds
    try:
        res = solve_land_assault(att_land, dfn, n_bb, n_cru, pa, pd)
    except SolveBudgetExceeded:
        out.update(stage="final", method="estimate")
        out["notes"].append(f"Battle too large to solve exactly with bombardment in {exact_seconds:g}s; "
                            "showing the land battle without bombardment (a lower bound for the attacker).")
        emit(dict(out))
        try:
            res = solve(att_land, dfn, pa, pd, time_limit=exact_seconds)
            out.update(outcomes=_outcomes(res))
        except SolveBudgetExceeded:
            pass
        except SolveCancelled:
            return out
        emit(dict(out))
        return out
    except SolveCancelled:
        return out
    finally:
        _solver._deadline[0] = None
    la, ld = _losses(att_land, dfn, res["final_states"])
    out.update(stage="final", method="exact", outcomes=_outcomes(res), loss_att=la, loss_def=ld)
    emit(dict(out))
    return out


def compute(att, dfn, emit, exact_seconds=8.0, att_policy=STANDARD, def_policy=STANDARD,
            spread_delay=0.0, spread_budget=SPREAD_BUDGET_SECONDS, quick_exact=0.0):
    """Compute and emit progressively better results. Returns the last emitted dict.

    ``quick_exact`` (seconds) first tries an exact solve that short, skipping the rough estimate when it succeeds.

    ``spread_delay`` waits that long (abandoning the comparison if the job is cancelled meanwhile) before the
    casualty-choice comparison starts; ``spread_budget`` is the time limit of each of its solves.

    ``att_policy`` / ``def_policy`` are casualty policies (see ``solver.parse_policy``). Simulation only knows
    the standard policy, so with another policy there is no quick estimate and, if the battle is too large
    to solve exactly, the fallback estimate uses the standard policy (and says so).
    """
    cancel_event.clear()
    custom = (att_policy, def_policy) != (STANDARD, STANDARD)
    split = split_amphibious(att, dfn)
    if split:
        return compute_amphibious(att, dfn, split, emit, exact_seconds, att_policy, def_policy)
    out = _base(att, dfn)
    stop = cancel_event.is_set
    try:
        res, t0 = None, time.monotonic()
        # Stage 0: most battles solve exactly in well under a tenth of a second, faster than a simulation would
        # give its rough first answer, so try that first (the work done is kept for the full solve if it fails).
        if quick_exact and not custom:
            try:
                res = solve(att, dfn, att_policy, def_policy, time_limit=quick_exact)
            except SolveBudgetExceeded:
                pass
        if res is None:
            # Stage 1: quick estimate (standard policy only).
            if not custom:
                est = simulate(att, dfn, runs=2000, seed=None, max_seconds=QUICK_SIM_SECONDS, should_stop=stop)
                if cancel_event.is_set():
                    return out
                out.update(stage="estimate", method="estimate", outcomes=_outcomes(est),
                           stderr=est["stderr"], runs=est["runs"])
                emit(dict(out))
            else:
                emit(dict(out))

            # Stage 2: exact solve under a budget.
            t0 = time.monotonic()
            try:
                res = solve(att, dfn, att_policy, def_policy, time_limit=exact_seconds)
            except SolveBudgetExceeded:
                more = simulate(att, dfn, runs=20000, max_seconds=min(exact_seconds, SIM_FALLBACK_SECONDS),
                                should_stop=stop)
                if cancel_event.is_set():
                    return out
                out.update(stage="final", method="estimate", outcomes=_outcomes(more),
                           stderr=more["stderr"], runs=more["runs"])
                out["notes"].append(f"Battle too large for an exact answer in {exact_seconds:g}s: Monte Carlo estimate.")
                if custom:
                    out["notes"].append("The estimate uses the standard casualty policy (simulation supports no other).")
                emit(dict(out))
                return out
        took = time.monotonic() - t0
        la, ld = _losses(att, dfn, res["final_states"])
        out.update(stage="exact", method="exact", outcomes=_outcomes(res), stderr=None, runs=None,
                   loss_att=la, loss_def=ld)
        emit(dict(out))

        # Stage 3: casualty-choice sensitivity (only if cheap).
        if took <= SPREAD_AFTER_EXACT_SECONDS and not custom:
            if spread_delay and cancel_event.wait(spread_delay):
                return out  # the setup changed while waiting: a newer job replaces this one
            spread = {"standard": res["attacker_win"]}
            for label, pa, pd in (("attacker_optimal", OPTIMAL, STANDARD), ("defender_optimal", STANDARD, OPTIMAL)):
                try:
                    spread[label] = solve(att, dfn, pa, pd, time_limit=spread_budget)["attacker_win"]
                except SolveBudgetExceeded:
                    spread[label] = None
            out.update(stage="final", spread=spread)
        else:
            out.update(stage="final")
        emit(dict(out))
    except SolveCancelled:
        pass
    return out


def compute_full_amphibious(fleet, cargo, air, def_fleet, def_land, bombard, emit, exact_seconds=8.0,
                            att_policy=STANDARD, def_policy=STANDARD):
    """Whole amphibious assault: sea battle (if the defender has ships) -> bombardment -> land battle.

    The attacker's side lists ``fleet`` (ships including loaded transports), ``cargo`` (land units on them,
    two per transport), ``air`` (aircraft joining the land battle) and ``bombard`` (ships that bombard).
    """
    from aa1942.amphibious import solve_amphibious
    cancel_event.clear()
    att_all = tuple(a + b + c + d for a, b, c, d in zip(fleet, cargo, air, bombard))
    dfn_all = tuple(a + b for a, b in zip(def_fleet, def_land))
    out = _base(att_all, dfn_all)
    out["attacker"] = "; ".join(f"{label}: {fmt_army(x)}" for label, x in
                                (("fleet", fleet), ("cargo", cargo), ("air", air), ("bombard", bombard)) if any(x))
    out["defender"] = "; ".join(f"{label}: {fmt_army(x)}" for label, x in
                                (("fleet", def_fleet), ("land", def_land)) if any(x))
    out["notes"].append("Whole assault: sea battle (if any) -> bombardment -> land battle. Transports carry two land "
                        "units each; ones lost at sea cannot land.")
    emit(dict(out))
    _solver._deadline[0] = time.monotonic() + exact_seconds
    try:
        res = solve_amphibious(fleet, cargo, air, def_fleet, def_land, bombard, att_policy, def_policy)
    except SolveBudgetExceeded:
        out.update(stage="final", method="exact")
        out["notes"].append(f"Too large to solve exactly in {exact_seconds:g}s; try fewer units.")
        emit(dict(out))
        return out
    except SolveCancelled:
        return out
    finally:
        _solver._deadline[0] = None
    out.update(stage="final", method="exact", outcomes=_outcomes(res),
               extra_rows=[("Sea zone cleared, landing made", res["p_landing"]),
                           ("Failed at sea before landing", res["sea_failure"])])
    emit(dict(out))
    return out


def compute_sbr(bombers, ipc_value, damage=0):
    """Strategic bombing raid: damage distribution (see ``aa1942.bombing``)."""
    from aa1942.bombing import solve_sbr
    return solve_sbr(bombers, ipc_value, damage)
