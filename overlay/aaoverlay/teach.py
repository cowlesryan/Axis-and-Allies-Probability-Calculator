"""Teaching the reader from collected panels - always with a human-confirmed label and a regression gate.

Flow for one collected panel (see ``collect.py``):

1. ``tasks_for`` lists what the reader could not decide: icons it does not know, counts it could not read, and
   icons it only just matched. Each task carries the candidate units for its die value (a unit rolling a 3 can only
   be a tank, fighter or cruiser) and, when exactly one unit fits, a *suggestion* (never applied automatically).
2. The user labels the tasks (unit and/or count).
3. ``teach`` adds the labelled icons and digits to a **staging copy** of the templates and runs the gate:
   every labelled sample (the training dataset, earlier taught samples, the new one) is read with the old and the
   new templates. The change is rejected if any panel is now read WRONG without a warning, if any panel that read
   exactly no longer does, or if the new sample itself does not read exactly as labelled. (Whether a panel is on
   screen at all does not depend on the templates, so the negative frames are checked by the reader tests instead.)
4. Only a passing change is copied into the live templates (after a dated backup). ``undo_last`` removes it again.
"""
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field


from . import collect as C
from . import dataset as D
from . import reader as R
from .templates import ROOT as TEMPLATE_ROOT, TemplateStore, icon_signature, iou
from aa1942.units import ORDER

STRONG = 0.88  # an existing template this close means the icon is already known
KEEP_BACKUPS = 10


class LabelError(ValueError):
    """The labels cannot be applied (missing, or a count that does not match the glyphs on screen)."""


@dataclass
class Task:
    row: int
    side: str                   # "att" or "dfn"
    idx: int                    # entry number within the side of the row
    die: object
    need_icon: bool
    need_count: bool
    confirm: bool               # the icon was recognised, but only just
    reader_unit: object
    reader_count: object
    candidates: list
    suggestion: object = None
    icon_x: tuple = (0, 0)
    cnt_x: tuple = (0, 0)
    y: tuple = (0, 0)

    def where(self):
        side = "Attacker" if self.side == "att" else "Defender"
        return f"{side}, die {self.die or '?'}, entry {self.idx + 1}"


def _candidates(side, die):
    table = R.ATT_BY_DIE if side == "att" else R.DEF_BY_DIE
    keys = table.get(die) if die else None
    cands = [k for k in ORDER if k not in ("bbd", "trn") and (keys is None or k in keys)]
    return cands


def tasks_for(item, store):
    """What the user needs to decide for a collected item. Returns ``(tasks, panel)``."""
    canvas = item.canvas()
    panel = R.read_panel(canvas, store)
    if panel is None:
        return [], None
    _cx, geoms = R.locate_rows(canvas)
    tasks = []
    for r, (row, g) in enumerate(zip(panel.rows, geoms)):
        for side, entries, segs, die in (("att", row.att, g.att_segs, row.die_att),
                                         ("dfn", row.dfn, g.def_segs, row.die_def)):
            for i, e in enumerate(entries):
                need_icon, need_count = e.unit is None, e.count is None
                confirm = (not need_icon) and e.icon_score < R.ICON_MIN_SCORE + C.BORDERLINE
                if not (need_icon or need_count or confirm):
                    continue
                cands = _candidates(side, die)
                suggestion = e.unit if e.unit else (cands[0] if len(cands) == 1 else None)
                tasks.append(Task(r, side, i, die, need_icon, need_count, confirm, e.unit, e.count, cands,
                                  suggestion, tuple(segs[2 * i]), tuple(segs[2 * i + 1]), (g.y0, g.y1)))
    return tasks, panel


def apply_labels(store, canvas, tasks, labels):
    """Add the labelled icons and digits to ``store``. ``labels[i]`` is ``{"unit": key, "count": n}`` for task i."""
    added = 0
    for i, task in enumerate(tasks):
        lab = labels.get(i, {})
        unit, count = lab.get("unit"), lab.get("count")
        y0, y1 = task.y
        if task.need_icon and not unit:
            raise LabelError(f"{task.where()}: choose the unit for the unrecognised icon.")
        if task.need_count and count is None:
            raise LabelError(f"{task.where()}: enter the count that could not be read.")
        if unit:
            if unit not in task.candidates:
                raise LabelError(f"{task.where()}: a {unit} cannot roll die {task.die}.")
            icon = canvas[y0:y1, task.icon_x[0]:task.icon_x[1]]
            sig = icon_signature(icon)
            if sig is None:
                raise LabelError(f"{task.where()}: could not outline the icon.")
            best = max((iou(sig, t) for u, _c, t in store.icons if u == unit), default=0.0)
            if best < STRONG:
                store.add_icon(unit, icon)
                added += 1
        if count is not None and task.need_count:
            glyphs = R.digit_glyphs(canvas, y0, y1, task.cnt_x[0] - 3, task.cnt_x[1] + 3)
            text = str(int(count))
            if len(glyphs) != len(text):
                raise LabelError(f"{task.where()}: the count shows {len(glyphs)} digit(s) but '{text}' has "
                                 f"{len(text)}.")
            for ch, glyph in zip(text, glyphs):
                d, s = store.match_digit(glyph)
                if d != ch or s < 0.8:
                    store.add_digit(ch, glyph)
                    added += 1
    return added


def labelled_army(panel, tasks, labels):
    """``(attackers, defenders, panel_aa)`` as dicts once every entry is known, else raises ``LabelError``."""
    by_pos = {(t.row, t.side, t.idx): labels.get(i, {}) for i, t in enumerate(tasks)}
    att, dfn = {}, {}
    for r, row in enumerate(panel.rows):
        for side, entries, army in (("att", row.att, att), ("dfn", row.dfn, dfn)):
            for i, e in enumerate(entries):
                lab = by_pos.get((r, side, i), {})
                unit = lab.get("unit") or e.unit
                count = lab.get("count") if lab.get("count") is not None else e.count
                if unit is None or count is None:
                    raise LabelError("Every entry needs a unit and a count before this panel can be taught.")
                army[unit] = army.get(unit, 0) + int(count)
    aa = dfn.pop("aa", 0)
    return att, dfn, aa


# -- the regression gate -------------------------------------------------------------------------------------
def _wanted(meta):
    t = meta["truth"]
    want_d = dict(t["defenders"])
    if t.get("panel_aa"):
        want_d["aa"] = t["panel_aa"]
    return dict(t["attackers"]), want_d


def status_of(store, name, meta, raw_dir):
    """``exact`` | ``flagged`` | ``wrong`` | ``none`` for one labelled sample read with ``store``."""
    panel = R.read_panel(D.load_canvas(name, meta, raw_dir), store)
    if panel is None:
        return "none"
    att, dfn, warnings = R.panel_to_armies(panel)
    if warnings:
        return "flagged"
    got = ({k: v for k, v in zip(ORDER, att) if v}, {k: v for k, v in zip(ORDER, dfn) if v})
    return "exact" if got == _wanted(meta) else "wrong"


def taught_dir():
    d = os.path.join(C.data_dir(), "taught")
    os.makedirs(d, exist_ok=True)
    return d


def all_samples(extra=()):
    """``[(name, meta, raw_dir)]``: the training dataset, earlier taught samples and ``extra``."""
    out = [(n, m, D.RAW) for n, m in D.samples()]
    td = taught_dir()
    out += [(n, m, td) for n, m in D.samples(td)]
    out += list(extra)
    return out


@dataclass
class Report:
    ok: bool
    message: str
    improved: int = 0
    unchanged: int = 0
    problems: list = field(default_factory=list)
    templates_added: int = 0


def gate(old_store, new_store, samples, must_be_exact=()):
    """Compare every labelled sample under the old and the new templates."""
    problems, improved, unchanged = [], 0, 0
    for name, meta, raw_dir in samples:
        before = status_of(old_store, name, meta, raw_dir)
        after = status_of(new_store, name, meta, raw_dir)
        if after == "wrong":
            problems.append(f"{name} would be read WRONG without a warning")
        elif before == "exact" and after != "exact":
            problems.append(f"{name} read exactly before and no longer does ({after})")
        elif after == "exact" and before != "exact":
            improved += 1
        else:
            unchanged += 1
    for name, meta, raw_dir in must_be_exact:
        if status_of(new_store, name, meta, raw_dir) != "exact":
            problems.append(f"the new sample {name} still does not read exactly as labelled")
    ok = not problems
    msg = ("Checked every known panel: nothing got worse." if ok else "Rejected: " + "; ".join(problems[:6]))
    return Report(ok, msg, improved, unchanged, problems)


# -- teaching ------------------------------------------------------------------------------------------------
def _log_path():
    return os.path.join(C.data_dir(), "teach_log.json")


def _load_log():
    try:
        with open(_log_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def _save_log(entries):
    with open(_log_path(), "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=1)


def _template_files(root):
    out = set()
    for sub in ("icons", "digits"):
        d = os.path.join(root, sub)
        if os.path.isdir(d):
            out |= {os.path.join(sub, f) for f in os.listdir(d) if f.endswith(".png")}
    return out


def teach(item, tasks, panel, labels, template_root=TEMPLATE_ROOT, collector=None):
    """Apply ``labels`` to ``item`` if (and only if) the gate passes. Returns a ``Report``."""
    canvas = item.canvas()
    try:
        att, dfn, aa = labelled_army(panel, tasks, labels)
    except LabelError as exc:
        return Report(False, str(exc))
    staging = tempfile.mkdtemp(prefix="aaoverlay_stage_")
    try:
        for sub in ("icons", "digits"):
            if os.path.isdir(os.path.join(template_root, sub)):
                shutil.copytree(os.path.join(template_root, sub), os.path.join(staging, sub))
        new_store = TemplateStore(root=staging)
        try:
            added = apply_labels(new_store, canvas, tasks, labels)
        except LabelError as exc:
            return Report(False, str(exc))
        name = "taught_" + item.id
        meta = {"name": name, "crop_box_1440p": item.meta["crop_box_1440p"], "panel": item.meta["panel"],
                "screen_height_1440p": item.meta.get("screen_height_1440p", R.BASE_H),
                "truth": {"attackers": att, "defenders": dfn, "panel_aa": aa, "attacker_power": "unknown",
                          "hidden": {"attackers": {}, "defenders": {}}},
                "source": {"game_id": "taught", "target": item.id}}
        extra = [(item.id, meta, item.folder)]  # the png on disk is named after the item
        report = gate(TemplateStore(root=template_root), new_store, all_samples(), must_be_exact=extra)
        report.templates_added = added
        if not report.ok:
            return report
        before_files = _template_files(template_root)
        backup = os.path.join(C.data_dir(), "template_backups", time.strftime("%Y%m%d_%H%M%S"))
        for sub in ("icons", "digits"):
            if os.path.isdir(os.path.join(template_root, sub)):
                shutil.copytree(os.path.join(template_root, sub), os.path.join(backup, sub))
        for rel in _template_files(staging) - before_files:
            os.makedirs(os.path.dirname(os.path.join(template_root, rel)), exist_ok=True)
            shutil.copy2(os.path.join(staging, rel), os.path.join(template_root, rel))
        new_files = sorted(_template_files(template_root) - before_files)
        td = taught_dir()
        shutil.copy2(os.path.join(item.folder, item.id + ".png"), os.path.join(td, name + ".png"))
        with open(os.path.join(td, name + ".json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)
        log = _load_log()
        log.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "item": item.id, "sample": name,
                    "files": new_files, "backup": backup, "template_root": template_root, "undone": False})
        _save_log(log)
        _prune_backups()
        (collector or C.Collector(enabled=False, folder=item.folder)).set_status(item.id, "taught")
        report.message = (f"Taught: {len(new_files)} new template(s) added. {report.improved} panel(s) now read "
                          "better, none got worse.")
        return report
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _prune_backups():
    root = os.path.join(C.data_dir(), "template_backups")
    if os.path.isdir(root):
        for old in sorted(os.listdir(root))[:-KEEP_BACKUPS]:
            shutil.rmtree(os.path.join(root, old), ignore_errors=True)


def undo_last(collector=None):
    """Remove the templates added by the most recent teach and put its panel back in the queue.
    Returns a message, or ``None`` when there is nothing to undo."""
    log = _load_log()
    for entry in reversed(log):
        if entry.get("undone"):
            continue
        root = entry.get("template_root", TEMPLATE_ROOT)
        for rel in entry["files"]:
            try:
                os.remove(os.path.join(root, rel))
            except OSError:
                pass
        for ext in (".png", ".json"):
            try:
                os.remove(os.path.join(taught_dir(), entry["sample"] + ext))
            except OSError:
                pass
        entry["undone"] = True
        _save_log(log)
        col = collector or C.Collector(enabled=False)
        col.set_status(entry["item"], "pending")
        return f"Undid the last teach ({len(entry['files'])} template(s) removed)."
    return None
