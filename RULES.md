# Rule Register

Every rule the calculator depends on, with where it came from. The same data is
in `aa1942/rules.py` (`python -m aa1942.cli rules`).

**Sources read**
* Official *Axis & Allies 1942 FAQ* (Sept 3, 2014) - **official-faq**
* Official *1942 and Revised Editions Rules Comparison* sheet - **comparison**.
  It describes the 1942 line as first printed; where it disagrees with the 2nd
  Edition (e.g. AA gun cost 6) the project owner's confirmation wins.
* A third-party text copy of the 2nd Edition rulebook (RulesPal) -
  **rulebook-copy**. Not independently verified.
* The official Wizards rulebook PDF (`media.wizards.com/2015/rules/AA_1942_rules.pdf`)
  is over the fetch tool's 10 MB limit, so it was **not** read directly.
* **owner** - confirmed by the project owner (AA gun costs 5 IPC; carrier attacks at 1).

## Unit stats

| Unit | Cost | Attack | Defend | Notes |
|---|---|---|---|---|
| Infantry | 3 | 1 (2 with artillery) | 2 | |
| Artillery | 4 | 2 | 2 | boosts one infantry |
| Tank | 6 | 3 | 3 | |
| AA gun | 5 (owner) | - | 0 | fires at aircraft only |
| Fighter | 10 | 3 | 4 | |
| Bomber | 12 | 4 | 1 | |
| Submarine | 6 | 2 | 1 | surprise strike |
| Destroyer | 8 | 2 | 2 | |
| Cruiser | 12 | 3 | 3 | bombards at 3 |
| Aircraft carrier | 14 | 1 (owner) | 2 | |
| Battleship | 20 | 4 | 4 | two hits; bombards at 4 |
| Transport | 7 | 0 | 0 | never rolls |

## Rules by status

### Confirmed by the official FAQ
* Subs cannot hit aircraft; aircraft cannot hit subs unless their side has a
  destroyer in the battle.
* An enemy destroyer cancels surprise strike, submerging and the "cannot be hit
  by air" ability. Destroyers of allies that are not in the battle do nothing.
* All legal hits must be applied (sub hit on the destroyer, cruiser hit on the fighter).
* Transports never roll, are casualties only when nothing else can be hit, and
  are removed automatically when only one side can still roll dice at them.
* Bombardment can hit aircraft.

### Confirmed by the comparison sheet
* Bombardment is a pre-combat step; bombarded defenders **can fire back** (this
  corrects an early assumption that they could not).
* Bombarding ships are limited to the number of seaborne land units.
* Total damage on an industrial complex is capped at twice the territory's IPC value.
* A defending fighter on a sunk carrier may move one space to land after combat
  (landing is not modelled; the fighter simply survives the battle).

### From the rulebook copy (not independently verified)
* AA gun: up to three shots, each aircraft shot at once, hit on a 1, fired once
  before the first round.
* Sub surprise strike: attacking subs hit on <= 2, defending subs on <= 1.
* Battleships take two hits; bombard at 4. Cruisers bombard at 3.
* Strategic bombing: the complex shoots at each bomber (hit on 1); each surviving
  bomber rolls one die of damage.

### Modelling assumptions (no source settles these)
* **Casualty policy.** Standard = weakest first. The AA gun may be a casualty and
  is taken first.
* **Optimal policy scope.** A best response to the other side's fixed policy;
  surprise-strike, bombardment and AA casualties always use the standard order.
* **Amphibious.** Bombarding ships are listed separately and do not also fight at
  sea; attacking aircraft go straight to the land battle; each transport carries 2
  land units and surplus cargo of sunk transports is lost, weakest first; a
  stalemate at sea means no landing; AA guns fire from the pre-bombardment army.
* **Bombing.** Escorts and interceptors are not modelled.
* **Not modelled:** retreat, submerge, battleship repair timing, carrier-landing
  decisions, convoy/other optional rules.

## How a rule becomes code

1. Add or change it in `aa1942/rules.py` with its source and status.
2. Implement it in one place (`units.py` for stats, `casualties.py` for target
   legality, `combat.py` for dice, `solver.py` for step order).
3. Add a test whose docstring names the rule and, where possible, derives the
   expected number by hand.
4. Confirm the exact solver and `simulate.py` still agree.
