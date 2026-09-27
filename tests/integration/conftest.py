"""The harness the integration tests share: the QML shell, and a bare controller.

``Main.qml`` is built for real (offscreen) and read back, because the failures that
matter here only appear once the component tree exists - a derived width that stops
re-evaluating, a panel that covers the text column, a shortcut whose spelling Qt
cannot parse, a control that is drawn but takes no clicks, or a warning at
construction.  Five modules need that harness - the columns (``test_layout.py``), the
scroll view (``test_scroll_view.py``, which drives the keys, the wheel and the scroll
bar), the outline panel (``test_outline.py``), the right-click menu (``test_menu.py``)
and the contents panel (``test_toc_panel.py``, which needs the controller fixture as
well) - so it lives here instead of in any of them.

The fixtures below are discovered by pytest; the helpers are imported by name.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from PySide6.QtCore import QEventLoop, QObject, QPoint, QPointF, Qt, QTimer
from PySide6.QtQuick import QQuickItem

if TYPE_CHECKING:
    from ebook_reader.app.controller import ReaderController

QML_MODULE = "EbookReader"

#: How long Main.qml waits before it re-lays out after a resize.
RESIZE_DEBOUNCE_MS = 140

#: The key combinations the shell's shortcuts use, by the name the menu shows for
#: them.  Key *codes* rather than the strings from Main.qml, because ``QTest`` needs
#: the code: a test that sent the string would only be testing its own translation of
#: it.  Ctrl+O and Ctrl+Q are deliberately absent - one opens a native file dialog and
#: the other quits the application, and neither belongs in a test run.
KEYS: dict[str, tuple[Qt.Key, Qt.KeyboardModifier]] = {
    "Down": (Qt.Key_Down, Qt.NoModifier),
    "Up": (Qt.Key_Up, Qt.NoModifier),
    "Left": (Qt.Key_Left, Qt.NoModifier),
    "Right": (Qt.Key_Right, Qt.NoModifier),
    "Space": (Qt.Key_Space, Qt.NoModifier),
    "PgDown": (Qt.Key_PageDown, Qt.NoModifier),
    "PgUp": (Qt.Key_PageUp, Qt.NoModifier),
    "Backspace": (Qt.Key_Backspace, Qt.NoModifier),
    "Home": (Qt.Key_Home, Qt.NoModifier),
    "End": (Qt.Key_End, Qt.NoModifier),
    "]": (Qt.Key_BracketRight, Qt.NoModifier),
    "[": (Qt.Key_BracketLeft, Qt.NoModifier),
    "T": (Qt.Key_T, Qt.NoModifier),
    "O": (Qt.Key_O, Qt.NoModifier),
    "S": (Qt.Key_S, Qt.NoModifier),
    "Esc": (Qt.Key_Escape, Qt.NoModifier),
}

_registered = False


def _register() -> None:
    """Register the custom item type; a second call in one process is redundant."""
    global _registered
    if _registered:
        return
    from PySide6.QtQml import qmlRegisterType

    from ebook_reader.app.page_item import PageItem

    qmlRegisterType(PageItem, QML_MODULE, 1, 0, "PageItem")
    _registered = True


def pump(milliseconds: int = 150) -> None:
    """Run the event loop, so deferred calls and debounce timers can fire."""
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()


def item(window, name: str) -> QQuickItem:
    """A named item from ``Main.qml``; the names exist for these tests."""
    found = window.findChild(QQuickItem, name)
    assert found is not None, f"{name} is missing from Main.qml"
    return found


def visible(window, name: str) -> bool:
    return bool(item(window, name).property("visible"))


def on_screen(child: QQuickItem) -> bool:
    """Whether *child* is drawn inside the window - a drawer slides out of it instead.

    ``visible`` cannot answer this for the settings drawer: it stays ``visible: true``
    and is moved past the right edge when it closes, so only its position says whether
    the reader can see it.
    """
    left, _ = at(child, 0, 0)
    return left < child.window().width() and left + child.width() > 0


def click(window, position: tuple[int, int], right: bool = False) -> None:
    """A real click on the window, through Qt's own event delivery.

    Deliberately not a call into a QML function: whether the event reaches the item
    that is supposed to receive it is exactly what these tests are about.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    button = Qt.RightButton if right else Qt.LeftButton
    QTest.mouseClick(window, button, Qt.NoModifier, QPoint(*position))


def at(child: QQuickItem, x: float, y: float) -> tuple[int, int]:
    """A point inside *child* in window coordinates, which is what a click needs.

    Not ``child.x() + x``: the page column starts after the panels, so an item's own
    coordinates and the window's differ by however many columns are open - and a click
    sent at the wrong one lands on the text instead.
    """
    point: QPointF = child.mapToScene(QPointF(x, y))
    return int(point.x()), int(point.y())


def centre(child: QQuickItem) -> tuple[int, int]:
    """Where to click to hit *child*, in window coordinates."""
    return at(child, child.width() / 2, child.height() / 2)


def press(window, name: str) -> None:
    """Press one documented key combination, through Qt's own event delivery.

    A real key event, not a call into the controller and not a call into QML: whether
    the shortcut is *reachable* is exactly what these tests are about, and a shortcut
    whose sequence Qt cannot parse is registered but never fires.
    """
    from PySide6.QtTest import QTest

    code, modifier = KEYS[name]
    QTest.keyClick(window, code, modifier)
    pump(120)


def wheel(window, position: tuple[int, int], notches: float = 1, pixels: int = 0) -> None:
    """One wheel event at *position*; positive notches scroll down (FR-063).

    A mouse sends one notch - 120 units of angle - which is what ``notches`` builds,
    and a half or quarter notch is what a high-resolution wheel sends, so it is not
    required to be whole.  A touchpad, a high-resolution wheel or a free-spinning
    wheel in a smooth-scrolling session sends screen pixels with no angle at all,
    which is what ``pixels`` builds - and it is the shape that used to scroll by
    nothing.  The two are separate arguments because the controller deliberately
    treats them differently; a test wanting both can pass both.

    Sent to the window rather than to an item, so the same hit-testing decides where
    it lands as for a reader's own wheel.
    """
    from PySide6.QtGui import QGuiApplication, QWheelEvent

    point = QPointF(*position)
    event = QWheelEvent(
        point,
        point,
        QPoint(0, int(round(pixels))),
        QPoint(0, int(round(-120 * notches))),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
    )
    QGuiApplication.sendEvent(window, event)
    pump(120)
    return event


def shortcut_sequences(window) -> list[str]:
    """The sequences Main.qml registers, exactly as QML spells them.

    Read off the live ``Shortcut`` objects rather than from the QML source, so a
    shortcut that is declared but never added to the shell cannot pass - and what
    comes back is the text Qt had to parse, which is the part that can be wrong.
    """
    found: list[str] = []
    for child in window.findChildren(QObject):
        if "Shortcut" not in child.metaObject().className():
            continue
        single = child.property("sequence")
        if single is not None and str(single):
            found.append(str(single))
        else:
            found.extend(str(text) for text in (child.property("sequences") or []))
    return found


def shortcut_keys(window) -> set[str]:
    """The same sequences as ``QKeySequence`` reads them, in Qt's own spelling.

    The spelling is load-bearing: ``QKeySequence.fromString("PageDown")`` gives an
    empty sequence, and an empty sequence is a shortcut that never fires - which is
    how the scroll keys were silently dead once already.
    """
    from PySide6.QtGui import QKeySequence

    return {
        parsed
        for parsed in (
            QKeySequence.fromString(text).toString()
            for text in shortcut_sequences(window)
        )
        if parsed
    }


@pytest.fixture
def controller(qapp, tmp_path: Path) -> "ReaderController":
    """A controller with a throwaway config file, never the user's own.

    For the tests that are about the controller's own rules - which panel yields to
    which, what a click on a row does - and do not need the QML shell over it.
    """
    from ebook_reader.app.controller import ReaderController
    from ebook_reader.app.settings_store import SettingsStore

    return ReaderController(store=SettingsStore(tmp_path / "config.json"))


@pytest.fixture
def warnings(qapp) -> list[str]:
    """Everything Qt logs during one test; QML reports its problems through qWarning."""
    from PySide6.QtCore import qInstallMessageHandler

    collected: list[str] = []
    previous = qInstallMessageHandler(
        lambda mode, context, message: collected.append(message)  # noqa: ARG005
    )
    yield collected
    qInstallMessageHandler(previous)


@pytest.fixture
def shell(qapp, tmp_path: Path, warnings):
    """``Main.qml`` loaded and given a controller, exactly as ``main()`` does it."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from ebook_reader.app.controller import ReaderController
    from ebook_reader.app.main import qml_dir
    from ebook_reader.app.settings_store import SettingsStore

    _register()
    controller = ReaderController(store=SettingsStore(tmp_path / "config.json"))
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
    roots = engine.rootObjects()
    assert roots, "Main.qml did not load"
    window = roots[0]
    window.setProperty("ctl", controller)
    # The engine owns the components; keeping it in the fixture keeps them alive.
    return engine, window, controller
