"""The pointer on the page: marking a passage, and moving the text with the hand.

Two requirements meet in this module.  *FR-070* is the passage: holding Shift and
dragging marks the characters between the two ends, letting go copies them, and the
bands the mark is drawn in stay on their own lines while the window slides under them.
*FR-079* is the pan: a plain left drag takes hold of the page and moves it with the
pointer, measured from where the drag started.

The tests come in three kinds, in three sections below:

* **where a point falls, and where a band is drawn** - asked of Qt's own text engine, on
  documents laid out here rather than on a book.  This is the layer the reader actually
  points at, and its rules are not visible from the outside: which glyph is under a
  point, that a point past the edge of the column still belongs to the line it is beside,
  what a Chinese "word" is, that an indent and an empty line move and widen the bands.
  A book cannot show any of that - a book only shows the one passage the test happened
  to drag over.
* **what ``PageItem`` draws** - the one place a band's coordinates are scaled, checked on
  an item that has been letterboxed, because ``paint()`` is never called in an offscreen
  test and what it drew cannot be read back out of a window.
* **the gestures through the shell** - real presses, moves and releases, because "the
  event reaches the item that is supposed to get it" is a different claim from "the
  controller marks a passage".

The *look* of the wash is not tested here: that is a palette question, held to its two
contrast floors in ``tests/unit/test_theme_colors.py`` (FR-090).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRectF, QSizeF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QPainter,
    QTextBlockFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtQuick import QQuickItem

from conftest import (
    RESIZE_DEBOUNCE_MS,
    at,
    click,
    double_click,
    drag,
    item,
    open_long_section,
    press,
    pump,
    wheel,
)
from ebook_reader.app.page_item import PageItem
from ebook_reader.domain.html.kinsoku import WORD_JOINER
from ebook_reader.typeset.selection import (
    position_at,
    selected_text,
    selection_rects,
    word_range,
)


#: Float arithmetic over layout pixels: a whole pixel is far below what a reader could
#: see and far above the noise in the sums (the same figure the other geometry test uses).
TOLERANCE = 1.0

#: The type the sample documents below are laid out in.  A size is set rather than left to
#: Qt's default, because the wrapping the tests rely on has to be the same everywhere.
_FONT_POINTS = 14

#: A paragraph that must wrap in a column this narrow, whatever font the machine has.
#: The tests read the line breaks off the layout rather than predicting them, so this only
#: has to be long enough that there is more than one line - and there is a way to tell if
#: it is not: the first test asserts it.
_LONG = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi"


def _document(text: str, *, width: float = 300.0) -> QTextDocument:
    """*text* laid out as the reader's own section is: by Qt, in a column of *width*.

    The document's own margin is dropped so that a band's y is the line's own y and the
    arithmetic in the assertions can be followed by eye; the reader's sections get their
    margin back from the controller, which is where the page's margins are decided.
    Newlines in *text* are paragraph breaks, exactly as they are in a section.
    """
    document = QTextDocument()
    document.setDocumentMargin(0.0)
    font = document.defaultFont()
    font.setPointSizeF(_FONT_POINTS)
    document.setDefaultFont(font)
    document.setPageSize(QSizeF(width, 4000.0))
    document.setPlainText(text)
    return document


def _block(document: QTextDocument):
    """The first block, with the document laid out first.

    Qt builds each block's ``QTextLayout`` when the document's own layout is asked for
    something, and ``QTextBlock.layout()`` is null until then - touching it is a crash
    rather than a ``None``.  The engine's real callers reach a line the same way, through
    ``documentLayout()``, so these documents are measured in the state the reader's are in.
    """
    document.documentLayout().blockBoundingRect(document.firstBlock())
    return document.firstBlock()


def _lines(document: QTextDocument) -> int:
    """How many layout lines the first block has - the frame the sample was laid out in."""
    layout = _block(document).layout()
    return layout.lineCount() if layout is not None else 0


def _line(document: QTextDocument, index: int):
    """Line *index* of the first block; the samples below are one paragraph each."""
    return _block(document).layout().lineAt(index)


def _middle(line) -> float:
    """A y inside *line*, halfway down its type box."""
    return line.y() + line.height() / 2.0


def _glyph_middle(line, index: int) -> float:
    """An x inside glyph *index* of *line*: between its two edges, so neither owns it."""
    return (line.cursorToX(index)[0] + line.cursorToX(index + 1)[0]) / 2.0


# --------------------------------------------------------- where a point falls


def test_a_point_on_a_glyph_is_that_glyph(qapp) -> None:
    """The character the reader pointed at, not the one nearest a round number.

    Every glyph of a line is asked for, because the ones a guess gets wrong are exactly
    the ones a reader notices: a space between words, the first glyph after an indent,
    and the last glyph of a line, whose own width has nothing to do with the rest.
    """
    document = _document(_LONG)
    line = _line(document, 0)
    assert line.textLength() > 8, "the sample has to wrap for this test to mean anything"

    for index in range(line.textLength()):
        found = position_at(document, _glyph_middle(line, index), _middle(line))
        assert found == line.textStart() + index, f"glyph {index} of the line"


def test_a_point_in_the_margin_lands_on_the_nearest_character(qapp) -> None:
    """Off the text is not off the page: the margins still answer with a character (FR-070).

    This is what lets a marking drag start a little outside the column - on the paper
    beside a line rather than on a glyph - and still mark the line the reader meant.
    """
    document = _document(_LONG)
    line = _line(document, 0)

    assert position_at(document, -40.0, _middle(line)) == line.textStart()
    # And below the last line: the nearest character is the last one in the section.
    last = _line(document, _lines(document) - 1)
    below = position_at(document, 0.0, 4000.0)
    assert last.textStart() <= below <= last.textStart() + last.textLength()


def test_a_point_past_the_column_stays_on_the_line_it_is_beside(qapp) -> None:
    """A drag may leave the column without leaving the line (FR-070).

    ``hitTest`` is fuzzy: a point far to the right of a line answers with a character
    *of that line* rather than with nothing or with the start of the section.  That is
    what keeps a selection dragged off the right-hand edge growing along the line the
    reader left from, instead of collapsing to the first character of the chapter.
    """
    document = _document(_LONG)

    for index in range(_lines(document)):
        line = _line(document, index)
        found = position_at(document, 10000.0, _middle(line))
        assert line.textStart() <= found <= line.textStart() + line.textLength(), (
            f"line {index} answered with {found}"
        )


# ----------------------------------------------------------- where a band is drawn


def test_a_passage_on_one_line_is_one_band_over_its_glyphs(qapp) -> None:
    """A band starts at the first selected glyph and stops after the last (FR-070).

    Measured by the engine's own cursor positions rather than by counting characters: a
    proportional font, a line stretched by justification and a line kinsoku pushed a
    punctuation mark out of all give different widths per glyph, and a band computed
    from an average would sit beside the glyphs it is supposed to cover.
    """
    document = _document(_LONG)
    line = _line(document, 0)
    first, last = line.textStart() + 2, line.textStart() + 7

    bands = selection_rects(document, first, last)

    assert len(bands) == 1
    band = bands[0]
    assert band.left() == pytest.approx(line.cursorToX(first)[0], abs=TOLERANCE)
    assert band.right() == pytest.approx(line.cursorToX(last)[0], abs=TOLERANCE)
    assert band.top() == pytest.approx(line.y(), abs=TOLERANCE)
    assert band.height() >= line.height() - TOLERANCE


def test_a_passage_that_runs_on_fills_the_line_it_leaves(qapp) -> None:
    """A line the passage continues past is marked to its own right edge (FR-070).

    Stopping at the last glyph would leave a ragged gap down the right-hand side of a
    multi-line passage that reads as the end of the selection; the band of the *last*
    line stops at its text, because what is below it belongs to the paragraph after.
    """
    document = _document(_LONG)
    first, second = _line(document, 0), _line(document, 1)
    assert _lines(document) > 1, "the sample has to wrap for this test to mean anything"

    bands = selection_rects(document, first.textStart() + 3, second.textStart() + 4)

    assert len(bands) == 2
    assert bands[0].right() == pytest.approx(first.width(), abs=TOLERANCE)
    assert bands[1].left() == pytest.approx(0.0, abs=TOLERANCE)
    # The two meet: the leading between the lines is part of the passage, so the reader
    # sees one shape rather than a stripe per line.
    assert bands[1].top() <= bands[0].bottom() + TOLERANCE


def test_a_band_keeps_off_the_indent_of_a_first_line(qapp) -> None:
    """The reader's paragraph indent (FR-032) is paper, and the band starts after it.

    The band is measured where the line was drawn, not from the column's left edge: the
    indent is not text and a click on it selects nothing, so a band covering it would be
    a highlight of the margin.
    """
    document = _document(_LONG)
    cursor = QTextCursor(document)
    cursor.select(QTextCursor.SelectionType.Document)
    indent = QTextBlockFormat()
    indent.setTextIndent(48.0)
    cursor.mergeBlockFormat(indent)

    first, second = _line(document, 0), _line(document, 1)
    assert first.cursorToX(first.textStart())[0] == pytest.approx(48.0, abs=TOLERANCE)

    bands = selection_rects(document, 0, second.textStart() + 3)

    assert bands[0].left() == pytest.approx(48.0, abs=TOLERANCE)
    assert bands[1].left() == pytest.approx(0.0, abs=TOLERANCE)


def test_a_band_over_an_empty_line_runs_the_width_of_the_column(qapp) -> None:
    """A blank line inside a passage is part of it (FR-070).

    An empty line has no glyph to measure, so its band is the width of the column - the
    bar Qt's own text views draw for a selected empty line.  Without it, a passage across
    a paragraph break would look like two passages.
    """
    text = "abc\n\ndef"
    document = _document(text)

    bands = selection_rects(document, 0, len(text))

    assert len(bands) == 3
    assert bands[1].left() == pytest.approx(0.0, abs=TOLERANCE)
    assert bands[1].width() == pytest.approx(document.pageSize().width(), abs=TOLERANCE)


def test_a_passage_facing_backwards_is_the_same_passage(qapp) -> None:
    """A drag upwards is the same gesture: the two ends are sorted, not assumed (FR-070).

    Both halves of the module do it - the bands here and the text below - because the end
    being dragged may pass the anchor, and what is marked must not depend on which way
    the reader's hand went.
    """
    document = _document(_LONG)
    first, last = _line(document, 0).textStart() + 2, _line(document, 2).textStart() + 3

    forwards = selection_rects(document, first, last)
    backwards = selection_rects(document, last, first)

    assert forwards == backwards
    assert not selection_rects(document, first, first)   # nothing selected, no bands


def test_the_passage_comes_back_as_text_other_applications_understand(qapp) -> None:
    """What leaves the reader: real newlines, no paragraph marks, no layout artefacts.

    Two translations are made for the world outside (FR-070).  Paragraph breaks arrive
    from Qt as ``U+2029``, which other applications show as a box, so they become
    newlines; and the word joiner the typesetter injects to keep punctuation off line
    starts is not part of the book's text at all (FR-109) - a passage pasted into a mail
    must read as the book reads.
    """
    text = f"第一章{WORD_JOINER}正文开始\n后面还有一段"
    document = _document(text)

    whole = selected_text(document, 0, len(text))

    assert whole == "第一章正文开始\n后面还有一段"
    assert "\u2029" not in whole and WORD_JOINER not in whole
    assert selected_text(document, 4, 4) == ""       # a position is not a passage


# ------------------------------------------------------- the word a click takes


def _qt_word(document: QTextDocument, position: int) -> tuple[int, int]:
    """What ``WordUnderCursor`` alone would mark - the answer the Han rule may not touch."""
    cursor = QTextCursor(document)
    cursor.setPosition(position)
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    return cursor.selectionStart(), cursor.selectionEnd()


def test_a_double_click_takes_the_word_under_it(qapp) -> None:
    """One whole word, whichever letter of it the reader pointed at (FR-070).

    And a position *between* two words belongs to the one before it, which is Qt's rule
    and the one the hand expects: a reader aiming at a word's end is far more likely than
    one aiming at the next word's start.
    """
    document = _document("hello world again")

    assert selected_text(document, *word_range(document, 6)) == "world"
    assert selected_text(document, *word_range(document, 9)) == "world"
    assert selected_text(document, *word_range(document, 12)) == "again"
    assert selected_text(document, *word_range(document, 5)) == "hello"


def test_a_latin_run_is_left_exactly_as_qt_gives_it(qapp) -> None:
    """The narrowing that Han script needs must not reach Latin text (FR-070).

    Asserted at *every* position of the sample rather than on one word: a rule that cut
    runs at punctuation would cut "don't" and "well-known" in half, and there is nothing
    to repair there - so the whole of the Latin alphabet is left to the engine that knows
    it, and this test is what says so.
    """
    text = "don't stop - well-known 3.14"
    document = _document(text)

    for position in range(len(text)):
        assert word_range(document, position) == _qt_word(document, position), (
            f"position {position} ({text[position]!r})"
        )


def test_a_chinese_double_click_takes_the_clause_not_the_paragraph(qapp) -> None:
    """Over Han script Qt's word is the paragraph, and the reader gets the clause (FR-070).

    ``WordUnderCursor`` answers with everything up to the next space or Latin word, and a
    Chinese paragraph has no spaces in it - so on the reference book, whose paragraphs run
    to hundreds of characters, a double click would put a whole paragraph on the clipboard
    from a menu row that says 复制.  The clause between the punctuation marks around the
    pointer is what a reader means by "one thing out of the page"; the last two lines here
    are Qt's own answer, kept as the evidence for *why* the rule exists.
    """
    text = "第一章正文开始，第二章结束。第三章的另一段话。"
    document = _document(text)

    assert selected_text(document, *word_range(document, 3)) == "第一章正文开始"
    assert selected_text(document, *word_range(document, 9)) == "第二章结束"
    assert selected_text(document, *word_range(document, 16)) == "第三章的另一段话"

    start, end = _qt_word(document, 3)
    assert len(selected_text(document, start, end)) == len(text), "Qt marks the paragraph"


def test_pointing_at_a_mark_takes_the_clause_it_closes(qapp) -> None:
    """A click on the full stop means the sentence before it, not the one after (FR-070).

    The two marks a reader aims at are the ones at the ends of clauses; the second of them
    has no letters after it to extend into, so the rule looks *back* first and only then
    forward.
    """
    text = "第一章正文开始，第二章结束。"
    document = _document(text)

    assert selected_text(document, *word_range(document, 7)) == "第一章正文开始"    # the comma
    assert selected_text(document, *word_range(document, 13)) == "第二章结束"       # the full stop


def test_letters_and_digits_inside_a_clause_belong_to_it(qapp) -> None:
    """A clause is a run of letters and digits, so a number in a sentence stays whole (FR-070).

    ``isalnum()`` is the whole rule - letters and digits in every script - which is why
    「7 月」 is one thing to click.  Its one edge is a decimal point, which the rule cuts:
    3.14 is two runs.  That is the price of a rule the reader can predict, and it is here
    as a decision rather than as a surprise for the next reader of this file.
    """
    text = "数字3.14与3月。"
    document = _document(text)

    assert selected_text(document, *word_range(document, 0)) == "数字3"
    assert selected_text(document, *word_range(document, 7)) == "14与3月"


# ---------------------------------------------------------- what the item draws

#: The item under test: a 200x300 page inside a 200x400 image - the extra strip is the
#: rendering quantum the page pans through (ADR-016) - with one band on it.
_PAGE = QSizeF(200.0, 300.0)
_BAND = QRectF(20.0, 50.0, 60.0, 20.0)
_PAPER = QColor("#ffffff")
_WASH = QColor(200, 20, 20, 255)


def _page_item() -> PageItem:
    """A ``PageItem`` given a page, an image, one band and a wash, as QML would give it."""
    item = PageItem()
    image = QImage(200, 400, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(_PAPER)
    item.setProperty("pageSize", _PAGE)
    item.setProperty("image", image)
    item.setProperty("background", _PAPER)
    item.setProperty("selectionRects", [_BAND])
    item.setProperty("selectionColor", _WASH)
    return item


def _painted(item: PageItem, size: QSizeF) -> QImage:
    """The item painted into an image of *size* - the call the scene graph would make."""
    target = QImage(
        int(size.width()), int(size.height()), QImage.Format.Format_ARGB32_Premultiplied
    )
    target.fill(_PAPER)
    item.setSize(size)
    painter = QPainter(target)
    item.paint(painter)
    painter.end()
    return target


def _washed(image: QImage, x: int, y: int) -> bool:
    """Whether the pixel at (*x*, *y*) is the wash rather than the page."""
    return QColor(image.pixelColor(x, y)).name() == _WASH.name()


def test_the_wash_is_drawn_where_the_band_is_and_scales_with_the_page(qapp) -> None:
    """A band is mapped through the same transform as the image, so it stays on its line.

    Three sizes, one page (200x300 in a 200x400 image - the extra strip is the rendering
    quantum the page pans through, ADR-016), sampled pixel by pixel:

    * at the page's own size the wash covers exactly the pixels the controller named, and
      no others: the four edges of the band are checked, one pixel outside each;
    * at twice that - a window the layout has not caught up with, so the item scales the
      page up - it covers twice as much, because it is the same place *on the page*;
    * in a window wider than the page, where the item letterboxes it, it appears offset by
      the letterbox - a band mapped without that origin would be drawn beside its text.

    Nothing else can see this: the bands arrive in page coordinates and are mapped inside
    ``paint()``, which an offscreen window never calls, and whose output could not be read
    back out of a window if it did.
    """
    page = _page_item()

    once = _painted(page, QSizeF(200.0, 300.0))
    assert _washed(once, 25, 55), "the band's own pixels are washed"
    assert _washed(once, 79, 55), "right up to its last pixel column"
    assert not _washed(once, 19, 55), "and not one pixel to the left of it"
    assert not _washed(once, 85, 55), "nor to the right"
    assert not _washed(once, 25, 45), "nor above: the band's top edge holds"
    assert not _washed(once, 25, 75), "nor below its last row"

    twice = _painted(page, QSizeF(400.0, 600.0))
    assert _washed(twice, 45, 105), "the same band, twice as far out"
    assert _washed(twice, 159, 105)
    assert not _washed(twice, 39, 105)
    assert not _washed(twice, 165, 105)
    assert not _washed(twice, 45, 95)

    wider = _painted(page, QSizeF(400.0, 300.0))
    assert _washed(wider, 125, 55), "the page is centred, and the band went with it"
    assert _washed(wider, 179, 55)
    assert not _washed(wider, 119, 55)
    assert not _washed(wider, 185, 55)
    assert not _washed(wider, 25, 55), "the band is not drawn where the page is not"


# ---------------------------------------------------------------- moving the page


def _page(window) -> QQuickItem:
    """The item the page is drawn on: its own coordinates *are* the page's.

    The item fills the reading view, which fills the page column, so a point taken with
    ``conftest.at()`` on it is a point in the space the controller's ``selectionRects``
    live in - which is what lets a test say where a band should be drawn.
    """
    return item(window, "pageView")


def _drag_through(window, points: list[tuple[int, int]], modifier=Qt.NoModifier) -> None:
    """One press, a move to each point after the first, one release (FR-079).

    ``conftest.drag()`` walks a straight line, which is all a two-point gesture needs.
    The gestures here turn around inside themselves, so the points are given one at a time
    and in order.
    """
    from PySide6.QtTest import QTest

    QTest.mousePress(window, Qt.LeftButton, modifier, QPoint(*points[0]))
    for point in points[1:]:
        QTest.mouseMove(window, QPoint(*point))
        pump(20)
    QTest.mouseRelease(window, Qt.LeftButton, modifier, QPoint(*points[-1]))
    pump(RESIZE_DEBOUNCE_MS + 40)


def test_a_drag_moves_the_text_with_the_pointer(shell, kangpo_path: Path) -> None:
    """A plain left drag takes hold of the page, and the text follows the pointer (FR-079).

    Dragging *up* pulls the text up, which moves the window *down* the section: the
    direction every map and every touch screen uses, and the opposite of the sign a scroll
    offset invites.  Nothing is marked and nothing is copied - this is the gesture the
    reader uses constantly, so it may not leave anything behind, least of all on the
    clipboard they are carrying something else in.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("别的东西")

    start = at(page, int(page.width() / 2), 600)
    drag(window, start, (start[0], start[1] - 200))

    assert controller.scrollOffset == pytest.approx(200.0, abs=TOLERANCE)
    assert not controller.hasSelection
    assert clipboard.text() == "别的东西"


def test_a_drag_is_measured_from_where_it_started(shell, kangpo_path: Path) -> None:
    """The page follows the pointer's *distance*, not the sum of its moves (FR-079).

    At the top of a section the page cannot move up, so a gesture that added up deltas
    would lose that distance for good: pushing 300 px past the top and then pulling back to
    100 px past it would leave the text where it started rather than 100 px along, and the
    reader would have to keep pulling before the page answered.  Measured from the anchor,
    the text is where the pointer is - the first place it can be.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    origin = at(page, int(page.width() / 2), 400)

    # Down 300 px (the section is already at its top, so nothing moves), then up 400 px.
    _drag_through(
        window, [origin, (origin[0], origin[1] + 300), (origin[0], origin[1] - 100)]
    )

    assert controller.scrollOffset == pytest.approx(100.0, abs=TOLERANCE)


# ------------------------------------------------------------- marking a passage


def _bands(value) -> list[QRectF]:
    """The bands as rectangles: QML hands back whatever the property was given."""
    return [QRectF(band) for band in (value or [])]


def _on_a_line(controller, x: int, y: int) -> int:
    """A y that is *inside* a line of text near *y*: the middle of the word found there.

    A raw pixel count is not a point *on* the text: a paragraph break leaves a few pixels
    of space between two lines, and Qt answers a point in that gap with the nearest
    character - which can be a whole line away, and would make this test's geometry depend
    on how the section happens to be broken up.  Asking the controller which word is under
    a point (what a double click asks) and taking the middle of that word's band gives a y
    in the same neighbourhood that is inside a line whatever the grid is.
    """
    controller.selectWordAt(x, y)
    band = _bands(controller.selectionRects)[0]
    controller.clearSelection()
    return int(band.center().y())


def _mark(
    window, page: QQuickItem, controller, *, down: int = 6, lines: int = 6
) -> tuple[int, int]:
    """A real Shift drag down the middle of the page; returns the two y it used.

    Both ends are put on a line of text (see :func:`_on_a_line`), and the release is
    *lines* lines below the press, so the passage is several lines of the column.
    """
    x = int(page.width() / 2)
    pressed = _on_a_line(controller, x, int(down * controller.lineStep))
    released = _on_a_line(controller, x, pressed + int(lines * controller.lineStep))
    drag(window, at(page, x, pressed), at(page, x, released), modifier=Qt.ShiftModifier)
    return pressed, released


def test_a_shift_drag_marks_the_text_between_its_ends(shell, kangpo_path: Path) -> None:
    """Shift turns the same gesture into a passage, and never pans (FR-070 / FR-079).

    The mark begins at the line the pointer pressed on and ends at the line it let go on -
    said in the bands' own page coordinates, which is the part a wrong mapping between the
    page and the document would get wrong.  And the text does not move while it is being
    marked: a Shift drag that scrolled would pull the passage out from under the pointer
    drawing it.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    pressed, released = _mark(window, page, controller)
    x = int(page.width() / 2)

    assert controller.hasSelection
    assert len(controller.selectedText) > 20, "several lines of prose"
    assert controller.scrollOffset == 0.0, "a marking drag is not a pan"

    bands = _bands(controller.selectionRects)
    assert len(bands) >= 3, "several lines were swept"
    assert bands[0].top() <= pressed <= bands[0].bottom()
    # `hitTest` answers with the nearest *boundary* between two characters, so the anchor's
    # own left edge sits within one glyph of the pointer rather than exactly under it.
    assert abs(bands[0].left() - x) <= controller.fontSize
    assert bands[-1].top() <= released <= bands[-1].bottom()
    assert bands[-1].bottom() > bands[0].top(), "and it faces downwards"


def test_letting_go_copies_the_passage_and_says_how_much(shell, kangpo_path: Path) -> None:
    """The copy happens on release, and the status bar reports it (FR-070).

    No second gesture: the reader has marked the passage they want, and there is nothing
    inside the reader to paste it into - asking for a key as well would be asking twice.
    The count is their check that the whole passage went, and it is the length of what is
    now on the clipboard.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)

    _mark(window, page, controller)

    text = controller.selectedText
    assert text
    assert QGuiApplication.clipboard().text() == text
    label = item(window, "messageLabel")
    assert label.property("text") == f"已复制 {len(text)} 字"
    assert label.property("visible")


def test_the_view_is_handed_the_bands_and_the_wash(shell, kangpo_path: Path) -> None:
    """What the item draws is what the controller says, in the page's coordinates (FR-070).

    Both halves of the contract, because either one failing is invisible: the bands are the
    controller's arithmetic (the item never guesses where a passage is), and the wash is
    the theme's colour *already carrying its alpha*, so the item never decides what a
    marked passage looks like - the palette keeps that decision and its floors (FR-090).
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)

    assert not page.property("selectionRects")
    assert page.property("selectionColor") == controller.selectionColor
    assert page.property("selectionColor").alpha() not in (0, 255)

    _mark(window, page, controller)

    assert _bands(page.property("selectionRects")) == _bands(controller.selectionRects)


def test_the_mark_stays_on_its_own_line_when_the_text_moves(shell, kangpo_path: Path) -> None:
    """The bands are in page coordinates, so scrolling moves them with the text (FR-070).

    The passage itself is characters, not pixels: it survives the window moving under it.
    A band kept in document coordinates would slide off its line the moment the reader
    scrolled, leaving a highlight over a paragraph they never chose - worse than none.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    _mark(window, page, controller)
    before = _bands(controller.selectionRects)
    text = controller.selectedText

    wheel(window, at(page, int(page.width() / 2), 700))
    scrolled = controller.scrollOffset
    assert scrolled > 0

    after = _bands(controller.selectionRects)

    assert len(after) == len(before)
    for was, now in zip(before, after):
        assert now.top() == pytest.approx(was.top() - scrolled, abs=TOLERANCE)
        assert now.left() == pytest.approx(was.left(), abs=TOLERANCE)
        assert now.height() == pytest.approx(was.height(), abs=TOLERANCE)
    assert controller.selectedText == text, "the same characters, not the same pixels"


# ----------------------------------------------------- one word, and putting it down


def test_a_shift_click_marks_the_word_under_the_pointer(shell, kangpo_path: Path) -> None:
    """A Shift press that never moves is a click on a word, and it copies it (FR-070).

    The same gesture as a Shift drag, which is why it needs no timer: the release is where
    a word is decided, and by then the hand has said whether it meant to sweep across the
    text or to point at one thing in it.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    on_a_line = _on_a_line(controller, int(page.width() / 2), int(6 * controller.lineStep))
    point = at(page, int(page.width() / 2), on_a_line)

    drag(window, point, point, modifier=Qt.ShiftModifier)

    word = controller.selectedText
    assert 1 <= len(word) <= 20 and "\n" not in word, word
    assert QGuiApplication.clipboard().text() == word
    assert controller.scrollOffset == 0.0


def test_a_double_click_takes_one_word(shell, kangpo_path: Path) -> None:
    """The shortest way to take one word out of a page (FR-070).

    What the reader sees is that the mark is on the line they clicked: a band whose y range
    contains the point they pointed at, which is what a wrong hit-test or a stale scroll
    offset would get wrong.  *Which* word is the controller's rule - the geometry tests
    above pin it - so this test is about the gesture reaching it at all.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    x = int(page.width() / 2)
    on_a_line = _on_a_line(controller, x, int(6 * controller.lineStep))

    double_click(window, at(page, x, on_a_line))

    word = controller.selectedText
    assert 1 <= len(word) <= 20 and "\n" not in word, word
    assert QGuiApplication.clipboard().text() == word
    bands = _bands(controller.selectionRects)
    assert bands
    assert any(band.top() <= on_a_line <= band.bottom() for band in bands), bands
    assert controller.scrollOffset == 0.0


def test_a_click_puts_the_mark_down_and_leaves_the_clipboard_alone(
    shell, kangpo_path: Path
) -> None:
    """A press and release in one place is a click, and a click dismisses the mark (FR-070).

    The reader is left with the passage on the clipboard and a clean page: putting the
    highlight down is not the same as undoing the copy, and what was copied stays copied.
    The page does not move either - a click is not a pan, which is the half of this that
    FR-079 owns.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    _mark(window, page, controller)
    text = controller.selectedText
    assert text

    click(window, at(page, int(page.width() / 2), int(6 * controller.lineStep)))

    assert not controller.hasSelection
    assert not controller.selectionRects
    assert QGuiApplication.clipboard().text() == text
    assert controller.scrollOffset == 0.0


def test_copying_nothing_leaves_the_clipboard_alone(shell, kangpo_path: Path) -> None:
    """`Ctrl+C` with nothing marked must not take away what the reader had (FR-070).

    The clipboard belongs to the whole session, not to this window: there is something in
    it from somewhere else, and a key that ran the *feature* rather than the *action* would
    wipe it out - which is the sort of loss nobody notices until they paste.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("别的东西")

    press(window, "Ctrl+C")

    assert clipboard.text() == "别的东西"
    assert "已复制" not in str(item(window, "messageLabel").property("text"))


def test_ctrl_c_puts_the_marked_passage_back(shell, kangpo_path: Path) -> None:
    """The passage is copied when the gesture ends; `Ctrl+C` puts it back (FR-070).

    Something else always takes the clipboard over eventually - the reader copies a link,
    a password, a paragraph from elsewhere - and the mark is still on the page, so the key
    is the way back to it without marking the passage a second time.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    _mark(window, page, controller)
    text = controller.selectedText
    clipboard = QGuiApplication.clipboard()
    clipboard.setText("别的东西")

    press(window, "Ctrl+C")

    assert clipboard.text() == text


def test_the_mark_belongs_to_the_chapter_it_was_made_in(shell, kangpo_path: Path) -> None:
    """Changing chapter drops the passage: it is two positions in *that* document (FR-070).

    The next chapter's characters sit at the same positions and are different characters,
    so keeping the mark would highlight an arbitrary stretch of a text the reader never
    pointed at.  What is already on the clipboard stays there - the copy happened when the
    marking gesture ended, and this is a different thing happening afterwards.
    """
    _, window, controller = shell
    index = open_long_section(controller, kangpo_path)
    if not controller.hasNextSection:
        pytest.skip("this test needs a chapter after the one it starts in")
    page = _page(window)
    _mark(window, page, controller)
    text = controller.selectedText
    assert text

    press(window, "]")

    assert controller.sectionIndex == index + 1
    assert not controller.hasSelection
    assert not controller.selectionRects
    assert QGuiApplication.clipboard().text() == text


def test_the_mark_goes_with_the_book_it_was_made_in(shell, kangpo_path: Path) -> None:
    """Closing the book drops the passage - state *and* announcement (FR-070).

    A closed book has no document to hold two character positions, so the mark goes: the
    state (`hasSelection`, which greys the menu's 复制 row) and the drawing, which QML only
    reads when ``selectionChanged`` arrives - without that the item would go on washing
    bands over a document that no longer exists.  The clipboard is not touched, as
    everywhere else here.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = _page(window)
    _mark(window, page, controller)
    text = controller.selectedText
    assert text

    controller.closeBook()

    assert not controller.hasSelection
    assert not controller.selectionRects
    assert not page.property("selectionRects"), "the item is still drawing a closed book"
    assert QGuiApplication.clipboard().text() == text
