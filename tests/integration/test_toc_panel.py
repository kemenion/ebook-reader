"""The table of contents as a permanent fixture: it stays, has a button, and holds its place.

Three failures are behind this module.  The panel used to close itself as soon as a
chapter was clicked - a rule inherited from the days it floated over the text
(FR-015), and left standing after it took a column of its own, so a reader following
the contents had to reopen it at every hop.  The only way to open it was the `T` key,
which is documented inside the settings drawer, which is opened with a key: a reader
who does not know the shortcut cannot find the map at all (FR-013).  And in a long
contents - the omnibus has 134 rows - clicking a chapter threw the list back to its own
first row, because the reader's place travelled *inside* the rows: every jump
republished them, and a view handed a new model starts over (缺陷 25).

So the panel now opens with the book and stays until it is closed on purpose, the one
control that is always on screen - the button at the bottom left - toggles it with a
single click, and the rows describe the book while the reader's place travels on a
signal of its own, so scrolling to a chapter keeps the map where the reader put it.
The harness is the shared one; the tests click the real item through Qt's own event
delivery, because a control that is drawn but does not react is exactly the failure a
screenshot cannot tell apart from success.
"""

from __future__ import annotations

import base64
from pathlib import Path

from PySide6.QtCore import QPointF
from PySide6.QtQuick import QQuickItem

from conftest import RESIZE_DEBOUNCE_MS, centre, click, item, press, pump, visible


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


def _on_screen_rows(listing: QQuickItem) -> list[QQuickItem]:
    """The rows a click can actually reach, top to bottom.

    After the list has been scrolled most of its delegates are outside the viewport,
    and the view clips them: a click aimed at one of those would land on whatever is
    drawn at that window position instead.  Scene coordinates are what tells the two
    apart - a delegate's own ``y`` is measured in the content, not on the screen.
    """
    top = listing.mapToScene(QPointF(0, 0)).y()
    bottom = top + listing.height()
    return [
        row
        for row in _rows(listing)
        if top <= row.mapToScene(QPointF(0, row.height() / 2)).y() <= bottom
    ]


def _label(row: QQuickItem) -> QQuickItem:
    """The text item a row draws its title with.

    Found by the two properties only it has - ``text`` and the ``leftPadding`` that
    carries the row's level - so a row drawn without its indentation fails the test
    rather than answering from the QML source.
    """
    pending = list(row.childItems())
    while pending:
        child = pending.pop(0)
        if child.property("leftPadding") is not None and child.property("text") is not None:
            return child
        pending.extend(child.childItems())
    raise AssertionError("the contents row draws no label")


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
    # The row the reader landed on is the one the panel marks - and saying so did not
    # rebuild the rows: the highlight travels on its own signal (缺陷 25).
    assert any(child.property("highlighted") for child in _rows(listing))
    assert warnings == []


def test_a_part_and_the_chapters_inside_it_are_drawn_as_a_tree(
    shell, kangpo_path: Path, warnings
) -> None:
    """The hierarchy a book's flat navigation leaves out is what the panel draws (FR-012).

    the EPUB 3 book's ``nav.xhtml`` is a single ``<ol>`` of 26 rows on one level, so 第一部分 and the
    eighteen numbered chapters after it were drawn as siblings: the map said the book was a
    list of 26 equal things, and the three parts it is actually made of were invisible
    (缺陷 26).  The rows carry the level their own headings were written at now, and each
    nested row is drawn one step in from its parent - which is the difference between
    knowing the level and showing it.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    levels = [entry["level"] for entry in controller.tocItems]
    assert levels.count(0) == 8, "the front matter, the three parts and the afterword"
    assert levels.count(1) == 18, "every numbered chapter"
    assert levels[:6] == [0, 0, 0, 0, 0, 1], "第一部分 at the top, 01 under it"

    listing = item(window, "tocList")
    rows = _on_screen_rows(listing)
    part = next(row for row in rows if row.property("rowLevel") == 0)
    chapter = next(row for row in rows if row.property("rowLevel") == 1)
    assert part.mapToScene(QPointF(0, 0)).y() < chapter.mapToScene(QPointF(0, 0)).y()
    assert _label(chapter).property("leftPadding") == _label(part).property("leftPadding") + 16
    assert warnings == []


def test_clicking_a_chapter_keeps_the_map_where_the_reader_scrolled_it(
    shell, linqi_path: Path, warnings
) -> None:
    """Finding a chapter in a long map and clicking it is not a reason to lose the scroll.

    the omnibus is the book that shows the failure (缺陷 25): 134 rows, about a third of
    them on screen at once.  The highlighted row used to travel *inside* the rows, so
    every jump handed the view a rebuilt model - and a view given a new model starts
    over at its first row.  A reader who scrolled to a chapter and clicked it was thrown
    back to the top of the map, which is precisely where they did not want to be.

    The highlight now travels on its own signal and the rows describe the book and
    nothing else, so a click leaves the model, and with it `contentY`, the delegates and
    the scroll bar, exactly where the reader left them.
    """
    _, window, controller = shell
    assert controller.openBook(str(linqi_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    listing = item(window, "tocList")
    reachable = listing.property("contentHeight") - listing.property("height")
    assert reachable > 500, "the map has to be scrollable for this test to mean anything"
    listing.setProperty("contentY", reachable / 2)
    pump(150)
    scrolled_to = listing.property("contentY")
    assert scrolled_to > 0

    rows = _on_screen_rows(listing)
    assert len(rows) > 2, "a click needs a row to land on where the reader scrolled to"
    clicked = rows[len(rows) // 2]
    launched = controller.sectionIndex

    click(window, centre(clicked))
    pump(RESIZE_DEBOUNCE_MS + 100)

    assert controller.sectionIndex != launched                 # the jump happened ...
    assert controller.currentTocRow >= 0
    assert listing.property("contentY") == scrolled_to         # ... and the map stayed put
    # The row the reader asked for is the one marked: on this book the rows are one per
    # section in reading order, so no earlier row can be the last one behind the place
    # the click landed on.
    assert clicked.property("highlighted")
    assert warnings == []


def test_moving_on_leaves_the_map_where_the_reader_left_it(
    shell, linqi_path: Path, warnings
) -> None:
    """The map follows the reader by being *reopened*, not by shifting under their hand.

    A chapter change announces the row it landed on, and that announcement used to
    arrive as a rebuilt list - so `]`, the next-chapter key, dragged the column back to
    its first row while the reader was reading (缺陷 25).  The row has a signal of its
    own now: the highlight moves, the map does not, and `revealCurrent()` - which runs
    when the column opens - is what centres the map on the reader's place.
    """
    _, window, controller = shell
    assert controller.openBook(str(linqi_path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    listing = item(window, "tocList")
    reachable = listing.property("contentHeight") - listing.property("height")
    assert reachable > 500
    listing.setProperty("contentY", reachable / 2)
    pump(150)
    scrolled_to = listing.property("contentY")
    before_row = controller.currentTocRow

    press(window, "]")                       # the next chapter, through the real key path
    pump(RESIZE_DEBOUNCE_MS + 100)

    assert controller.currentTocRow != before_row and controller.currentTocRow >= 0
    assert listing.property("contentY") == scrolled_to
    assert warnings == []


def test_a_row_that_names_an_anchor_lands_on_it(
    shell, binan_path: Path, warnings
) -> None:
    """Two rows may share a document and still lead to different places (FR-014).

    the EPUB 2 book's contents points 幣安上線 and 早年歲月 at the same file, the second at an
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

    # The same delegates, in the same places: a jump announces a new highlight and
    # republishes nothing (缺陷 25), so the pair below can still be clicked.
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

    # The rows filled in before the book's first name and its three volume rows are the top
    # level; each volume is followed by exactly the chapters inside it, and the next volume
    # starts the level again - a tree, not a list with an indent in it (ADR-020).
    assert [entry["level"] for entry in controller.tocItems] == (
        [0, 0, 0] + [1] * 31 + [0] + [1] * 66 + [0] + [1] * 32
    )

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


# ------------------------------ the page a chapter opens on (FR-016 / ADR-021 / 缺陷 27)

#: Two chapters, each a title picture in a document of its own followed by its text:
#: the shape a converted book has, and the one that made a row lead to a cover.
FRONTISPIECE_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章 标题图</text></navLabel>
      <content src="text/ch1.xhtml"/></navPoint>
    <navPoint id="n2"><navLabel><text>第二章 收尾</text></navLabel>
      <content src="text/ch2.xhtml"/></navPoint>
  </navMap>
</ncx>
"""

#: A 2x2 PNG, so that the frontispiece really is an image the renderer can decode.
ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAADklEQVR42mP4DwYMEAoA"
    "U7oL9YXEbhEAAAAASUVORK5CYII="
)


def test_clicking_the_row_of_a_title_picture_reads_the_chapter(
    shell, build_epub, warnings
) -> None:
    """A row that points at a page with nothing to read leads to the chapter (FR-016).

    This is the shape that made the panel say one thing and do another: the contents
    points at the chapter's title picture, the chapter itself is the next document, and
    read as one document per section the row led to a picture while the text sat one
    section away - reachable only by paging into it, while the line at the end of the
    picture announced the chapter *after* the one the reader asked for (缺陷 27).

    Now the row and the chapter are one section: what the reader clicks opens the
    chapter, the way on is at the end of it, and the panel lists exactly the rows it
    listed before - the documents that joined the section are the ones that had no row.
    """
    body = "".join(f"<p>第{i}句话写在这里。</p>" for i in range(140))
    path = build_epub(
        {
            "text/ch1.xhtml": "<html><body><h1><img src='title.png'/></h1></body></html>",
            "text/ch1txt.xhtml": f"<html><body>{body}</body></html>",
            "text/ch2.xhtml": "<html><body><p>第二章 收尾</p></body></html>",
        },
        resources={"toc.ncx": FRONTISPIECE_NCX, "text/title.png": ONE_PIXEL_PNG},
        manifest={"toc.ncx": ("application/x-dtbncx+xml", "ncx")},
    )
    _, window, controller = shell
    assert controller.openBook(str(path))
    pump(RESIZE_DEBOUNCE_MS + 100)

    assert [entry["title"] for entry in controller.tocItems] == ["第一章 标题图", "第二章 收尾"]
    assert controller.sectionCount == 2

    rows = _rows(item(window, "tocList"))
    click(window, centre(rows[0]))
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.sectionIndex == 0
    assert controller.sectionTitle == "第一章 标题图"

    # The picture is not the whole of it: the chapter is in the same column, so there is
    # text to read below it - and the chapter ends where the *chapter* ends, which is
    # where the line naming the next one appears.
    assert controller.contentHeight > 3 * controller.viewportHeight
    controller.scrollToSectionEnd()
    pump(RESIZE_DEBOUNCE_MS + 100)
    assert controller.atSectionEnd
    assert controller.nextSectionTitle == "第二章 收尾"
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
