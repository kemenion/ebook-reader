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


def test_a_row_that_names_an_anchor_lands_on_it(
    shell, binan_path: Path, warnings
) -> None:
    """Two rows may share a document and still lead to different places (FR-014).

    币安's contents points 幣安上線 and 早年歲月 at the same file, the second at an
    anchor well inside it.  The anchor was dropped on both parsing paths, so the
    second row landed on the top of the file - exactly where the first one had just
    been - and the panel marked the first row as the reader's place even after they
    had asked for the second.

    The pair is found by name rather than by index, because the list also carries the
    rows the book leaves unnamed (FR-012) and those come first.
    """
    _, window, controller = shell
    assert controller.openBook(str(binan_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    listing = item(window, "tocList")
    rows = _rows(listing)
    titles = [entry["title"] for entry in controller.tocItems]
    at = titles.index("幣安上線，2017年7月14日12點")
    assert titles[at + 1] == "早年歲月", "the anchored row sits right below its sibling"
    assert len(rows) > at + 1, "the pair has to be on screen to be clicked"

    click(window, centre(rows[at]))                     # the first row of the file
    pump(RESIZE_DEBOUNCE_MS + 100)
    section = controller.sectionIndex
    assert controller.scrollOffset == 0.0               # a row with no anchor starts here
    assert controller.currentTocRow == at

    # Read again: the jump rebuilt the rows, and the old delegates went with it.
    rows = _rows(listing)
    click(window, centre(rows[at + 1]))                 # the anchored row, same file
    pump(RESIZE_DEBOUNCE_MS + 100)

    assert controller.sectionIndex == section            # same document ...
    assert controller.scrollOffset > 0.0                 # ... and inside it, not at its top
    assert controller._anchor_block() > 0
    assert controller.currentTocRow == at + 1            # and the panel says so
    assert warnings == []


def test_the_panel_lists_the_chapters_of_an_omnibus_that_names_three_volumes(
    shell, linqi_path: Path, warnings
) -> None:
    """135 sections, a three-row NCX: the map still shows every chapter (FR-012).

    This is the book that made the gap visible: its contents names its three volumes,
    `]` walks 135 sections, and the panel showed three rows - so the reader could move
    through the book but could not see where they were going or jump anywhere.  The
    sections the book does not name now name themselves, in reading order, which is
    what makes the panel show the same list the keys walk through.
    """
    _, window, controller = shell
    assert controller.openBook(str(linqi_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    titles = [entry["title"] for entry in controller.tocItems]
    assert len(titles) >= 130, "135 sections, less the handful that name nothing"
    assert "战胜华尔街Beating the Street（珍藏版）" in titles      # the book's own row
    assert "第1章 业余投资者比专业投资者业绩更好" in titles          # a chapter's own name

    # Every row leads somewhere different: the panel highlights by position, so two
    # rows answering with the same place would make one of them unreachable.
    targets = [entry["section"] for entry in controller.tocItems]
    assert targets == sorted(targets), "the rows are in reading order"
    assert len(set(targets)) == len(targets)

    # A real click on a row the book never named lands in that chapter.
    rows = _rows(item(window, "tocList"))
    click(window, centre(rows[0]))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.sectionIndex == targets[0]
    assert controller.currentTocRow == 0

    # And a chapter past the visible rows is reachable by its own row.
    at = titles.index("后记 马里兰之旅的感悟")
    controller.goToTocRow(at)
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.sectionIndex == targets[at] == 134
    assert controller.currentTocRow == at
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
