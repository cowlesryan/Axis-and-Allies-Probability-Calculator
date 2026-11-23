"""Entry point: ``python -m aaoverlay`` (run from the overlay folder, using .venv)."""
import argparse

import os
import sys
import time
import traceback

from . import __version__
from .paths import FROZEN, data_dir


def _setup_logging():
    """The packaged app has no console: send output to a log file and record unhandled errors there."""
    folder = os.path.join(data_dir(), "logs")
    os.makedirs(folder, exist_ok=True)
    logs = sorted(f for f in os.listdir(folder) if f.endswith(".log"))
    for old in logs[:-4]:  # keep the last few
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    path = os.path.join(folder, time.strftime("overlay_%Y%m%d_%H%M%S.log"))
    log = open(path, "w", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = log

    def hook(kind, exc, tb):
        traceback.print_exception(kind, exc, tb, file=log)
        try:  # one visible message, since nobody is watching a console
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"The overlay hit an unexpected error and will close.\n\nDetails were saved to:\n{path}",
                "A&A odds overlay", 0x10)
        except Exception:
            pass

    sys.excepthook = hook
    print(f"A&A odds overlay {__version__}", file=log)
    return path


def main():
    if FROZEN:
        _setup_logging()
    if "--selftest" in sys.argv:  # checks every dependency and writes a JSON report (used by tests/test_frozen.py)
        from .selftest import run as selftest
        selftest(sys.argv[sys.argv.index("--selftest") + 1])
        return
    from .app import run
    ap = argparse.ArgumentParser(description="Battle-odds overlay for Axis & Allies 1942 Online")
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between screen reads")
    ap.add_argument("--exact-seconds", type=float, default=3.0, help="exact-solve budget before estimating")
    ap.add_argument("--perf", action="store_true", help="print timing, CPU use and GUI stalls every 5 seconds")
    ap.add_argument("--normal-priority", action="store_true",
                    help="do not lower the overlay's CPU priority (it runs below normal so the game comes first)")
    ap.add_argument("--collect", action="store_true",
                    help="keep panels the reader cannot read for the Teach window (Ctrl+Alt+F7); stays on this PC")
    ap.add_argument("--image", default=None, help="read this screenshot instead of the live screen (testing)")
    ap.add_argument("--no-exclude", action="store_true",
                    help="let screen captures include the overlay (only for taking verification screenshots)")
    ap.add_argument("--run-seconds", type=float, default=0, help="quit automatically after this many seconds")
    ap.add_argument("--debug", action="store_true", help="print what the reader and solver are doing")
    raise SystemExit(run(ap.parse_args()))


if __name__ == "__main__":
    main()
