# Collect and teach: plan and what was built

**Question:** should the overlay collect screenshots of scenarios it hasn't seen and learn from them?
**Answer:** yes, but collect automatically (opt-in) and learn only from labels a person confirmed, behind a regression
gate. Online games have no save file, so there is no ground truth; an automatic label would be the reader's own guess,
and a wrong guess could make it return a wrong army without a warning, the one failure the project must not have.

## Built
| Piece | File | What it does |
|---|---|---|
| Shared screen helpers | `windows.py` | `crop_box`, `occluding_windows` (moved out of `tools/harvest.py`) |
| Collector | `collect.py` | opt-in; keeps flagged or borderline panels (crop only, nothing if another window overlaps), dedupes, caps at 150, local folder |
| Teach logic | `teach.py` | tasks per uncertain entry with die-restricted candidates and unique-fit suggestions; staging templates; regression gate; backup; undo; taught samples |
| Teach window | `teachwin.py` | Ctrl+Alt+F7: queue, panel preview, unit/count pickers, Teach, Discard, Undo, collection checkbox |
| Wiring | `app.py`, `__main__.py` | hotkey, `--collect`, live template reload, overlay paused while the window is open |
| Tests | `tests/test_teach.py` | off by default and persistence, dedupe, candidates by die, correct teach + undo, **wrong label rejected**, no label no learning, window end-to-end |

## Gate rules
Reject if any labelled sample is read wrong without a warning, any exact sample is no longer exact, or the new sample
does not read exactly as labelled.
