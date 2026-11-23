# Phase 0 findings (discovery)

Date: 2026-11-23. Game: Axis & Allies 1942 Online 1.0.19 (Electron 6.1.5 wrapper around a web client).
Monitor: 2560x1440, game fullscreen.

## Results

| Question | Answer |
|---|---|
| Can the fullscreen game be captured? | **Yes.** A plain Windows screen grab (`tools/capture_screen.ps1`) captures the game at native 2560x1440. |
| Can an overlay draw over the fullscreen game? | **Yes.** A transparent, click-through, no-activate topmost window (`tools/overlay_visibility_test.ps1`) drew over the game, and the game stayed the foreground window (focus was not stolen). See `reference/overlay_visibility_test.png`. |
| Is the attack information readable on screen? | **Yes, and in a convenient form** (below). |
| Do the game's rules match the calculator? | **Yes for land and air** on the one battle seen: defender bomber 1, artillery/infantry 2, tank 3, fighter 4; attacker infantry boosted to 2 by artillery. Sea rules not yet checked. |
| Does the calculator agree with the game? | The game labelled the Caucasus battle **IMPLAUSIBLE**; the calculator gives the attacker 0.13%. Consistent. |

## The attack panel (what the overlay will read)

When an attack is set up the game shows a panel titled with the province name
(here `CAUCASUS`), a red outcome label (`IMPLAUSIBLE`), and four rows. Each row
groups units by the die value they hit on; attackers (red) are on the left and
defenders (grey/blue) on the right, split by a red `VS` line. Each unit is an
icon followed by `xN`, next to a die showing the hit value.

```
Row (die)  Attacker                          Defender
1          infantry x26                      bomber x1
2          infantry x1 (boosted), art x1     artillery x4, infantry x11
3          tank x1, fighter x2               tank x9
4          -                                 fighter x5
```

Reconstructing armies: attacker infantry = 26 + 1 (boosted) = 27, artillery 1,
tank 1, fighters 2; defender bomber 1, artillery 4, infantry 11, tank 9,
fighters 5. The bottom bar also lists committed attackers (`x27` infantry,
artillery, tank, `x2` planes) and a `BATTLE 31` total.

Pixel positions in the 2560x1440 capture (panel only; verify per screen):
panel x 857-1712, y 305-920; row 1 y~485, row 2 y~610, row 3 y~740, row 4 y~865;
die icons at x~1247 (attacker side) and x~1322 (defender side).

## Gaps found

1. **AA guns are not shown** in the panel (they never roll). Defender AA must come
   from the map/territory view or a manual toggle in the overlay.
2. **Unit art is recoloured per power** (the Soviet attacker is red, the German
   defender grey). Recognition must work across all five powers: use shape or
   colour-normalised matching plus per-power templates.
3. Only one battle has been captured. Still needed: a sea battle (subs,
   destroyers, battleships, carriers, transports), an amphibious assault, a
   defender with AA guns, other powers' colours, and a strategic bombing raid.
4. The game's dice mode (true random vs. low luck) is unconfirmed; it changes
   every percentage. Check the game's settings or Beamdog's FAQ.
5. `reference/*.png` are full-screen captures and also contain the Claude app
   window; crop to the game panel before using them as templates or sharing them.

## Not changed

No game files were touched or read beyond directory listings and two small
launcher scripts (`index.js`, `preload.js`) for structure. No hooks, injection,
debug ports or memory reading were used, and none are planned.
