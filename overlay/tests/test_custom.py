"""The custom battle window: every case the live overlay and the calculator handle, driven headlessly."""
import os
import re
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from aaoverlay.custom import CustomBattleWindow  # noqa: E402
from aaoverlay import service  # noqa: E402
from aa1942.amphibious import solve_amphibious  # noqa: E402
from aa1942.bombing import solve_sbr  # noqa: E402
from aa1942.solver import OPTIMAL, STANDARD, solve  # noqa: E402
from aa1942.units import parse_army as A  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(app):
    w = CustomBattleWindow(exclude_from_capture=False, exact_seconds=8.0)
    yield w
    w.shutdown()


def pcts(html):
    """All percentages in a rendered result, in order."""
    return [float(x) for x in re.findall(r"([\d.]+)%", html)]


def test_land_battle_matches_the_calculator(win):
    win.set_battle(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"))
    html = win.compute_blocking()
    want = solve(A("5 inf, 2 art, 3 tank"), A("6 inf, 2 tank, 1 aa"))
    assert "Attacker wins" in html
    assert pcts(html)[0] == pytest.approx(100 * want["attacker_win"], abs=0.06)


def test_air_battle_with_aa(win):
    win.set_battle(A("2 ftr, 1 bmb, 3 inf"), A("3 inf, 2 aa, 1 ftr"))
    html = win.compute_blocking()
    want = solve(A("2 ftr, 1 bmb, 3 inf"), A("3 inf, 2 aa, 1 ftr"))
    assert pcts(html)[0] == pytest.approx(100 * want["attacker_win"], abs=0.06)


def test_sea_battle_with_damaged_battleship_and_transport(win):
    att, dfn = A("2 des, 1 cru, 1 ftr, 1 bb"), A("1 bbd, 2 sub, 1 trn")
    win.set_battle(att, dfn)
    html = win.compute_blocking()
    assert pcts(html)[0] == pytest.approx(100 * solve(att, dfn)["attacker_win"], abs=0.06)


def test_attacker_cannot_have_aa(win):
    assert not win.att.boxes["aa"].isEnabled()
    win.set_battle(A("2 inf"), A("2 inf, 1 aa"))
    win.swap_sides()  # the defender's AA gun is not carried over to the attacker
    assert win.att.values()["aa"] == 0
    assert win.dfn.values()["inf"] == 2


def test_ships_with_land_units_are_shore_bombardment(win):
    att, dfn = A("1 inf, 1 bb"), A("1 inf, 2 tank")
    win.set_battle(att, dfn)
    html = win.compute_blocking()
    assert "Amphibious" in html
    plain = solve(A("1 inf"), dfn)["attacker_win"]
    assert pcts(html)[0] > 100 * plain


def test_mixed_land_and_sea_defenders_are_rejected(win):
    win.set_battle(A("2 inf"), A("1 inf, 1 des"))
    with pytest.raises(ValueError):
        win.build_job()


def test_one_sided_input_is_rejected(win):
    win.set_battle(A("2 inf"), A(""))
    with pytest.raises(ValueError):
        win.build_job()


def test_empty_input_does_nothing(win):
    assert win.build_job() is None
    assert win.compute_blocking() == ""


def test_policies(win):
    att, dfn = A("3 inf, 2 tank, 1 ftr"), A("4 inf, 1 tank, 1 ftr")
    win.set_battle(att, dfn)
    win.policy.combos["Attacker"].setCurrentIndex(1)  # attacker chooses optimally
    html = win.compute_blocking()
    assert pcts(html)[0] == pytest.approx(100 * solve(att, dfn, OPTIMAL, STANDARD)["attacker_win"], abs=0.06)
    win.policy.combos["Defender"].setCurrentIndex(1)  # both optimal is not allowed
    with pytest.raises(ValueError):
        win.compute_blocking()
    win.policy.combos["Attacker"].setCurrentIndex(0)
    win.policy.combos["Defender"].setCurrentIndex(2)
    with pytest.raises(ValueError):  # custom order needs unit names
        win.policy.policies()
    win.policy.orders["Defender"].setText("ftr,tank,inf")
    html = win.compute_blocking()
    assert pcts(html)


def test_whole_amphibious_assault_matches_the_calculator(win):
    win.mode.setCurrentIndex(1)
    win.a_fleet.set_values({"trn": 2, "des": 1})
    win.a_cargo.set_values({"inf": 2, "tank": 2})
    win.a_air.set_values({"ftr": 1})
    win.a_bomb.set_values({"bb": 1, "cru": 1})
    win.d_fleet.set_values({"sub": 1})
    win.d_land.set_values({"inf": 3, "aa": 1})
    html = win.compute_blocking()
    want = solve_amphibious(A("2 trn, 1 des"), A("2 inf, 2 tank"), A("1 ftr"), A("1 sub"), A("3 inf, 1 aa"),
                            A("1 bb, 1 cru"))
    assert pcts(html)[0] == pytest.approx(100 * want["attacker_win"], abs=0.06)
    assert "landing made" in html and "Failed at sea" in html
    landing = float(re.search(r"landing made</td><td[^>]*><b>\s*([\d.]+)%", html).group(1))
    assert landing == pytest.approx(100 * want["p_landing"], abs=0.06)


def test_amphibious_requires_defenders(win):
    win.mode.setCurrentIndex(1)
    win.a_cargo.set_values({"inf": 2})
    with pytest.raises(ValueError):
        win.build_job()


def test_bombing_raid(win):
    win.mode.setCurrentIndex(2)
    win.sbr["bombers"].setValue(3)
    win.sbr["ipc"].setValue(4)
    win.sbr["damage"].setValue(1)
    html = win.compute_blocking()
    want = solve_sbr(3, 4, 1)
    assert f'{want["expected_damage"]:.2f}' in html
    assert "damage cap" in html


def test_clear_resets_everything(win):
    win.set_battle(A("3 inf"), A("2 inf"))
    win.policy.combos["Attacker"].setCurrentIndex(1)
    win.clear_all()
    assert not any(win.att.values().values()) and not any(win.dfn.values().values())
    assert win.policy.combos["Attacker"].currentIndex() == 0


def test_load_from_game_panel(app):
    got = {"v": (A("2 inf, 1 bb"), A("3 inf"))}
    w = CustomBattleWindow(lambda: got["v"], exclude_from_capture=False)
    try:
        w.load_from_panel()
        assert w.att.values()["bb"] == 1 and w.dfn.values()["inf"] == 3
        got["v"] = None
        w.load_from_panel()
        assert "No game attack panel" in w.result.text()
    finally:
        w.shutdown()


def test_typing_updates_the_result_in_the_background(app, win):
    win.set_battle(A("3 inf"), A("2 inf"))
    win.evaluate()
    end = time.time() + 15
    while time.time() < end and "Attacker wins" not in win.result.text():
        app.processEvents()
        time.sleep(0.05)
    assert "Attacker wins" in win.result.text()


def test_no_aa_hotkey_left_in_the_app():
    from aaoverlay import app as live
    assert "f11" not in live.VK or "f11" not in open(live.__file__, encoding="utf-8").read().split("def poll_keys")[1]
    assert not hasattr(service, "manual_aa")
