"""The overlay: a transparent, click-through window that shows battle odds.

Threads
-------
* ``ReaderThread``  grabs the screen a couple of times a second and reads the
  attack panel (``reader.read_panel``). It only looks at pixels.
* ``SolverThread``  runs ``service.compute`` for the newest reading and cancels
  stale work when the setup changes.
* The GUI thread only renders.

The window never takes focus or mouse input (``WindowTransparentForInput`` +
``WindowDoesNotAcceptFocus``), and is excluded from screen capture so the
reader never sees the overlay itself.

Hotkeys (polled, not registered, so the game still receives its own keys):
    Ctrl+Alt+F7   open / close the Teach window (label panels the reader could not read; see teachwin.py)
    Ctrl+Alt+F8   open / close the custom battle window (type in any units; see custom.py)
    Ctrl+Alt+F9   show / hide the overlay
    Ctrl+Alt+F10  force a re-read of the panel
    Ctrl+Alt+F12  quit
"""
import contextlib
import ctypes
import sys
import threading
import time

import cv2
import numpy as np
from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import aaoverlay  # noqa: F401
from . import reader as R
from . import service
from .render import render  # noqa: F401  (re-exported for callers and tests)
from .collect import Collector
from .custom import CustomBattleWindow, WDA_EXCLUDEFROMCAPTURE
from .teachwin import TeachWindow
from .templates import TemplateStore
from . import windows as W
from .perf import Perf
from aa1942.units import AA, army_cost, fmt_army

VK = {"ctrl": 0x11, "alt": 0x12, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B}


def _key_down(name):
    return bool(ctypes.windll.user32.GetAsyncKeyState(VK[name]) & 0x8000)


class ReaderThread(QThread):
    """Reads the attack panel from the live screen (or a fixed image for tests)."""
    reading = Signal(object)

    SAME_PIXELS = 2       # the panel itself counts as unchanged when no pixel differs by more than this
    REGION_SIDE, REGION_TOP, REGION_BOTTOM = 200, 200, 400   # extra area watched around the panel (1440p pixels)
    RING_DIFF = 24        # in that margin a pixel counts as changed when a channel differs by more than this ...
    RING_FRACTION = 0.004  # ... and the margin as changed when more than this fraction of it did
    CONFIRM_WAIT = 0.03   # a new or changed panel is read again almost at once: the overlay acts on two agreeing reads

    def __init__(self, interval=0.2, image_path=None, debug=False, perf=None):
        super().__init__()
        self.interval, self.image_path, self.debug, self.perf = interval, image_path, debug, perf
        self.force = threading.Event()
        self.reload = threading.Event()   # set after templates changed on disk (teach / undo)

    def _timed(self, name):
        return self.perf.timer(name) if self.perf else contextlib.nullcontext()

    @classmethod
    def _region(cls, shot_shape, mon, panel):
        """What to watch once ``panel`` is known: ``(box, rect, core)``.

        ``box`` is the monitor-local screen rectangle ``(x0, y0, x1, y1)``, ``rect`` the same as a global ``mss`` dict
        and ``core`` the panel itself (title, rows, buttons) relative to ``box``. The box reaches well beyond the
        panel (``REGION_SIDE`` / ``REGION_TOP`` / ``REGION_BOTTOM`` px at 1440p) so a panel that grows, the lists and
        buttons around it, or anything else that touches the combat data is seen.
        """
        fh, fw = panel.frame.shape[:2]
        cx0, cy0, cx1, cy1 = W.crop_box(panel.frame, panel)
        bx0, by0 = max(0, cx0 - cls.REGION_SIDE), max(0, cy0 - cls.REGION_TOP)
        bx1, by1 = min(fw, cx1 + cls.REGION_SIDE), min(fh, cy1 + cls.REGION_BOTTOM)
        s = shot_shape[0] / fh
        x0, y0 = int(bx0 * s), int(by0 * s)
        x1, y1 = min(int(bx1 * s), shot_shape[1]), min(int(by1 * s), shot_shape[0])
        core = (int(cx0 * s) - x0, int(cy0 * s) - y0, min(int(cx1 * s), x1) - x0, min(int(cy1 * s), y1) - y0)
        return (x0, y0, x1, y1), {"left": mon["left"] + x0, "top": mon["top"] + y0, "width": x1 - x0,
                                  "height": y1 - y0}, core

    def _same_region(self, now, before, core):
        """No change that matters: the panel itself is pixel-identical (within ``SAME_PIXELS``), and the margin
        around it has at most a sprinkle of changed pixels (the map behind it animates)."""
        if now.shape != before.shape:
            return False
        diff = cv2.absdiff(now, before)
        cx0, cy0, cx1, cy1 = core
        if int(diff[cy0:cy1, cx0:cx1].max()) > self.SAME_PIXELS:
            return False
        diff[cy0:cy1, cx0:cx1] = 0
        ring = diff.size - 3 * (cx1 - cx0) * (cy1 - cy0)
        return np.count_nonzero(diff > self.RING_DIFF) <= self.RING_FRACTION * max(ring, 1)

    def _unchanged_panel(self, sct, cached):
        """The previous panel when the pixels around it are unchanged (so reading it again would give the same
        answer), else ``None``. Costs one small grab instead of a full-screen grab and read."""
        panel, rect, pixels, core = cached
        with self._timed("region"):
            try:
                same = self._same_region(np.array(sct.grab(rect))[:, :, :3], pixels, core)
            except Exception as exc:  # the shortcut failing must only cost speed: read the whole screen instead
                print(f"[reader] region check failed: {exc!r}", file=sys.stderr)
                same = False
        return panel if same else None

    def run(self):
        import mss
        store = TemplateStore()
        fixed = cv2.imread(self.image_path) if self.image_path else None
        sct = None if fixed is not None else mss.mss()
        cached = None        # (panel, global grab rect, its pixels) of the last live panel
        last_sig = None      # signature of the previous reading (None while no panel was seen)
        winmm = ctypes.windll.winmm
        winmm.timeBeginPeriod(1)   # 1 ms timer resolution: Windows' 15.6 ms steps would stretch a 0.2 s interval
        try:
            while not self.isInterruptionRequested():
                tick = time.monotonic()
                if self.reload.is_set():
                    store.load()
                    self.reload.clear()
                    cached = None
                panel, from_cache = None, False
                if cached is not None:
                    panel = self._unchanged_panel(sct, cached)
                    from_cache = panel is not None
                if not from_cache:
                    cached = None
                    with self._timed("grab"):
                        shot = fixed if fixed is not None else np.array(sct.grab(sct.monitors[1]))[:, :, :3]
                    t0 = time.monotonic()
                    try:
                        with self._timed("read"):
                            panel = R.read_panel(shot, store)
                    except Exception as exc:  # never let a bad frame kill the reader
                        panel = None
                        print(f"[reader] error: {exc!r}", file=sys.stderr)
                    if self.debug:
                        print(f"[reader] {'panel' if panel else 'no panel'} in {1000 * (time.monotonic() - t0):.0f} ms")
                    if panel is not None and fixed is None:
                        try:
                            (x0, y0, x1, y1), rect, core = self._region(shot.shape, sct.monitors[1], panel)
                            cached = (panel, rect, shot[y0:y1, x0:x1].copy(), core)
                        except Exception:  # no region, no shortcut: every tick reads the whole screen as before
                            cached = None
                elif self.debug:
                    print("[reader] panel region unchanged")
                self.reading.emit(panel)
                sig = panel.signature() if panel is not None else None
                # The next check is due ``interval`` after the start of this one (or very soon, to confirm a new or
                # changed panel), but never immediately: other threads need the CPU too.
                due = max(tick + (self.CONFIRM_WAIT if sig is not None and sig != last_sig else self.interval),
                          time.monotonic() + 0.005)
                last_sig = sig
                while not self.isInterruptionRequested():
                    remaining = due - time.monotonic()
                    if remaining <= 0 or self.force.wait(min(remaining, 0.02)):
                        break
                if self.force.is_set():
                    cached = None  # F10: read the whole screen again
                self.force.clear()
        finally:
            winmm.timeEndPeriod(1)
            if sct is not None:
                sct.close()


class SolverThread(QThread):
    """Runs the newest compute job; older jobs are cancelled."""
    result = Signal(dict)

    def __init__(self, exact_seconds, perf=None):
        super().__init__()
        self.exact_seconds = exact_seconds
        self.perf = perf
        self._job = None
        self._job_id = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()

    def submit(self, att, dfn):
        with self._lock:
            self._job_id += 1
            self._job = (self._job_id, att, dfn)
        service.request_cancel()
        self._wake.set()
        return self._job_id

    def run(self):
        while not self.isInterruptionRequested():
            if not self._wake.wait(0.2):
                continue
            self._wake.clear()
            with self._lock:
                job = self._job
            if job is None:
                continue
            job_id, att, dfn = job
            try:
                t0 = time.perf_counter()

                def emit(r, j=job_id):
                    if self.perf:
                        self.perf.record("solve:" + r.get("stage", "?"), 1000 * (time.perf_counter() - t0))
                    self.result.emit({**r, "job": j})

                service.compute(att, dfn, emit, exact_seconds=self.exact_seconds,
                                spread_delay=service.LIVE_SPREAD_DELAY_SECONDS,
                                spread_budget=service.LIVE_SPREAD_BUDGET_SECONDS,
                                quick_exact=service.LIVE_QUICK_EXACT_SECONDS)
            except Exception as exc:  # e.g. a reading the calculator rejects: say so, and keep the thread alive
                self.result.emit({"job": job_id, "error": str(exc) or type(exc).__name__, "outcomes": None})


class Overlay(QWidget):
    """The click-through window."""

    def __init__(self, exclude_from_capture=True, width=540):
        super().__init__()
        self.exclude = exclude_from_capture
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool |
                            Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput |
                            Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setFixedWidth(width)
        self.label = QLabel()
        self.label.setTextFormat(Qt.RichText)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("background-color: rgba(14, 17, 22, 228); border-radius: 12px; padding: 14px;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.label)
        geo = QGuiApplication.primaryScreen().geometry()
        self._anchor = (geo.x() + int(geo.width() * 0.675), geo.y() + int(geo.height() * 0.17))

    def place_beside(self, bounds):
        """Anchor the overlay next to the game's panel (``bounds`` in 1440p pixels).

        It goes to the right of the panel when it fits, otherwise to the left, so it
        never covers the units being read. The panel moves when the map is panned.
        """
        geo = QGuiApplication.primaryScreen().geometry()
        k = geo.height() / R.BASE_H  # 1440p pixels -> Qt logical pixels
        left, top, right, bottom = (v * k for v in bounds)
        gap = 28 * k
        x = geo.x() + right + gap
        if x + self.width() > geo.x() + geo.width():
            x = geo.x() + left - gap - self.width()
        x = max(geo.x(), min(x, geo.x() + geo.width() - self.width()))
        y = max(geo.y(), min(geo.y() + top - 40 * k, geo.y() + geo.height() - self.height()))
        self._anchor = (int(x), int(y))

    def set_html(self, text):
        self.label.setText(text)
        # Size to the wrapped text; adjustSize() can request less than the layout's minimum,
        # which makes Qt log "Unable to set geometry" warnings.
        width = self.width()
        height = max(self.label.heightForWidth(width), self.label.minimumSizeHint().height())
        self.setFixedSize(width, height)
        self.move(*self._anchor)

    def showEvent(self, e):
        super().showEvent(e)
        if self.exclude:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)


class Controller:
    """Glue: readings -> solver jobs -> overlay text."""

    def __init__(self, app, opts):
        self.opts = opts
        self.overlay = Overlay(exclude_from_capture=not opts.no_exclude)
        self.perf = Perf() if getattr(opts, "perf", False) else None
        self.reader = ReaderThread(opts.interval, opts.image, opts.debug, self.perf)
        self.solver = SolverThread(opts.exact_seconds, self.perf)
        self.visible = True
        self.last_armies = None
        self.collector = Collector(enabled=True if getattr(opts, "collect", False) else None)
        if getattr(opts, "collect", False):
            self.collector.set_enabled(True)
        self.custom = CustomBattleWindow(self.last_panel_armies, opts.exact_seconds,
                                         exclude_from_capture=not opts.no_exclude)
        self.teachwin = TeachWindow(self.collector, lambda: TemplateStore(), self.templates_changed,
                                    exclude_from_capture=not opts.no_exclude)
        self.missing = 0
        self.last_key = None
        self._pending_key = None
        self.bounds = (0, 0, 0, 0)
        self.job = 0
        self.state = {}
        self.reader.reading.connect(self.on_reading)
        self.solver.result.connect(self.on_result)
        self._prev_keys = set()
        self.timer = QTimer()
        self.timer.timeout.connect(self.poll_keys)
        self.timer.start(60)
        if self.perf:
            self._stall_t = time.monotonic()
            self.stall_timer = QTimer()          # how late the GUI thread runs a 20 ms tick = how long it was blocked
            self.stall_timer.timeout.connect(self._tick_stall)
            self.stall_timer.start(20)
            self.report_timer = QTimer()
            self.report_timer.timeout.connect(lambda: print(self.perf.report(), flush=True))
            self.report_timer.start(5000)
        self.app = app
        self.reader.start()
        self.solver.start()

    def _tick_stall(self):
        now = time.monotonic()
        self.perf.record("gui_stall", max(0.0, 1000 * (now - self._stall_t) - 20))
        self._stall_t = now

    # -- panel readings -------------------------------------------------------
    def last_panel_armies(self):
        """``(att, dfn)`` of the panel currently being read, for the custom window's 'Load' button."""
        return self.last_armies

    def templates_changed(self):
        """The templates on disk changed (teach / undo): the reader thread reloads them."""
        self.reader.reload.set()
        self.last_key = None

    def on_reading(self, panel):
        if self.custom.isVisible() or self.teachwin.isVisible():  # the custom window is open: the live overlay is paused
            self.overlay.hide()
            self.last_key = None
            self._pending_key = None
            if panel is not None:
                att, dfn, warnings = R.panel_to_armies(panel)
                self.last_armies = None if warnings else (att, dfn)
            return
        if panel is None:
            self.missing += 1
            if self.missing >= 3:  # tolerate a dropped frame before hiding
                self.overlay.hide()
                self.last_key = None
            return
        self.missing = 0
        self.bounds = panel.bounds
        att, dfn, warnings = R.panel_to_armies(panel)
        self.last_armies = None if warnings else (att, dfn)
        # Defending AA guns are on the panel (die 1) whenever the attackers have aircraft, which is the only
        # time they matter; against ground attackers they are hidden behind "VIEW NON COMBATANTS" and never roll.
        key = (panel.signature(), tuple(warnings))
        if key == self.last_key:
            self._pending_key = None
            self.overlay.place_beside(self.bounds)
            self.overlay.move(*self.overlay._anchor)
            if self.visible and not self.overlay.isVisible():
                self.overlay.show()
            return
        # Confirm a change over two consecutive frames so half-drawn animation frames
        # (the panel opening, the map panning) never flash warnings or trigger solves.
        if key != self._pending_key:
            self._pending_key = key
            return
        self._pending_key = None
        self.last_key = key
        self.collector.offer(panel)  # opt-in: keeps panels the reader could not read (or barely read)
        self.state = {"warnings": warnings, "att_text": fmt_army(att), "def_text": fmt_army(dfn),
                      "att_ipc": army_cost(att), "def_ipc": army_cost(dfn), "aa": dfn[AA], "result": None}
        if self.opts.debug:
            print(f"[panel] att={fmt_army(att)} | def={fmt_army(dfn)} | warnings={warnings}")
        self.refresh()
        if not warnings:
            self.job = self.solver.submit(att, dfn)

    def on_result(self, res):
        if res.get("job") != self.job:
            return  # stale result from a cancelled job
        self.state["result"] = res
        if self.opts.debug and res.get("outcomes"):
            o = res["outcomes"]
            print(f"[result] {res['stage']} att={o['attacker_win']:.4f} def={o['defender_win']:.4f}")
        self.refresh()

    def refresh(self):
        self.overlay.set_html(render(self.state))
        self.overlay.place_beside(self.bounds)
        self.overlay.move(*self.overlay._anchor)
        if self.visible and not self.overlay.isVisible():
            self.overlay.show()

    # -- hotkeys ---------------------------------------------------------------
    def poll_keys(self):
        if not (_key_down("ctrl") and _key_down("alt")):
            self._prev_keys = set()
            return
        for name in ("f7", "f8", "f9", "f10", "f12"):
            if _key_down(name) and name not in self._prev_keys:
                self._prev_keys.add(name)
                self.on_hotkey(name)
            elif not _key_down(name):
                self._prev_keys.discard(name)

    def _toggle(self, win):
        if win.isVisible():
            win.hide()
            self.last_key = None  # the live overlay resumes on the next reading
        else:
            self.overlay.hide()
            win.show()
            win.raise_()
            win.activateWindow()

    def on_hotkey(self, name):
        if name == "f7":
            self._toggle(self.teachwin)
        elif name == "f8":
            self._toggle(self.custom)
        elif name == "f9":
            self.visible = not self.visible
            self.overlay.setVisible(self.visible and self.last_key is not None)
        elif name == "f10":
            self.last_key = None
            self.reader.force.set()
        elif name == "f12":
            self.shutdown()

    def shutdown(self):
        self.reader.requestInterruption()
        self.solver.requestInterruption()
        self.custom.shutdown()
        self.teachwin.hide()
        service.request_cancel()
        self.reader.wait(2000)
        self.solver.wait(2000)
        self.overlay.hide()
        self.app.quit()


def lower_priority():
    """Run below normal priority so the game always gets the CPU first (the overlay is a background helper)."""
    BELOW_NORMAL_PRIORITY_CLASS = 0x4000
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    return bool(k32.SetPriorityClass(k32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))


def run(opts):
    if not getattr(opts, "normal_priority", False):
        lower_priority()
    cv2.setNumThreads(2)  # OpenCV defaults to one thread per core: about 20% more CPU per read for 5 ms of speed
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    ctl = Controller(app, opts)
    if opts.run_seconds:
        QTimer.singleShot(int(opts.run_seconds * 1000), ctl.shutdown)
    return app.exec()
