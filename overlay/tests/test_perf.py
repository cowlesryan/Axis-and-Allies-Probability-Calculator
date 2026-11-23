"""Performance work: the deferred/cheap casualty comparison, the reader's unchanged-region shortcut and idle
back-off, the lower CPU priority and the ``--perf`` log. Each shortcut must change speed only, never an answer."""
import ctypes
import os
import sys
import threading
import time
import types
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PySide6.QtWidgets import QApplication

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import aaoverlay  # noqa: E402,F401
from aaoverlay import app as APP  # noqa: E402
from aaoverlay import dataset as D  # noqa: E402
from aaoverlay import reader as R  # noqa: E402
from aaoverlay import service  # noqa: E402
from aaoverlay.perf import Perf  # noqa: E402
from aaoverlay.templates import TemplateStore  # noqa: E402
from aa1942.units import parse_army as A  # noqa: E402

SAMPLES = list(D.samples())


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def store():
    return TemplateStore()


def run(att, dfn, **kw):
    got = []
    t0 = time.perf_counter()
    service.compute(A(att), A(dfn), got.append, **kw)
    return got, time.perf_counter() - t0


# ---------------------------------------------------------------------------------------------------------------
# casualty comparison: later, cheaper, cancellable, and never different
# ---------------------------------------------------------------------------------------------------------------
def test_live_settings_are_cheap_and_deferred():
    assert service.LIVE_SPREAD_BUDGET_SECONDS <= 1.5 and service.LIVE_SPREAD_DELAY_SECONDS >= 1.0
    assert service.SIM_FALLBACK_SECONDS <= 2.5


def test_exact_result_is_emitted_before_the_delay_and_the_comparison_follows():
    stamps = []
    t0 = time.perf_counter()
    service.compute(A("3 inf, 1 tank"), A("4 inf"), lambda d: stamps.append((d["stage"], time.perf_counter() - t0)),
                    spread_delay=0.6)
    stages = [s for s, _t in stamps]
    assert stages == ["estimate", "exact", "final"]
    assert stamps[1][1] < 0.5                 # the answer does not wait for the extra
    assert stamps[2][1] >= 0.6                # the comparison waited


def test_a_new_job_during_the_wait_drops_the_comparison_quietly():
    got = []
    threading.Timer(0.2, service.request_cancel).start()
    out = service.compute(A("3 inf, 1 tank"), A("4 inf"), got.append, spread_delay=3.0)
    assert [g["stage"] for g in got][-1] == "exact" and "spread" not in got[-1] or not got[-1].get("spread")
    assert out["stage"] == "exact"


def test_delay_and_budget_never_change_the_numbers():
    for att, dfn in [("3 inf, 1 tank", "4 inf"), ("2 inf, 1 art, 1 ftr", "2 inf, 1 tank"), ("1 bb, 1 des", "2 des")]:
        a, _ = run(att, dfn)
        b, _ = run(att, dfn, spread_delay=0.1, spread_budget=2.0)
        assert a[-1]["outcomes"] == b[-1]["outcomes"] and a[-1]["spread"] == b[-1]["spread"], (att, dfn)


def test_a_mid_size_battle_no_longer_burns_seconds_on_the_comparison():
    got, took = run("6 inf, 2 art, 3 tank, 2 ftr", "8 inf, 3 tank, 1 art", spread_budget=0.3)
    assert got[-1]["method"] == "exact" and took < 3.0, took       # it used to take about 8 s
    sp = got[-1].get("spread")
    assert sp is None or set(sp) == {"standard", "attacker_optimal", "defender_optimal"}


def test_fallback_simulation_is_capped(monkeypatch):
    monkeypatch.setattr(service, "SIM_FALLBACK_SECONDS", 0.3)
    got, took = run("60 inf, 30 art, 30 tank", "60 inf, 30 tank", exact_seconds=0.2)
    assert got[-1]["method"] == "estimate" and got[-1]["runs"] and took < 6.0, took


def test_solver_thread_uses_the_live_settings(app, monkeypatch):
    seen = {}

    def fake(att, dfn, emit, **kw):
        seen.update(kw)
        emit({"stage": "final", "outcomes": None})
    monkeypatch.setattr(service, "compute", fake)
    th = APP.SolverThread(exact_seconds=2.0)
    got = []
    th.result.connect(got.append)
    th.start()
    th.submit(A("1 inf"), A("1 inf"))
    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    th.requestInterruption()
    th.wait(3000)
    assert seen["spread_delay"] == service.LIVE_SPREAD_DELAY_SECONDS
    assert seen["spread_budget"] == service.LIVE_SPREAD_BUDGET_SECONDS and seen["exact_seconds"] == 2.0


# ---------------------------------------------------------------------------------------------------------------
# reader: the unchanged-region shortcut
# ---------------------------------------------------------------------------------------------------------------
def panel_frame(store, pick=lambda m: True):
    for name, meta in SAMPLES:
        if pick(meta):
            frame = D.load_canvas(name, meta)
            panel = R.read_panel(frame, store)
            if panel is not None and not R.panel_to_armies(panel)[2]:
                return frame, panel
    pytest.skip("no suitable sample")


def test_region_is_inside_the_screen_and_covers_the_panel(store):
    frame, panel = panel_frame(store)
    mon = {"left": 0, "top": 0, "width": 2560, "height": 1440}
    (x0, y0, x1, y1), rect = APP.ReaderThread._region(frame.shape, mon, panel)
    assert 0 <= x0 < x1 <= frame.shape[1] and 0 <= y0 < y1 <= frame.shape[0]
    assert x0 <= panel.bounds[0] and x1 >= panel.bounds[2] and y0 <= panel.bounds[1] and y1 >= panel.bounds[3]
    assert rect == {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}
    # on a second monitor the global rectangle is offset, the local one is not
    (lx0, ly0, _x1, _y1), rect2 = APP.ReaderThread._region(frame.shape, {**mon, "left": 2560, "top": 100}, panel)
    assert (lx0, ly0) == (x0, y0) and rect2["left"] == 2560 + x0 and rect2["top"] == 100 + y0


def test_region_scales_with_the_screen_height(store):
    frame, panel = panel_frame(store)
    small = cv2.resize(frame, (1920, 1080))
    mon = {"left": 0, "top": 0, "width": 1920, "height": 1080}
    (x0, y0, x1, y1), _ = APP.ReaderThread._region(small.shape, mon, panel)
    (X0, Y0, X1, Y1), _ = APP.ReaderThread._region(frame.shape, {**mon, "width": 2560, "height": 1440}, panel)
    assert abs(x0 - X0 * 0.75) <= 1 and abs(y1 - Y1 * 0.75) <= 1


class FakeSct:
    """Stands in for ``mss.mss()``: a screen that tests can change, and a count of grabs by size."""

    def __init__(self, screen):
        self.screen = screen
        self.monitors = [None, {"left": 0, "top": 0, "width": screen.shape[1], "height": screen.shape[0]}]
        self.full, self.small = 0, 0

    def grab(self, rect):
        h, w = self.screen.shape[:2]
        if rect["width"] >= w and rect["height"] >= h:
            self.full += 1
        else:
            self.small += 1
        part = self.screen[rect["top"]:rect["top"] + rect["height"], rect["left"]:rect["left"] + rect["width"]]
        return np.dstack([part, np.full(part.shape[:2], 255, np.uint8)])

    def close(self):
        pass


@pytest.fixture
def live(monkeypatch, store, app):
    """A ReaderThread on a fake screen showing a panel; ``calls`` counts real reads."""
    frame, panel = panel_frame(store)
    sct = FakeSct(frame.copy())
    monkeypatch.setitem(sys.modules, "mss", types.SimpleNamespace(mss=lambda: sct))
    calls = []
    real = R.read_panel
    monkeypatch.setattr(R, "read_panel", lambda img, st: (calls.append(1), real(img, st))[1])
    th = APP.ReaderThread(interval=0.03)
    got = []
    th.reading.connect(got.append)
    yield SimpleNamespace(th=th, sct=sct, calls=calls, got=got, panel=panel)
    th.requestInterruption()
    th.wait(3000)


def wait_for(cond, seconds=8.0):
    end = time.time() + seconds
    while time.time() < end:
        QApplication.processEvents()        # signals from the reader thread are queued onto the GUI thread
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_an_unchanged_panel_is_read_once_then_reused(live):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 12)
    assert len(live.calls) == 1                          # one real read, then the cheap check
    assert live.sct.full == 1 and live.sct.small >= 10   # and one full-screen grab, then small ones
    assert all(g is not None for g in live.got) and len({id(g) for g in live.got}) == 1
    assert R.panel_to_armies(live.got[0])[:2] == R.panel_to_armies(live.panel)[:2]


def test_a_change_inside_the_panel_forces_a_real_read(live):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 4)
    (x0, y0, x1, y1), _ = APP.ReaderThread._region(live.sct.screen.shape, live.sct.monitors[1], live.panel)
    live.sct.screen[(y0 + y1) // 2 - 20:(y0 + y1) // 2 + 20, x0 + 5:x0 + 60] = (255, 0, 255)   # something new in it
    assert wait_for(lambda: len(live.calls) >= 2)


def test_tiny_pixel_noise_does_not_defeat_the_shortcut(live):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 3)
    (x0, y0, x1, y1), _ = APP.ReaderThread._region(live.sct.screen.shape, live.sct.monitors[1], live.panel)
    live.sct.screen[y0:y1, x0:x1] = np.clip(live.sct.screen[y0:y1, x0:x1].astype(int) + 1, 0, 255).astype(np.uint8)
    n = len(live.got)
    assert wait_for(lambda: len(live.got) >= n + 8)
    assert len(live.calls) == 1


def test_the_panel_closing_is_noticed_at_once(live):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 3)
    live.sct.screen[:] = 40                              # the panel closed: the region changed completely
    assert wait_for(lambda: any(g is None for g in live.got))
    assert len(live.calls) >= 2


def test_templates_reload_and_force_discard_the_shortcut(live):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 3)
    live.th.reload.set()
    assert wait_for(lambda: len(live.calls) >= 2)
    live.th.force.set()
    assert wait_for(lambda: len(live.calls) >= 3)


def test_a_failing_region_grab_never_kills_the_reader(live, monkeypatch):
    live.th.start()
    assert wait_for(lambda: len(live.got) >= 3)
    monkeypatch.setattr(live.sct, "grab", lambda rect: (_ for _ in ()).throw(OSError("boom")) if rect["width"] < 2560
                        else FakeSct.grab(live.sct, rect))
    reads, n = len(live.calls), len(live.got)
    assert wait_for(lambda: len(live.calls) >= reads + 3 and len(live.got) >= n + 3)   # falls back to full reads
    assert live.th.isRunning() and all(g is not None for g in live.got)


def test_idle_backoff_slows_reads_when_nothing_is_on_screen(live):
    live.sct.screen[:] = 40
    live.th.start()
    wait_for(lambda: False, 2.0)
    reads = len(live.calls)
    assert 5 <= reads <= 9, reads        # five quick looks, then one about every 0.8 s
    assert live.th.idle_interval() == max(live.th.interval, live.th.IDLE_INTERVAL)


def test_fixed_image_mode_still_reads_every_tick(monkeypatch, tmp_path, store, app):
    frame, _p = panel_frame(store)
    path = str(tmp_path / "f.png")
    cv2.imwrite(path, frame)
    calls = []
    real = R.read_panel
    monkeypatch.setattr(R, "read_panel", lambda img, st: (calls.append(1), real(img, st))[1])
    th = APP.ReaderThread(interval=0.03, image_path=path)
    th.start()
    assert wait_for(lambda: len(calls) >= 4)
    th.requestInterruption()
    th.wait(3000)


# ---------------------------------------------------------------------------------------------------------------
# priority and the perf log
# ---------------------------------------------------------------------------------------------------------------
def test_overlay_can_lower_its_priority_and_restore_it():
    k32 = ctypes.windll.kernel32
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    k32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    me = k32.GetCurrentProcess()
    before = k32.GetPriorityClass(me)
    try:
        assert APP.lower_priority() is True
        assert k32.GetPriorityClass(me) == 0x4000
    finally:
        k32.SetPriorityClass(me, before)


def test_perf_report_summarises_and_resets():
    p = Perf()
    p.record("read", 10.0)
    p.record("read", 30.0)
    with p.timer("grab"):
        time.sleep(0.01)
    line = p.report()
    assert "[perf]" in line and "read n=2 mean=20.0ms max=30ms" in line and "grab n=1" in line
    assert p.report().endswith("idle")


def test_controller_with_perf_prints_a_report_and_records_stalls(app, tmp_path, monkeypatch, capsys):
    from aaoverlay.app import Controller
    monkeypatch.setenv("AAOVERLAY_DATA", str(tmp_path / "data"))
    blank = tmp_path / "b.png"
    cv2.imwrite(str(blank), np.zeros((1440, 2560, 3), np.uint8))
    opts = SimpleNamespace(no_exclude=True, interval=0.05, image=str(blank), debug=False, exact_seconds=2.0,
                           collect=False, run_seconds=0, perf=True)
    ctl = Controller(app, opts)
    try:
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)
        line = ctl.perf.report()
        assert "gui_stall" in line and "read n=" in line and "cpu=" in line
    finally:
        ctl.reader.requestInterruption()
        ctl.solver.requestInterruption()
        ctl.custom.shutdown()
        ctl.reader.wait(3000)
        ctl.solver.wait(3000)
        ctl.overlay.hide()
