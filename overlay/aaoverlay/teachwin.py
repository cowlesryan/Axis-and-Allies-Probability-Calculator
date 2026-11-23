"""The Teach window (Ctrl+Alt+F7): review panels the reader could not read and teach it what they show.

Left: collected panels waiting for a label. Right: the panel, and for every icon or count the reader was unsure
about, the choices that are possible for that die value. *Teach* adds the labelled icons/digits only if the
regression gate passes (nothing that reads correctly today may get worse); *Undo last teach* reverses it.
Collection itself is opt-in (checkbox) and stays on this computer.
"""
import ctypes

import cv2
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget)

from . import teach as T
from .custom import STYLE, WDA_EXCLUDEFROMCAPTURE
from aa1942.units import UNIT_LIST

NAME = {u.key: u.name for u in UNIT_LIST}


def to_pixmap(bgr, max_w=None, max_h=None):
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    pm = QPixmap.fromImage(QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy())
    if max_w or max_h:
        pm = pm.scaled(max_w or w, max_h or h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pm


class TeachWorker(QThread):
    done = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # report, never crash the overlay
            self.done.emit(T.Report(False, f"Error: {exc}"))


class TeachWindow(QWidget):
    """``collector`` is a ``collect.Collector``; ``store_getter`` returns the live ``TemplateStore``;
    ``on_learned`` is called after a successful teach/undo so the reader reloads its templates."""

    def __init__(self, collector, store_getter, on_learned=None, template_root=None, exclude_from_capture=True):
        super().__init__()
        self.col, self.store_getter, self.on_learned = collector, store_getter, on_learned
        self.template_root = template_root or T.TEMPLATE_ROOT
        self.exclude = exclude_from_capture
        self.setWindowTitle("Teach the reader - A&A odds")
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.CustomizeWindowHint | Qt.WindowTitleHint |
                            Qt.WindowCloseButtonHint)
        self.setStyleSheet(STYLE)
        self.resize(1040, 720)
        self.item = self.tasks = self.panel = self.worker = None
        self.pickers = {}

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        self.collect_box = QCheckBox("Collect panels the reader can't read (stored on this computer only, never uploaded)")
        self.collect_box.setChecked(self.col.enabled)
        self.collect_box.toggled.connect(lambda on: self.col.set_enabled(on))
        top.addWidget(self.collect_box, 1)
        self.undo_btn = QPushButton("Undo last teach")
        self.undo_btn.clicked.connect(self.undo)
        top.addWidget(self.undo_btn)
        root.addLayout(top)

        body = QHBoxLayout()
        root.addLayout(body, 1)
        left = QVBoxLayout()
        left.addWidget(QLabel("Waiting for a label"))
        self.listing = QListWidget()
        self.listing.setMinimumWidth(240)
        self.listing.currentRowChanged.connect(self.select)
        left.addWidget(self.listing, 1)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        left.addWidget(refresh)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        self.preview = QLabel("No panel selected.")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(260)
        right.addWidget(self.preview)
        self.task_area = QScrollArea()
        self.task_area.setWidgetResizable(True)
        self.task_host = QWidget()
        self.task_grid = QGridLayout(self.task_host)
        self.task_area.setWidget(self.task_host)
        right.addWidget(self.task_area, 1)
        buttons = QHBoxLayout()
        self.teach_btn = QPushButton("Teach (checks every known panel first)")
        self.teach_btn.clicked.connect(self.run_teach)
        self.discard_btn = QPushButton("Discard this panel")
        self.discard_btn.clicked.connect(self.discard)
        buttons.addWidget(self.teach_btn)
        buttons.addWidget(self.discard_btn)
        right.addLayout(buttons)
        self.report = QLabel("")
        self.report.setWordWrap(True)
        right.addWidget(self.report)
        body.addLayout(right, 3)
        hint = QLabel("Label only what you can see in the panel. A wrong label is caught by the check and rejected. "
                      "Esc or Ctrl+Alt+F7 closes this window.")
        hint.setStyleSheet("color: #6f7f90; font-size: 11px;")
        root.addWidget(hint)
        self.refresh()

    # -- list and selection ---------------------------------------------------------------------
    def refresh(self):
        self.collect_box.setChecked(self.col.enabled)
        self.listing.blockSignals(True)
        self.listing.clear()
        self.pending = self.col.items(status="pending")
        for it in self.pending:
            first = (it.meta.get("warnings") or ["borderline match"])[0]
            QListWidgetItem(f"{it.meta['captured'][5:]}  -  {first[:60]}", self.listing)
        self.listing.blockSignals(False)
        if self.pending:
            self.listing.setCurrentRow(0)
            self.select(0)
        else:
            self.item = None
            self.preview.setText("Nothing to teach right now. Turn collection on and play; panels the reader "
                                 "cannot read show up here.")
            self._clear_tasks()

    def _clear_tasks(self):
        while self.task_grid.count():
            w = self.task_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.pickers = {}

    def select(self, row):
        self._clear_tasks()
        self.report.setText("")
        if not (0 <= row < len(self.pending)):
            return
        self.item = self.pending[row]
        store = self.store_getter()
        self.tasks, self.panel = T.tasks_for(self.item, store)
        self.preview.setPixmap(to_pixmap(self.item.crop(), 640, 300))
        canvas = self.item.canvas()
        if not self.tasks:
            self.report.setText("The reader now reads this panel without doubts, so there is nothing to label.")
            return
        for r, h in enumerate(("Where", "Icon", "Reader says", "This unit is", "Count")):
            lab = QLabel(h)
            lab.setStyleSheet("color:#8fa1b3;")
            self.task_grid.addWidget(lab, 0, r)
        for i, t in enumerate(self.tasks, start=1):
            self.task_grid.addWidget(QLabel(t.where()), i, 0)
            icon = QLabel()
            y0, y1 = t.y
            icon.setPixmap(to_pixmap(canvas[y0:y1, t.icon_x[0]:t.icon_x[1]], 64, 48))
            self.task_grid.addWidget(icon, i, 1)
            says = NAME.get(t.reader_unit, "unknown icon") + \
                   (f", x{t.reader_count}" if t.reader_count is not None else ", count unreadable")
            self.task_grid.addWidget(QLabel(says), i, 2)
            combo = QComboBox()
            combo.addItem("(not sure - leave)", None)
            for k in t.candidates:
                combo.addItem(NAME[k] + (" (suggested)" if k == t.suggestion else ""), k)
            if t.suggestion and not t.need_icon:
                combo.setCurrentIndex(1 + t.candidates.index(t.suggestion))
            combo.setEnabled(t.need_icon or t.confirm)
            self.task_grid.addWidget(combo, i, 3)
            spin = QSpinBox()
            spin.setRange(0, 999)
            spin.setEnabled(t.need_count)
            self.task_grid.addWidget(spin, i, 4)
            self.pickers[i - 1] = (combo, spin)
        self.task_grid.setRowStretch(len(self.tasks) + 1, 1)

    def labels(self):
        out = {}
        for i, (combo, spin) in self.pickers.items():
            t = self.tasks[i]
            lab = {}
            if combo.isEnabled() and combo.currentData():
                lab["unit"] = combo.currentData()
            if t.need_count:
                lab["count"] = spin.value()
            out[i] = lab
        return out

    # -- actions ------------------------------------------------------------------------------------
    def run_teach(self):
        if self.item is None or not self.tasks:
            return
        self.teach_btn.setEnabled(False)
        self.report.setText("Checking every known panel with the new templates...")
        item, tasks, panel, labels = self.item, self.tasks, self.panel, self.labels()
        self.worker = TeachWorker(lambda: T.teach(item, tasks, panel, labels, self.template_root, self.col))
        self.worker.done.connect(self._taught)
        self.worker.start()

    def _taught(self, report):
        self.teach_btn.setEnabled(True)
        colour = "#5ad07a" if report.ok else "#ff7a6b"
        self.report.setText(f'<span style="color:{colour};">{report.message}</span>')
        if report.ok:
            if self.on_learned:
                self.on_learned()
            self.refresh()
            self.report.setText(f'<span style="color:{colour};">{report.message}</span>')

    def discard(self):
        if self.item is not None:
            self.col.set_status(self.item.id, "discarded")
            self.refresh()

    def undo(self):
        msg = T.undo_last(self.col)
        self.report.setText(msg or "Nothing to undo.")
        if msg:
            if self.on_learned:
                self.on_learned()
            self.refresh()
            self.report.setText(msg)

    # -- window behaviour ----------------------------------------------------------------------------
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()
        if self.exclude:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)

    def closeEvent(self, e):
        self.hide()
        e.ignore()
