"""Where things live, for a source checkout and for the packaged (PyInstaller) app alike.

* ``bundle_dir()`` - read-only resources shipped with the program: the seed templates and the regression-gate dataset.
* ``data_dir()`` - the user's writable data: settings, collected panels, taught samples, template backups, logs and
  (in the packaged app) the working copy of the templates. ``%LOCALAPPDATA%\\aaoverlay``, or the folder named by the
  ``AAOVERLAY_DATA`` environment variable.
"""
import os
import sys

FROZEN = bool(getattr(sys, "frozen", False))


def bundle_dir():
    """The ``overlay`` folder in a checkout, or PyInstaller's unpack folder when packaged."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def data_dir():
    root = os.environ.get("AAOVERLAY_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "aaoverlay")
    os.makedirs(root, exist_ok=True)
    return root
