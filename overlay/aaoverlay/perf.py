"""Optional performance log (``--perf``): where the time goes while the overlay runs.

Every few seconds one line is printed (to the log file in the packaged app) with, per stage, how many times it ran and
its mean and worst time, the process's CPU use over the interval, and the worst delay of the GUI thread (how long the
window was unresponsive). Recording is a lock and a dict update, so it is cheap enough to leave on while playing.
"""
import threading
import time


class Perf:
    def __init__(self):
        self._lock = threading.Lock()
        self._stats = {}
        self._t0 = time.monotonic()
        self._cpu0 = time.process_time()

    def record(self, name, ms):
        with self._lock:
            n, total, worst = self._stats.get(name, (0, 0.0, 0.0))
            self._stats[name] = (n + 1, total + ms, max(worst, ms))

    def timer(self, name):
        return _Timer(self, name)

    def report(self):
        """One line summarising the interval since the last report, then reset."""
        now, cpu = time.monotonic(), time.process_time()
        with self._lock:
            stats, self._stats = self._stats, {}
        wall = max(now - self._t0, 1e-6)
        parts = [f"{k} n={n} mean={total / n:.1f}ms max={worst:.0f}ms" for k, (n, total, worst) in sorted(stats.items())]
        line = f"[perf] cpu={100 * (cpu - self._cpu0) / wall:.1f}% of one core | " + (" | ".join(parts) or "idle")
        self._t0, self._cpu0 = now, cpu
        return line


class _Timer:
    def __init__(self, perf, name):
        self.perf, self.name = perf, name

    def __enter__(self):
        self.t0 = time.perf_counter()

    def __exit__(self, *exc):
        self.perf.record(self.name, 1000 * (time.perf_counter() - self.t0))
        return False
