"""A small, deliberately restricted mouse driver for the offline training-data work.

Approved use: drive the game's OFFLINE scenario copies named "Overlay <power>" to open battle panels
for screenshots. Safety rules enforced in code:

* clicks go only to the game's own window, and only while it is the foreground window;
* in scenario mode a click is refused unless the newest save file is a scenario copy whose name starts
  with "Overlay " and was written in the last 40 seconds (so never the user's real game, never online play);
* in menu mode only a fixed list of named buttons can be clicked (``MENU``), and the ONLINE and
  DELETE GAME buttons are in a forbidden zone that is refused outright;
* holding / pressing Esc at any time aborts everything immediately (``AbortRequested``);
* every action is logged and rate limited.

Coordinates are in the 1456x819 frame used by the screen-control tool and are scaled to the window.
"""
import ctypes
import glob
import json
import os
import threading
import time
from ctypes import wintypes

FRAME_W, FRAME_H = 1456, 819
SAVE_DIR = os.path.join(os.environ.get("APPDATA", ""), "axisandalliesonline-desktop", "local-games")
SCENARIO_PREFIX = "Overlay "
FRESH_SECONDS = 40
MIN_GAP = 0.25  # seconds between actions

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.SetProcessDPIAware()
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

VK_ESCAPE = 0x1B
LEFTDOWN, LEFTUP = 0x0002, 0x0004

# Fixed menu buttons (frame coordinates). Only these may be clicked in menu mode.
MENU = {
    "gear": (30, 30),                  # in-game: open the menu
    "return_to_main": (728, 497),      # in-game menu
    "confirm_return": (725, 443),      # "Are you sure you want to return to main menu?"
    "single_player": (148, 108),       # main menu
    "exit_to_desktop_collapsed": (172, 341),  # main menu with no submenu open
    "exit_to_desktop_expanded": (174, 376),   # main menu with Single Player expanded
    "confirm_exit": (727, 443),
}
# Regions that must never be clicked (frame coordinates). The ONLINE menu entry is forbidden everywhere;
# free clicks inside a game may not touch the gear icon, so the in-game menu (with its DELETE GAME
# button) can never be opened in scenario mode; the main menu's campaign cards are clicked via ``card``.
FORBIDDEN_ALWAYS = [(20, 50, 260, 90)]
FORBIDDEN_INGAME = [(0, 0, 80, 75)]  # the gear icon: scenario mode never opens the in-game menu
# Single Player list: card centres by position (frame coordinates, x fixed).
CARD_X = 640
CARD_Y = [158, 287, 417, 547, 678]


class AbortRequested(Exception):
    pass


class Refused(Exception):
    pass


def _proc_name(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(260)
    size = wintypes.DWORD(260)
    kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower()


def find_game_window():
    found = []
    enum = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _proc_name(hwnd) == "axisandallies1942online.exe":
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            if r.right - r.left > 400:
                found.append(hwnd)
        return True

    user32.EnumWindows(enum(cb), 0)
    return found[0] if found else None


def newest_save_id():
    """File id (the game id) of the most recently written offline save, or None."""
    files = glob.glob(os.path.join(SAVE_DIR, "*.json"))
    return os.path.basename(max(files, key=os.path.getmtime))[:-5] if files else None


def newest_save():
    """``(name, age_seconds)`` of the most recently written offline save, or ``(None, None)``."""
    files = glob.glob(os.path.join(SAVE_DIR, "*.json"))
    if not files:
        return None, None
    f = max(files, key=os.path.getmtime)
    try:
        with open(f, encoding="utf-8") as fh:
            name = json.load(fh).get("name", "")
    except (OSError, ValueError):
        return None, None
    return name, time.time() - os.path.getmtime(f)


class Driver:
    def __init__(self, mode="scenario", log=print):
        assert mode in ("scenario", "menu")
        self.mode, self.log = mode, log
        self.hwnd = find_game_window()
        if not self.hwnd:
            raise Refused("game window not found")
        self._abort = threading.Event()
        self._last = 0.0
        t = threading.Thread(target=self._watch_escape, daemon=True)
        t.start()

    # -- safety ----------------------------------------------------------------
    def _watch_escape(self):
        while True:
            if user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000:
                self._abort.set()
            time.sleep(0.03)

    def check(self):
        if self._abort.is_set():
            raise AbortRequested("Esc pressed: stopping")

    def foreground(self):
        """Bring the game to the front (only the game window; nothing else is ever activated)."""
        if user32.GetForegroundWindow() != self.hwnd:
            user32.keybd_event(0x12, 0, 0, 0)       # a tap of Alt lets SetForegroundWindow succeed
            user32.keybd_event(0x12, 0, 2, 0)
            user32.ShowWindow(self.hwnd, 9)
            user32.SetForegroundWindow(self.hwnd)
            time.sleep(0.4)
        if user32.GetForegroundWindow() != self.hwnd:
            raise Refused("the game is not the foreground window")

    def _guard_scenario(self):
        name, age = newest_save()
        if self.mode == "scenario" and not (name and name.startswith(SCENARIO_PREFIX) and age is not None
                                            and age < FRESH_SECONDS):
            raise Refused(f"newest save is not a fresh scenario copy (name={name!r}, age={age}); "
                          "refusing to click: this might be your real or an online game")

    def _screen_point(self, fx, fy):
        r = wintypes.RECT()
        user32.GetWindowRect(self.hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        return r.left + int(fx * w / FRAME_W), r.top + int(fy * h / FRAME_H)

    @staticmethod
    def _forbidden(fx, fy, zones=None):
        zones = FORBIDDEN_ALWAYS + FORBIDDEN_INGAME if zones is None else zones
        return any(x0 <= fx <= x1 and y0 <= fy <= y1 for x0, y0, x1, y1 in zones)

    def _pre(self, fx, fy):
        self.check()
        if not (0 <= fx <= FRAME_W and 0 <= fy <= FRAME_H):
            raise Refused(f"click outside the window: {(fx, fy)}")
        if self._forbidden(fx, fy):
            raise Refused(f"click in a forbidden zone: {(fx, fy)}")
        self.foreground()
        self._guard_scenario()
        gap = MIN_GAP - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)

    # -- actions ---------------------------------------------------------------
    def click(self, fx, fy, note=""):
        if self.mode == "menu":
            raise Refused("menu mode only allows named menu buttons (use .menu)")
        self._pre(fx, fy)
        self._click_raw(fx, fy, note)

    def _click_raw(self, fx, fy, note):
        x, y = self._screen_point(fx, fy)
        user32.SetCursorPos(x, y)
        time.sleep(0.05)
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
        time.sleep(0.05)
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        self._last = time.time()
        self.log(f"click ({fx},{fy}) {note}")

    def menu(self, name):
        """Click one of the fixed menu buttons by name."""
        if name not in MENU:
            raise Refused(f"unknown menu button {name!r}")
        fx, fy = MENU[name]
        self._pre_menu(fx, fy)
        self._click_raw(fx, fy, f"[menu {name}]")

    def card(self, index):
        """Click the Nth campaign card in the Single Player list (0 = the first card)."""
        if not 0 <= index < len(CARD_Y):
            raise Refused("card index out of range (scroll not supported)")
        fx, fy = CARD_X, CARD_Y[index]
        self._pre_menu(fx, fy)
        self._click_raw(fx, fy, f"[campaign card {index}]")

    def _pre_menu(self, fx, fy):
        self.check()
        if self._forbidden(fx, fy, FORBIDDEN_ALWAYS):
            raise Refused(f"click in a forbidden zone: {(fx, fy)}")
        self.foreground()
        gap = MIN_GAP - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)

    def drag(self, f1, f2, steps=12):
        if self.mode == "menu":
            raise Refused("no dragging in menu mode")
        for p in (f1, f2):
            self._pre(*p)
        x1, y1 = self._screen_point(*f1)
        x2, y2 = self._screen_point(*f2)
        user32.SetCursorPos(x1, y1)
        time.sleep(0.05)
        user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
        for i in range(1, steps + 1):
            self.check()
            user32.SetCursorPos(x1 + (x2 - x1) * i // steps, y1 + (y2 - y1) * i // steps)
            time.sleep(0.02)
        user32.mouse_event(LEFTUP, 0, 0, 0, 0)
        self._last = time.time()
        self.log(f"drag {f1}->{f2}")

    def wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check()
            time.sleep(0.05)
