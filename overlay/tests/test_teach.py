"""Collecting unreadable panels and teaching the reader from them (human labels + regression gate)."""
import os
import shutil

import pytest

from aaoverlay import collect as C
from aaoverlay import dataset as D
from aaoverlay import reader as R
from aaoverlay import teach as T
from aaoverlay.templates import ROOT, TemplateStore
from aa1942.units import ORDER

SAMPLES = list(D.samples())
pytestmark = pytest.mark.skipif(len(SAMPLES) < 20, reason="needs the harvested dataset")


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    return tmp_path


def poor_templates(tmp_path, unit):
    """A copy of the templates with every icon of ``unit`` removed (the reader has 'never seen' it)."""
    root = str(tmp_path / "templates")
    shutil.copytree(ROOT, root)
    for f in os.listdir(os.path.join(root, "icons")):
        if f.startswith(unit + "__"):
            os.remove(os.path.join(root, "icons", f))
    return root


def pick(unit, store):
    """An attacker-side sample that reads exactly with the full templates and shows ``unit``."""
    for name, meta in SAMPLES:
        t = meta["truth"]
        if unit in t["attackers"] and len(t["attackers"]) == 1 and not t.get("panel_aa") and len(t["defenders"]) <= 2:
            panel = R.read_panel(D.load_canvas(name, meta), store)
            if panel and not R.panel_to_armies(panel)[2]:
                return name, meta
    pytest.skip(f"no simple {unit} sample")


def test_collection_is_off_by_default_and_remembers_the_choice(data):
    col = C.Collector(folder=str(data / "c"), check_windows=False)
    assert not col.enabled
    full = TemplateStore()
    name, meta = SAMPLES[0]
    poor = TemplateStore(root=poor_templates(data, "inf"))
    panel = R.read_panel(D.load_canvas(name, meta), poor)
    assert col.offer(panel) is None and col.items() == []
    col.set_enabled(True)
    assert C.Collector(folder=str(data / "c2")).enabled  # persisted in settings.json
    assert full is not None


def test_only_flagged_or_borderline_panels_are_kept_once(data):
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    full = TemplateStore()
    name, meta = pick("art", full)
    good = R.read_panel(D.load_canvas(name, meta), full)
    assert "flagged" not in C.reasons(good)
    poor = TemplateStore(root=poor_templates(data, "art"))
    bad = R.read_panel(D.load_canvas(name, meta), poor)
    assert "flagged" in C.reasons(bad)
    first = col.offer(bad)
    assert first and len(col.items()) == 1
    col._last = 0.0
    assert col.offer(bad) is None  # the same panel is not stored twice
    item = col.items()[0]
    assert item.status == "pending" and item.crop().shape[0] > 100
    assert max(item.crop().shape[:2]) < 1700  # a panel crop, never a whole frame


def test_tasks_offer_only_the_units_that_can_roll_that_die(data):
    full = TemplateStore()
    name, meta = pick("art", full)
    poor_root = poor_templates(data, "art")
    poor = TemplateStore(root=poor_root)
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    col.offer(R.read_panel(D.load_canvas(name, meta), poor))
    tasks, _panel = T.tasks_for(col.items()[0], poor)
    assert tasks and all(t.need_icon for t in tasks)
    for t in tasks:
        assert set(t.candidates) <= R.ATT_BY_DIE[t.die] and "art" in t.candidates
        assert t.suggestion is None or t.suggestion in t.candidates


def test_teach_with_correct_labels_improves_and_can_be_undone(data):
    full = TemplateStore()
    name, meta = pick("art", full)
    root = poor_templates(data, "art")
    poor = TemplateStore(root=root)
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    col.offer(R.read_panel(D.load_canvas(name, meta), poor))
    item = col.items()[0]
    tasks, panel = T.tasks_for(item, poor)
    assert T.status_of(poor, name, meta, D.RAW) == "flagged"
    labels = {i: {"unit": "art"} for i, t in enumerate(tasks) if t.need_icon}
    report = T.teach(item, tasks, panel, labels, template_root=root, collector=col)
    assert report.ok, report.message
    assert report.improved >= 1 and report.templates_added >= 1
    assert T.status_of(TemplateStore(root=root), name, meta, D.RAW) == "exact"
    assert col.get(item.id).status == "taught"
    assert any(n.endswith(".json") for n in os.listdir(T.taught_dir()))
    msg = T.undo_last(collector=col)
    assert msg and "removed" in msg
    assert T.status_of(TemplateStore(root=root), name, meta, D.RAW) == "flagged"
    assert col.get(item.id).status == "pending"
    assert T.undo_last(collector=col) is None


def test_a_wrong_label_is_rejected_by_the_gate(data):
    full = TemplateStore()
    name, meta = pick("art", full)
    root = poor_templates(data, "art")
    poor = TemplateStore(root=root)
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    col.offer(R.read_panel(D.load_canvas(name, meta), poor))
    item = col.items()[0]
    tasks, panel = T.tasks_for(item, poor)
    before = len(os.listdir(os.path.join(root, "icons")))
    # label the artillery icon as another unit that also rolls a 2 (the user made a mistake)
    labels = {i: {"unit": "des"} for i, t in enumerate(tasks) if t.need_icon}
    report = T.teach(item, tasks, panel, labels, template_root=root, collector=col)
    assert not report.ok and report.problems
    assert len(os.listdir(os.path.join(root, "icons"))) == before  # nothing reached the live templates
    assert col.get(item.id).status == "pending"


def test_no_label_means_no_learning(data):
    full = TemplateStore()
    name, meta = pick("art", full)
    root = poor_templates(data, "art")
    poor = TemplateStore(root=root)
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    col.offer(R.read_panel(D.load_canvas(name, meta), poor))
    item = col.items()[0]
    tasks, panel = T.tasks_for(item, poor)
    before = sorted(os.listdir(os.path.join(root, "icons")))
    report = T.teach(item, tasks, panel, {}, template_root=root, collector=col)
    assert not report.ok and "unit" in report.message
    assert sorted(os.listdir(os.path.join(root, "icons"))) == before
    bad = T.teach(item, tasks, panel, {0: {"unit": "tank"}}, template_root=root, collector=col)
    assert not bad.ok  # a tank cannot roll that die


def test_teach_window_end_to_end(data):
    import time
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from aaoverlay.teachwin import TeachWindow
    app = QApplication.instance() or QApplication([])
    full = TemplateStore()
    name, meta = pick("art", full)
    root = poor_templates(data, "art")
    col = C.Collector(enabled=True, folder=str(data / "c"), check_windows=False)
    col.offer(R.read_panel(D.load_canvas(name, meta), TemplateStore(root=root)))
    learned = []
    w = TeachWindow(col, lambda: TemplateStore(root=root), lambda: learned.append(1), template_root=root,
                    exclude_from_capture=False)
    w.show()
    app.processEvents()
    assert w.listing.count() == 1 and w.tasks
    for i, (combo, spin) in w.pickers.items():
        combo.setCurrentIndex(1 + w.tasks[i].candidates.index("art"))
    w.run_teach()
    end = time.time() + 120
    while time.time() < end and not learned:
        app.processEvents()
        time.sleep(0.1)
    assert learned, w.report.text()
    assert w.listing.count() == 0  # the taught panel left the queue
    w.undo()
    assert w.listing.count() == 1 and len(learned) == 2
    w.hide()
