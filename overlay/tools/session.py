"""Restart the game and open one of the generated scenario campaigns (offline training data only).

Uses ``ui_driver`` (menu mode: only the fixed menu buttons; ONLINE and DELETE GAME are forbidden).

Usage:
    python tools\\session.py exit                 # leave the current game and quit to desktop
    python tools\\session.py launch               # start the game through Steam and wait for the menu
    python tools\\session.py open germany         # open the "Overlay germany" campaign from the main menu
"""
import glob
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ui_driver as U  # noqa: E402

ORDER_FILE = os.path.join(os.path.dirname(HERE), "dataset", "scenarios", "_order.json")


def game_running():
    # tasklist truncates long image names in its table, so test for its "nothing found" message instead
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AxisAndAllies1942Online.exe"],
                         capture_output=True, text=True).stdout
    return "No tasks are running" not in out and "AxisAndAllies1942Online" in out


def in_game():
    """An open offline game autosaves every few seconds; a menu does not."""
    name, age = U.newest_save()
    return name is not None and age is not None and age < 10


def exit_game(log=print, ingame=None):
    """Quit the game to the desktop (from inside a game, or from the main menu).

    ``ingame`` overrides the autosave-age guess (an idle open game stops autosaving).
    """
    d = U.Driver(mode="menu", log=log)
    if ingame if ingame is not None else in_game():
        for step in ("gear", "return_to_main", "confirm_return"):
            d.menu(step)
            d.wait(2.0)
        d.wait(2.0)
    d.menu("exit_to_desktop_collapsed")
    d.wait(1.5)
    d.menu("confirm_exit")
    for _ in range(40):
        if not game_running():
            log("game exited")
            return
        time.sleep(1)
    log("game still running after 40s; ending the process (all games are already saved)")
    subprocess.run(["taskkill", "/F", "/IM", "AxisAndAllies1942Online.exe"], capture_output=True)
    time.sleep(3)
    if game_running():
        raise U.Refused("game did not exit")


def launch_game(log=print, wait=28):
    os.startfile("steam://rungameid/898920")
    for _ in range(150):
        if game_running() and U.find_game_window():
            break
        time.sleep(1)
    else:
        raise U.Refused("game did not start")
    time.sleep(wait)  # splash, login screen and main menu
    log("game started")


def open_campaign(power, index, game_id, log=print, tries=2):
    """Open the campaign ``Overlay <power>`` (card ``index`` in the list) and verify by its game id."""
    want = f"{U.SCENARIO_PREFIX}{power}"
    d = U.Driver(mode="menu", log=log)
    for attempt in range(tries):
        d.menu("single_player")
        d.wait(4.0)
        d.card(index)
        for _ in range(25):
            name, age = U.newest_save()
            if name == want and U.newest_save_id() == str(game_id) and age is not None and age < 12:
                d.wait(4.0)  # let the map finish drawing
                log(f"opened {want}")
                return
            time.sleep(1)
        log(f"attempt {attempt + 1}: expected {want!r}, newest save is {name!r}")
        # back to the menu and try again
        for step in ("gear", "return_to_main", "confirm_return"):
            try:
                d.menu(step)
                d.wait(2.0)
            except U.Refused:
                break
    raise U.Refused(f"could not open {want}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "exit":
        exit_game(ingame=("--ingame" in sys.argv) or None)
    elif cmd == "launch":
        launch_game()
    elif cmd == "open":
        open_campaign(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    else:
        sys.exit(__doc__)


def to_main_menu(log=print):
    """From inside a campaign, return to the main menu (the game autosaves first)."""
    d = U.Driver(mode="menu", log=log)
    for step in ("gear", "return_to_main", "confirm_return"):
        d.menu(step)
        d.wait(2.0)
    d.wait(3.0)
