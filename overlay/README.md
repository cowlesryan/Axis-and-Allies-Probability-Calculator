# A&A 1942 Online - battle-odds overlay

A transparent, click-through window that shows the full output of the
[`aa1942`](../README.md) calculator next to the game's attack panel while you set
up an attack. It is **passive**: it only looks at screenshots of your screen. It
never touches the game's files, process, memory, network traffic or debug ports.

## Run it

The Python packages live in a virtual environment **outside OneDrive** at
`%USERPROFILE%\aaoverlay-venv` (it is about 900 MB because of PySide6, so it should not sync).
Double-click or run `run_overlay.bat`, or from this folder:

```bash
%USERPROFILE%\aaoverlay-venv\Scripts\python.exe -m aaoverlay
```

To recreate the environment on another machine:

```bash
python -m venv %USERPROFILE%\aaoverlay-venv
%USERPROFILE%\aaoverlay-venv\Scripts\python.exe -m pip install -r requirements.txt
```

Start the game, open an attack setup (the battle panel with the dice), and the
overlay appears beside the panel. Hotkeys (polled, so the game still gets its keys):

| Keys | Action |
|---|---|
| Ctrl+Alt+F7 | open / close the **Teach window** (label panels the reader could not read) |
| Ctrl+Alt+F8 | open / close the **custom battle window** (type in any units) |
| Ctrl+Alt+F9 | show / hide the overlay |
| Ctrl+Alt+F10 | force a re-read of the panel |
| Ctrl+Alt+F12 | quit |

Options: `--interval 0.4` (seconds between reads), `--exact-seconds 8` (exact-solve
budget before falling back to a Monte Carlo estimate),
`--image FILE` (read a saved screenshot instead of the screen), `--debug`,
`--no-exclude` (let screenshots include the overlay; only for verification).

## What it shows

Attacker and defender armies with IPC value, **EXACT** or **ESTIMATE +/-x%** (a quick
Monte Carlo result appears within about a second and is replaced by the exact one),
attacker/defender/mutual-wipeout/stalemate percentages with a bar, expected IPC lost,
the casualty-choice comparison (standard vs. optimal for either side), and the
assumptions in effect. If the panel cannot be read completely it says exactly what
failed instead of guessing.

## How it works

```
screen (mss) -> reader.read_panel -> armies -> service.compute -> overlay (Qt)
                  find centre line     (aa1942 solver: estimate, then exact)
                  find bars + edges
                  read dice (pips)
                  match icons (silhouette IoU) and count digits
```

* The panel is located by its coloured centre line (any colour; it follows the game's
  outlook label) and the bars' grey margin, so it works wherever the map pans it and
  at any 16:9 resolution (screenshots are rescaled to 1440p first).
* A change must appear in two consecutive reads before it is acted on, so half-drawn
  animation frames never flash warnings or trigger solves.
* The overlay window is excluded from screen capture (`WDA_EXCLUDEFROMCAPTURE`), so the
  reader never sees its own text, and it is placed beside the panel, never over it.
* Stale solves are cancelled when you change the setup.

## Known limits (honest list)

1. **Icons are learned, not built in.** Each power draws its units in its own colour and
   pose, so the reader recognises only what it has been taught. Taught so far (see
   `aaoverlay/templates/`): Soviet infantry, artillery, tank, fighter; German bomber,
   artillery, infantry, tank, fighter, submarine, cruiser; and a UK-coloured battleship.
   Anything else shows "unrecognised icon" and the overlay refuses to guess.
2. **Digits 0, 3, 7 and 8 have not been seen yet**, so counts containing them are
   reported as unreadable until taught. Digits 1, 2, 4, 5, 6, 9 work, including across
   different digit sizes.
3. **Non-combatants are hidden by the game** behind "VIEW NON COMBATANTS" (transports,
   AA guns). They only affect the odds when the attackers have aircraft, and then the game
   lists the defending AA guns on the panel itself (die 1), so the overlay reads them.
4. The calculator's rule assumptions still apply (see [RULES.md](../RULES.md)); the dice
   mode of the online game (true random vs. low luck) is unconfirmed.
5. Only the primary monitor is read.

## Teaching the reader a new screen

1. Set up an attack that shows the units you want to teach, then capture it at full
   resolution: `powershell -File tools\capture_screen.ps1 -Path reference\<name>.png`.
2. Add an entry to `LABELLED` in `tools/seed_templates.py`: one `(attacker, defender)`
   pair of `[(unit, count), ...]` lists per panel row, top to bottom.
3. Run `%USERPROFILE%\aaoverlay-venv\Scripts\python.exe tools\seed_templates.py`. It checks that the detected
   layout matches your labels before saving anything.

Good captures to take next: every power attacking with each unit type (destroyer,
carrier, bomber, battleship, all five colours), counts containing 0, 3, 7 and 8, and an
amphibious assault.

## Tests

```bash
%USERPROFILE%\aaoverlay-venv\Scripts\python.exe -m pytest   # 17 tests: reader on real captures, scaling,
                                          # unknown-icon handling, service, rendering
```


## Custom battle window (Ctrl+Alt+F8)
A normal window where you type unit counts for both sides; results update as you type. The live overlay is
paused while it is open. Esc or Ctrl+Alt+F8 closes it. It covers everything the overlay and the calculator do:

* **Battle: land, air or sea** - all 13 unit types per side (the attacker cannot bring AA guns), damaged
  battleships and transports included. Ships on the attacker's side of a land battle are shore bombardment, exactly
  as on the game's attack panel (one ship per landing unit; the land battle follows).
* **Amphibious assault** - the whole chain (sea battle, bombardment, land battle): attacking fleet with loaded
  transports, cargo (2 per transport), aircraft, bombarding ships, defending fleet and defending land units.
* **Strategic bombing raid** - bombers, the territory's IPC value and damage already on the complex.
* **Casualty policies** per side: standard, optimal (best response; not both sides at once) or a custom removal order.
* Buttons: *Load from game panel* (fills the battle page with the attack panel currently being read), *Swap sides*, *Clear*.
Input the calculator cannot fight (for example land and sea defenders together, or one side empty) is explained in the window.
See [CUSTOM_BATTLE_PLAN.md](CUSTOM_BATTLE_PLAN.md) for the design.


## Teaching the reader new screens (Ctrl+Alt+F7)
For panels the reader has not seen (a new icon, an unusual layout, a game update) the overlay can keep them and you
can teach it, safely:

1. **Collect (opt-in).** Tick the box in the Teach window, or start with `--collect`. Whenever the reader warns about
   a panel (unrecognised icon, unreadable count) or only just matches an icon, it saves a crop of the panel (never
   the whole screen, and never when another window overlaps it) to `%LOCALAPPDATA%aoverlay\collected`. Nothing is
   uploaded. Off by default.
2. **Label.** Open the window. For each icon or count the reader was unsure about, pick the unit (only units that can
   roll that die are offered; a lone possibility is suggested, never applied for you) and type any unreadable count.
3. **Teach.** The new icons/digits go into a staging copy of the templates first. Every labelled panel in the
   dataset and every panel you taught before is re-read with old and new templates. The change
   is rejected if any panel is now read wrongly without a warning, if a panel that read exactly no longer does, or if
   the new panel does not read as you labelled it. Only a passing change
   is applied (after a dated backup in `template_backups`), and the live reader reloads at once.
4. **Undo last teach** removes it again. Panels you taught are kept in `taught/` and join the check from then on.

Online games have no save file to check against, so the reader never labels anything itself: no label, no learning.
See [TEACH_PLAN.md](TEACH_PLAN.md).


## Installer (for friends)
`powershell -File build.ps1` runs the tests, builds the app with PyInstaller (`aaoverlay.spec`) and compiles
`installer.iss` with Inno Setup into `%USERPROFILE%\aaoverlay-build\installer\AAOdds-<version>-setup.exe` (about 90 MB).
It installs per user (no admin rights) and can add a desktop shortcut or start with Windows. Needs Windows 10 2004+,
the game on the primary 16:9 monitor. Setup tools: `pip install pyinstaller` in the venv and Inno Setup 6.

Where things go when installed: the program in `%LOCALAPPDATA%\Programs\AAOdds`; your data (a writable copy of the
templates that Teach changes, collected panels, taught samples, settings, logs) in `%LOCALAPPDATA%\aaoverlay`, which
uninstalling keeps. Logs: `%LOCALAPPDATA%\aaoverlay\logs`. Bump `__version__` in `aaoverlay/__init__.py` for a release.
The build is unsigned, so Windows SmartScreen may warn ("More info" > "Run anyway").

The build leaves out what the app never uses (OpenCV's video library, Qt QML/Quick/PDF/SVG/keyboard modules, Qt
translations) and stores the bundled dataset with lossless maximum PNG compression: see `packaging_trim.py`. Installed
size is about 217 MB and the installer about 69 MB. `tests/test_frozen.py` guards this: it runs the app's own
`--selftest` (OpenCV, NumPy, Qt, calculator, reader, Teach gate) from the exe, the installed app and the source tree and
requires identical results, runs the exe on real frames, and checks that nothing needed was removed.
