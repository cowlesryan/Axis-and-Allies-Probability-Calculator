"""Proves the packaged app (and its trimmed contents) behaves exactly like the source tree.

* ``test_source_*`` always run: the trimming list is consistent with what the code uses, and re-encoding the dataset
  is lossless.
* The rest need a built app (``AAOVERLAY_DIST`` or ``%USERPROFILE%\\aaoverlay-build\\dist\\AAOdds``) and are skipped
  without one. They run the app's own ``--selftest`` (OpenCV, NumPy, Qt, calculator, reader, Teach gate; see
  ``aaoverlay/selftest.py``) from the exe and from source and require identical reports, run the exe on real frames
  and compare with the source run, and check that nothing needed was removed.
* ``test_installed_*`` do the same for the real installer (silent install to a temp folder, run, uninstall).
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
OVERLAY = os.path.dirname(HERE)
sys.path.insert(0, OVERLAY)
import packaging_trim as PT  # noqa: E402
from aaoverlay import dataset as D  # noqa: E402

BUILD = os.path.join(os.environ.get("USERPROFILE", ""), "aaoverlay-build")
DIST = os.environ.get("AAOVERLAY_DIST") or os.path.join(BUILD, "dist", "AAOdds")
EXE = os.path.join(DIST, "AAOdds.exe")
INTERNAL = os.path.join(DIST, "_internal")
INSTALLER = (sorted(glob.glob(os.path.join(BUILD, "installer", "AAOdds-*-setup.exe"))) or [""])[-1]
PY = sys.executable
MAX_DIST_MB = 235

needs_dist = pytest.mark.skipif(not os.path.exists(EXE), reason="no packaged build (run build.ps1)")
needs_installer = pytest.mark.skipif(not os.path.exists(INSTALLER), reason="no installer built")


def run_selftest(cmd, cwd, tmp_path, name):
    data = tmp_path / (name + "_data")
    out = tmp_path / (name + ".json")
    env = dict(os.environ, AAOVERLAY_DATA=str(data))
    r = subprocess.run(cmd + ["--selftest", str(out)], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
    assert out.exists(), f"{name}: no report written (rc={r.returncode}) {r.stderr[-800:]}"
    return json.loads(out.read_text(encoding="utf-8")), r.returncode


@pytest.fixture(scope="module")
def source_report(tmp_path_factory):
    report, rc = run_selftest([PY, "-m", "aaoverlay"], OVERLAY, tmp_path_factory.mktemp("src"), "source")
    assert rc == 0
    return report


@pytest.fixture(scope="module")
def frozen_report(tmp_path_factory):
    return run_selftest([EXE], DIST, tmp_path_factory.mktemp("frozen"), "frozen")


# ---------------------------------------------------------------------------------------------------------------
# always-on checks
# ---------------------------------------------------------------------------------------------------------------
def test_source_selftest_is_healthy(source_report):
    errors = {k: v["error"] for k, v in source_report.items() if isinstance(v, dict) and "error" in v}
    assert not errors, errors
    assert source_report["resources"]["samples"] >= 100 and source_report["teach"]["gate_ok"]
    assert all(source_report["no_panel"]) and source_report["cv2"]["png"] and source_report["qt"]["custom_exact"]
    assert all(v is True for sec in source_report["live"].values() for v in sec.values())


def test_source_never_uses_what_the_build_leaves_out():
    """No source file may use OpenCV video or the Qt modules the packaged app drops."""
    bad = []
    for folder in ("aaoverlay", "tools"):
        for root, _d, files in os.walk(os.path.join(OVERLAY, folder)):
            for f in files:
                if f.endswith(".py") and f != "packaging_trim.py":
                    text = open(os.path.join(root, f), encoding="utf-8").read()
                    bad += [f"{f}: {w}" for w in PT.FORBIDDEN_SOURCE if re.search(r"\b" + w + r"\b", text)]
    assert not bad, bad


def test_drop_patterns_match_what_they_should_and_nothing_else():
    drops = ["cv2/opencv_videoio_ffmpeg510_64.dll", "PySide6\\translations\\qt_de.qm", "PySide6/Qt6Quick.dll",
             "PySide6/Qt6QmlMeta.dll", "PySide6/Qt6Pdf.dll", "PySide6/plugins/imageformats/qpdf.dll",
             "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll", "_internal/PySide6/Qt6Svg.dll"]
    keeps = [k for k in PT.KEEP] + ["cv2/cv2.pyd", "PySide6/Qt6Widgets.dll", "numpy.libs/libscipy_openblas64_x.dll",
                                    "PySide6/opengl32sw.dll", "PySide6/plugins/platforms/qwindows.dll",
                                    "PySide6/plugins/imageformats/qjpeg.dll", "aaoverlay/templates/icons/inf.png"]
    assert all(PT.is_dropped(p) for p in drops), [p for p in drops if not PT.is_dropped(p)]
    assert not any(PT.is_dropped(p) for p in keeps), [p for p in keeps if PT.is_dropped(p)]
    assert PT.filter_entries([("a/b.dll", "x", "BINARY"), ("cv2/opencv_videoio_ffmpeg1.dll", "y", "BINARY")]) == \
        [("a/b.dll", "x", "BINARY")]


def test_dataset_repack_is_lossless_and_smaller(tmp_path):
    import cv2
    import numpy as np
    dst = str(tmp_path / "raw")
    before, after = PT.repack_dataset(D.RAW, dst)
    names = sorted(os.listdir(D.RAW))
    assert sorted(os.listdir(dst)) == names
    assert after < before
    for n in names:
        a, b = os.path.join(D.RAW, n), os.path.join(dst, n)
        if n.endswith(".png"):
            ia, ib = cv2.imread(a, cv2.IMREAD_UNCHANGED), cv2.imread(b, cv2.IMREAD_UNCHANGED)
            assert ia.shape == ib.shape and ia.dtype == ib.dtype and np.array_equal(ia, ib), n
        else:
            assert open(a, "rb").read() == open(b, "rb").read(), n


def test_repacked_dataset_reads_identically(tmp_path):
    from aaoverlay import reader as R
    from aaoverlay import teach as T
    from aaoverlay.templates import TemplateStore
    dst = str(tmp_path / "raw")
    PT.repack_dataset(D.RAW, dst)
    store = TemplateStore()
    for name, meta in D.samples():
        assert T.status_of(store, name, meta, D.RAW) == T.status_of(store, name, meta, dst), name
    assert R is not None


# ---------------------------------------------------------------------------------------------------------------
# checks on the packaged build
# ---------------------------------------------------------------------------------------------------------------
@needs_dist
def test_build_has_everything_needed():
    missing = [k for k in PT.KEEP if not os.path.exists(os.path.join(INTERNAL, *k.split("/")))]
    assert not missing, missing
    assert len(os.listdir(os.path.join(INTERNAL, "dataset", "raw"))) == len(os.listdir(D.RAW))
    assert len(os.listdir(os.path.join(INTERNAL, "aaoverlay", "templates", "icons"))) >= 200


@needs_dist
def test_build_has_none_of_what_was_dropped():
    found = []
    for root, _d, files in os.walk(INTERNAL):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), INTERNAL)
            if PT.is_dropped(rel):
                found.append(rel)
    assert not found, found[:10]


@needs_dist
def test_build_is_not_bloated():
    total = sum(os.path.getsize(os.path.join(r, f)) for r, _d, fs in os.walk(DIST) for f in fs)
    assert total / 2 ** 20 <= MAX_DIST_MB, f"{total / 2 ** 20:.0f} MB"


@needs_dist
def test_frozen_selftest_has_no_errors(frozen_report):
    report, rc = frozen_report
    errors = {k: v["error"] for k, v in report.items() if isinstance(v, dict) and "error" in v}
    assert not errors, errors
    assert rc == 0
    assert all(v is True for sec in report["live"].values() for v in sec.values())


@needs_dist
def test_frozen_matches_source_exactly(frozen_report, source_report):
    report, _rc = frozen_report
    for key in source_report:
        if key == "live":
            continue
        assert report[key] == source_report[key], f"section {key!r} differs between the exe and the source tree"


@needs_dist
def test_frozen_reads_every_dataset_sample_never_wrong_without_a_warning(frozen_report):
    report, _rc = frozen_report
    exact = 0
    for name, meta in D.samples():
        got = report["dataset"][name]
        assert got is not None, name
        t = meta["truth"]
        want_d = dict(t["defenders"])
        if t.get("panel_aa"):
            want_d["aa"] = t["panel_aa"]
        if not got[2]:
            assert (got[0], got[1]) == (dict(t["attackers"]), want_d), f"{name}: silently wrong in the exe"
            exact += 1
    assert exact >= 95


def _debug_lines(text):
    return [ln.strip() for ln in text.splitlines() if ln.startswith(("[panel]", "[result] final", "[result] exact"))]


def _frames(tmp_path):
    import cv2
    picks, seen = [], set()
    for name, meta in D.samples():
        rows = len(meta["panel"]["dice"])
        kind = (min(rows, 3), bool(meta["truth"].get("amphibious")))
        if kind not in seen and not meta.get("flagged"):
            seen.add(kind)
            p = str(tmp_path / f"{name}.png")
            cv2.imwrite(p, D.load_canvas(name, meta))
            picks.append(p)
        if len(picks) == 4:
            break
    return picks


@needs_dist
def test_frozen_app_matches_source_app_on_real_frames(tmp_path):
    """The live app (real Qt windows, reader and solver threads) shows the same armies and odds from the exe."""
    frames = _frames(tmp_path)
    assert len(frames) >= 3
    for i, frame in enumerate(frames):
        env_src = dict(os.environ, AAOVERLAY_DATA=str(tmp_path / f"s{i}"))
        src = subprocess.run([PY, "-m", "aaoverlay", "--image", frame, "--run-seconds", "7", "--debug"], cwd=OVERLAY,
                             env=env_src, capture_output=True, text=True, timeout=120)
        env_exe = dict(os.environ, AAOVERLAY_DATA=str(tmp_path / f"e{i}"))
        exe = subprocess.run([EXE, "--image", frame, "--run-seconds", "7", "--debug"], cwd=DIST, env=env_exe,
                             capture_output=True, text=True, timeout=120)
        assert exe.returncode == 0, exe.stderr[-500:]
        log = open(glob.glob(str(tmp_path / f"e{i}" / "logs" / "*.log"))[0], encoding="utf-8").read()
        assert "Traceback" not in log and "[reader] error" not in log
        want, got = _debug_lines(src.stdout), _debug_lines(log)
        assert got, f"frame {i}: the exe never read a panel"
        assert got == want, f"frame {i}: exe {got} != source {want}"


@needs_dist
def test_frozen_app_survives_a_screen_with_no_panel(tmp_path):
    import cv2
    import numpy as np
    p = str(tmp_path / "blank.png")
    cv2.imwrite(p, np.full((1440, 2560, 3), 40, np.uint8))
    env = dict(os.environ, AAOVERLAY_DATA=str(tmp_path / "d"))
    r = subprocess.run([EXE, "--image", p, "--run-seconds", "4", "--debug"], cwd=DIST, env=env, capture_output=True,
                       text=True, timeout=120)
    log = open(glob.glob(str(tmp_path / "d" / "logs" / "*.log"))[0], encoding="utf-8").read()
    assert r.returncode == 0 and "no panel" in log and "Traceback" not in log


@needs_dist
def test_frozen_app_seeds_user_templates_and_keeps_taught_ones(tmp_path):
    data = tmp_path / "d"
    env = dict(os.environ, AAOVERLAY_DATA=str(data))
    subprocess.run([EXE, "--selftest", str(tmp_path / "a.json")], cwd=DIST, env=env, timeout=300)
    icons = data / "templates" / "icons"
    assert len(list(icons.glob("*.png"))) >= 200
    mine = icons / "inf__red__99.png"          # something a user taught
    mine.write_bytes(b"taught")
    victim = next(icons.glob("tank__*.png"))   # a seed file the user deleted comes back, a taught one stays
    victim.unlink()
    subprocess.run([EXE, "--selftest", str(tmp_path / "b.json")], cwd=DIST, env=env, timeout=300)
    assert mine.read_bytes() == b"taught" and victim.exists()


# ---------------------------------------------------------------------------------------------------------------
# the installer itself
# ---------------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    folder = tmp_path_factory.mktemp("inst") / "app"
    subprocess.run([INSTALLER, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/DIR={folder}"], check=True,
                   timeout=600)
    yield str(folder)
    un = os.path.join(str(folder), "unins000.exe")
    if os.path.exists(un):
        subprocess.run([un, "/VERYSILENT", "/SUPPRESSMSGBOXES"], timeout=300)


@needs_installer
def test_installed_app_selftest_matches_source(installed, source_report, tmp_path):
    exe = os.path.join(installed, "AAOdds.exe")
    assert os.path.exists(exe)
    report, rc = run_selftest([exe], installed, tmp_path, "installed")
    assert rc == 0
    for key in source_report:
        if key != "live":
            assert report[key] == source_report[key], key
    assert os.path.exists(os.path.join(installed, "unins000.exe"))


@needs_installer
def test_uninstall_removes_the_program_but_keeps_user_data(installed, tmp_path):
    data = tmp_path / "keep"
    env = dict(os.environ, AAOVERLAY_DATA=str(data))
    subprocess.run([os.path.join(installed, "AAOdds.exe"), "--selftest", str(tmp_path / "x.json")], cwd=installed,
                   env=env, timeout=300)
    subprocess.run([os.path.join(installed, "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES"], check=True,
                   timeout=300)
    assert not os.path.exists(os.path.join(installed, "AAOdds.exe"))
    assert (data / "templates" / "icons").is_dir()
    shutil.rmtree(installed, ignore_errors=True)
