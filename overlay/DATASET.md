# Training data for the panel reader

The reader recognises icons and digits it has been taught. Samples are collected from the
game's **offline** (Hot Seat / Single Player) campaigns, where the save file gives exact
ground truth, so no hand labelling is needed. Multiplayer games are never used.

## The three tools (run with `%USERPROFILE%\aaoverlay-venv\Scripts\python.exe`)

| Tool | What it does |
|---|---|
| `tools\harvest.py snap --region <id>` | Crops the attack panel on screen into `dataset\raw\NNNN_tag.png` plus a `.json` sidecar with the save's truth, the reader's guess and whether they agree. Refuses if any other window overlaps the panel, so nothing unrelated is ever captured. `harvest.py regions` lists regions under attack. |
| `tools\learn.py` | Teaches new icons and digits from the samples, only when the match to the truth is unambiguous (one entry; distinct counts; or a clearly best assignment by elimination). Ambiguous rows are listed, never guessed. |
| `tools\make_scenario.py <name>` | Writes an **edited copy** of the offline save with custom pending attacks (see below). |

`aaoverlay/savetruth.py` reads the save: attackers = units in the current phase's pending
`moveUnit` list; defenders = enemy-alliance units whose `location` is the region;
AA guns and transports are reported separately because the game hides them behind
"VIEW NON COMBATANTS".

## Editing the save (verified working on 2026-11-23)

The offline save is a JSON file in `%APPDATA%\axisandalliesonline-desktop\local-games`. The game
accepts an edited **copy** with a custom attack, and shows it on the map and in the panel. What
it took:

1. Never edit a game that is open: the running game rewrites its save every few seconds. Write a
   new file while the game is closed.
2. The copy needs its own identity. The campaign list keys on the top-level `"id"` field inside
   the JSON (not the file name), so a copy that kept the original `id` simply opened the original.
   `make_scenario.py` sets a new `id`, `name`, `state.phaseStorage.gameId` and the four timestamps.
   Fields that identify the owner are left exactly as they were.
3. A pending attack needs both parts: `moveUnit` entries in `state.phaseStorage.delta` (path lists
   the steps *after* the start, e.g. `["karelia_ssr"]`) and a record in `state.pendingEncounters`
   with attacker/defender unit ids. A pending move does not change a unit's `location` until the
   battle resolves.
4. The game reads the folder only at startup. Exit to desktop, write the copy, relaunch via Steam
   (`steam://rungameid/898920`), then pick the copy by name in Single Player.

Safety: originals are backed up to `dataset\backup_saves` first; the copy is a separate game you
can delete in-game (gear icon on its card) or by deleting its `.json`. Offline games only.

## Findings that matter

* The offline dice mode is `random` (labelled "Standard Dice"), matching the calculator.
* Each pending battle stores the game's own `prediction` (win %), which can be used later to
  compare the calculator against the game.
* Panel position follows the territory, so panels can end up under another window. Pan the map or
  move that window; `harvest.py` will refuse rather than capture it.
* `learn.py` needs the panel's digits to have been seen. Still unseen: 0, 3, 7, 8.

## The scenario generator (built 2026-11-23)

`tools\scenario_gen.py --all` writes one scenario save per power, each with 13 battles (8 land,
5 sea) made of **brand-new units**: attackers placed in a friendly neighbouring region and moved
to the target, defenders owned by enemy powers, counts from lists that together contain every
digit, units chosen so panels have 1-4 rows, and a different `prediction` per battle so every
outlook label and centre-line colour appears. It validates each battle's truth before writing and
records a manifest in `dataset\scenarios`. `scenario_gen.py --clean` deletes the copies it made
(only saves whose name starts with "Overlay "). Verified: the game lists them with the right
power's emblem and loads them, with all battles on the map.

Capture cycle (done through the screen-control tool; one battle at a time):
1. `tools\find_markers.py` prints where the crossed-swords markers are (template match) and,
   with `--arrow`, where the collapsed bottom bar's expand arrow is.
2. Click a marker, then VIEW at (440, 643) in the 1456x819 frame; panels near the bottom of the
   screen sit behind the bottom bar, so click its collapse arrow (1157, 647) afterwards.
3. `tools\harvest.py snap --manifest <scenario id>` identifies *which* battle the panel is from
   its layout and counts (no need to know which marker was clicked), checks nothing overlaps the
   panel, and saves the labelled crop.
4. `tools\learn.py` teaches new icons and digits; `tools\coverage.py` reports coverage and, most
   importantly, how many readings are **wrong without a warning** (must be 0).

Gotchas found: the map wraps horizontally (pan it to move a panel clear of another window); the
panel list is read from the encounter record, not from every unit in the region; a defender's AA
guns appear on the panel (die 1) only when the attackers include aircraft; counts can contain
digits the reader has never seen, which it can misread as a similar digit until that digit is
taught, so all ten digits must be learned before trusting it.

## Status

* 9 labelled samples (Soviet attacker, German attacker) read: 6 exact, 3 flagged, 0 wrong without
  a warning. A battle the reader had not been taught from (Kwangtung) read exactly.
* All ten digits 0-9 now have templates.
* No samples yet for UK, Japan or US as attacker; no carrier, and no submarine/cruiser defender.
* An honest held-out test (a scenario kept out of learning) has not been run yet.
* Four of the Soviet scenario's battles (Finland, Manchuria, several sea zones) are not captured.

## Capture automation, hold-out and negatives (latest status)

Tools (all drive only OFFLINE "Overlay <power>" scenario copies through a restricted click helper: game window
only, Esc aborts, never ONLINE/DELETE GAME, no account files touched):

* `tools/scenario_gen.py` – coverage-driven generator of scenario copies (new units, friendly-origin attackers).
  Outlook labels are deliberately **not** read or varied (every battle uses one fixed prediction).
* `tools/ui_driver.py`, `tools/session.py` (launch/open campaign verified by game id/exit/main menu),
  `tools/capture_run.py` (closed-loop: click marker, expand bar, VIEW, snap), `tools/run_all.py`.
* `tools/learn.py` – learns icons/digits from labelled samples. Scenario ids listed in `dataset/holdout.txt`
  are **never learned from** (currently the UK scenario).
* `tools/coverage.py --holdout <id>` – reports the held-out scenario separately.
* `tools/negatives.py` – saves game frames with no attack panel to `dataset/negatives/`
  (test: the reader must return "no panel" on all of them).

Findings: panel entries are per owner (units of different powers, and AA per owner, are separate entries);
defending AA appears at die 1 when attackers include aircraft.

Status: 47 labelled samples across Soviet, Germany, Japan, UK (held out) and US attackers; 41 exact, 2 flagged,
0 wrong-without-warning on the training side; held-out UK: 3 exact, 1 flagged, 0 wrong. 30 negatives pass.
Not yet captured: a few battles near screen edges (burma, finland, sea_zone_3, ...), the "VIEW NON COMBATANTS"
view, damaged-battleship display, amphibious/bombing panels.

## Round 2: small battles, one-row panels, more negatives

* **New reader capability:** one-row panels (a single unit type per side, e.g. "5 inf + 6 art vs 7 inf") have only
  short centre-line stubs at the top and bottom of the row. `reader.find_thin_lines` + `_has_stubs` locate them
  (the long-line detector is tried first, so multi-row panels are unaffected). This was a real gap found by the
  first small-battle capture: the old reader saw no panel at all.
* `scenario_gen.py --small` builds small battles (counts 1-9, one or two panel rows); enemy units are stripped from
  the land target in the scenario copy so the panel stays small. Four scenarios (UK, Japan, US, Soviet) captured.
  The new UK scenario was scored unseen before learning from it: 2 exact, 4 flagged, 0 wrong (the one-row
  detector was not there yet for most of them; after the fix all read).
* **Negatives:** 48 frames in `dataset/negatives/`: 30 map pans (`negatives.py`) and 18 game screens saved with
  `tools/neg_snap.py` (game menu, graphics/gameplay settings, purchased units, map notes, war diary, war report,
  end-phase tooltip, Combat Move Summary (has "VS" rows), combat phase map, battle intro, combat board
  before/after rolls, retreat/press-on, zoomed retreat map). The Claude chat window is blanked out of a frame
  if it overlaps; the settings *Account* tab is never captured (it shows personal details).
* **Not done:** amphibious assaults and strategic bombing raids. The save format for loaded transports
  (`cargo`, `transportDrop`) and for raids (`pendingRaids`) cannot be reproduced safely without real gameplay
  (guessing risks the game crashing on load). Amphibious battles use the same attack panel (it lists the
  encounter's units), so the reader should handle them; a bombing raid uses a separate raid board, which is
  not an attack panel.
* Status: 87+ labelled samples (layouts: 35 one-row, 10 two-row, 16 three-row, 26 four-row); 81 exact, 2 flagged,
  0 wrong-without-warning; held-out original UK scenario 4/4 exact. 117 tests pass.

## Amphibious assaults (Phase 1 findings, verified by playing one in an offline copy)

Played through the game's own screens (load transport by dragging a unit onto the sea zone, drag the transport
onto the enemy coast to offload, drag a battleship onto the coast to bombard) and read the save after each step.
(The raw save copies were deleted: they contain account fields.)

* Save structure: `boardTransport`/`transportDrop`/`addShipToBombardment` entries in `phaseStorage.delta`; the
  encounter holds `amphibiousAttackers` (offloaded units), `bombarderSlots` ({sea_zone: n}, one per offloaded unit)
  and `bombardingUnits` ({sea_zone: [ship ids]}). Offloaded units keep their old `location` and get `droppedFrom` /
  `transporter`; defending AA is in `initialDefenders` (hidden behind VIEW NON COMBATANTS).
* **The attack panel is the normal panel.** Offloaded land units are ordinary attacker entries; each bombarding
  ship is an ordinary attacker entry with its normal attack die (battleship on die 4). The existing reader read all
  three panels seen (plain, with the non-combatants column open, with a bombarding battleship) exactly, no changes.
  The amphibious flag only shows on the combat board (anchor icon), not on the panel.
* `savetruth.truth_for` now adds bombarding ships to `attackers` and returns `amphibious`, `bombarders`,
  `bombard_slots`. Sample `0088_amph` is labelled from it.
* Overlay: `service.split_amphibious` treats ships on the attacker side of a land battle as shore bombardment
  (battleships and cruisers; one ship per landing unit) and runs the calculator's bombardment + land battle
  (`solve_land_assault`); ships with no land unit on either side stay an ordinary sea battle. A real sea battle that
  precedes a landing (defender ships in the zone) is a separate panel. Not covered: transports in the sea battle.

### Amphibious sample generator
`python tools\scenario_gen.py --power <power> --amphibious` (code in `tools/amphibious_gen.py`) builds one
amphibious assault per available route (home region -> empty sea zone -> enemy coastal territory), with
new transports carrying 1-8 land units, 0-2 bombarding ships, optional aircraft and varied defenders, in the
exact structure the game writes. The game loads these saves and draws them like a real assault.
Captured: UK 6/8 (held out: never learned from; 5 exact, 1 flagged, 0 wrong), Germany 1/5, Soviet 3/4, Japan 2/3.
The uncaptured ones are far-north/edge targets (finland, norway, iceland) or markers the detector does not see.
No US routes exist (no US-owned region touches a sea zone next to an enemy coast in the start position).

### Amphibious capture complete (20 battles)
UK 8/8 (held out), Germany 5/5, Soviet 4/4, Japan 3/3. Far-north and edge targets needed the map panned first
(`capture_run.run(..., pan=(dx, dy))`, or `python tools\capture_run.py <id> <tag> dx,dy`). One reader bug found on
the way (Yunnan): a stray vertical-line candidate could pass the bar/dice checks on a single row and hide the real
centre line, so a panel with an attacker-only row above a defender-only row read as nothing. `locate_rows` now
takes the candidate that explains the most rows.
