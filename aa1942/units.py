"""Unit registry for Axis & Allies 1942 (Second Edition).

Every unit the calculator knows about lives in ``UNIT_LIST``. All other
modules refer to units by their *index* in that list, so adding or changing a
unit happens in exactly one place.

Army representation
-------------------
An *army* is a tuple of non-negative ints with one slot per registry entry, in
``ORDER``::

    (inf, art, tank, aa, ftr, bmb, sub, des, cru, car, bb, bbd, trn)

Tuples are hashable, which lets the solver memoize on them. ``bbd`` is an
internal slot for a *damaged* battleship (a battleship that has taken its
first of two hits). Users normally never type it.

Stats come from the 1942 2nd Edition rulebook as cross-checked against the
official FAQ and rules-comparison sheet (see ``rules.py`` for per-rule
sources). Two values were confirmed directly by the project owner: AA gun cost
is 5 IPC and the aircraft carrier attacks at 1.
"""
from dataclasses import dataclass

LAND, AIR, SEA = "land", "air", "sea"


@dataclass(frozen=True)
class Unit:
    """Static description of one unit type.

    Attributes:
        key: short name used on the command line and in army strings.
        name: human readable name.
        cost: purchase cost in IPCs.
        attack: a die roll <= this value scores a hit when attacking (0 = never rolls).
        defend: a die roll <= this value scores a hit when defending (0 = never rolls).
        domain: ``LAND``, ``AIR`` or ``SEA``.
        source: where the numbers were verified.
    """
    key: str
    name: str
    cost: int
    attack: int
    defend: int
    domain: str
    source: str = "rulebook copy + FAQ"


UNIT_LIST = (
    Unit("inf", "Infantry", 3, 1, 2, LAND),      # attack 2 when paired with artillery (see combat.py)
    Unit("art", "Artillery", 4, 2, 2, LAND),
    Unit("tank", "Tank", 6, 3, 3, LAND),
    Unit("aa", "Anti-aircraft gun", 5, 0, 0, LAND, "cost confirmed by project owner"),
    Unit("ftr", "Fighter", 10, 3, 4, AIR),
    Unit("bmb", "Bomber", 12, 4, 1, AIR),
    Unit("sub", "Submarine", 6, 2, 1, SEA),
    Unit("des", "Destroyer", 8, 2, 2, SEA),
    Unit("cru", "Cruiser", 12, 3, 3, SEA),
    Unit("car", "Aircraft carrier", 14, 1, 2, SEA, "attack confirmed by project owner"),
    Unit("bb", "Battleship", 20, 4, 4, SEA),
    Unit("bbd", "Battleship (damaged)", 20, 4, 4, SEA, "internal state: 1 hit taken"),
    Unit("trn", "Transport", 7, 0, 0, SEA),
)

ORDER = tuple(u.key for u in UNIT_LIST)
N = len(ORDER)
IDX = {k: i for i, k in enumerate(ORDER)}
INF, ART, TANK, AA, FTR, BMB, SUB, DES, CRU, CAR, BB, BBD, TRN = range(N)
assert IDX["trn"] == TRN and IDX["bbd"] == BBD  # guards the constant block above

ZERO = (0,) * N
AIR_IDX = frozenset(i for i, u in enumerate(UNIT_LIST) if u.domain == AIR)
LAND_IDX = frozenset(i for i, u in enumerate(UNIT_LIST) if u.domain == LAND)
SEA_IDX = frozenset(i for i, u in enumerate(UNIT_LIST) if u.domain == SEA)
# Units that never roll to hit and therefore cannot keep a side "alive":
# the AA gun (defense 0 in land combat) and the transport (no combat values).
NONCOMBAT_IDX = (AA, TRN)
COMBAT_IDX = tuple(i for i in range(N) if i not in NONCOMBAT_IDX)

ALIASES = {
    "infantry": "inf", "i": "inf",
    "artillery": "art", "a": "art",
    "armor": "tank", "t": "tank", "tanks": "tank",
    "aagun": "aa", "aa gun": "aa", "aaa": "aa", "antiaircraft": "aa",
    "fighter": "ftr", "fighters": "ftr", "f": "ftr",
    "bomber": "bmb", "bombers": "bmb", "b": "bmb",
    "submarine": "sub", "submarines": "sub", "subs": "sub",
    "destroyer": "des", "destroyers": "des", "dd": "des",
    "cruiser": "cru", "cruisers": "cru",
    "carrier": "car", "carriers": "car", "aircraft carrier": "car",
    "battleship": "bb", "battleships": "bb",
    "damaged battleship": "bbd",
    "transport": "trn", "transports": "trn", "tr": "trn",
}


def resolve_unit(name):
    """Return the registry index for a unit name or alias (case-insensitive).

    Raises:
        ValueError: if the name matches no unit.
    """
    n = name.strip().lower()
    n = ALIASES.get(n, n)
    if n not in IDX and n.endswith("s"):  # tolerate other plurals, e.g. "inf s"
        n = n[:-1]
    if n not in IDX:
        raise ValueError(f"Unknown unit name: {name!r}")
    return IDX[n]


def parse_army(text):
    """Convert ``"5 inf, 2 art, 3 tank"`` into an army tuple.

    Groups are comma separated ``"<count> <unit name>"``; repeated units add up;
    an empty string gives an empty army.

    Raises:
        ValueError: on a malformed group or unknown unit.

    Example:
        >>> parse_army("2 inf, 1 ftr")[:6]
        (2, 0, 0, 0, 1, 0)
    """
    counts = [0] * N
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        num, _, name = part.partition(" ")
        if not num.isdigit() or not name.strip():
            raise ValueError(f"Can't parse unit group: {part!r}")
        counts[resolve_unit(name)] += int(num)
    return tuple(counts)


def army_cost(army):
    """Total IPC cost of an army (damaged battleships count at full cost)."""
    return sum(n * u.cost for n, u in zip(army, UNIT_LIST))


def combatants(army):
    """Number of units that can fight (everything except AA guns and transports).

    A side with zero combatants is defeated: remaining AA guns are captured or
    destroyed, and remaining transports are "defenseless" (they are removed
    automatically once the enemy can still roll dice at them).
    """
    return sum(army[i] for i in COMBAT_IDX)


def fmt_army(army):
    """Readable form of an army, omitting zero counts (``"none"`` if empty)."""
    return ", ".join(f"{n} {k}" for n, k in zip(army, ORDER) if n) or "none"
