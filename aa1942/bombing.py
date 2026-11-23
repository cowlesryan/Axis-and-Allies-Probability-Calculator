"""Strategic bombing raid on an industrial complex (1942 2nd Edition).

Rules modelled (sources in RULES.md)
------------------------------------
* The complex shoots once at each attacking bomber; a roll of 1 destroys it.
* Each surviving bomber rolls one die; the result is the damage it inflicts.
* An industrial complex cannot hold more total damage than twice the IPC value
  of its territory (existing damage counts toward that cap).
* Escort and interception by fighters are *not* part of the 1942 raid rules as
  far as the available sources show, so they are not modelled.

The result is a damage distribution rather than a win percentage.
"""
from .combat import binom, convolve

BOMBER_COST = 12


def _sum_of_d6(k):
    """pmf (list indexed by total) of the sum of ``k`` six-sided dice."""
    dist = [1.0]
    for _ in range(k):
        dist = convolve(dist, [0.0] + [1 / 6] * 6)
    return dist


def solve_sbr(bombers, ipc_value, existing_damage=0):
    """Distribution of damage from one strategic bombing raid.

    Args:
        bombers: number of attacking bombers.
        ipc_value: IPC value of the territory holding the complex.
        existing_damage: damage markers already on the complex.

    Returns:
        dict with ``damage_distribution`` ({points: probability}),
        ``expected_damage``, ``p_no_damage``, ``p_cap_reached``,
        ``expected_bombers_lost`` and ``expected_bomber_cost_lost``.
    """
    if bombers < 0 or ipc_value < 0 or existing_damage < 0:
        raise ValueError("bombers, ipc_value and existing_damage must be >= 0")
    cap = max(0, 2 * ipc_value - existing_damage)
    dmg = {}
    for survivors, p in enumerate(binom(bombers, 5 / 6)):
        for total, q in enumerate(_sum_of_d6(survivors)):
            if q == 0:
                continue
            applied = min(total, cap)
            dmg[applied] = dmg.get(applied, 0.0) + p * q
    exp_lost = bombers / 6
    return {
        "damage_distribution": dict(sorted(dmg.items())),
        "expected_damage": sum(k * v for k, v in dmg.items()),
        "p_no_damage": dmg.get(0, 0.0),
        "p_cap_reached": dmg.get(cap, 0.0) if cap > 0 else 1.0,
        "cap": cap,
        "expected_bombers_lost": exp_lost,
        "expected_bomber_cost_lost": exp_lost * BOMBER_COST,
    }
