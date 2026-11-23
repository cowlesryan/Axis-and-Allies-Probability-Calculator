"""Custom battle window: type in any attacking and defending units and get the odds.

Opened with Ctrl+Alt+F8 (see ``app.py``). It covers everything the live overlay and the calculator handle:

* **Battle** - land, air or sea, with every unit type (including AA guns, damaged battleships and transports).
  As on the game's attack panel, ships on the attacker's side of a *land* battle are shore bombardment.
* **Amphibious assault** - the whole chain: sea battle -> bombardment -> land battle (``aa1942.amphibious``).
* **Strategic bombing raid** - damage distribution (``aa1942.bombing``).
* Casualty policies per side: standard, optimal (best response) or a custom removal order.

Results update as you type. The window is a normal, focusable window (the live overlay is click-through),
it is excluded from screen capture, and while it is open the live overlay is paused.
"""
import ctypes
import threading

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout,
                               QWidget)

import aaoverlay  # noqa: F401
from . import service
from .render import render, render_sbr
from aa1942.solver import SolveBudgetExceeded, parse_policy, STANDARD
from aa1942.units import IDX, N, ORDER, UNIT_LIST, army_cost, fmt_army

WDA_EXCLUDEFROMCAPTURE = 0x11
MAX_UNITS = 999

LAND = ("inf", "art", "tank")
AIR = ("ftr", "bmb")
SEA = ("sub", "des", "cru", "car", "bb", "bbd", "trn")
NAME = {u.key: u.name for u in UNIT_LIST}

MODES = ("Battle: land, air or sea", "Amphibious assault: sea battle + landing", "Strategic bombing raid")

STYLE = """
QWidget { background: #14181f; color: #eceff4; font-family: 'Segoe UI'; font-size: 13px; }
QGroupBox { border: 1px solid #2c3542; border-radius: 6px; margin-top: 10px; padding: 8px 6px 4px 6px; }
QGroupBox::title { subcontrol-origin: margin; left: 8px; color: #8fa1b3; }
QSpinBox, QLineEdit, QComboBox { background: #1e2530; border: 1px solid #364152; border-radius: 4px; padding: 2px 4px; }
QSpinBox { min-width: 64px; }
QPushButton { background: #263040; border: 1px solid #3a475a; border-radius: 4px; padding: 5px 12px; }
QPushButton:hover { background: #31405a; }
QScrollArea { border: none; }
"""


class Worker(QThread):
    """Runs the newest job (a callable taking ``emit``); starting a new one cancels the old."""
    update = Signal(int, object)   # job id, result dict
    failed = Signal(int, str)      # job id, message

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._job = None
        self._id = 0
        self._wake = threading.Event()

    def submit(self, fn):
        with self._lock:
            self._id += 1
            self._job = (self._id, fn)
        service.request_cancel()
        self._wake.set()
        return self._id

    def run(self):
        while not self.isInterruptionRequested():
            if not self._wake.wait(0.2):
                continue
            self._wake.clear()
            with self._lock:
                job = self._job
            if job is None:
                continue
            jid, fn = job
            try:
                fn(lambda r, j=jid: self.update.emit(j, r))
            except SolveBudgetExceeded:
                self.failed.emit(jid, "Too large to solve in the time budget.")
            except Exception as exc:  # bad input is reported in the window, never crashes the app
                self.failed.emit(jid, str(exc))


def _spin(maximum=MAX_UNITS):
    box = QSpinBox()
    box.setRange(0, maximum)
    box.setAlignment(Qt.AlignRight)
    return box


class UnitGrid(QGroupBox):
    """A titled group of unit-count spin boxes."""

    def __init__(self, title, keys, disabled=()):
        super().__init__(title)
        self.keys = list(keys)
        self.boxes = {}
        grid = QGridLayout(self)
        grid.setVerticalSpacing(3)
        for row, key in enumerate(self.keys):
            grid.addWidget(QLabel(NAME[key]), row, 0)
            box = _spin()
            box.setEnabled(key not in disabled)
            self.boxes[key] = box
            grid.addWidget(box, row, 1)

    def values(self):
        return {k: b.value() for k, b in self.boxes.items()}

    def set_values(self, counts):
        for k, b in self.boxes.items():
            b.setValue(int(counts.get(k, 0)))

    def clear(self):
        for b in self.boxes.values():
            b.setValue(0)


def army(*grids):
    """Army tuple (ORDER) from the values of one or more ``UnitGrid`` s (counts of one unit add up)."""
    counts = [0] * N
    for g in grids:
        for k, v in g.values().items():
            counts[IDX[k]] += v
    return tuple(counts)


class PolicyRow(QWidget):
    """Casualty policy for both sides."""

    def __init__(self, on_change):
        super().__init__()
        lay = QGridLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combos, self.orders = {}, {}
        for col, side in enumerate(("Attacker", "Defender")):
            lay.addWidget(QLabel(f"{side} casualties"), 0, col)
            combo = QComboBox()
            combo.addItems(["Standard", "Optimal (best response)", "Custom order"])
            edit = QLineEdit()
            edit.setPlaceholderText("e.g. inf,art,tank - first removed first")
            edit.setVisible(False)
            combo.currentIndexChanged.connect(lambda _i, c=combo, e=edit: (e.setVisible(c.currentIndex() == 2),
                                                                           on_change()))
            edit.textChanged.connect(lambda _t: on_change())
            lay.addWidget(combo, 1, col)
            lay.addWidget(edit, 2, col)
            self.combos[side], self.orders[side] = combo, edit

    def policies(self):
        out = []
        for side in ("Attacker", "Defender"):
            i = self.combos[side].currentIndex()
            if i == 0:
                out.append(STANDARD)
            elif i == 1:
                out.append(parse_policy("optimal"))
            else:
                text = self.orders[side].text().strip()
                if not text:
                    raise ValueError(f"{side} custom order: list unit names, e.g. inf,art,tank")
                out.append(parse_policy("order:" + text))
        return out

    def reset(self):
        for c in self.combos.values():
            c.setCurrentIndex(0)


class CustomBattleWindow(QWidget):
    """The window. ``panel_source`` (optional) returns ``(att, dfn)`` from the live panel, or ``None``."""

    def __init__(self, panel_source=None, exact_seconds=8.0, exclude_from_capture=True):
        super().__init__()
        self.panel_source = panel_source
        self.exact_seconds = exact_seconds
        self.exclude = exclude_from_capture
        self.setWindowTitle("Custom battle - A&A odds")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint | Qt.WindowTitleHint |
                            Qt.WindowCloseButtonHint)
        self.setStyleSheet(STYLE)
        self.resize(940, 700)
        self.worker = Worker()
        self.worker.update.connect(self._on_update)
        self.worker.failed.connect(self._on_failed)
        self._job = 0
        self._last = None
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.evaluate)

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Calculation"))
        self.mode = QComboBox()
        self.mode.addItems(MODES)
        top.addWidget(self.mode, 1)
        self.load_btn = QPushButton("Load from game panel")
        self.swap_btn = QPushButton("Swap sides")
        self.clear_btn = QPushButton("Clear")
        for b in (self.load_btn, self.swap_btn, self.clear_btn):
            top.addWidget(b)
        root.addLayout(top)

        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = QVBoxLayout()
        body.addLayout(left, 3)
        self.pages = QStackedWidget()
        left.addWidget(self.pages, 1)
        self.policy = PolicyRow(self.schedule)
        left.addWidget(self.policy)

        # -- page 0: battle ----------------------------------------------------------------------
        page = QWidget()
        row = QHBoxLayout(page)
        keys = [u.key for u in UNIT_LIST]
        self.att = UnitGrid("Attacker", keys, disabled=("aa",))
        self.dfn = UnitGrid("Defender", keys)
        row.addWidget(self.att)
        row.addWidget(self.dfn)
        self.pages.addWidget(page)

        # -- page 1: amphibious ------------------------------------------------------------------
        page = QScrollArea()
        page.setWidgetResizable(True)
        inner = QWidget()
        grid = QGridLayout(inner)
        self.a_fleet = UnitGrid("Attacking fleet (incl. loaded transports)", ("sub", "des", "cru", "car", "bb", "bbd", "trn"))
        self.a_cargo = UnitGrid("Cargo (2 land units per transport)", LAND)
        self.a_air = UnitGrid("Attacking aircraft (join the land battle)", AIR)
        self.a_bomb = UnitGrid("Bombarding ships (one per landing unit)", ("cru", "bb", "bbd"))
        self.d_fleet = UnitGrid("Defending fleet (in the sea zone)", SEA)
        self.d_land = UnitGrid("Defending land units", LAND + ("aa",) + AIR)
        grid.addWidget(self.a_fleet, 0, 0)
        grid.addWidget(self.a_cargo, 1, 0)
        grid.addWidget(self.a_air, 2, 0)
        grid.addWidget(self.a_bomb, 3, 0)
        grid.addWidget(self.d_fleet, 0, 1)
        grid.addWidget(self.d_land, 1, 1, 2, 1)
        page.setWidget(inner)
        self.pages.addWidget(page)

        # -- page 2: bombing raid ---------------------------------------------------------------
        page = QWidget()
        form = QGridLayout(page)
        self.sbr = {"bombers": _spin(), "ipc": _spin(99), "damage": _spin(198)}
        for r, (label, key) in enumerate((("Attacking bombers", "bombers"), ("IPC value of the territory", "ipc"),
                                          ("Damage already on the complex", "damage"))):
            form.addWidget(QLabel(label), r, 0)
            form.addWidget(self.sbr[key], r, 1)
        form.setRowStretch(3, 1)
        self.pages.addWidget(page)

        # -- results -----------------------------------------------------------------------------
        self.result = QLabel("Enter units to see the odds.")
        self.result.setTextFormat(Qt.RichText)
        self.result.setWordWrap(True)
        self.result.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.result)
        scroll.setMinimumWidth(380)
        frame = QFrame()
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.addWidget(scroll)
        body.addWidget(frame, 2)
        hint = QLabel("Esc or Ctrl+Alt+F8 closes this window. Results update as you type.")
        hint.setStyleSheet("color: #6f7f90; font-size: 11px;")
        root.addWidget(hint)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.load_btn.clicked.connect(self.load_from_panel)
        self.swap_btn.clicked.connect(self.swap_sides)
        self.clear_btn.clicked.connect(self.clear_all)
        for g in (self.att, self.dfn, self.a_fleet, self.a_cargo, self.a_air, self.a_bomb, self.d_fleet, self.d_land):
            for b in g.boxes.values():
                b.valueChanged.connect(self.schedule)
        for b in self.sbr.values():
            b.valueChanged.connect(self.schedule)
        self.worker.start()

    # -- input helpers --------------------------------------------------------------------------
    def _mode_changed(self, i):
        self.pages.setCurrentIndex(i)
        self.policy.setVisible(i != 2)
        self.load_btn.setEnabled(i == 0)
        self.swap_btn.setEnabled(i == 0)
        self.schedule()

    def set_battle(self, att, dfn):
        """Fill the battle page from two army tuples (ORDER) or ``{key: n}`` dicts."""
        def as_dict(a):
            return a if isinstance(a, dict) else {k: n for k, n in zip(ORDER, a)}
        self.mode.setCurrentIndex(0)
        self.att.set_values(as_dict(att))
        self.dfn.set_values(as_dict(dfn))

    def load_from_panel(self):
        got = self.panel_source() if self.panel_source else None
        if got is None:
            self.result.setText('<div style="color:#ffb454;">No game attack panel is being read right now.</div>')
            return
        self.set_battle(*got)

    def swap_sides(self):
        a, d = self.att.values(), self.dfn.values()
        d.pop("aa", None)  # the new attacker cannot bring AA guns
        self.att.set_values(d)
        self.dfn.set_values(a)

    def clear_all(self):
        for g in (self.att, self.dfn, self.a_fleet, self.a_cargo, self.a_air, self.a_bomb, self.d_fleet, self.d_land):
            g.clear()
        for b in self.sbr.values():
            b.setValue(0)
        self.policy.reset()
        self.result.setText("Enter units to see the odds.")

    def schedule(self):
        self.debounce.start()

    # -- calculation ----------------------------------------------------------------------------
    def build_job(self):
        """``(kind, callable, state)`` for the current inputs, or ``None`` when nothing is entered.

        Raises ``ValueError`` for input the calculator rejects (for example a mixed land/sea battle).
        """
        i = self.mode.currentIndex()
        if i == 2:
            b, ipc, dmg = (self.sbr[k].value() for k in ("bombers", "ipc", "damage"))
            if b == 0:
                return None
            return "sbr", None, {"bombers": b, "ipc": ipc, "damage": dmg}
        pa, pd = self.policy.policies()
        t = self.exact_seconds
        if i == 0:
            att, dfn = army(self.att), army(self.dfn)
            if not any(att) and not any(dfn):
                return None
            if not any(att) or not any(dfn):
                raise ValueError("Enter units for both sides.")
            from aa1942.solver import validate
            if not service.split_amphibious(att, dfn):
                validate(att, dfn)
            else:  # ships + land units: shore bombardment; only the land battle itself is validated
                validate(tuple(0 if i in service.SEA_IDX else n for i, n in enumerate(att)), dfn)
            return "battle", (lambda emit: service.compute(att, dfn, emit, t, pa, pd)), \
                {"att_text": fmt_army(att), "def_text": fmt_army(dfn), "att_ipc": army_cost(att),
                 "def_ipc": army_cost(dfn)}
        fleet, cargo, air, bomb = army(self.a_fleet), army(self.a_cargo), army(self.a_air), army(self.a_bomb)
        dfl, dland = army(self.d_fleet), army(self.d_land)
        if not any(fleet) and not any(cargo) and not any(air) and not any(dfl) and not any(dland):
            return None
        if not any(dfl) and not any(dland):
            raise ValueError("Enter the defending units.")
        return "amph", (lambda emit: service.compute_full_amphibious(fleet, cargo, air, dfl, dland, bomb, emit, t,
                                                                    pa, pd)), {}

    def evaluate(self):
        try:
            job = self.build_job()
        except ValueError as exc:
            self._show_error(str(exc))
            return
        service.request_cancel()
        if job is None:
            self.result.setText("Enter units to see the odds.")
            return
        kind, fn, state = job
        self._state, self._kind = state, kind
        if kind == "sbr":
            r = service.compute_sbr(state["bombers"], state["ipc"], state["damage"])
            self.result.setText(render_sbr(r, state["bombers"], state["ipc"], state["damage"]))
            return
        self.result.setText('<div style="color:#ffd479;">Calculating...</div>')
        self._job = self.worker.submit(fn)

    def compute_blocking(self):
        """Run the current inputs to the final result on this thread (tests and scripting). Returns the HTML."""
        job = self.build_job()
        if job is None:
            return ""
        kind, fn, state = job
        if kind == "sbr":
            r = service.compute_sbr(state["bombers"], state["ipc"], state["damage"])
            return render_sbr(r, state["bombers"], state["ipc"], state["damage"])
        got = []
        fn(got.append)
        return self._html(kind, state, got[-1])

    def _html(self, kind, state, res):
        st = dict(state)
        st.update(warnings=[], aa=0, result=res, title="CUSTOM BATTLE")
        if kind == "amph":
            st.update(att_text=res["attacker"], def_text=res["defender"], att_ipc=res["att_ipc"],
                      def_ipc=res["def_ipc"])
        return render(st)

    def _on_update(self, job, res):
        if job != self._job:
            return
        self.result.setText(self._html(self._kind, self._state, res))

    def _on_failed(self, job, message):
        if job == self._job:
            self._show_error(message)

    def _show_error(self, message):
        self.result.setText(f'<div style="color:#ff7a6b; font-size:14px;">{message}</div>')

    # -- window behaviour -----------------------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        if self.exclude:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)

    def closeEvent(self, e):
        self.hide()
        e.ignore()  # the app owns the window; it is hidden, not destroyed

    def shutdown(self):
        self.worker.requestInterruption()
        service.request_cancel()
        self.worker.wait(2000)


def _demo():  # pragma: no cover - manual check:  python -m aaoverlay.custom
    import sys
    app = QApplication(sys.argv)
    w = CustomBattleWindow(exclude_from_capture=False)
    w.show()
    app.exec()
    w.shutdown()


if __name__ == "__main__":
    _demo()
