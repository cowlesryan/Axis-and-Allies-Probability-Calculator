"""Open each listed scenario campaign in turn and capture all its battles.

Usage:  python tools\run_all.py germany:1:<id> soviet_union:2:<id> ...   (power:card index:game id)
Between scenarios it returns to the main menu with the restricted menu driver. Esc stops everything.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import capture_run as C  # noqa: E402
import session as S  # noqa: E402

if __name__ == "__main__":
    first = True
    for spec in sys.argv[1:]:
        power, index, gid = spec.split(":")
        S.to_main_menu() if not first else None
        first = False
        S.open_campaign(power, int(index), gid)
        missing = C.run(gid, power[:2])
        subprocess.run([sys.executable, "-W", "ignore", os.path.join(HERE, "learn.py")], capture_output=True)
        print(f"== {power}: missing {missing}")
