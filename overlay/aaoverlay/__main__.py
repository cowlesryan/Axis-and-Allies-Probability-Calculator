"""Entry point: ``python -m aaoverlay`` (run from the overlay folder, using .venv)."""
import argparse

from .app import run


def main():
    ap = argparse.ArgumentParser(description="Battle-odds overlay for Axis & Allies 1942 Online")
    ap.add_argument("--interval", type=float, default=0.4, help="seconds between screen reads")
    ap.add_argument("--exact-seconds", type=float, default=8.0, help="exact-solve budget before estimating")
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
