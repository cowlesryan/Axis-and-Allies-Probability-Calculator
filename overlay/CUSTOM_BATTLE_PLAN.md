# Custom battle window: plan and what was built

**Goal:** a hotkey (Ctrl+Alt+F8) that opens a window where the user types attacking and defending unit counts
and gets the odds, for every case the overlay and the calculator support.

## Cases to cover (from `service.py` and `aa1942/`)
| Case | Calculator entry point | Inputs |
|---|---|---|
| Land / air / sea battle | `solve`, `simulate` (via `service.compute`) | 13 unit types per side; attacker has no AA |
| Shore bombardment (ships + land units on the attacker side) | `solve_land_assault` (via `service.split_amphibious`) | same page; ships on the attacker side bombard |
| Whole amphibious assault | `solve_amphibious` | attacking fleet, cargo, air, bombarders, defending fleet, defending land |
| Strategic bombing raid | `solve_sbr` | bombers, IPC value, existing damage |
| Casualty policies | `parse_policy` (standard / optimal / order:...) | per side; both optimal is rejected |
| Staged results (estimate -> exact -> policy spread), time budget, cancel | `service.compute` | automatic |

## Design
* `custom.py`: `CustomBattleWindow` (Qt, focusable, excluded from capture) with a mode selector, spin-box
  grids per unit group, a policy row, a result pane and *Load from game panel / Swap sides / Clear*.
  A `Worker` thread runs the newest job and cancels older ones; input is debounced 250 ms.
* `render.py`: HTML rendering moved out of `app.py` so the live overlay and this window share it (+ `render_sbr`).
* `service.py`: `compute(..., att_policy, def_policy)`, `compute_full_amphibious`, `compute_sbr`.
* `app.py`: Ctrl+Alt+F8 toggles the window; the live overlay is paused while it is open.
* Errors the calculator raises (`ValueError`) are shown in the window, never crash the app.

## Also done in this change
The Ctrl+Alt+F11 "defending AA guns" hotkey and the `--aa` option were removed. Defending AA guns only matter
when the attackers have aircraft, and then the game lists them on the panel (die 1), which the reader reads.

## Tests
`tests/test_custom.py` drives the window headlessly (Qt offscreen) and checks every mode against the calculator's own
numbers, the rejection cases, policies, the panel loader, the background update path, and that the AA hotkey is gone.
