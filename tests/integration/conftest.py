"""The harness the integration tests share: the QML shell, and a bare controller.

``Main.qml`` is built for real (offscreen) and read back, because the failures that
matter here only appear once the component tree exists - a derived width that stops
re-evaluating, a panel that covers the text column, a shortcut whose spelling Qt
cannot parse, a control that is drawn but takes no clicks, or a warning at
construction.  Six modules need that harness - the columns (``test_layout.py``), the
scroll view (``test_scroll_view.py``, which drives the keys, the wheel and the scroll
bar), the pointer on the page (``test_selection.py``, which pans and marks), the outline
panel (``test_outline.py``), the right-click menu (``test_menu.py``)
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
    "Ctrl+C": (Qt.Key_C, Qt.ControlModifier),
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


def dominant_colour(image, paper: str) -> str:
    """Colour the ink on a rasterised page was drawn in, given that page's paper.

    Sampled from the bitmap rather than asked of the engine, because the claim being
    made is the reader-facing one: this is what the page in front of them is made of.
    Antialiased glyph edges are a mixture of ink and paper, so the *most* common colour
    that is not the paper is the ink itself.
    """
    from collections import Counter

    counts = Counter(
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), 2)
        for x in range(0, image.width(), 2)
    )
    for colour, _ in counts.most_common():
        if colour != paper:
            return colour
    raise AssertionError("the page has no ink on it at all")


def painted_colours(image, *, step: int = 2) -> set[str]:
    """Every colour that appears in a rasterised page, sampled on a grid.

    The *set* rather than the dominant colour, for images that carry window chrome as
    well as text: the claim there is "nothing on this page is drawn in the old theme's
    ink", which the set answers without having to know which element is the largest.
    """
    return {
        image.pixelColor(x, y).name()
        for y in range(0, image.height(), step)
        for x in range(0, image.width(), step)
    }


def click(window, position: tuple[int, int], right: bool = False) -> None:
    """A real click on the window, through Qt's own event delivery.

    Deliberately not a call into a QML function: whether the event reaches the item
    that is supposed to receive it is exactly what these tests are about.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    button = Qt.RightButton if right else Qt.LeftButton
    QTest.mouseClick(window, button, Qt.NoModifier, QPoint(*position))


def double_click(window, position: tuple[int, int]) -> None:
    """Two clicks in one place, as Qt delivers them (a press, a release, again)."""
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(*position))
    pump(120)


def drag(
    window,
    start: tuple[int, int],
    end: tuple[int, int],
    steps: int = 5,
    modifier: Qt.KeyboardModifier = Qt.NoModifier,
) -> None:
    """Press at *start*, move to *end* in *steps*, and release there.

    A real drag, for the same reason :func:`click` is a real click: the divider has
    to be reachable and has to track the pointer while it is held, and a test that
    called the QML function behind it would prove neither.  The moves are the plain
    events a pointer sends, not "press, then jump" - a handle that only worked on a
    single jump would pass a one-step version of this.

    *modifier* is held down for the whole gesture, which is how Shift turns a pan
    into a passage (FR-070): the item reads the modifier off each event, so a test
    that only pressed the key first would not prove the gesture works.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    QTest.mousePress(window, Qt.LeftButton, modifier, QPoint(*start))
    for step in range(1, steps + 1):
        x = int(round(start[0] + (end[0] - start[0]) * step / steps))
        y = int(round(start[1] + (end[1] - start[1]) * step / steps))
        QTest.mouseMove(window, QPoint(x, y))
        pump(20)
    QTest.mouseRelease(window, Qt.LeftButton, modifier, QPoint(*end))
    # Long enough for the resize debounce (140 ms) to have re-laid the section out:
    # the drag ends with one report to Python, and the page box is an assertion.
    pump(RESIZE_DEBOUNCE_MS + 40)


def at(child: QQuickItem, x: float, y: float) -> tuple[int, int]:
    """A point inside *child* in window coordinates, which is what a click needs.

    Not ``child.x() + x``: the page column starts after the panels, so an item's own
    coordinates and the window's differ by however many columns are open - and a click
    sent at the wrong one lands on the text instead.
    """
    point: QPointF = child.mapToScene(QPointF(x, y))
    return int(point.x()), int(point.y())


def set_window_size(window, width: int | None = None, height: int | None = None) -> None:
    """Give the window a size a test can measure; the rest is left as it was.

    Opening a book maximizes the window (FR-077), and a maximized window ignores the
    ``width`` a test sets - so a test that is about a particular size has to leave
    full size first.  Called with no size at all it restores the size ``Main.qml``
    builds the shell with, which is what a test that wants "the documented default"
    means.
    """
    window.showNormal()
    if width is not None:
        window.setProperty("width", width)
    if height is not None:
        window.setProperty("height", height)
    pump(2 * RESIZE_DEBOUNCE_MS)


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


def open_long_section(controller, path: Path, *, screens: float = 2.0) -> int:
    """Open *path* on a section with room to scroll in, and return its index.

    Deliberately *not* section 0: both books open on a cover that fits on one screen,
    where every scroll assertion would hold trivially - and where a drag would have
    nothing to move and a passage nothing to mark.

    Shared, because the two modules that need it are asking the same question about
    different things: one about what moves the text (``test_scroll_view.py``, the keys,
    the wheel and the scroll bar) and one about what the pointer does to it
    (``test_selection.py``, panning and marking).
    """
    assert controller.openBook(str(path))
    pump(240)
    for index in range(controller.sectionCount):
        controller.goToSection(index)
        if controller.scrollMax > screens * controller.viewportHeight:
            break
    else:
        pytest.skip("reference book has no section long enough to scroll in")
    controller.scrollToTop()
    pump(120)
    assert controller.scrollOffset == 0.0
    return index


def touch_device():
    """A pointing device of the kind Qt's Wayland plugin reports the wheel from.

    On Wayland the compositor's ``wl_pointer.axis`` events carry no device of their
    own, so Qt attributes every one of them to the seat's pointer device - which it
    registers as a TouchPad.  ``WheelHandler`` accepts ``Mouse`` alone unless it is
    told otherwise, so the reader's own mouse wheel does nothing there while ``↓``
    works (defect 24).  Handing an event this device to the shell reproduces that on
    any platform, offscreen included.
    """
    from PySide6.QtGui import QInputDevice, QPointingDevice

    return QPointingDevice(
        "touchpad",
        8589934592,                       # the id Qt gives the seat's pointer
        QInputDevice.DeviceType.TouchPad,
        QPointingDevice.PointerType.Finger,
        QInputDevice.Capability.Position,
        10,
        0,
    )


def wheel(
    window,
    position: tuple[int, int],
    notches: float = 1,
    pixels: int = 0,
    device=None,
) -> None:
    """One wheel event at *position*; positive notches scroll down (FR-063).

    A mouse sends one notch - 120 units of angle - which is what ``notches`` builds,
    and a half or quarter notch is what a high-resolution wheel sends, so it is not
    required to be whole.  A touchpad, a high-resolution wheel or a free-spinning
    wheel in a smooth-scrolling session sends screen pixels with no angle at all,
    which is what ``pixels`` builds - and it is the shape that used to scroll by
    nothing.  The two are separate arguments because the controller deliberately
    treats them differently; a test wanting both can pass both.  *device* names the
    pointing device the event came from - see :func:`touch_device`.

    Sent to the window rather than to an item, so the same hit-testing decides where
    it lands as for a reader's own wheel.
    """
    from PySide6.QtGui import QGuiApplication, QWheelEvent

    point = QPointF(*position)
    arguments = {}
    if device is not None:
        arguments["device"] = device
    event = QWheelEvent(
        point,
        point,
        QPoint(0, int(round(pixels))),
        QPoint(0, int(round(-120 * notches))),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.ScrollUpdate,
        False,
        **arguments,
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


def build_shell(config_path: Path):
    """``Main.qml`` loaded and given a controller, exactly as ``main()`` does it.

    A function as well as a fixture, because "the width is remembered" is a statement
    about two sessions: a test builds one shell, drags, and builds a second one over
    the same configuration file to see what the next session opens with.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from ebook_reader.app.controller import ReaderController
    from ebook_reader.app.main import qml_dir
    from ebook_reader.app.settings_store import SettingsStore

    _register()
    controller = ReaderController(store=SettingsStore(config_path))
    engine = QQmlApplicationEngine()
    engine.load(QUrl.fromLocalFile(str(qml_dir() / "Main.qml")))
    roots = engine.rootObjects()
    assert roots, "Main.qml did not load"
    window = roots[0]
    window.setProperty("ctl", controller)
    # The engine owns the components; the caller keeps it alive.
    return engine, window, controller


@pytest.fixture
def shell(qapp, tmp_path: Path, warnings):
    """``Main.qml`` loaded and given a controller, exactly as ``main()`` does it."""
    return build_shell(tmp_path / "config.json")

