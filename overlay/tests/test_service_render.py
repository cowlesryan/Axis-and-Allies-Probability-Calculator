"""Tests for the compute service and the overlay HTML (no screen or Qt window needed)."""
import time

from aaoverlay import service
from aaoverlay.app import render
from aa1942.units import parse_army as A


def test_compute_emits_estimate_then_exact_then_final():
    stages = []
    out = service.compute(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"), stages.append)
    assert [s["stage"] for s in stages] == ["estimate", "exact", "final"]
    exact = stages[1]
    assert abs(exact["outcomes"]["attacker_win"] - 0.7174) < 5e-5
    assert exact["loss_att"] > 0 and exact["loss_def"] > 0
    assert set(out["spread"]) == {"standard", "attacker_optimal", "defender_optimal"}
    assert "assumptions" in out and out["assumptions"]


def test_estimate_agrees_with_exact():
    stages = []
    service.compute(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"), stages.append)
    est, exact = stages[0], stages[1]
    assert abs(est["outcomes"]["attacker_win"] - exact["outcomes"]["attacker_win"]) < 6 * max(est["stderr"], 0.01)


def test_large_battle_falls_back_to_estimate_quickly():
    stages = []
    t0 = time.monotonic()
    out = service.compute(A("30 inf, 15 art, 15 tank, 8 ftr"), A("35 inf, 12 art, 12 tank, 6 ftr, 2 aa"),
                          stages.append, exact_seconds=0.5)
    assert time.monotonic() - t0 < 15
    assert out["method"] == "estimate" and out["stage"] == "final"
    assert any("Monte Carlo" in n for n in out["notes"])


def test_cancel_stops_a_running_job():
    import threading
    stages = []
    th = threading.Thread(target=service.compute, args=(
        A("30 inf, 15 art, 15 tank, 8 ftr"), A("35 inf, 12 art, 12 tank, 6 ftr, 2 aa"), stages.append, 30.0))
    t0 = time.monotonic()
    th.start()
    time.sleep(1.5)
    service.request_cancel()
    th.join(10)
    assert not th.is_alive()
    assert time.monotonic() - t0 < 10
    assert not any(s["stage"] == "final" for s in stages)  # a cancelled job never reports a final answer


def test_render_shows_result():
    stages = []
    service.compute(A("1 ftr, 4 sub, 1 cru"), A("1 bb"), stages.append)
    state = {"warnings": [], "att_text": "1 ftr, 4 sub, 1 cru", "def_text": "1 bb", "att_ipc": 46,
             "def_ipc": 20, "aa": 0, "result": stages[-1]}
    html = render(state)
    for text in ("Attacker wins", "Defender wins", "EXACT", "Expected IPC lost", "Casualty choice", "Assumptions"):
        assert text in html


def test_headline_is_the_attackers_chance_and_comes_first():
    """The overlay always speaks from the attacker's side: the big number is the attacker's win chance,
    it appears before any defender figure, and the attacker's army is listed first."""
    import re
    stages = []
    service.compute(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"), stages.append)
    res = stages[-1]
    state = {"warnings": [], "att_text": "5 inf, 2 art, 3 tank", "def_text": "6 inf, 2 tank, 1 aa", "att_ipc": 41,
             "def_ipc": 35, "aa": 1, "result": res}
    html = render(state)
    assert "ATTACKER" in html and "Attacker wins" in html
    big = re.search(r"font-size:30px[^>]*><b>([\d.]+)%", html)
    assert big and abs(float(big.group(1)) - 100 * res["outcomes"]["attacker_win"]) < 0.06
    assert html.index("Attacker wins") < html.index("Defender wins")
    assert html.index("<b>Attacker</b>") < html.index("<b>Defender</b>")


def test_panel_left_side_is_always_the_attacker():
    """The reader maps the LEFT side of the panel to the attacker and the right to the defender."""
    import os
    import cv2
    from aaoverlay import reader as R
    from aaoverlay.templates import TemplateStore
    ref = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reference",
                       "attack_setup_caucasus_2560x1440.png")
    if not os.path.exists(ref):
        return
    att, dfn, _ = R.panel_to_armies(R.read_panel(cv2.imread(ref), TemplateStore()))
    assert sum(att) == 31 and sum(dfn) == 30  # the red attackers on the left (31), the defenders on the right (30)


def test_render_warnings_and_pending_states():
    html = render({"warnings": ["defender: unrecognised icon at x=1,y=2"], "att_text": "", "def_text": "",
                   "att_ipc": 0, "def_ipc": 0, "aa": 0, "result": None})
    assert "Could not read the whole panel" in html and "unrecognised icon" in html
    html = render({"warnings": [], "att_text": "1 inf", "def_text": "1 inf", "att_ipc": 3, "def_ipc": 3,
                   "aa": 0, "result": None})
    assert "Calculating" in html


def test_amphibious_panel_uses_bombardment():
    """Ships on the attacker side of a land battle are shore bombardment: the answer beats the plain land battle."""
    from aa1942.units import parse_army
    from aaoverlay import service
    att = parse_army("1 inf, 1 bb")      # the real panel read from an amphibious assault on France
    dfn = parse_army("1 inf, 2 tank")
    assert service.split_amphibious(att, dfn) is not None
    assert service.split_amphibious(parse_army("2 des, 1 cru"), parse_army("1 bb")) is None  # a sea battle
    got = []
    service.compute(att, dfn, got.append)
    final = got[-1]
    plain = []
    service.compute(parse_army("1 inf"), dfn, plain.append)
    assert final["method"] == "exact"
    assert final["outcomes"]["attacker_win"] > plain[-1]["outcomes"]["attacker_win"]
    assert any("Amphibious" in n for n in final["notes"])
