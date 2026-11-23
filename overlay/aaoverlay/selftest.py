"""Self-test used to prove a packaged build still behaves like the source tree.

``python -m aaoverlay --selftest OUT.json`` (or ``AAOdds.exe --selftest OUT.json``) exercises every library the app
depends on (OpenCV, NumPy, Qt, the calculator, the reader, the Teach gate) and writes a JSON report. The report holds
only deterministic values, so ``tests/test_frozen.py`` can run it from source and from the packaged exe and require the
two reports to be identical. A section that raises is recorded as ``{"error": ...}`` instead of hiding the others.
"""
import hashlib
import json
import os
import sys
import tempfile
import traceback


def _section(report, name, fn):
    try:
        report[name] = fn()
    except BaseException as exc:  # noqa: BLE001 - the point is to report, not to crash
        report[name] = {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}


def _resources():
    from . import dataset as D
    from .templates import ROOT
    out = {"samples": sum(1 for _ in D.samples())}
    for sub in ("icons", "digits"):
        folder = os.path.join(ROOT, sub)
        out[sub] = len([f for f in os.listdir(folder) if f.endswith(".png")])
    return out


def _dataset():
    from . import dataset as D
    from . import reader as R
    from .templates import TemplateStore
    from aa1942.units import ORDER
    store, out = TemplateStore(), {}
    for name, meta in D.samples():
        panel = R.read_panel(D.load_canvas(name, meta), store)
        if panel is None:
            out[name] = None
            continue
        att, dfn, warnings = R.panel_to_armies(panel)
        out[name] = [{k: v for k, v in zip(ORDER, att) if v}, {k: v for k, v in zip(ORDER, dfn) if v},
                     list(warnings)]
    return out


def _no_panel():
    import numpy as np
    from . import reader as R
    from .templates import TemplateStore
    store = TemplateStore()
    rng = np.random.default_rng(1)
    frames = [np.zeros((1440, 2560, 3), np.uint8), np.full((1440, 2560, 3), 128, np.uint8),
              rng.integers(0, 255, (1440, 2560, 3), dtype=np.uint8), np.zeros((720, 1280, 3), np.uint8)]
    return [R.read_panel(f, store) is None for f in frames]


def _calc():
    from aa1942.amphibious import solve_amphibious
    from aa1942.bombing import solve_sbr
    from aa1942.simulate import simulate
    from aa1942.solver import OPTIMAL, STANDARD, solve, solve_land_assault
    from aa1942.units import parse_army as A

    def r(d):
        return {k: (round(v, 12) if isinstance(v, float) else v) for k, v in d.items()
                if isinstance(v, (int, float))}

    out = {"land": r(solve(A("6 inf, 2 art, 3 tank, 2 ftr"), A("8 inf, 3 tank, 1 art"))),
           "sea": r(solve(A("2 bb, 3 des, 1 car, 2 ftr, 1 sub"), A("2 bb, 4 des, 1 sub"))),
           "optimal": r(solve(A("5 inf, 3 tank"), A("6 inf, 2 art"), OPTIMAL, STANDARD)),
           "bombard": r(solve_land_assault(A("4 inf, 2 art"), A("5 inf"), 1, 1, STANDARD, STANDARD)),
           "sim": r(simulate(A("4 inf, 2 tank"), A("5 inf, 1 art"), runs=3000, seed=7)),
           "sbr": {str(k): round(v, 12) for k, v in solve_sbr(3, 4, 0)["damage_distribution"].items()}}
    u = A("1 trn, 1 des"), A("1 inf, 1 art"), A("1 ftr"), A("1 des"), A("2 inf"), A("1 cru")
    amph = solve_amphibious(*u)
    out["amph"] = r(amph) if isinstance(amph, dict) else str(type(amph))
    return out


def _service():
    from . import service
    from aa1942.units import parse_army as A
    last = []
    service.compute(A("3 inf, 1 tank"), A("4 inf"), lambda d: last.append(d), exact_seconds=5)
    f = last[-1]
    return {"method": f["method"], "stage": f["stage"], "win": round(f["outcomes"]["attacker_win"], 12),
            "loss": [round(f["loss_att"], 9), round(f["loss_def"], 9)], "spread": bool(f.get("spread"))}


def _cv2():
    import cv2
    import numpy as np
    rng = np.random.default_rng(3)
    img = rng.integers(0, 255, (120, 200, 3), dtype=np.uint8)
    img[30:80, 40:150] = (20, 200, 40)
    ok, png = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    back = cv2.imdecode(png, cv2.IMREAD_COLOR)
    ok2, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
    jback = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
    mask = (img[..., 1] > 150).astype(np.uint8) * 255
    contours, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)

    def h(a):
        return hashlib.sha1(np.ascontiguousarray(a).tobytes()).hexdigest()[:12]

    with tempfile.TemporaryDirectory() as tmp:  # the file path the app uses for imread / imwrite
        p = os.path.join(tmp, "x.png")
        cv2.imwrite(p, img)
        disk = cv2.imread(p)
        gray = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    return {"png": bool(ok and np.array_equal(back, img)), "jpg": bool(ok2 and jback.shape == img.shape),
            "disk": bool(np.array_equal(disk, img)), "gray": h(gray),
            "resize": h(cv2.resize(img, (64, 48), interpolation=cv2.INTER_AREA)),
            "hsv": h(cv2.cvtColor(img, cv2.COLOR_BGR2HSV)), "blur": h(cv2.GaussianBlur(img, (5, 5), 0)),
            "contours": len(contours), "jpg_hash": h(jback)}


def _qt():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from . import collect as C
    from .custom import CustomBattleWindow
    from .render import render
    from .teachwin import TeachWindow
    from .templates import TemplateStore
    from .app import Overlay

    out = {"platform": QGuiApplication.platformName()}
    ov = Overlay(exclude_from_capture=False)
    ov.set_html(render({"warnings": [], "att_text": "3 inf", "def_text": "2 inf", "att_ipc": 9, "def_ipc": 6,
                        "result": None}))
    ov.show()
    app.processEvents()
    img = ov.grab().toImage()
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "o.png")
        saved = img.save(p, "PNG")
        again = QImage(p)
        out["overlay_png"] = bool(saved and not again.isNull() and again.size() == img.size())
    out["overlay_has_text"] = len({img.pixel(x, y) for x in range(0, img.width(), 7)
                                   for y in range(0, img.height(), 5)}) > 3
    ov.close()

    w = CustomBattleWindow(exclude_from_capture=False, exact_seconds=3.0)
    try:
        w.att.set_values({"inf": 3, "tank": 1})
        w.dfn.set_values({"inf": 3})
        w.show()
        app.processEvents()
        html = w.compute_blocking()
        out["custom_html"] = hashlib.sha1(html.encode()).hexdigest()[:12]
        out["custom_exact"] = "EXACT" in html and "Attacker wins" in html
        w.mode.setCurrentIndex(2)
        for b in w.sbr.values():
            b.setValue(2)
        out["sbr_html"] = "STRATEGIC BOMBING" in w.compute_blocking()
    finally:
        w.shutdown()
    with tempfile.TemporaryDirectory() as tmp:
        col = C.Collector(enabled=False, folder=os.path.join(tmp, "c"), check_windows=False)
        tw = TeachWindow(col, lambda: TemplateStore(), lambda: None, exclude_from_capture=False)
        tw.show()
        app.processEvents()
        out["teach_window"] = bool(tw.isVisible())
        tw.close()
        tw.deleteLater()
    for widget in (ov, w):
        widget.deleteLater()
    app.processEvents()
    out["qt_flags"] = int(Qt.WindowType.WindowStaysOnTopHint) > 0
    return out


def _teach():
    from . import teach as T
    from .templates import TemplateStore
    store = TemplateStore()
    samples = T.all_samples()
    rep = T.gate(store, store, samples)
    statuses = {}
    for name, meta, raw in samples[:40]:
        statuses[T.status_of(store, name, meta, raw)] = statuses.get(T.status_of(store, name, meta, raw), 0) + 1
    return {"gate_ok": rep.ok, "samples": len(samples), "unchanged": rep.unchanged, "problems": rep.problems[:3],
            "statuses": statuses}


def _windows_api():
    import ctypes
    from . import windows as W
    user32 = ctypes.windll.user32
    return {"screen_h": bool(user32.GetSystemMetrics(1) > 0), "key_state": isinstance(user32.GetAsyncKeyState(0x70), int),
            "occluders": isinstance(W.occluding_windows((0, 0, 10, 10)), list)}


def _mss():
    import mss
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[1])
        return {"grab": bool(shot.width > 0 and shot.height > 0 and len(shot.raw) > 0)}


SECTIONS = (("resources", _resources), ("dataset", _dataset), ("no_panel", _no_panel), ("calc", _calc),
            ("service", _service), ("cv2", _cv2), ("qt", _qt), ("teach", _teach))
LIVE_SECTIONS = (("windows_api", _windows_api), ("mss", _mss))  # machine-dependent: checked for success only


def run(out_path):
    """Run every section, write the JSON report to ``out_path`` and return it."""
    report = {}
    for name, fn in SECTIONS:
        _section(report, name, fn)
    live = {}
    for name, fn in LIVE_SECTIONS:
        _section(live, name, fn)
    report["live"] = live
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, sort_keys=True, default=str)
    return report


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "selftest.json")
