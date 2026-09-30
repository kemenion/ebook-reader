"""What a passage looks like on the page: positions, words and the bands they cover.

A selection is two character positions in the section's document - positions rather
than pixels, because a passage outlives the window it was chosen in: change the type
size and the same characters are still selected, drawn somewhere else (FR-070).

Everything here is measured by Qt's own text engine rather than estimated from the
character count:

* where a point falls is ``QAbstractTextDocumentLayout.hitTest``, so the reader selects
  the glyph they pointed at;
* how wide a line is comes from ``QTextLine.cursorToX``, so an indented first line, a
  line stretched by justification and a line that kinsoku pushed a punctuation mark out
  of all report what was actually drawn (FR-109).

One rule is not Qt's, and it is the only one here that is about reading rather than
measuring: ``word_range``.  ``QTextCursor.WordUnderCursor`` knows Latin words and calls
everything between two spaces one word of whatever script it is - which in a Chinese
book, where a paragraph has no spaces in it, means a double click marks the *paragraph*.
So a run containing Han script is narrowed to the clause around the pointer, and a run
without any is left exactly as Qt gave it (FR-070).

Coordinates in and out of this module are the *document's*: the origin of the text
column, y growing downwards.  Putting them on the page - the content margin and the
scroll offset - is the controller's business, because the window on the document is.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QTextBlock, QTextCursor, QTextDocument

from ..domain.html.kinsoku import WORD_JOINER

__all__ = ["position_at", "selected_text", "selection_rects", "word_range"]

#: The ranges of the Unicode Han script the reader's books are written in: the unified
#: ideographs, their extension A, and the compatibility ideographs.  A *range* rather than
#: a table of characters, because it is a property of the script, not a list to maintain.
_HAN_RANGES = (
    ("\u3400", "\u4dbf"),
    ("\u4e00", "\u9fff"),
    ("\uf900", "\ufaff"),
)


def position_at(document: QTextDocument, x: float, y: float) -> int:
    """The character position nearest (*x*, *y*), in the document's own coordinates.

    A point in the margins - or anywhere off the text - lands on the nearest position
    inside the document rather than failing, which is what lets a drag leave the column
    and still extend the selection along the way it went.
    """
    layout = document.documentLayout()
    if layout is None:
        return 0
    hit = layout.hitTest(QPointF(x, y), Qt.HitTestAccuracy.FuzzyHit)
    return max(0, min(int(hit), max(0, document.characterCount() - 1)))


def word_range(document: QTextDocument, position: int) -> tuple[int, int]:
    """The word around *position*, as ``(start, end)`` - what a double click selects.

    Qt's own idea of a word, narrowed where it is too broad to read: a run of Latin
    letters is the word, apostrophes, hyphens and all, exactly as ``WordUnderCursor``
    gives it; a run containing Han script is the *clause* the position is in, because
    Qt's rule - everything up to the next space - makes one word of a whole Chinese
    paragraph (see :func:`_clause`).

    A position with nothing under it - between two words, in the margin - gives the run
    Qt would select there, which may be empty; the caller decides what an empty word
    means rather than this function having an opinion.
    """
    cursor = QTextCursor(document)
    cursor.setPosition(max(0, min(position, max(0, document.characterCount() - 1))))
    cursor.select(QTextCursor.SelectionType.WordUnderCursor)
    start, end = cursor.selectionStart(), cursor.selectionEnd()
    if start >= end:
        return start, end
    return _clause(document.toPlainText(), start, end, position)


def _clause(text: str, start: int, end: int, position: int) -> tuple[int, int]:
    """A run Qt called one word, narrowed to the clause *position* falls in.

    Only runs that contain Han script are touched.  Over Han, ``WordUnderCursor`` returns
    everything up to the next space or Latin word - and a Chinese paragraph has no spaces
    in it, so a double click on the reference book would put three hundred characters on
    the clipboard.  A reader double-clicks to take *one thing* out of a page, so the run
    is cut back to the neighbours that are letters or digits: the clause between the two
    punctuation marks around the pointer.

    A run without Han script is returned untouched.  It is not that Qt gets Latin wrong -
    it stops at the apostrophe in "don't" and at the hyphen in "well-known" all by itself -
    but that there is nothing to fix there, and a rule applied where it is not needed is a
    rule that can only do harm.  Nor is a *punctuation-only* run touched: pointing between
    two clauses and getting the dash back beats getting nothing.
    """
    run = text[start:end]
    if not any(_is_han(char) for char in run):
        return start, end
    if not any(char.isalnum() for char in run):  # nothing to narrow to
        return start, end

    index = min(max(position, start), end - 1) - start
    if not run[index].isalnum():
        # The pointer is on a mark rather than on a word.  It belongs to the clause the
        # mark closes when there is one - the reader clicked the full stop at the end of
        # a sentence - and to the one it opens otherwise.
        if index > 0 and run[index - 1].isalnum():
            index -= 1
        else:
            index += 1
    if index >= len(run) or not run[index].isalnum():
        return start, end  # two marks in a row: Qt's run is as good as any answer

    low, high = index, index + 1
    while low > 0 and run[low - 1].isalnum():
        low -= 1
    while high < len(run) and run[high].isalnum():
        high += 1
    return start + low, start + high


def _is_han(char: str) -> bool:
    """Whether *char* is Han script - what makes Qt's word rule too broad to use."""
    return any(low <= char <= high for low, high in _HAN_RANGES)


def selected_text(document: QTextDocument, start: int, end: int) -> str:
    """The selected passage, as plain text for the rest of the world.

    Two things have to be undone before the text leaves the reader.  Paragraph breaks
    arrive as ``U+2029``, which other applications show as a box, so they become
    newlines.  And the word joiner kinsoku injects to keep punctuation off line starts
    (FR-109) travels in the text too: it is a typesetting artefact, and a passage pasted
    into an email must not inherit it.
    """
    first, last = sorted((start, end))
    if first >= last:
        return ""
    cursor = QTextCursor(document)
    cursor.setPosition(first)
    cursor.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
    return cursor.selectedText().replace("\u2029", "\n").replace(WORD_JOINER, "")


def selection_rects(document: QTextDocument, start: int, end: int) -> list[QRectF]:
    """The bands the passage covers, in document coordinates, top to bottom.

    One rectangle per drawn *line*, not per paragraph: a paragraph that wraps is
    highlighted where each of its lines actually is, and those lines start at different
    x (the first line carries the paragraph indent) and can end at different widths.  A
    line the passage runs *through* is highlighted to the full column - the band every
    text view draws between two lines of one selection - while the last line of the
    passage stops where its text does.  An empty line has no glyphs to measure, so it
    gets the width of the column, again as Qt's own views draw it.
    """
    first, last = sorted((start, end))
    if first >= last:
        return []
    rects: list[QRectF] = []
    block = document.findBlock(first)
    while block.isValid() and block.position() <= last:
        rects.extend(_block_rects(document, block, first, last))
        block = block.next()
    return rects


def _block_rects(
    document: QTextDocument, block: QTextBlock, first: int, last: int
) -> list[QRectF]:
    """The bands of one block that the passage covers.

    A block's own coordinates start at its first character, which is the space both
    ``QTextLine.textStart`` and ``QTextLine.cursorToX`` are measured in - *not* the
    document's - so the passage is shifted into them once, here.
    """
    layout = block.layout()
    if layout is None:  # a block that was never laid out has nothing to show
        return []
    # Where the block was drawn, in document coordinates; the lines below are offsets
    # from there.
    by_layout = document.documentLayout()
    top = by_layout.blockBoundingRect(block).top() if by_layout is not None else 0.0
    begin = first - block.position()
    stop = last - block.position()

    rects: list[QRectF] = []
    for index in range(layout.lineCount()):
        line = layout.lineAt(index)
        line_start = line.textStart()
        line_end = line_start + line.textLength()
        height = _line_height(layout, index)
        if line_end == line_start:
            # The passage covers this empty line: it has no glyph, so the band runs the
            # width of the column - the bar Qt's own text views draw for it.
            if begin != stop and begin <= line_start <= stop:
                rects.append(QRectF(0.0, top + line.y(), _column_width(document, line), height))
            continue
        low = max(begin, line_start)
        high = min(stop, line_end)
        if high <= low:
            continue
        if high == line_end and high < stop:
            # The passage runs on from here, so this line is highlighted to the edge of
            # the column rather than stopping at its last glyph - the whole line is
            # inside the selection.
            right = line.width()
        else:
            right = line.cursorToX(high)[0]
        left = line.cursorToX(low)[0]
        rects.append(QRectF(left, top + line.y(), max(0.0, right - left), height))
    return rects


def _line_height(layout: object, index: int) -> float:
    """How tall the band of line *index* is.

    A line's own height is its type box, and two of those leave the leading between them
    unpainted - which would turn one passage into a stripe per line.  The band of a line
    that has another one under it therefore reaches that next line, as far as a text view
    fills it; the last line of the block stops at its own type, because the space below
    it belongs to the paragraph after.
    """
    line = layout.lineAt(index)
    if index + 1 < layout.lineCount():
        return max(line.height(), layout.lineAt(index + 1).y() - line.y())
    return line.height()


def _column_width(document: QTextDocument, line: object) -> float:
    """The width of the text column, for a line with no glyphs to measure."""
    page = document.pageSize()
    width = page.width() if page.width() > 0.0 else line.width()
    return max(0.0, float(width))
