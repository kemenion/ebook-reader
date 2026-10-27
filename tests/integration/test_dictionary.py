"""The word card through the shell: the switch, the pointer, and what ends up on screen.

Only a real component tree can answer the questions here: that a pointer event over the
page is what starts the dwell, that the card lands beside the pointer instead of off the
page, that the 悬停查词 switch in the right-click menu both silences the feature and takes
a card that is already up away, and that scrolling takes it away again (FR-095).

The rules *behind* the card - which keys a word is worth, what the gloss keeps, that a
missing dictionary answers nothing - are pure strings and one SQLite file, and are tested
in ``tests/unit/test_lexicon.py``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtQuick import QQuickItem

from conftest import at, item, pump
from ebook_reader.app.controller import ReaderController
from ebook_reader.app.settings_store import SettingsStore

#: Comfortably longer than the dwell the controller waits before looking a word up, so a
#: `pump` of this length cannot be the reason a card is missing.
DWELL_MS = 450

#: The word the English samples put on their first line, and the one the Chinese sample
#: does.  Both are ordinary words; only the first is in the shipped dictionary.
_ENGLISH = "apple"
_CHINESE = "读书"


def _book(build_epub, word: str, name: str) -> Path:
    """A one-line book: *word* alone on its first line, where a point can be aimed."""
    return build_epub({"OEBPS/text.xhtml": f"<html><body><p>{word}</p></body></html>"}, name=name)


def _long_book(build_epub) -> Path:
    """The same word over plenty of lines, so the page has somewhere to scroll to."""
    paragraphs = "".join(f"<p>{_ENGLISH} line {index}</p>" for index in range(60))
    return build_epub(
        {"OEBPS/text.xhtml": f"<html><body>{paragraphs}</body></html>"}, name="long.epub"
    )


def _word_point(window, controller) -> QPoint:
    """A point inside the word on the first line, in window coordinates.

    The first glyph starts exactly at the content origin - the left margin and the top
    margin - and two pixels into it is inside the first letter at any type size.
    """
    left, top = at(item(window, "readingView"), controller.margin + 2, controller.topMargin + 2)
    return QPoint(left, top)


def _move_pointer(window, point: QPoint) -> None:
    """Report the pointer at *point*, as a real event rather than a jump.

    A ``QMouseEvent`` through ``QGuiApplication.sendEvent``: this is what QML's
    ``onPositionChanged`` sees, so the hover lands on ``pagePointer`` exactly the way it
    does when a hand moves a mouse - nothing held, nothing clicked, just a position.
    """
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(point),
        QPointF(window.mapToGlobal(point)),
        Qt.NoButton,
        Qt.NoButton,
        Qt.NoModifier,
    )
    QGuiApplication.sendEvent(window, event)


def _card(window) -> QQuickItem:
    """The card itself; gone from the screen does not mean gone from the tree."""
    return item(window, "dictionaryPopup")


def _shown(window) -> bool:
    return bool(_card(window).property("visible"))


def test_pointing_at_an_english_word_raises_its_card(shell, build_epub, warnings) -> None:
    """Pointing at a word is the whole gesture: no key held, nothing clicked (FR-095)."""
    _engine, window, controller = shell
    assert controller.openBook(str(_book(build_epub, _ENGLISH, "english.epub")))
    pump(240)

    _move_pointer(window, _word_point(window, controller))
    pump(DWELL_MS)

    assert _shown(window)
    assert item(window, "dictionaryWord").property("text") == _ENGLISH
    assert item(window, "dictionaryPhonetic").property("text").startswith("/")
    # The gloss lines live in a Repeater inside the column, and a delegate is a *visual*
    # child of the column: the object tree does not lead there, so they are read from it.
    senses = [
        child.property("text")
        for child in item(window, "dictionaryBody").childItems()
        if child.objectName() == "dictionarySense"
    ]

    assert any("苹果" in sense for sense in senses)

    # Laid out beside the pointer and inside the column: a card pushed off the page would
    # be as good as no card at all.
    card = _card(window)
    assert card.x() >= 0
    assert card.y() > controller.topMargin
    assert card.width() > 0
    assert card.x() + card.width() <= item(window, "readingView").width()
    assert warnings == []


def test_the_card_waits_for_the_pointer_to_rest(shell, build_epub) -> None:
    """A pointer crossing a page must not flash a card for every word it passes."""
    _engine, window, controller = shell
    assert controller.openBook(str(_book(build_epub, _ENGLISH, "english.epub")))
    pump(240)

    _move_pointer(window, _word_point(window, controller))
    pump(60)  # well short of the dwell

    assert not _shown(window)
    pump(DWELL_MS)
    assert _shown(window)


def test_turning_the_switch_on_needs_nothing_but_a_pointer_move(
    shell, build_epub
) -> None:
    """Off means off - and on takes effect on the next move, with no key in between.

    With the switch off the dwell is never started, so the dictionary is not consulted at
    all; switching it on is read at the next pointer event, because the pointer is what
    has always driven the card (FR-095).
    """
    _engine, window, controller = shell
    controller.setDictionaryEnabled(False)
    assert controller.openBook(str(_book(build_epub, _ENGLISH, "english.epub")))
    pump(240)

    point = _word_point(window, controller)
    _move_pointer(window, point)
    pump(DWELL_MS)
    assert not _shown(window)               # off: no timer, no query, nothing on screen

    controller.setDictionaryEnabled(True)
    # A pixel to the right, and not the very same point: Qt drops a mouse move that lands
    # where the pointer already is, so re-sending it would test nothing.
    _move_pointer(window, QPoint(point.x() + 2, point.y()))
    pump(DWELL_MS)

    assert _shown(window)
    assert item(window, "dictionaryWord").property("text") == _ENGLISH


def test_switching_the_cards_off_takes_the_card_away_and_remembers_it(
    shell, build_epub, tmp_path: Path
) -> None:
    """A switch that left a card on screen would be a lie; one forgotten, a nuisance.

    Both claims are one rule - the switch is the whole of the feature's state (FR-095) -
    so they are asserted together: switching off clears what is up and stops the dwell,
    and the next session starts off, because this is a reading preference and not a mode
    of the current page.
    """
    _engine, window, controller = shell
    assert controller.openBook(str(_book(build_epub, _ENGLISH, "english.epub")))
    pump(240)

    point = _word_point(window, controller)
    _move_pointer(window, point)
    pump(DWELL_MS)
    assert _shown(window)

    controller.setDictionaryEnabled(False)
    pump(40)
    assert not _shown(window)
    assert not controller.dictionaryEnabled

    _move_pointer(window, point)
    pump(DWELL_MS)
    assert not _shown(window)               # the page stays quiet until it is switched on

    # ... and tomorrow's session starts with the switch where this one left it.
    reopened = ReaderController(store=SettingsStore(tmp_path / "config.json"))
    assert not reopened.dictionaryEnabled


def test_a_chinese_word_raises_nothing(shell, build_epub) -> None:
    """The bundled list is English: pointing at 汉字 must not open an empty card (FR-097)."""
    _engine, window, controller = shell
    assert controller.openBook(str(_book(build_epub, _CHINESE, "chinese.epub")))
    pump(240)

    _move_pointer(window, _word_point(window, controller))
    pump(DWELL_MS)

    assert not _shown(window)


def test_scrolling_takes_the_card_down(shell, build_epub) -> None:
    """The card belongs to a place on the page; scrolling moves that place away (FR-095)."""
    _engine, window, controller = shell
    assert controller.openBook(str(_long_book(build_epub)))
    pump(240)
    assert controller.scrollMax > 0.0

    _move_pointer(window, _word_point(window, controller))
    pump(DWELL_MS)
    assert _shown(window)

    controller.scrollDown()
    pump(40)

    assert not _shown(window)
