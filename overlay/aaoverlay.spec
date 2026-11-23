# PyInstaller spec: ``pyinstaller aaoverlay.spec --noconfirm`` (or run build.ps1). Output: dist\AAOdds\AAOdds.exe
import os
import sys

sys.path.insert(0, SPECPATH)
import packaging_trim as trim  # what the app leaves out (shared with tests/test_frozen.py)

block_cipher = None

EXCLUDES = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtMultimedia", "PySide6.Qt3DCore", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtNetwork", "PySide6.QtOpenGL",
            "PySide6.QtSvg", "PySide6.QtTest", "tkinter", "matplotlib", "pytest"]

# The labelled panels re-encoded with maximum (lossless, verified) PNG compression.
work = os.path.join(os.environ.get("USERPROFILE", SPECPATH), "aaoverlay-build", "work", "dataset_raw")
trim.repack_dataset(os.path.join(SPECPATH, "dataset", "raw"), work)

a = Analysis(
    ["launcher.py"],
    pathex=[".."],  # the calculator package (aa1942) sits one folder above
    datas=[("aaoverlay/templates", "aaoverlay/templates"),   # seed icons and digits (copied to the user's data folder)
           (work, "dataset/raw")],                           # labelled panels: Teach's regression gate
    hiddenimports=["aa1942.amphibious", "aa1942.bombing"],
    excludes=EXCLUDES,
    noarchive=False,
)
a.binaries = trim.filter_entries(a.binaries)
a.datas = trim.filter_entries(a.datas)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AAOdds", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AAOdds", upx=False)
