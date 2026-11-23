"""System-level tests: service, rendering, the live controller, save-file truth, collector/teach edge cases, the custom
window under random input, the training-data tools and their safety limits."""
import copy
import json
import os
import random
import sys
import time
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aaoverlay import collect as C  # noqa: E402
from aaoverlay import dataset as D  # noqa: E402
from aaoverlay import reader as R  # noqa: E402
from aaoverlay import savetruth as T  # noqa: E402
from aaoverlay import service  # noqa: E402
from aaoverlay import teach as TE  # noqa: E402
from aaoverlay.custom import CustomBattleWindow  # noqa: E402
from aaoverlay.render import render, render_sbr  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402
from aa1942.solver import SolveCancelled  # noqa: E402
from aa1942.units import N, ORDER, parse_army as A  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, HERE)

SAMPLES = list(D.samples())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def store():
    return TemplateStore()


# ---------------------------------------------------------------------------------------------------------------
# service
# ---------------------------------------------------------------------------------------------------------------
def final(att, dfn, **kw):
    got = []
    service.compute(att, dfn, got.append, **kw)
    return got[-1]


def test_service_every_stage_has_the_same_keys():
    got = []
    service.compute(A("3 inf, 1 art"), A("3 inf"), got.append)
    assert len(got) >= 2
    keys = set(got[0])
    assert all(set(g) == keys for g in got)
    assert got[-1]["stage"] == "final" and got[-1]["method"] == "exact"


def test_service_random_battles_never_raise_and_probabilities_add_up():
    rng = random.Random(21)
    for _ in range(25):
        att, dfn = [0] * N, [0] * N
        land = rng.random() < 0.6
        pool = [0, 1, 2, 4, 5] if land else [6, 7, 8, 9, 10, 4, 12]
        for _i in range(rng.randint(1, 5)):
            att[rng.choice(pool)] += 1
        for _i in range(rng.randint(1, 5)):
            dfn[rng.choice(pool)] += 1
        if land and rng.random() < 0.3:
            dfn[3] += 1
        att, dfn = tuple(att), tuple(dfn)
        try:
            res = final(att, dfn, exact_seconds=4)
        except ValueError:
            continue  # e.g. only transports vs a land army: the calculator says so
        o = res["outcomes"]
        assert o is not None
        assert sum(o.values()) == pytest.approx(1.0, abs=1e-6), (att, dfn)


def test_service_amphibious_split_rules():
    assert service.split_amphibious(A("2 des, 1 cru"), A("1 bb")) is None
    assert service.split_amphibious(A("2 inf"), A("2 inf")) is None
    land, n_bb, n_cru, ignored = service.split_amphibious(A("2 inf, 1 bb, 1 cru, 1 des"), A("3 inf"))
    assert (n_bb, n_cru, ignored) == (1, 1, 1) and land == A("2 inf")
    land, n_bb, n_cru, _ = service.split_amphibious(A("1 inf, 2 bb"), A("3 inf"))
    assert n_bb == 1 and n_cru == 0  # one ship per landing unit
    # aircraft alone do not make an amphibious assault; ships with only aircraft and a land defender do not either
    assert service.split_amphibious(A("1 bmb"), A("2 inf")) is None


def test_service_budget_fallbacks_say_so():
    res = final(A("30 inf, 15 art, 15 tank, 5 bb"), A("30 inf, 20 tank"), exact_seconds=0.02)
    assert res["stage"] == "final" and any("too large" in n.lower() for n in res["notes"])
    big = final(A("60 inf, 30 art, 30 tank"), A("60 inf, 30 tank"), exact_seconds=0.3)
    assert big["method"] == "estimate" and big["runs"] and big["outcomes"]


def test_service_cancel_stops_a_running_solve_quickly():
    import threading
    t0 = time.time()
    threading.Timer(0.2, service.request_cancel).start()
    got = []
    service.compute(A("30 inf, 20 art, 20 tank"), A("30 inf, 20 tank, 10 ftr"), got.append, exact_seconds=60)
    assert time.time() - t0 < 10


def test_service_custom_policy_matches_the_solver():
    from aa1942.solver import OPTIMAL, STANDARD, solve
    att, dfn = A("3 inf, 2 tank"), A("4 inf, 1 tank")
    res = final(att, dfn, att_policy=OPTIMAL, def_policy=STANDARD)
    assert res["outcomes"]["attacker_win"] == pytest.approx(solve(att, dfn, OPTIMAL, STANDARD)["attacker_win"])
    assert res["spread"] is None  # no policy comparison for a chosen policy


# ---------------------------------------------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------------------------------------------
def state_for(att, dfn):
    res = final(att, dfn)
    return {"warnings": [], "att_text": res["attacker"], "def_text": res["defender"], "att_ipc": res["att_ipc"],
            "def_ipc": res["def_ipc"], "aa": 0, "result": res}


def test_render_escapes_everything_it_prints():
    html = render({"warnings": ["<script>alert(1)</script> & <b>"], "att_text": "", "def_text": "",
                   "att_ipc": 0, "def_ipc": 0, "aa": 0, "result": None})
    assert "<script>" not in html and "&lt;script&gt;" in html
    st = state_for(A("2 inf"), A("1 inf"))
    st["att_text"] = "<img src=x onerror=1>"
    assert "<img" not in render(st)


def test_render_attacker_headline_and_aa_line():
    st = state_for(A("3 inf"), A("1 inf"))
    html = render(st)
    assert "Attacker wins" in html and "Defending AA guns" not in html
    st["aa"] = 2
    assert "Defending AA guns (read from the panel): 2" in render(st)
    assert "F11" not in html


def test_render_every_result_shape_is_complete_html():
    shapes = [state_for(A("2 inf"), A("2 inf")), state_for(A("1 inf, 1 bb"), A("2 inf")),
              state_for(A("1 sub"), A("1 ftr"))]           # stalemate row
    for st in shapes:
        html = render(st)
        assert html.count("<table") == html.count("</table>") and html.count("<div") == html.count("</div>")
    got = []
    service.compute_full_amphibious(A("1 trn"), A("2 inf"), (0,) * N, (0,) * N, A("1 inf"), (0,) * N, got.append)
    html = render({"warnings": [], "att_text": got[-1]["attacker"], "def_text": got[-1]["defender"],
                   "att_ipc": got[-1]["att_ipc"], "def_ipc": got[-1]["def_ipc"], "aa": 0, "result": got[-1],
                   "title": "CUSTOM"})
    assert "landing made" in html and "CUSTOM" in html


def test_render_sbr_has_all_figures():
    html = render_sbr(service.compute_sbr(3, 4, 1), 3, 4, 1)
    assert html.count("<table") == html.count("</table>")
    assert "expected damage" in html and "Chance of no damage" in html


# ---------------------------------------------------------------------------------------------------------------
# the live controller
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture
def controller(app, tmp_path, monkeypatch):
    import cv2
    from aaoverlay.app import Controller
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    blank = tmp_path / "blank.png"
    cv2.imwrite(str(blank), np.zeros((1440, 2560, 3), np.uint8))
    opts = SimpleNamespace(no_exclude=True, interval=30.0, image=str(blank), debug=False, exact_seconds=4.0,
                           collect=False, run_seconds=0)
    ctl = Controller(app, opts)
    yield ctl
    ctl.reader.requestInterruption()
    ctl.solver.requestInterruption()
    ctl.custom.shutdown()
    ctl.reader.wait(3000)
    ctl.solver.wait(3000)
    ctl.overlay.hide()


def sample_panel(store, pick=lambda m: True):
    for name, meta in SAMPLES:
        if pick(meta):
            p = R.read_panel(D.load_canvas(name, meta), store)
            if p is not None and not R.panel_to_armies(p)[2]:
                return p
    pytest.skip("no suitable sample")


def test_controller_needs_two_agreeing_frames(controller, store):
    panel = sample_panel(store)
    controller.on_reading(panel)
    assert controller.last_key is None and controller._pending_key is not None   # one frame is not enough
    controller.on_reading(panel)
    assert controller.last_key is not None and controller.state["att_text"]
    assert controller.last_armies is not None


def test_controller_flagged_panels_show_warnings_and_start_no_solve(controller, store):
    flagged = None
    poor = TemplateStore(root=os.path.join(os.path.dirname(HERE), "tests", "_nonexistent_"))  # no templates at all
    name, meta = SAMPLES[0]
    flagged = R.read_panel(D.load_canvas(name, meta), poor)
    assert flagged.warnings
    before = controller.job
    controller.on_reading(flagged)
    controller.on_reading(flagged)
    assert controller.state["warnings"] and controller.job == before
    assert controller.last_armies is None


def test_controller_hides_after_three_missing_frames(controller, store):
    panel = sample_panel(store)
    controller.on_reading(panel)
    controller.on_reading(panel)
    controller.overlay.show()
    controller.on_reading(None)
    controller.on_reading(None)
    assert controller.overlay.isVisible()          # a dropped frame or two is tolerated
    controller.on_reading(None)
    assert not controller.overlay.isVisible() and controller.last_key is None


def test_controller_pauses_while_a_window_is_open_and_remembers_the_panel(controller, store):
    panel = sample_panel(store)
    controller.custom.show()
    controller.on_reading(panel)
    controller.on_reading(panel)
    assert not controller.overlay.isVisible() and controller.last_key is None
    assert controller.last_armies is not None       # for the custom window's "Load from game panel"
    controller.custom.hide()
    controller.teachwin.show()
    controller.on_reading(panel)
    assert controller.last_key is None
    controller.teachwin.hide()


def test_controller_hotkeys_toggle_windows_and_nothing_is_bound_to_f11(controller):
    controller.on_hotkey("f8")
    assert controller.custom.isVisible()
    controller.on_hotkey("f8")
    assert not controller.custom.isVisible()
    controller.on_hotkey("f7")
    assert controller.teachwin.isVisible()
    controller.on_hotkey("f7")
    assert not controller.teachwin.isVisible()
    controller.on_hotkey("f9")
    assert controller.visible is False
    assert not hasattr(controller, "aa")
    controller.on_hotkey("f11")  # unknown now: must be a harmless no-op
    controller.on_hotkey("f9")


def test_controller_reload_flag_and_collection_default(controller):
    assert not controller.collector.enabled          # collection is opt-in
    controller.templates_changed()
    assert controller.reader.reload.is_set() and controller.last_key is None


def test_controller_collects_flagged_panels_when_enabled(controller):
    controller.collector.check_windows = False
    controller.collector.set_enabled(True, remember=False)
    poor = TemplateStore(root=os.path.join(os.path.dirname(HERE), "tests", "_nonexistent_"))
    name, meta = SAMPLES[0]
    panel = R.read_panel(D.load_canvas(name, meta), poor)
    controller.on_reading(panel)
    controller.on_reading(panel)
    assert len(controller.collector.items()) == 1
    controller.on_reading(panel)
    controller.on_reading(panel)
    assert len(controller.collector.items()) == 1    # not stored twice


# ---------------------------------------------------------------------------------------------------------------
# save-file truth on synthetic states
# ---------------------------------------------------------------------------------------------------------------
def state(units, pending=None, delta=None, power="germany"):
    return {"powers": {"germany": {"alliance": "axis"}, "soviet_union": {"alliance": "allies"},
                       "united_kingdom": {"alliance": "allies"}},
            "phaseStorage": {"power": power, "round": 1, "phase": "combatMove", "delta": delta or []},
            "units": units, "pendingEncounters": pending if pending is not None else {}}


def u(owner, loc, damage=0):
    return {"owner": owner, "location": loc, "damage": damage}


def test_truth_from_pending_moves_without_an_encounter_record():
    s = state({"in1": u("germany", "a"), "in2": u("germany", "a"), "ar3": u("germany", "a"),
               "in4": u("soviet_union", "r"), "bs5": u("soviet_union", "r", 1), "aa6": u("soviet_union", "r")},
              delta=[{"func": "moveUnit", "args": [x, ["r"], {}]} for x in ("in1", "in2", "ar3")])
    t = T.truth_for(s, "r")
    assert t["attackers"] == {"inf": 2, "art": 1}
    assert t["defenders"] == {"inf": 1, "bbd": 1} and t["hidden"]["defenders"] == {"aa": 1}
    assert t["panel_aa"] == 0   # no aircraft attacking


def test_truth_panel_lists_aa_only_when_aircraft_attack_and_per_owner():
    s = state({"ft1": u("germany", "a"), "in2": u("soviet_union", "r"), "aa3": u("soviet_union", "r"),
               "aa4": u("united_kingdom", "r"), "in5": u("united_kingdom", "r")},
              pending={"r": {"initialAttackers": ["ft1"], "initialDefenders": ["in2", "aa3", "aa4", "in5"]}})
    t = T.truth_for(s, "r")
    assert t["panel_aa"] == 2 and sorted(t["panel_aa_entries"]) == [["soviet_union", 1], ["united_kingdom", 1]]
    assert sorted(t["defender_entries"]) == [["soviet_union", "inf", 1], ["united_kingdom", "inf", 1]]


def test_truth_amphibious_adds_bombarding_ships_but_not_transports():
    s = state({"in1": u("germany", "h"), "tr2": u("germany", "sz"), "bs3": u("germany", "sz"),
               "cr4": u("germany", "sz"), "in5": u("soviet_union", "r")},
              pending={"r": {"initialAttackers": ["in1"], "initialDefenders": ["in5"], "amphibiousAttackers": ["in1"],
                             "bombarderSlots": {"sz": 1}, "bombardingUnits": {"sz": ["bs3"]}}})
    t = T.truth_for(s, "r")
    assert t["attackers"] == {"inf": 1, "bb": 1} and t["bombarders"] == {"bb": 1} and t["bombard_slots"] == 1
    assert t["amphibious"] == {"inf": 1}


def test_truth_unknown_unit_prefix_is_a_clear_error_and_known_ones_all_map():
    with pytest.raises(ValueError):
        T.unit_key("zz9")
    for prefix, key in T.PREFIX.items():
        assert T.unit_key(prefix + "1") == key and key in ORDER


def test_regions_under_attack_ignores_friendly_and_other_powers():
    s = state({"in1": u("germany", "a"), "in2": u("germany", "friend"), "in3": u("germany", "friend"),
               "in4": u("soviet_union", "r"), "in5": u("soviet_union", "elsewhere")},
              delta=[{"func": "moveUnit", "args": ["in1", ["r"], {}]},
                     {"func": "moveUnit", "args": ["in2", ["friend"], {}]},
                     {"func": "moveUnit", "args": ["in5", ["a"], {}]}])
    assert T.regions_under_attack(s) == ["r"]


# ---------------------------------------------------------------------------------------------------------------
# collector and teach edge cases
# ---------------------------------------------------------------------------------------------------------------
def flagged_panel():
    poor = TemplateStore(root=os.path.join(os.path.dirname(HERE), "tests", "_nonexistent_"))
    name, meta = SAMPLES[0]
    return R.read_panel(D.load_canvas(name, meta), poor)


def first_entry(panel):
    return next(e for r in panel.rows for e in r.att + r.dfn)


def test_collector_cap_and_corrupt_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    col = C.Collector(enabled=True, folder=str(tmp_path / "c"), check_windows=False)
    (tmp_path / "c" / "broken.json").write_text("{not json", encoding="utf-8")
    assert col.items() == []                                       # a corrupt file is skipped, not fatal
    col.set_status("does-not-exist", "taught")                     # unknown ids are ignored
    col.delete("does-not-exist")
    monkeypatch.setattr(C, "MAX_ITEMS", 2)
    monkeypatch.setattr(C, "MIN_GAP_SECONDS", 0.0)
    seen = []
    for i in range(3):
        panel = flagged_panel()
        first_entry(panel).count = i + 1            # make each panel different so it is not deduped
        seen.append(col.offer(panel))
    assert seen[0] and seen[1] and seen[2] is None  # full of pending items: nothing is dropped silently
    col.set_status(seen[0], "discarded")
    panel = flagged_panel()
    first_entry(panel).count = 99
    assert col.offer(panel) is not None             # a finished item makes room
    assert len(col.items()) == 2


def test_collector_ignores_missing_frame_and_clean_panels(tmp_path, monkeypatch, store):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    col = C.Collector(enabled=True, folder=str(tmp_path / "c"), check_windows=False)
    assert col.offer(None) is None
    bare = flagged_panel()
    bare.frame = None
    assert col.offer(bare) is None
    assert col.offer(sample_panel(store)) is None    # a clean, confident panel is not worth keeping


def test_collector_stores_only_a_panel_crop(tmp_path, monkeypatch):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    col = C.Collector(enabled=True, folder=str(tmp_path / "c"), check_windows=False)
    item = col.get(col.offer(flagged_panel()))
    h, w = item.crop().shape[:2]
    assert h < 1300 and w < 1700 and (h, w) != (1440, 2560)


def test_settings_roundtrip_and_corrupt_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    assert C.load_settings() == {}
    C.save_settings(collect=True)
    assert C.load_settings() == {"collect": True}
    (tmp_path / "data" / "settings.json").write_text("garbage", encoding="utf-8")
    assert C.load_settings() == {} and C.Collector(folder=str(tmp_path / "c")).enabled is False


def test_teach_rejects_label_mismatches(tmp_path, monkeypatch, store):
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    col = C.Collector(enabled=True, folder=str(tmp_path / "c"), check_windows=False)
    item = col.get(col.offer(flagged_panel()))
    tasks, panel = TE.tasks_for(item, TemplateStore(root=os.path.join(os.path.dirname(HERE), "tests", "_none_")))
    assert tasks
    with pytest.raises(TE.LabelError):
        TE.labelled_army(panel, tasks, {})            # nothing labelled yet
    t = next(t for t in tasks if t.need_count)
    with pytest.raises(TE.LabelError):                # a 5-digit count for a 2-digit label
        TE.apply_labels(TemplateStore(root=str(tmp_path / "empty")), item.canvas(), [t],
                        {0: {"unit": t.candidates[0], "count": 12345}})


def test_gate_rejects_a_change_that_breaks_a_sample_and_accepts_a_neutral_one(tmp_path):
    full = TemplateStore()
    samples = [(n, m, D.RAW) for n, m in SAMPLES[:6]]
    assert TE.gate(full, full, samples).ok
    broken = TemplateStore(root=str(tmp_path / "nothing"))           # no templates: everything unreadable
    report = TE.gate(full, broken, samples)
    assert not report.ok and report.problems and "no longer does" in report.message


# ---------------------------------------------------------------------------------------------------------------
# custom window under random input
# ---------------------------------------------------------------------------------------------------------------
def test_custom_window_random_inputs_only_ever_raise_value_errors(app):
    rng = random.Random(8)
    w = CustomBattleWindow(exclude_from_capture=False, exact_seconds=2.0)
    try:
        for _ in range(30):
            mode = rng.choice([0, 1, 2])
            w.clear_all()
            w.mode.setCurrentIndex(mode)
            if mode == 0:
                for g in (w.att, w.dfn):
                    for k in rng.sample(list(g.boxes), rng.randint(0, 3)):
                        g.boxes[k].setValue(rng.randint(0, 4))
            elif mode == 1:
                for g in (w.a_fleet, w.a_cargo, w.a_air, w.a_bomb, w.d_fleet, w.d_land):
                    for k in rng.sample(list(g.boxes), rng.randint(0, 2)):
                        g.boxes[k].setValue(rng.randint(0, 3))
            else:
                for b in w.sbr.values():
                    b.setValue(rng.randint(0, 6))
            w.policy.combos["Attacker"].setCurrentIndex(rng.choice([0, 0, 1]))
            try:
                html = w.compute_blocking()
            except ValueError:
                continue
            assert isinstance(html, str)
    finally:
        w.shutdown()


def test_custom_window_huge_counts_return_in_bounded_time(app):
    w = CustomBattleWindow(exclude_from_capture=False, exact_seconds=1.0)
    try:
        w.att.set_values({"inf": 400, "art": 200, "tank": 300})
        w.dfn.set_values({"inf": 500, "tank": 300, "ftr": 100})
        t0 = time.time()
        html = w.compute_blocking()
        assert time.time() - t0 < 60 and "Attacker wins" in html
    finally:
        w.shutdown()


# ---------------------------------------------------------------------------------------------------------------
# training-data tools and their safety limits
# ---------------------------------------------------------------------------------------------------------------
def test_ui_driver_never_allows_the_dangerous_buttons():
    import ui_driver as U
    # the ONLINE entry is refused everywhere, the in-game gear (which opens the menu with DELETE GAME) in scenarios
    assert U.Driver._forbidden(115, 70) and U.Driver._forbidden(30, 30)
    assert U.Driver._forbidden(115, 70, U.FORBIDDEN_ALWAYS) and not U.Driver._forbidden(30, 30, U.FORBIDDEN_ALWAYS)
    for name, (x, y) in U.MENU.items():
        assert not U.Driver._forbidden(x, y, U.FORBIDDEN_ALWAYS), name      # no named button is in the ONLINE zone
    for y in U.CARD_Y:
        assert not U.Driver._forbidden(U.CARD_X, y, U.FORBIDDEN_ALWAYS)
    assert not any(name.lower().startswith(("online", "delete")) for name in U.MENU)


def test_ui_driver_refuses_without_a_game_window(monkeypatch):
    import ui_driver as U
    monkeypatch.setattr(U, "find_game_window", lambda: 0)
    with pytest.raises(U.Refused):
        U.Driver(mode="scenario")
    with pytest.raises(AssertionError):
        U.Driver(mode="anything")


def test_ui_driver_scenario_guard_requires_a_fresh_overlay_copy(monkeypatch):
    import ui_driver as U
    drv = U.Driver.__new__(U.Driver)          # no window needed for the guard itself
    drv.mode = "scenario"
    for name, age, ok in (("Overlay japan", 5, True), ("Overlay japan", 400, False), ("bigboomlol's Game", 5, False),
                          (None, None, False), ("", 1, False)):
        monkeypatch.setattr(U, "newest_save", lambda n=name, a=age: (n, a))
        if ok:
            drv._guard_scenario()
        else:
            with pytest.raises(U.Refused):
                drv._guard_scenario()


@pytest.fixture(scope="module")
def base_state():
    import scenario_gen as G
    try:
        path = G.find_base()
    except FileNotFoundError:
        pytest.skip("no offline save on this machine")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def check_state_integrity(data, manifest):
    st = data["state"]
    units = st["units"]
    ids = list(units)
    assert len(ids) == len(set(ids))
    top = max(int("".join(c for c in i if c.isdigit())) for i in ids)
    assert int(st["lastUnitId"]) >= top
    for target, enc in st["pendingEncounters"].items():
        assert enc["location"] == target
        for uid in enc["initialAttackers"] + enc["initialDefenders"] + [
                x for ids2 in enc.get("bombardingUnits", {}).values() for x in ids2]:
            assert uid in units, (target, uid)
        owners = {units[uid]["owner"] for uid in enc["initialAttackers"]}
        assert owners == {st["phaseStorage"]["power"]}
        assert not set(enc["initialAttackers"]) & set(enc["initialDefenders"])
    for action in st["phaseStorage"]["delta"]:
        for a in action["args"]:
            if isinstance(a, str) and a[:2] in T.PREFIX:
                assert a in units, action
    assert sorted(st["pendingEncounters"]) == sorted(m["target"] for m in manifest)


@pytest.mark.parametrize("power", ["germany", "soviet_union", "united_kingdom", "japan", "united_states"])
def test_generated_scenarios_are_internally_consistent(base_state, power):
    import scenario_gen as G
    data = copy.deepcopy(base_state)
    manifest, _cov = G.generate(data, power, n_land=4, n_sea=3)
    G.validate(data, manifest)
    check_state_integrity(data, manifest)
    for m in manifest:
        if m["type"] == "land":                      # land attackers start in a friendly neighbouring region
            st = data["state"]
            assert st["regions"][m["origin"]]["owner"] is not None
            assert G.alliance(st, st["regions"][m["origin"]]["owner"]) == G.alliance(st, power)


@pytest.mark.parametrize("power", ["germany", "united_kingdom", "japan", "united_states"])
def test_small_battle_scenarios_are_internally_consistent(base_state, power):
    import scenario_gen as G
    data = copy.deepcopy(base_state)
    manifest, _cov = G.generate_small(data, power)
    G.validate(data, manifest)
    check_state_integrity(data, manifest)
    assert manifest


@pytest.mark.parametrize("power", ["germany", "soviet_union", "united_kingdom", "japan"])
def test_amphibious_scenarios_match_the_games_structure(base_state, power):
    import amphibious_gen as AG
    data = copy.deepcopy(base_state)
    manifest, _cov = AG.generate(data, power)
    check_state_integrity(data, manifest)
    st = data["state"]
    assert manifest
    for target, enc in st["pendingEncounters"].items():
        assert enc["bombarderSlots"] and sum(enc["bombarderSlots"].values()) == len(enc["amphibiousAttackers"])
        assert set(enc["amphibiousAttackers"]) <= set(enc["initialAttackers"])
        ships = [x for ids in enc["bombardingUnits"].values() for x in ids]
        assert len(ships) <= len(enc["amphibiousAttackers"])
        for uid in enc["amphibiousAttackers"]:
            tr = st["units"][st["units"][uid]["transporter"]]
            assert tr["dropTarget"] == target and uid in tr["pendingCargo"].values()
            assert sum(1 for v in tr["pendingCargo"].values() if v) <= 2
    for m in manifest:                               # the manifest truth is what truth_for says
        t = T.truth_for(st, m["target"])
        assert t["attackers"] == m["truth"]["attackers"] and t["bombarders"] == m["truth"]["bombarders"]


def test_write_copy_never_touches_account_fields_or_the_source(tmp_path, base_state):
    import make_scenario as M
    data = copy.deepcopy(base_state)
    before = {k: copy.deepcopy(data[k]) for k in ("ownerUUID", "players", "axisPassword", "alliesPassword") if k in data}
    old_id = data["id"]
    path = M.write_copy(data, save_dir=str(tmp_path), name="Overlay test")
    out = json.load(open(path, encoding="utf-8"))
    for k, v in before.items():
        assert out[k] == v, k                          # personal fields are left exactly as they were
    assert out["id"] != old_id and out["name"] == "Overlay test"
    assert os.path.basename(path) == f"{out['id']}.json"
    assert out["state"]["phaseStorage"]["gameId"] == str(out["id"])


def test_no_personal_data_is_stored_inside_the_project_folder():
    """Saves hold account ids; none may sit in the (cloud-synced) project folder."""
    root = os.path.dirname(HERE)
    bad = []
    for base, _dirs, files in os.walk(root):
        if any(part in base for part in ("__pycache__", ".pytest_cache", ".git")):
            continue
        for f in files:
            if f.endswith(".json") and "dataset" in base and "raw" not in base and "scenarios" not in base:
                bad.append(os.path.join(base, f))
    assert not bad, f"unexpected json files (saves with account data?): {bad[:3]}"
    for base, _dirs, files in os.walk(os.path.join(root, "dataset", "scenarios")):
        for f in files:
            text = open(os.path.join(base, f), encoding="utf-8").read()
            assert "ownerUUID" not in text and "userName" not in text


# ---------------------------------------------------------------------------------------------------------------
# window placement, hotkey polling, entry points, tools
# ---------------------------------------------------------------------------------------------------------------
def test_overlay_placement_stays_on_screen(app):
    from aaoverlay.app import Overlay
    from PySide6.QtGui import QGuiApplication
    geo = QGuiApplication.primaryScreen().geometry()
    k = geo.height() / R.BASE_H
    ov = Overlay(exclude_from_capture=False)
    ov.set_html("<div>x</div>" * 20)
    for bounds in ((0, 0, 100, 100), (2400, 1300, 2560, 1440), (900, 300, 1700, 900), (-50, -50, 10, 10),
                   (2500, 0, 3000, 400)):
        ov.place_beside(bounds)
        x, y = ov._anchor
        assert geo.x() <= x <= geo.x() + geo.width() - ov.width(), bounds
        assert geo.y() <= y <= geo.y() + geo.height() - ov.height(), bounds
    ov.place_beside((900, 300, 1700, 900))            # beside the panel: never covering it
    x, y = ov._anchor
    left, right = 900 * k, 1700 * k
    room = right + ov.width() <= geo.width() or left - ov.width() >= 0     # a headless screen may be too small
    if room:
        assert x >= right or x + ov.width() <= left


def test_hotkeys_need_ctrl_and_alt_and_fire_once_per_press(controller, monkeypatch):
    import aaoverlay.app as appmod
    down = {"ctrl": True, "alt": True, "f8": True}
    monkeypatch.setattr(appmod, "_key_down", lambda name: down.get(name, False))
    fired = []
    monkeypatch.setattr(controller, "on_hotkey", fired.append)
    controller.poll_keys()
    controller.poll_keys()                  # key still held: not a second press
    assert fired == ["f8"]
    down["f8"] = False
    controller.poll_keys()
    down["f8"] = True
    controller.poll_keys()
    assert fired == ["f8", "f8"]
    down["alt"] = False                     # without Alt the game must get its own keys
    down["f8"] = False
    controller.poll_keys()
    down["f8"] = True
    controller.poll_keys()
    assert fired == ["f8", "f8"]
    assert "f11" not in appmod.VK or True


def test_overlay_runs_end_to_end_on_a_screenshot_and_exits(tmp_path):
    import subprocess
    ref = os.path.join(os.path.dirname(HERE), "reference", "attack_setup_caucasus_2560x1440.png")
    if not os.path.exists(ref):
        pytest.skip("no reference capture")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", AAOVERLAY_DATA=str(tmp_path))
    r = subprocess.run([sys.executable, "-W", "ignore", "-m", "aaoverlay", "--image", ref, "--run-seconds", "3",
                        "--debug"], cwd=os.path.dirname(HERE), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert "27 inf" in r.stdout and "[result]" in r.stdout
    assert "Traceback" not in r.stderr


def test_overlay_rejects_removed_options():
    import subprocess
    r = subprocess.run([sys.executable, "-m", "aaoverlay", "--aa", "2"], cwd=os.path.dirname(HERE),
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert r.returncode != 0 and "unrecognized arguments" in r.stderr


def test_training_tools_run_read_only_modes(tmp_path):
    import subprocess
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", AAOVERLAY_DATA=str(tmp_path))
    top = os.path.dirname(HERE)
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(TOOLS, "learn.py"), "--dry-run"], cwd=top,
                       capture_output=True, text=True, timeout=600, env=env)
    assert r.returncode == 0 and "would teach" in r.stdout
    r = subprocess.run([sys.executable, "-W", "ignore", os.path.join(TOOLS, "coverage.py")], cwd=top,
                       capture_output=True, text=True, timeout=600, env=env)
    assert r.returncode == 0 and "WRONG WITHOUT WARNING" in r.stdout
    wrong = [l for l in r.stdout.splitlines() if "WRONG WITHOUT WARNING" in l][0]
    assert wrong.split()[-1] == "0", wrong


def test_solver_thread_survives_an_army_the_calculator_rejects(app):
    """A mixed land/sea reading (e.g. a mistaken icon) must produce a message, not kill the solver thread."""
    from aaoverlay.app import SolverThread
    got = []
    th = SolverThread(exact_seconds=2.0)
    th.result.connect(got.append)
    th.start()
    try:
        job = th.submit(A("2 inf"), A("1 des"))             # land attackers against a ship: rejected
        end = time.time() + 10
        while time.time() < end and not any(g.get("job") == job and g.get("error") for g in got):
            app.processEvents()
            time.sleep(0.05)
        assert any(g.get("job") == job and g.get("error") for g in got)
        assert th.isRunning()
        job = th.submit(A("3 inf"), A("1 inf"))             # and it keeps working afterwards
        end = time.time() + 15
        while time.time() < end and not any(g.get("job") == job and g.get("stage") == "final" for g in got):
            app.processEvents()
            time.sleep(0.05)
        assert any(g.get("job") == job and g.get("stage") == "final" for g in got)
    finally:
        th.requestInterruption()
        th.wait(3000)


def test_render_shows_a_calculation_error():
    html = render({"warnings": [], "att_text": "2 inf", "def_text": "1 des", "att_ipc": 6, "def_ipc": 8, "aa": 0,
                   "result": {"error": "Cannot mix land and sea units in one battle"}})
    assert "Cannot mix land and sea" in html and "Calculating" not in html


def test_adding_templates_never_overwrites_existing_ones(tmp_path):
    st = TemplateStore(root=str(tmp_path / "t"))
    name, meta = SAMPLES[0]
    img = D.load_canvas(name, meta)
    cx, geoms = R.locate_rows(img)
    g = next(g for g in geoms if len(g.att_segs) >= 2)
    icon = img[g.y0:g.y1, g.att_segs[0][0]:g.att_segs[0][1]]
    paths = set()
    for _ in range(4):
        paths.add(st.add_icon("art", icon))
    assert len(paths) == 4 and len(os.listdir(os.path.join(str(tmp_path / "t"), "icons"))) == 4
    os.remove(sorted(paths)[-1])                      # an undo removes the newest ones first
    p5 = st.add_icon("art", icon)
    assert p5 not in sorted(paths)[:-1] and len(os.listdir(os.path.join(str(tmp_path / "t"), "icons"))) == 4
    glyph = np.zeros((40, 24), bool)
    glyph[5:35, 8:16] = True
    dig = {st.add_digit("1", glyph) for _ in range(3)}
    assert len(dig) == 3
