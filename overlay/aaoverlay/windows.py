"""Screen helpers shared by the dataset tools and the live collector.

``crop_box`` gives the region of a frame that holds the attack panel; ``occluding_windows`` names any other
window floating over a screen region, so a capture never contains unrelated content.
"""
import os

from . import reader as R


def crop_box(img, panel):
    """Panel crop with room for the title, emblem and the non-combatants button."""
    bars = R.find_bars(img, panel.cx)
    title_top = min(b[0] for b in bars) if bars else panel.bounds[1]
    left, top, right, bottom = panel.bounds
    h, w = img.shape[:2]
    return (max(0, left - 40), max(0, title_top - 110), min(w, right + 40), min(h, bottom + 120))


def occluding_windows(box):
    """Process names of visible windows above the game that overlap ``box`` (screen pixels).

    Another window floating over the panel would corrupt the sample (and could put
    unrelated content into the dataset), so ``snap`` refuses to capture when any exists.
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    GW_HWNDPREV = 3
    pid = wintypes.DWORD()

    def proc_name(hwnd):
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return "?"
        buf = ctypes.create_unicode_buffer(260)
        size = wintypes.DWORD(260)
        k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        return os.path.basename(buf.value) or "?"

    game = None
    fg = user32.GetForegroundWindow()
    if "axisandallies" in proc_name(fg).lower():
        game = fg
    else:  # find the game's top-level window
        found = []
        enum = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(enum(lambda h, _: (found.append(h) if user32.IsWindowVisible(h) and
                                              "axisandallies" in proc_name(h).lower() else None) or True), 0)
        game = found[0] if found else None
    if game is None:
        return ["game window not found"]
    x0, y0, x1, y1 = box
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    out = []
    hwnd = user32.GetWindow(game, GW_HWNDPREV)
    while hwnd:
        r = wintypes.RECT()
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd) and user32.GetWindowRect(hwnd, ctypes.byref(r)):
            exstyle = user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF  # GWL_EXSTYLE
            # Click-through layers (WS_EX_TRANSPARENT) draw nothing that could cover the panel,
            # e.g. the full-screen cursor indicator used while driving the game.
            click_through = bool(exstyle & 0x20)
            if (not click_through and r.right - r.left > 40 and r.bottom - r.top > 40
                    and r.left < x1 and r.right > x0 and r.top < y1 and r.bottom > y0):
                out.append(proc_name(hwnd))
        hwnd = user32.GetWindow(hwnd, GW_HWNDPREV)
    return sorted(set(out))


def screen_height():
    """Height in pixels of the primary screen (0 if unknown)."""
    try:
        import ctypes
        return int(ctypes.windll.user32.GetSystemMetrics(1))
    except Exception:
        return 0
