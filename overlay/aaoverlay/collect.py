"""Opt-in collection of attack panels the reader could not read (or only just read).

Nothing is collected unless the user turns it on (``--collect`` or the checkbox in the Teach window, which is
remembered in ``settings.json``). What is stored, locally only and never uploaded, is a crop of the attack panel
(the same crop the training dataset uses) with a small JSON file next to it. The folder is
``%LOCALAPPDATA%\\aaoverlay\\collected`` (override with the ``AAOVERLAY_DATA`` environment variable).

A frame is skipped when another window overlaps the panel, so no unrelated content is ever stored.
"""
import hashlib
import json
import os
import shutil
import sys
import time
from dataclasses import dataclass

import cv2

from . import reader as R
from . import windows as W

MAX_ITEMS = 150
MIN_GAP_SECONDS = 4.0
BORDERLINE = 0.12   # an icon matched within this margin above the reader's minimum score counts as borderline


def data_dir():
    root = os.environ.get("AAOVERLAY_DATA") or os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "aaoverlay")
    os.makedirs(root, exist_ok=True)
    return root


def _settings_path():
    return os.path.join(data_dir(), "settings.json")


def load_settings():
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    s = load_settings()
    s.update(changes)
    with open(_settings_path(), "w", encoding="utf-8") as fh:
        json.dump(s, fh, indent=1)


def reasons(panel):
    """Why a panel is worth keeping: ``flagged`` (the reader warned) and/or ``borderline`` (a weak icon match)."""
    out = []
    if panel.warnings or any(e.unit is None or e.count is None for r in panel.rows for e in r.att + r.dfn):
        out.append("flagged")
    if any(e.unit is not None and e.icon_score < R.ICON_MIN_SCORE + BORDERLINE
           for r in panel.rows for e in r.att + r.dfn):
        out.append("borderline")
    return out


@dataclass
class Item:
    id: str
    meta: dict
    folder: str

    @property
    def status(self):
        return self.meta.get("status", "pending")

    def canvas(self):
        """The crop pasted back at its position on a 1440p canvas (what the reader works on)."""
        from . import dataset as D
        return D.load_canvas(self.id, self.meta, self.folder)

    def crop(self):
        return cv2.imread(os.path.join(self.folder, self.id + ".png"))


class Collector:
    def __init__(self, enabled=None, folder=None, check_windows=True):
        self.folder = folder or os.path.join(data_dir(), "collected")
        os.makedirs(self.folder, exist_ok=True)
        self._enabled = load_settings().get("collect", False) if enabled is None else bool(enabled)
        self.check_windows = check_windows
        self._seen = set()
        self._last = 0.0

    @property
    def enabled(self):
        return self._enabled

    def set_enabled(self, flag, remember=True):
        self._enabled = bool(flag)
        if remember:
            save_settings(collect=self._enabled)

    # -- storing ----------------------------------------------------------------------------------
    def offer(self, panel):
        """Store ``panel`` if collection is on and it is worth keeping. Returns the new item id or ``None``."""
        if not self._enabled or panel is None or panel.frame is None:
            return None
        why = reasons(panel)
        if not why or time.monotonic() - self._last < MIN_GAP_SECONDS:
            return None
        frame = panel.frame
        box = W.crop_box(frame, panel)
        x0, y0, x1, y1 = box
        crop = frame[y0:y1, x0:x1]
        small = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), (16, 16), interpolation=cv2.INTER_AREA)
        key = hashlib.sha1((repr(panel.signature()) + repr(tuple(panel.warnings))).encode() +
                           (small > small.mean()).tobytes()).hexdigest()
        if key in self._seen:
            return None
        if self.check_windows and self._covered(box, frame.shape[0]):
            return None
        if len(self.items()) >= MAX_ITEMS and not self._make_room():
            return None
        self._seen.add(key)
        self._last = time.monotonic()
        from aa1942.units import ORDER
        att, dfn, _w = R.panel_to_armies(panel)
        item_id = time.strftime("c%Y%m%d_%H%M%S") + f"_{int(time.time() * 1000) % 1000:03d}_{len(self._seen):02d}"
        cv2.imwrite(os.path.join(self.folder, item_id + ".png"), crop)
        meta = {"id": item_id, "name": item_id, "captured": time.strftime("%Y-%m-%d %H:%M:%S"),
                "crop_box_1440p": list(box), "screen_height_1440p": int(frame.shape[0]),
                "panel": {"cx": panel.cx, "bounds": list(panel.bounds),
                          "dice": [[r.die_att, r.die_def] for r in panel.rows]},
                "reasons": why, "warnings": list(panel.warnings), "status": "pending",
                "reading": {"attackers": {k: n for k, n in zip(ORDER, att) if n},
                            "defenders": {k: n for k, n in zip(ORDER, dfn) if n}}}
        with open(os.path.join(self.folder, item_id + ".json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)
        return item_id

    def _covered(self, box, frame_h):
        """True when another program's window overlaps the panel (the live overlay's own windows do not count)."""
        try:
            k = (W.screen_height() or frame_h) / frame_h
            names = W.occluding_windows(tuple(int(v * k) for v in box))
        except Exception:
            return False
        own = os.path.basename(sys.executable).lower()
        return any(n.lower() not in (own, "python.exe", "pythonw.exe") for n in names)

    def _make_room(self):
        for item in self.items():
            if item.status != "pending":
                self.delete(item.id)
                return True
        return False

    # -- reading back -----------------------------------------------------------------------------
    def items(self, status=None):
        out = []
        for f in sorted(os.listdir(self.folder)):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(self.folder, f), encoding="utf-8") as fh:
                        meta = json.load(fh)
                except (OSError, ValueError):
                    continue
                if status is None or meta.get("status", "pending") == status:
                    out.append(Item(meta["id"], meta, self.folder))
        return out

    def get(self, item_id):
        try:
            with open(os.path.join(self.folder, item_id + ".json"), encoding="utf-8") as fh:
                meta = json.load(fh)
        except (OSError, ValueError):
            return None
        return Item(meta["id"], meta, self.folder)

    def set_status(self, item_id, status):
        item = self.get(item_id)
        if item:
            item.meta["status"] = status
            with open(os.path.join(self.folder, item_id + ".json"), "w", encoding="utf-8") as fh:
                json.dump(item.meta, fh, indent=1)

    def delete(self, item_id):
        for ext in (".png", ".json"):
            try:
                os.remove(os.path.join(self.folder, item_id + ext))
            except OSError:
                pass

    def clear(self):
        shutil.rmtree(self.folder, ignore_errors=True)
        os.makedirs(self.folder, exist_ok=True)
