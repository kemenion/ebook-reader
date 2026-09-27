"""The table of contents as a permanent fixture: it stays, and it has a button.

Two failures are behind this module.  The panel used to close itself as soon as a
chapter was clicked - a rule inherited from the days it floated over the text
(FR-015), and left standing after it took a column of its own, so a reader following
the contents had to reopen it at every hop.  And the only way to open it was the `T`
key, which is documented inside the settings drawer, which is opened with a key: a
reader who does not know the shortcut cannot find the map at all (FR-013).

So the panel now opens with the book and stays until it is closed on purpose, and
the one control that is always on screen - the button at the bottom left - toggles it
with a single click.  The harness is the shared one; the tests click the real item
through Qt's own event delivery, because a control that is drawn but does not react
is exactly the failure a screenshot cannot tell apart from success.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtQuick import QQuickItem

from conftest import RESIZE_DEBOUNCE_MS, centre, click, item, pump, visible


def toggle_button(window) -> QQuickItem:
    return item(window, "tocToggle")


def _rows(listing: QQuickItem) -> list[QQuickItem]:
    """The chapter rows the panel is currently showing, top to bottom.

    Walked through the *visual* tree, because a view's delegates are not in the
    QObject tree the way the panels' items are; found by the property only a row
    has, rather than read off the QML source, so a row that is declared but never
    instantiated fails the test.
    """
    found: list[QQuickItem] = []
    pending = list(listing.childItems())
    while pending:
        child = pending.pop()
        if child.property("highlighted") is not None:
            found.append(child)
        pending.extend(child.childItems())
    return sorted(found, key=lambda child: child.y())


# ---------------------------------------------------------------------- the button


def test_the_button_shows_and_hides_the_panel_with_the_mouse(
    shell, kangpo_path: Path, warnings
) -> None:
    """One click each way, no key to remember (FR-013)."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    toggle = toggle_button(window)
    assert toggle.property("text") == "目录"
    assert toggle.property("hint") == "T"             # the shortcut is on the button
    assert bool(toggle.property("checked"))           # the book brought the panel up
    assert visible(window, "tocPanel")

    click(window, centre(toggle))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert not controller.tocVisible
    assert not visible(window, "tocPanel")
    assert not bool(toggle.property("checked"))
    # The width goes back to the text, exactly as it does for the `T` key, and the
    # page box follows the column rather than the window.
    assert window.property("openColumns") == 0
    assert window.property("pageColumnWidth") == window.property("width")
    assert controller._view_size.width() == window.property("pageColumnWidth")

    click(window, centre(toggle))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.tocVisible
    assert visible(window, "tocPanel")
    assert bool(toggle.property("checked"))
    assert window.property("openColumns") == 1
    assert controller._view_size.width() == window.property("pageColumnWidth")
    assert warnings == []


def test_the_button_takes_no_space_from_the_page(shell, kangpo_path: Path) -> None:
    """It lives in the status bar, which is outside the page and its click zones."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    toggle = toggle_button(window)
    page = item(window, "pageArea")
    page_bottom = page.mapToScene(QPointF(0, page.height())).y()
    assert toggle.mapToScene(QPointF(0, 0)).y() >= page_bottom

    # And clicking it is not a scroll or a chapter change: the click zones are inside
    # the page column, where the button is not.
    before = (controller.sectionIndex, controller.scrollOffset)
    click(window, centre(toggle))
    pump(200)
    assert (controller.sectionIndex, controller.scrollOffset) == before
    assert not controller.tocVisible          # it did what it says, and nothing else


def test_the_button_is_greyed_out_when_there_is_nothing_to_show(
    shell, kangpo_path: Path, warnings
) -> None:
    """No book - or a book without a table of contents - means nothing to offer."""
    _, window, controller = shell
    toggle = toggle_button(window)
    assert not bool(toggle.property("enabled"))

    # A greyed button swallows the click: nothing opens, nothing falls through.
    click(window, centre(toggle))
    pump(200)
    assert not controller.tocVisible
    assert not visible(window, "tocPanel")

    assert controller.openBook(str(kangpo_path))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.tocAvailable
    assert bool(toggle.property("enabled"))

    controller.closeBook()
    pump(200)
    assert not bool(toggle.property("enabled"))
    assert not visible(window, "tocPanel")
    assert warnings == []


# -------------------------------------------------------------------- the panel


def test_clicking_a_chapter_leaves_the_map_on_screen(
    shell, kangpo_path: Path, warnings
) -> None:
    """A map, not a one-shot dialog: the panel survives the jump it was used for."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert visible(window, "tocPanel")

    listing = item(window, "tocList")
    rows = _rows(listing)
    assert len(rows) > 2, "the panel is showing too few rows to click a later one"
    # The book opens on its cover, which is not a chapter: nothing in the panel is
    # highlighted yet, and that is what makes the assertion after the click mean
    # something.
    launched = (controller.sectionIndex, controller.scrollOffset)
    before_row = controller.currentTocRow

    # A chapter well past the first, so the jump is one the test can see.
    click(window, centre(rows[len(rows) // 2]))
    pump(RESIZE_DEBOUNCE_MS + 100)

    assert visible(window, "tocPanel")        # still there: nothing put it away
    assert controller.tocVisible
    assert (controller.sectionIndex, controller.scrollOffset) != launched
    assert controller.currentTocRow != before_row and controller.currentTocRow >= 0
    # Read again: the view rebuilt its rows for the new place in the book, and the
    # row the reader landed on is the one it marks as current.
    assert any(child.property("highlighted") for child in _rows(listing))
    assert warnings == []


def test_only_closing_it_on_purpose_takes_the_panel_away(
    controller, kangpo_path: Path
) -> None:
    """Every other operation leaves it standing; `T` and `Esc` are the ways out."""
    assert controller.openBook(str(kangpo_path))
    assert controller.tocVisible

    controller.goToTocRow(len(controller.tocItems) - 1)      # a chapter, the last one
    assert controller.tocVisible
    assert controller.currentTocRow == len(controller.tocItems) - 1

    controller.scrollDown()                                  # ordinary scrolling
    controller.previousSection()
    controller.toggleOutline()                               # even the right-hand panel
    assert controller.tocVisible

    controller.toggleToc()
    assert not controller.tocVisible

    # And `Esc` - every panel at once - takes it away too.
    controller.toggleToc()
    controller.closePanels()
    assert not controller.tocVisible
