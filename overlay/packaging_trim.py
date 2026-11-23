"""What the packaged app leaves out, shared by ``aaoverlay.spec`` (which applies it) and ``tests/test_frozen.py``
(which proves the build is free of it and still works).

Everything here is something the app never loads: OpenCV's video backend, Qt's QML / Quick / PDF / SVG / virtual
keyboard modules (PyInstaller pulls them in as plugin dependencies), and Qt's translation files. The dataset images
are re-encoded with maximum PNG compression (lossless; verified pixel for pixel).
"""
import fnmatch
import os
import shutil

# Paths are relative to the app's ``_internal`` folder, lower case, forward slashes.
DROP = (
    "cv2/opencv_videoio_ffmpeg*.dll",
    "pyside6/translations/*",
    "pyside6/qt6quick*.dll", "pyside6/qt6qml*.dll", "pyside6/qt6pdf.dll", "pyside6/qt6svg.dll",
    "pyside6/qt6virtualkeyboard.dll",
    "pyside6/plugins/imageformats/qpdf.dll", "pyside6/plugins/imageformats/qsvg.dll",
    "pyside6/plugins/iconengines/qsvgicon.dll", "pyside6/plugins/platforminputcontexts/*",
)

# Things that must survive the trimming.
KEEP = (
    "cv2/cv2.pyd", "pyside6/qt6core.dll", "pyside6/qt6gui.dll", "pyside6/qt6widgets.dll",
    "pyside6/plugins/platforms/qwindows.dll", "pyside6/plugins/platforms/qoffscreen.dll",
    "pyside6/plugins/styles/qmodernwindowsstyle.dll", "pyside6/plugins/imageformats/qjpeg.dll",
    "pyside6/qtcore.pyd", "pyside6/qtgui.pyd", "pyside6/qtwidgets.pyd", "python311.dll",
    "aaoverlay/templates", "dataset/raw",
)

# Source files must not use anything removed.
FORBIDDEN_SOURCE = ("VideoCapture", "VideoWriter", "QtQml", "QtQuick", "QtPdf", "QtSvg", "QtMultimedia",
                    "QtWebEngine", "QtVirtualKeyboard")


def norm(path):
    return path.replace("\\", "/").lower().lstrip("/")


def is_dropped(path):
    p = norm(path)
    if p.startswith("_internal/"):
        p = p[len("_internal/"):]
    return any(fnmatch.fnmatch(p, pat) for pat in DROP)


def filter_entries(entries):
    """PyInstaller TOC entries ``(dest_name, src_path, typecode)`` without the dropped files."""
    return [e for e in entries if not is_dropped(e[0])]


def repack_dataset(src, dst):
    """Copy ``src`` to ``dst`` re-encoding PNGs with maximum compression; a file is only replaced when the decoded
    pixels are identical and the file got smaller. Returns ``(bytes_before, bytes_after)``."""
    import cv2
    import numpy as np
    shutil.rmtree(dst, ignore_errors=True)
    os.makedirs(dst)
    before = after = 0
    for name in sorted(os.listdir(src)):
        s, d = os.path.join(src, name), os.path.join(dst, name)
        before += os.path.getsize(s)
        shutil.copyfile(s, d)
        if name.lower().endswith(".png"):
            img = cv2.imread(s, cv2.IMREAD_UNCHANGED)
            ok, buf = cv2.imencode(".png", img, [cv2.IMWRITE_PNG_COMPRESSION, 9]) if img is not None else (False, None)
            if ok and len(buf) < os.path.getsize(s):
                again = cv2.imdecode(buf, cv2.IMREAD_UNCHANGED)
                if again is not None and again.shape == img.shape and again.dtype == img.dtype \
                        and np.array_equal(again, img):
                    with open(d, "wb") as fh:
                        fh.write(buf.tobytes())
        after += os.path.getsize(d)
    return before, after
