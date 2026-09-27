# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for the EPUB reader (architecture.md, P7).

``packaging/build.sh`` drives this twice and produces

    dist/ebook-reader-dir/    folder build -- fast start, the AppImage payload
    dist/ebook-reader         single file  -- convenient to hand out

Only our own QML sources need declaring.  The Qt side is handled by the PySide6
hooks: importing ``PySide6.QtQml`` makes them walk the whole ``PySide6/Qt/qml``
tree and collect every directory carrying a ``qmldir``.  That is what brings in
the modules which appear *only* inside QML and are therefore invisible to the
byte-code scanner -- ``QtQuick.Controls``, ``QtQuick.Layouts``,
``QtQuick.Dialogs``, ``QtQuick.Window``.  They cannot be listed as hidden
imports, because they are not Python modules.

The hooks over-collect by design, so the result then goes through
``packaging/bundle.py``, which applies the drop tables and prunes whatever they
leave unreferenced -- worth about 50 MB, mostly GTK and unused control styles.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, SPECPATH)
import bundle  # noqa: E402 - needs the line above

APP_NAME = "ebook-reader"
ROOT = Path(SPECPATH).parent
ONEFILE = os.environ.get("EBR_ONEFILE", "0") == "1"

datas = [
    (str(ROOT / "src" / "ebook_reader" / "qml"), "ebook_reader/qml"),
]

hiddenimports = [
    # QML-only imports: naming the Qt modules here is what keeps the matching
    # Qt libraries and QML plugin directories reachable.
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuickControls2",
]

# Qt ships a great deal this application never touches.  Every binding listed
# below is verified unused; if one ever becomes a real dependency the build
# fails loudly rather than quietly shipping another twenty megabytes.
excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets",
    "PySide6.QtHelp",
    "PySide6.QtHttpServer",
    "PySide6.QtLocation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtNfc",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtPrintSupport",
    "PySide6.QtQuick3D",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio",
    "PySide6.QtSql",
    "PySide6.QtStateMachine",
    "PySide6.QtTest",
    "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
    # Python-side fat we do not use.
    "unittest",
    "pydoc_data",
    "lib2to3",
    "tkinter",
    "pytest",
]

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(analysis.pure)

# Trim after Analysis rather than before: the hooks are the only reliable way to
# discover what Qt needs, so the job here is to subtract, not to predict.
analysis.binaries, analysis.datas = bundle.trim(analysis.binaries, analysis.datas)

if ONEFILE:
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
    )
else:
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=APP_NAME,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=True,
        disable_windowed_traceback=False,
    )
    collected = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name=APP_NAME + "-dir",
    )
