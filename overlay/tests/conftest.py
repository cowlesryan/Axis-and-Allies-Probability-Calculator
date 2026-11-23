import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
import aaoverlay  # noqa: E402,F401  (also puts the aa1942 package on sys.path)
