"""Application startup: QGuiApplication, QML engine, controller wiring (FR-001)."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QUrl, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuickControls2 import QQuickStyle

from .controller import ReaderController
from .page_item import PageItem
from .settings_store import SettingsStore

__all__ = ["main", "qml_dir"]

_LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"

#: QML module name that exposes the custom item type and the controller singleton.
QML_MODULE = "EbookReader"


def qml_dir() -> Path:
    """Directory holding the QML files, both in-tree and when packaged.

    A frozen build (PyInstaller) unpacks the bundled ``ebook_reader/qml`` data
    next to the modules, under ``sys._MEIPASS``; the source tree keeps it two
    levels up from this file.  Both layouts are probed explicitly rather than
    relying on ``__file__`` happening to resolve, so that a change in how the
    freezer lays modules out fails loudly (``main`` reports a missing file)
    instead of silently loading a stale copy from elsewhere on the system.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for candidate in (Path(base) / "ebook_reader" / "qml", Path(base) / "qml"):
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parent.parent / "qml"


def main(argv: list[str] | None = None) -> int:
    """Run the reader; returns a process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

    book_path = _book_argument(argv)
    if book_path is None and argv and not argv[0].startswith("-"):
        print(f"错误：找不到文件 {argv[0]}", file=sys.stderr)
        return 2

    QCoreApplication.setApplicationName("ebook-reader")
    QCoreApplication.setOrganizationName("ebook-reader")
    QCoreApplication.setApplicationVersion("0.1.0")

    # The control style decides how the handful of Qt Quick Controls we use --
    # the file dialog and the two sliders -- are drawn, and how the dialogs pick
    # their per-style variants.  Left alone it follows the desktop's theme
    # configuration, so the same build renders differently from one machine to
    # the next.  Pinning it keeps the UI identical everywhere, and it is also
    # what lets the packaging drop the other style implementations (see
    # packaging/bundle.py).
    QQuickStyle.setStyle("Fusion")

    app = QGuiApplication([sys.argv[0]])
    app.setApplicationDisplayName("电子书阅读器")

    controller = ReaderController(store=SettingsStore())
    qmlRegisterType(PageItem, QML_MODULE, 1, 0, "PageItem")

    source = qml_dir() / "Main.qml"
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(source)))
    roots = engine.rootObjects()
    if not roots:
        print(f"错误：QML 界面加载失败（{source}）", file=sys.stderr)
        return 3

    # The controller is handed to the QML root object *after* it is loaded rather
    # than through a context property.  A context property is only looked up when a
    # binding is first evaluated, and nested components (side bars, list
    # delegates) are constructed during the load, so each of their bindings logged
    # a "Cannot read property ... of null" type error at start-up.  Injecting the
    # object and guarding the bindings removes that whole failure mode, and it also
    # keeps the dependency explicit instead of relying on a global name.
    roots[0].setProperty("ctl", controller)

    app.aboutToQuit.connect(controller.shutdown)
    if book_path is not None:
        controller.openBook(str(book_path))
    return app.exec()


def _book_argument(argv: list[str]) -> Path | None:
    """First argument that names an existing file, or ``None``."""
    for argument in argv:
        if argument.startswith("-"):
            continue
        candidate = Path(os.path.expanduser(argument))
        if candidate.is_file():
            return candidate
        return None
    return None


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
