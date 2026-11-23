# A&A 1942 (2nd Edition) Battle Calculator

Exact win percentages for **land, air, sea, amphibious and strategic-bombing**
battles. Every battle is fought to the end (no retreat or submerge in this
release). Rule sources and their verification status are in
[RULES.md](RULES.md); `python -m aa1942.cli rules` prints the same register.

## Usage

```bash
# land / air / sea battle ("battle" may be omitted)
python -m aa1942.cli --att "5 inf, 2 art, 3 tank" --def "6 inf, 2 tank, 1 aa"
python -m aa1942.cli battle --att "2 des, 1 cru, 1 ftr, 1 car" --def "1 bb, 2 sub, 1 trn"

# amphibious assault: sea battle -> bombardment -> land battle
python -m aa1942.cli amph --fleet "2 trn, 1 des" --cargo "2 inf, 2 tank" \
    --def-fleet "1 sub" --def-land "3 inf, 1 aa" --bombard "1 bb, 1 cru"

# strategic bombing raid (damage distribution, not win %)
python -m aa1942.cli sbr --bombers 3 --ipc 4 --damage 0
```

Unit names: `inf art tank aa ftr bmb sub des cru car bb trn` (long names and
plurals such as `infantry`, `destroyers`, `battleship` also work).

### Battle options

| Option | Meaning |
|---|---|
| `--att-policy`, `--def-policy` | Casualty choice: `standard` (default), `optimal`, or `order:inf,art,...` |
| `--method auto\|exact\|sim` | `auto` (default) solves exactly, but falls back to Monte Carlo if the exact solve exceeds `--exact-seconds` (default 20); results are then labelled **ESTIMATES** |
| `--sim N` | N Monte Carlo runs (a cross-check beside the exact result, or the run count when simulating) |
| `--seed S` | Reproducible simulation |
| `--no-spread` | Skip the casualty-policy comparison |

## Output

* Attacker win %, defender win %, mutual wipeout %, and **stalemate %** (neither
  side can hit the other, e.g. fighters against a lone submarine).
* Expected IPC lost by each side.
* A casualty-choice sensitivity table (standard vs. optimal for either side).
* The unverified assumptions in effect.

## Casualty policies

Casualty choice is a real player decision, so it is explicit:

* **standard** - lowest combat strength first (ties: cheaper first); an
  undamaged battleship absorbs a hit before anything else; transports last;
  an AA gun first on defense.
* **optimal** - best response: after the dice are known, a side picks the legal
  casualty set that maximises its own chance of winning. Only one side may be
  optimal per run (it responds to the other side's fixed policy).
* **order:a,b,c** - your own removal priority; unlisted units follow in standard order.

Legal-target rules are always enforced: submarine hits can't hit aircraft,
aircraft can't hit submarines unless their side has a destroyer, AA hits only
hit aircraft, and all legal hits must be applied.

## How it works

```
aa1942/units.py       unit registry, army tuples, parsing, costs
aa1942/casualties.py  hit pools, legal targets, standard/optimal/custom casualties
aa1942/combat.py      dice groups and exact hit distributions
aa1942/solver.py      Markov-chain battle solver (surprise strike, AA fire, rounds,
                      stalemate detection, time budget)
aa1942/amphibious.py  chained sea battle -> cargo loss -> bombardment -> land battle
aa1942/bombing.py     strategic bombing damage distribution
aa1942/simulate.py    independent dice-level Monte Carlo (cross-check + fallback)
aa1942/rules.py       rule register with sources and status
aa1942/cli.py         command line
tests/                44 tests: hand-solved cases, rule checks, exact-vs-simulation
```

An army is a tuple of counts in this order:
`(inf, art, tank, aa, ftr, bmb, sub, des, cru, car, bb, bbd, trn)`;
`bbd` is a battleship that has taken its first hit.

Each battle is a Markov chain over `(attacker army, defender army)`. For each
state the solver enumerates the exact distribution of hits per pool, applies
casualties, recurses with memoisation and rescales by `1 / (1 - P(stay))`.
Chained battles (amphibious) pass the whole outcome distribution forward, and
tests check that probabilities stay normalised at every link.

## Tests

```bash
python -m unittest discover -s tests -t .
```
