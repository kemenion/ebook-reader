"""The Kai face ships inside the package and is registered before any text exists (ADR-025).

The kai choice used to be nothing but a list of system family names, so on a machine
without a Chinese Kai installed the reader asked for 楷体 and silently got 宋体: Qt walks
the fallback chain, finds a serif at the end of it, and nothing reports that the reader's
favourite typeface is missing.  The face therefore travels with the application, and this
module is the single place that hands it to Qt.

Registration only.  Which families to *ask for* is typography policy and stays in
``settings.py`` (``KAI_STACK``); rasterising glyphs is Qt's job (``style.py``).  A missing
or rejected font file is a degraded start, never a crash - the chains below the bundled
family still name system faces, so the reader keeps a readable page.

``QFontDatabase`` needs a live ``QGuiApplication``, so :func:`load_bundled_fonts` is called
from ``app/main.py`` right after the application object exists; the path helpers are pure
and can be exercised without one.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtGui import QFontDatabase

__all__ = [
    "BUNDLED_KAI_FAMILY",
    "BUNDLED_KAI_FILE",
    "LICENSE_FILE",
    "bundled_font_file",
    "fonts_dir",
    "load_bundled_fonts",
]

_log = logging.getLogger(__name__)

#: Directory inside the package - and inside a frozen build's ``_MEIPASS`` - that holds
#: the shipped face together with the licence it must travel with.
FONT_DIR_NAME = "fonts"

#: LXGW WenKai Regular (霞鹜文楷), upstream 1.511, 19 MB, SIL OFL 1.1.  Shipped byte for
#: byte as upstream publishes it: the licence only lets a *modified* font keep the
#: reserved names 霞鹜 / LXGW if it is rebuilt from the unmodified sources, and there is
#: no reason to subset it here (that would need ``fonttools``, i.e. a new dependency).
BUNDLED_KAI_FILE = "LXGWWenKai-Regular.ttf"

#: Family name Qt reports for :data:`BUNDLED_KAI_FILE`, read back from
#: ``QFontDatabase.applicationFontFamilies()`` on Qt 6.11.2 rather than guessed from the
#: filename (the file's style is ``Regular``, the localized family is 霞鹜文楷, and only
#: this spelling is the one a ``QFont`` resolves against).
BUNDLED_KAI_FAMILY = "LXGW WenKai"

#: The licence text, shipped beside the font because OFL-1.1 requires it to accompany
#: every copy - including the copies inside an AppImage.
LICENSE_FILE = "OFL.txt"


def fonts_dir() -> Path:
    """Directory holding the bundled font, both in-tree and when packaged.

    Mirrors :func:`ebook_reader.app.main.qml_dir`: a frozen build unpacks
    ``ebook_reader/fonts`` next to the modules under ``sys._MEIPASS``, the source tree
    keeps it one level above this file.  Both layouts are probed explicitly so that a
    change in how the freezer lays data out fails as a logged warning (the system chain
    takes over) instead of resolving to some unrelated font elsewhere on the disk.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        for candidate in (Path(base) / "ebook_reader" / FONT_DIR_NAME, Path(base) / FONT_DIR_NAME):
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parent.parent / FONT_DIR_NAME


def bundled_font_file(base: Path | None = None) -> Path | None:
    """The shipped TTF, or ``None`` when this build carries none.

    *base* exists so the "no font in this build" branch can be tested without deleting
    anything from the real package.
    """
    path = (fonts_dir() if base is None else base) / BUNDLED_KAI_FILE
    return path if path.is_file() else None


def load_bundled_fonts(base: Path | None = None) -> tuple[str, ...]:
    """Register the shipped face; returns the families Qt added, ``()`` on failure.

    Call once, after ``QGuiApplication`` exists.  The return value is what Qt actually
    registered (a ``.ttc`` can expose several faces); logging it is the only way to tell
    a packaged build whose ``datas`` went missing from one that works.
    """
    path = bundled_font_file(base)
    if path is None:
        _log.warning(
            "bundled font %s is missing; the kai choice falls back to system faces",
            BUNDLED_KAI_FILE,
        )
        return ()
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id < 0:
        _log.warning("Qt refused the bundled font %s", path)
        return ()
    families = tuple(QFontDatabase.applicationFontFamilies(font_id))
    _log.info("bundled font %s registered as %s", path.name, ", ".join(families) or "<unnamed>")
    return families
