"""``ReaderController``: the only surface QML talks to.

Holds all mutable application state (per C-013) and orchestrates the layers:
opening books, scrolling through a section, reacting to window resizes, and
persisting the reading position.  It contains no parsing and no typesetting logic -
those live behind :class:`~ebook_reader.typeset.engine.LayoutEngine`.

The position on screen is a distance from the top of the current section, because
that is what a continuous column has (ADR-016).  What gets *remembered* is still a
block index, because that is the one coordinate that survives a font-size or
window-size change (ADR-011); the distance inside that block is kept as a fraction
of its height.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Property, QObject, QRectF, QSizeF, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QImage, QTextDocument

from ..domain import BookError, EpubBook
from ..domain.lexicon import Lexicon, Lookup, lookup_keys, open_bundled
from ..domain.models import TocEntry
from ..typeset import (
    FontChoice,
    LayoutEngine,
    LaidOutSection,
    Theme,
    TypographySettings,
    quantise_offset,
)
from ..typeset.selection import position_at, selected_text, selection_rects, word_range
from ..typeset.settings import PageGeometry
from .settings_store import BookState, SettingsStore

__all__ = ["ReaderController"]

_log = logging.getLogger(__name__)

#: Rendered windows kept in memory.  A window image is a few megabytes (the text
#: column plus one rendering quantum), and keeping the last few makes scrolling back
#: instant; more than a handful would work against the memory budget
#: (ADR-008 / NFR-002).
_WINDOW_CACHE_SIZE = 5

#: Offsets closer than this are the same place.  Scrolling arrives as floating point
#: arithmetic from QML, and a repaint for a sub-pixel move is wasted work.
_OFFSET_EPSILON = 0.5

#: How many lines one wheel notch scrolls.  Three is what every browser on every
#: desktop uses, which is the behaviour this reader is imitating (FR-073).
_WHEEL_LINES = 3

#: How many pixels the margin control adds or removes per step.
_MARGIN_STEP = 12

#: How long the pointer must rest on a word before its card appears (FR-095).
#:
#: The gesture is *resting*, with no key to hold: something has to tell a deliberate
#: hover from the pointer crossing the page on its way to the scroll bar or the status
#: bar, and that something is this number.  300 ms is a third of a second - long enough
#: that a crossing never stops anywhere, short enough that a reader who has found the
#: word they do not know sees the card while their eyes are still on it.  It was 120 ms
#: while `Ctrl` was held, where the key itself was the declaration of intent; without it
#: the dwell has to carry that meaning alone, and a shorter one made cards flash at
#: every pause of the mouse.
_LOOKUP_DWELL_MS = 300

#: One row of a map column, in pixels: the floor both ``TocSidebar.qml`` and
#: ``OutlinePanel.qml`` give a row (``Math.max(38, title + 16)``), which is what a
#: title that fits on a single line comes out at.  A map has rows, not lines of body
#: text, so this - and not ``lineStep`` - is the unit the reader rolls over.
_MAP_ROW_HEIGHT = 38.0

#: How many rows of a map one wheel notch scrolls (FR-093).  Deliberately the same
#: number as ``_WHEEL_LINES``: the page and both maps then answer a notch with three
#: of their own units, and the only difference left between the gestures is whose unit
#: it is.  Left to Qt's own flickable arithmetic the maps moved about one row a notch
#: (measured on the omnibus's 134 rows: 48 px on the first notch, 36 px per notch over a
#: run of twelve, against rows 38 px tall) - that rule counts *text* lines, and a map
#: row is twice as tall as one - which turned the book into a 134 notch journey.
_MAP_WHEEL_ROWS = 3

#: The paces the auto-scroll offers (FR-094): name -> (label, lines per minute).
#:
#: Lines per *minute* rather than pixels per second, because the line is the reader's
#: unit and not the pixel: one line of Chinese body text is four to seven seconds of
#: reading, so those four to seven seconds are what a comfortable pace is made of.
#: Measured in px/s the same setting would scroll twice as fast in words the moment
#: the reader asked for a bigger type - the distance between two lines grows with
#: ``lineStep`` and the number of lines read does not.
_AUTO_SCROLL_PACES: dict[str, tuple[str, float]] = {
    "slow": ("慢", 6.0),
    "normal": ("中", 12.0),
    "fast": ("快", 24.0),
}

#: The pace a reader who has never chosen one gets: one line every five seconds,
#: which is a screen (about 28 lines here) in a bit over two minutes.
_AUTO_SCROLL_DEFAULT_PACE = "normal"

#: How often the auto-scroll timer ticks.  Twenty times a second is fine enough that
#: the text drifts instead of jumping - and coarse enough that a tick's work (one
#: offset, a handful of signals) costs nothing measurable (NFR-005).
_AUTO_SCROLL_TIMER_MS = 50

#: A longer gap than this between two ticks is not reading time.  The machine was
#: suspended or the process was blocked; carrying the whole interval into one step
#: would teleport the reader past pages they never saw, so the tick is dropped and
#: the clock restarts from now.
_AUTO_SCROLL_MAX_GAP = 1.0

#: Seconds between two writes of the resume point while the window is being moved
#: *continuously* (FR-094).  A move the reader made in one go - a wheel notch, a key,
#: a jump from either map - writes at once: it is a place they chose.  A move that
#: arrives as a stream is another matter: the auto-scroll moves twenty times a
#: second and a drag moves once per pointer event, and a write per move would be an
#: atomic file write per frame for a position a few pixels further on.  The place in
#: the file is at most this far behind the place on screen, and every gesture ends
#: with a write of its own (stopping the drift, letting go of a drag, changing
#: chapter, closing the book, quitting).
_SAVE_THROTTLE_S = 5.0

_FONT_SIZE_STEP = 1.0
_LINE_HEIGHT_STEP = 0.05

#: Deepest heading level the outline column lists (FR-019).  Levels 4 to 6 are
#: publishers' decoration: they are within 20% of the body size, which reads as
#: emphasized text rather than as structure, and listing them turns a map into noise.
_OUTLINE_MAX_LEVEL = 3

#: U+2060 WORD JOINER, which kinsoku injects to keep punctuation off line starts.
#: Right for layout, wrong for the UI: it survives into the outline, where it
#: makes a label differ from the same text read in the page (FR-109).
_WORD_JOINER = "\u2060"


def _clean_label(text: str) -> str:
    """Collapse whitespace and strip the layout-only word joiners."""
    return " ".join(text.replace(_WORD_JOINER, "").split())


def _wheel_distance(angle_y: float, pixel_y: float, wheel_step: float) -> float:
    """Turn one wheel event into a scroll distance in pixels (FR-063 / FR-073).

    Positive moves the text down, which is the sign :meth:`ReaderController.scrollBy`
    expects.  An event arrives in one of two shapes, and the two do *not* share a sign
    convention, so reading either one alone is wrong for the other:

    * ``angleDelta`` counts eighths of a degree and is positive when the wheel turns
      *away* from the user - so scrolling down is negative.  A notch is 120 units, and
      a notch is ``wheel_step`` (three lines).
    * ``pixelDelta`` is a distance on screen, which Qt documents as "used directly to
      scroll content": positive when scrolling down.  A smooth source - a touchpad, or
      a high-resolution or free-spinning wheel - sends *only* this, with an angle of
      zero, which is where the reader used to scroll by nothing at all.

    ``angleDelta`` wins whenever it is present, because Qt documents ``pixelDelta`` as
    driver-specific and unreliable on X11, and an event that carries both is a wheel
    notch that happens to have been given a distance too.
    """
    if angle_y:
        return -float(angle_y) / 120.0 * wheel_step
    if pixel_y:
        return float(pixel_y)
    return 0.0


def _auto_scroll_pace(name: str) -> str:
    """The pace *name* stands for, or the default one (FR-094).

    A name this version does not know - a hand-edited ``config.json``, or one written
    by a later version - is not an error and must not become a speed nobody chose.
    """
    return name if name in _AUTO_SCROLL_PACES else _AUTO_SCROLL_DEFAULT_PACE


def _auto_scroll_lines(pace: str) -> float:
    """How many lines a minute *pace* scrolls (FR-094)."""
    return _AUTO_SCROLL_PACES[_auto_scroll_pace(pace)][1]


def _auto_scroll_pace_label(pace: str) -> str:
    """The word the reader sees for *pace* (FR-094)."""
    return _AUTO_SCROLL_PACES[_auto_scroll_pace(pace)][0]


def _auto_scroll_step(elapsed: float, lines_per_minute: float, line_step: float) -> float:
    """How far the text moves in *elapsed* seconds, in pixels (FR-094).

    Worked out from the time that actually passed rather than from the number of
    ticks, so a busy machine - or a timer Qt fires late, which it is allowed to do -
    cannot make the text crawl or lurch: the reader asked for a speed, and the speed
    is what they get.  That is also why the test drives this with a whole minute and
    nobody has to wait for one.
    """
    if elapsed <= 0.0 or lines_per_minute <= 0.0 or line_step <= 0.0:
        return 0.0
    return float(elapsed) * float(lines_per_minute) * float(line_step) / 60.0


def _auto_scroll_elapsed(previous: float, now: float) -> float:
    """Seconds since the last tick, or ``0.0`` when that gap is not reading time.

    Two answers of zero, for the same reason: a clock that went backwards (there is
    nothing to move by) and a gap longer than :data:`_AUTO_SCROLL_MAX_GAP` - the
    machine slept, or the process was blocked for a second - where moving by the
    whole interval would skip pages the reader never saw.
    """
    elapsed = float(now) - float(previous)
    if elapsed <= 0.0 or elapsed > _AUTO_SCROLL_MAX_GAP:
        return 0.0
    return elapsed


def _build_outline(section: LaidOutSection) -> list[dict[str, object]]:
    """Rows describing the inside of one laid-out section (FR-018).

    Heading-based only (ADR-016).  With no pages to map, the alternative - rows for
    equally sized slices of a continuous column - would move every time the window
    is resized, and a map whose entries wander is not a map.  So the rule is simply:
    the section's own headings, down to level :data:`_OUTLINE_MAX_LEVEL`, and no rows
    at all when the section has none.  The column is not offered in that case
    (FR-019): an empty column is worse than no column.

    ``offset`` is what a row jumps to, ``block`` records which block it came from,
    and ``row`` is its index - the panel highlights a row by comparing that against
    the controller's ``currentOutlineRow``, which is a property of the scroll
    position rather than of the rows themselves.
    """
    rows: list[dict[str, object]] = []
    for index, block in enumerate(section.blocks):
        if not block.is_heading:
            continue
        level = max(1, min(6, block.level))
        if level > _OUTLINE_MAX_LEVEL:
            continue
        title = _clean_label(block.text)
        if not title:
            continue
        rows.append(
            {
                "title": title,
                "level": level,
                "offset": section.block_offset(index),
                "block": index,
                "row": len(rows),
            }
        )
    return rows


class ReaderController(QObject):
    """State machine behind the QML user interface.

    The two gestures that arrive from the page itself live here rather than in QML: a
    plain drag takes hold of the page and moves it (FR-079), and a Shift drag marks a
    passage and copies it on release (FR-070).  What both need - which character a point
    on the page is, and where a passage's bands are drawn - needs the document, the
    margins and the scroll offset at once, and that is the controller's to know.
    """

    viewChanged = Signal()
    layoutChanged = Signal()
    settingsChanged = Signal()
    bookChanged = Signal()
    tocChanged = Signal()
    tocRowChanged = Signal()
    errorOccurred = Signal(str)
    statusMessage = Signal(str)
    #: The auto-scroll went on or off, or changed pace (FR-094): the menu's tick, the
    #: status bar's word for the state and the three pace rows all read this.
    autoScrollChanged = Signal()
    #: The bands a selection is drawn in are part of the page, so this is announced both
    #: when the passage changes and when the window moves (FR-070).
    selectionChanged = Signal()
    #: The word card appeared, changed word or went away, or the point it points at moved
    #: with the page (FR-095).
    lookupChanged = Signal()
    #: 悬停查词 went on or off (FR-095): the right-click menu's tick, and nothing else -
    #: the card itself only listens to `lookupChanged`, because a switch that is off can
    #: never have a card to show.
    dictionaryChanged = Signal()

    def __init__(self, parent: QObject | None = None, store: SettingsStore | None = None) -> None:
        super().__init__(parent)
        self._store = store or SettingsStore()
        self._settings: TypographySettings = self._store.settings()
        self._book: EpubBook | None = None
        self._engine: LayoutEngine | None = None
        self._window_cache: OrderedDict[tuple[int, float], QImage] = OrderedDict()
        self._section_index = 0
        self._scroll_offset = 0.0
        self._view_size = QSizeF(900.0, 1300.0)
        self._device_ratio = 1.0
        self._toc_flat: list[tuple[TocEntry, int]] = []
        #: The selection, as two character positions in the current section's document
        #: (FR-070).  Positions rather than pixels, because the passage outlives the
        #: window it was chosen in: change the type size and the same characters are
        #: still selected, drawn where they now are.
        self._sel_anchor: int | None = None
        self._sel_start = 0
        self._sel_end = 0
        #: Where a hand-pan drag took hold: the pointer's y and the offset it started
        #: from, so the page follows the pointer's *distance* rather than a sum of
        #: deltas that clamps and unclamps at either end of the section (FR-079).
        self._drag_origin: tuple[float, float] | None = None
        #: Whether that drag moved the pointer at all.  A press and release in one place
        #: is a click, and a click is how the reader puts a marked passage down again
        #: (FR-070) - so the difference matters, and a distance of zero is a poor way to
        #: tell it: a drag into the clamp at either end of a section moves nothing either.
        self._drag_moved = False
        #: Whether a double click just took the word under the pointer.  Qt delivers one as
        #: press, release, press, doubleClick, release: the last of those has nothing
        #: between it and the press before it, so without this flag it would be read as a
        #: plain click and would put the word straight back down (FR-070).
        self._word_taken = False
        #: The row the panel last highlighted, so that scrolling inside one document
        #: only signals a change when it actually crosses a row (FR-013).
        self._toc_row = -1
        self._toc_visible = False
        self._settings_visible = False
        self._outline_visible = False
        self._outline_key: tuple[object, int, int, float] | None = None
        self._outline_cache: list[dict[str, object]] = []
        self._error_message = ""
        self._last_open_ms = 0.0
        self._last_section_ms = 0.0
        self._last_render_ms = 0.0
        self._startup_started = time.perf_counter()
        self._startup_ms = 0.0
        #: Auto-scroll (FR-094): a timer walks the window down the column by itself.
        #:
        #: The timer lives here rather than in QML, even though a QML animation would
        #: cost nothing per frame, because everything that *knows* where the window is
        #: is here: the scroll bar's handle, the progress counters, the contents
        #: highlight and the remembered resume point all read ``_scroll_offset``, and
        #: text moved by an animation would leave all four pointing at a place the
        #: reader is not.  What the timer does per tick is small - see
        #: :data:`_AUTO_SCROLL_TIMER_MS` - and the expensive part of scrolling, the
        #: bitmap, is still only rebuilt when a rendering quantum is crossed (ADR-008).
        self._auto_timer = QTimer(self)
        self._auto_timer.setInterval(_AUTO_SCROLL_TIMER_MS)
        self._auto_timer.timeout.connect(self._on_auto_tick)
        self._auto_running = False
        self._auto_pace = _auto_scroll_pace(self._store.auto_scroll_pace())
        #: Distance the reader has been granted but not yet moved by.  A tick at the
        #: slow pace is a quarter of a pixel, and `_set_offset` ignores moves below
        #: `_OFFSET_EPSILON`, so the remainder is carried to the next tick instead of
        #: being thrown away - which is what keeps the slow pace from standing still.
        self._auto_pending = 0.0
        self._auto_last = 0.0
        #: The word card (FR-095): where the pointer is resting, and what the dictionary
        #: said about the word under it.
        #:
        #: The point is kept while the switch is off too: turning the card back on should
        #: cost no pointer movement, and nothing is looked up until it is on.
        self._hover_point: tuple[float, float] | None = None
        #: The 悬停查词 switch, remembered across sessions (FR-095).  Off means the feature
        #: is off - no dwell timer, no query, no card - rather than merely hidden.
        self._dictionary_enabled = self._store.dictionary_enabled()
        #: The word last asked about.  Resting on a word costs one query, not one per
        #: pointer event - and a word the file does not have is remembered too, so a
        #: still pointer on an unknown word does not keep asking.
        self._lookup_asked = ""
        self._lookup: Lookup | None = None
        self._lookup_anchor = (0.0, 0.0)
        #: Opened lazily by ``Lexicon.lookup``: a reader who never points at a word never
        #: opens the file, and a test never needs the data to exist.
        self._lexicon: Lexicon = open_bundled()
        #: A single shot, restarted by every pointer event: the word is looked up once the
        #: pointer has *rested*, which is the gesture, rather than on the way past.
        self._lookup_timer = QTimer(self)
        self._lookup_timer.setSingleShot(True)
        self._lookup_timer.setInterval(_LOOKUP_DWELL_MS)
        self._lookup_timer.timeout.connect(self._on_lookup_dwell)
        #: When the resume point was last written, for the throttle in
        #: :meth:`_publish_view`.  Zero, so the first move always writes.
        self._saved_at = 0.0

    # -------------------------------------------------------------- properties

    def _get_has_book(self) -> bool:
        return self._book is not None

    hasBook = Property(bool, _get_has_book, notify=bookChanged)

    def _get_book_title(self) -> str:
        return self._book.meta.title if self._book else ""

    bookTitle = Property(str, _get_book_title, notify=bookChanged)

    def _get_book_author(self) -> str:
        return self._book.meta.author_line if self._book else ""

    bookAuthor = Property(str, _get_book_author, notify=bookChanged)

    def _get_view_image(self) -> QImage:
        """The rendered window at the current offset, rasterised on demand (ADR-008).

        Rendered at the *quantised* offset and cached under it, so nudging the wheel
        inside one quantum reuses the image QML already has; the sub-quantum part is
        drawn as a translate (see ``pan``).
        """
        if self._engine is None:
            return QImage()
        key = (self._section_index, self.windowOffset)
        image = self._window_cache.get(key)
        if image is None:
            started = time.perf_counter()
            image = self._engine.render_window(self._section_index, self._scroll_offset)
            self._last_render_ms = (time.perf_counter() - started) * 1000.0
            self._window_cache[key] = image
            while len(self._window_cache) > _WINDOW_CACHE_SIZE:
                self._window_cache.popitem(last=False)
        return image

    viewImage = Property(QImage, _get_view_image, notify=viewChanged)

    def _get_scroll_offset(self) -> float:
        """Distance from the top of the current section, in logical pixels."""
        return self._scroll_offset

    scrollOffset = Property(float, _get_scroll_offset, notify=viewChanged)

    def _get_window_offset(self) -> float:
        """Offset the current image was rasterised at, on the quantum grid."""
        return quantise_offset(self._scroll_offset)

    windowOffset = Property(float, _get_window_offset, notify=viewChanged)

    def _get_pan(self) -> float:
        """How far the current image is drawn above the window, in logical pixels.

        ``windowOffset + pan`` is exactly ``scrollOffset``, which is what makes the
        text move smoothly while Python only runs once per quantum (ADR-016).
        """
        return self._scroll_offset - self.windowOffset

    pan = Property(float, _get_pan, notify=viewChanged)

    def _get_scroll_max(self) -> float:
        section = self._current_section()
        return section.max_offset if section is not None else 0.0

    scrollMax = Property(float, _get_scroll_max, notify=viewChanged)

    def _get_content_height(self) -> float:
        section = self._current_section()
        return section.height if section is not None else 0.0

    contentHeight = Property(float, _get_content_height, notify=viewChanged)

    def _get_viewport_height(self) -> float:
        """Height of the content box, i.e. how much text is on screen at once."""
        return self._view_geometry().content_height

    viewportHeight = Property(float, _get_viewport_height, notify=viewChanged)

    def _get_view_page_size(self) -> QSizeF:
        """Page box a window image is rendered for; ``PageItem`` letterboxes it."""
        return self._view_geometry().page_size

    viewPageSize = Property(QSizeF, _get_view_page_size, notify=viewChanged)

    def _get_top_margin(self) -> float:
        """Height of the top margin.

        The position bar is drawn across the top of the column with this as its height
        (FR-074), for the same reason the 下一章 line sits in the bottom margin: at the
        top of a section the window begins with the margin, so a bar drawn there covers
        no text, and further down it covers no more than the margin the reader chose.
        """
        return self._view_geometry().margin_top

    topMargin = Property(float, _get_top_margin, notify=viewChanged)

    def _get_bottom_margin(self) -> float:
        """Height of the bottom margin.

        QML draws the "next chapter" line inside it (FR-074): the window can be
        scrolled until the last line sits exactly on the bottom of the content box,
        so anything drawn there would otherwise cover the text the reader is on.
        """
        return self._view_geometry().margin_bottom

    bottomMargin = Property(float, _get_bottom_margin, notify=viewChanged)

    def _get_line_step(self) -> float:
        """One line of body text, in pixels: the unit the scroll actions move by."""
        if self._engine is not None:
            return self._engine.line_step
        return max(1.0, self._settings.font_size * self._settings.line_height)

    lineStep = Property(float, _get_line_step, notify=viewChanged)

    def _get_wheel_step(self) -> float:
        """Distance one wheel notch scrolls (FR-073).

        Computed here rather than in QML so the wheel, the arrow keys and the menu
        all move by the same amount, and so a test can assert the number.
        """
        return self.lineStep * _WHEEL_LINES

    wheelStep = Property(float, _get_wheel_step, notify=viewChanged)

    def _get_map_wheel_step(self) -> float:
        """Distance one wheel notch scrolls a map column (FR-093).

        The contents and the outline draw rows, so they get a step in rows: three of
        them, the same count the page uses for its lines.  Computed here for the two
        reasons ``wheelStep`` is: QML never multiplies a delta by anything
        (ADR-018 ③), and a test can assert the number without a window.
        """
        return _MAP_ROW_HEIGHT * _MAP_WHEEL_ROWS

    # No notify: unlike the text's step, which follows the font size, this one is a
    # constant - the panels' rows do not change with the type size.
    mapWheelStep = Property(float, _get_map_wheel_step)

    def _get_at_section_end(self) -> bool:
        """Whether the window is as far down as the section goes (FR-074)."""
        return self._scroll_offset >= self.scrollMax - _OFFSET_EPSILON

    atSectionEnd = Property(bool, _get_at_section_end, notify=viewChanged)

    def _adjacent_section(self, delta: int) -> int:
        """The section a page-turn lands on, or ``-1`` when nothing is left (FR-016).

        A ``linear="no"`` document is in the spine but not in the text - a cover, an
        imprint page, an advertisement - and the book says so.  The walk therefore
        belongs to the domain layer (``EpubBook.next_section`` / ``previous_section``)
        rather than to a ``±1`` written here: it is the book's own statement, and a
        reader that pages into a cover has already disagreed with the publisher.
        """
        if self._book is None:
            return -1
        step = self._book.next_section if delta > 0 else self._book.previous_section
        return step(self._section_index)

    def _get_has_next_section(self) -> bool:
        return self._adjacent_section(1) >= 0

    hasNextSection = Property(bool, _get_has_next_section, notify=viewChanged)

    def _get_next_section_title(self) -> str:
        """Title of the chapter that follows, shown at the end of this one (FR-074).

        The reader is told what comes next instead of being taken there: moving on
        for them takes away the moment where they decide to stop.  Empty when
        nothing follows this section, or when no contents entry names it.
        """
        target = self._adjacent_section(1)
        return self._section_title_at(target) if target >= 0 else ""

    nextSectionTitle = Property(str, _get_next_section_title, notify=viewChanged)

    def _get_section_index(self) -> int:
        return self._section_index

    sectionIndex = Property(int, _get_section_index, notify=viewChanged)

    def _get_section_count(self) -> int:
        return self._book.section_count if self._book else 0

    sectionCount = Property(int, _get_section_count, notify=bookChanged)

    def _section_title_at(self, index: int) -> str:
        """First table-of-contents title pointing at *index*, or ``""``."""
        row = self._row_naming_section(index)
        return self._toc_flat[row][0].title if row >= 0 else ""

    def _row_naming_section(self, index: int) -> int:
        """Row of the first contents entry naming *index*, or ``-1`` (FR-012).

        Two callers need a *section* rather than a row: the 下一章 line at the end of a
        column (FR-074), which names a whole section, and the position bar's fallback
        where the book's own tree does not reach the reader's place.
        """
        for row, (entry, target) in enumerate(self._toc_flat):
            if target == index and entry.title:
                return row
        return -1

    def _get_section_title(self) -> str:
        # A section no contents entry names is an untitled part of the book, so the
        # book's own title is the honest answer there.
        return self._section_title_at(self._section_index) or self.bookTitle

    sectionTitle = Property(str, _get_section_title, notify=viewChanged)

    def _section_progress(self) -> float:
        """How far through the current section the reader is, 0..1 (FR-072).

        Measured to the *end* of the window rather than to its top: a chapter that
        fits on one screen reads as finished, not as 0%.
        """
        section = self._current_section()
        if section is None or section.height <= 0.0:
            return 0.0
        return max(0.0, min(1.0, (self._scroll_offset + self.viewportHeight) / section.height))

    def _get_progress_text(self) -> str:
        if self._book is None:
            return ""
        return (
            f"本卷 {self._section_progress() * 100:.0f}%"
            f"   ·   全书 {self._overall_percent():.0f}%"
        )

    progressText = Property(str, _get_progress_text, notify=viewChanged)

    def _get_progress(self) -> float:
        return self._overall_percent() / 100.0

    progress = Property(float, _get_progress, notify=viewChanged)

    def _get_auto_scroll_running(self) -> bool:
        return self._auto_running

    autoScrollRunning = Property(bool, _get_auto_scroll_running, notify=autoScrollChanged)

    def _get_auto_scroll_pace(self) -> str:
        return self._auto_pace

    autoScrollPace = Property(str, _get_auto_scroll_pace, notify=autoScrollChanged)

    def _get_auto_scroll_pace_label(self) -> str:
        return _auto_scroll_pace_label(self._auto_pace)

    autoScrollPaceLabel = Property(str, _get_auto_scroll_pace_label, notify=autoScrollChanged)

    def _get_auto_scroll_lines(self) -> float:
        return _auto_scroll_lines(self._auto_pace)

    autoScrollLinesPerMinute = Property(float, _get_auto_scroll_lines, notify=autoScrollChanged)

    def _get_auto_scroll_status(self) -> str:
        """What the status bar says while the text drifts by itself (FR-094).

        Empty when it is not drifting, so the label can fall back to the layout
        diagnostics without having to ask which of the two it is showing.
        """
        if not self._auto_running:
            return ""
        return (
            f"自动滚屏 · {self._get_auto_scroll_pace_label()}"
            f" {self._get_auto_scroll_lines():.0f} 行/分"
        )

    autoScrollStatus = Property(str, _get_auto_scroll_status, notify=autoScrollChanged)

    def _get_status_text(self) -> str:
        if self._error_message:
            return self._error_message
        if self._engine is None:
            return ""
        stats = self._engine.last_stats
        cache = self._engine.image_stats()
        parts = [
            f"打开 {self._last_open_ms:.0f} ms",
            f"排版 {self._last_section_ms:.0f} ms",
            f"绘制 {self._last_render_ms:.0f} ms",
        ]
        if stats is not None:
            screens = stats.height / max(1.0, self.viewportHeight)
            parts.append(f"{stats.blocks} 块 / {screens:.1f} 屏")
        parts.append(f"图片 {cache.items} 张 / {cache.bytes / 1e6:.1f} MB")
        return "   ·   ".join(parts)

    statusText = Property(str, _get_status_text, notify=viewChanged)

    def _get_startup_ms(self) -> float:
        """Wall-clock time from process start to the first window being ready.

        This is the number NFR-001 is about, so it is a first-class property
        rather than something only visible in a profiler.
        """
        return self._startup_ms

    startupMs = Property(float, _get_startup_ms, notify=viewChanged)

    def _get_error_message(self) -> str:
        return self._error_message

    errorMessage = Property(str, _get_error_message, notify=layoutChanged)

    # ------------------------------------------------------------------- theme

    def _colors(self):
        return self._settings.colors

    def _get_bg_color(self) -> QColor:
        return QColor(self._colors().background)

    backgroundColor = Property(QColor, _get_bg_color, notify=settingsChanged)

    def _get_fg_color(self) -> QColor:
        return QColor(self._colors().foreground)

    textColor = Property(QColor, _get_fg_color, notify=settingsChanged)

    def _get_panel_color(self) -> QColor:
        return QColor(self._colors().panel)

    panelColor = Property(QColor, _get_panel_color, notify=settingsChanged)

    def _get_panel_text_color(self) -> QColor:
        return QColor(self._colors().panel_text)

    panelTextColor = Property(QColor, _get_panel_text_color, notify=settingsChanged)

    def _get_muted_color(self) -> QColor:
        return QColor(self._colors().muted)

    mutedColor = Property(QColor, _get_muted_color, notify=settingsChanged)

    def _get_band_color(self) -> QColor:
        return QColor(self._colors().band)

    #: The surface the two strips drawn on the page share - the position bar and the
    #: 「下一章」 line (FR-074).  Not the panel colour: the panels are the map beside the
    #: book, and the strips are the paper (FR-090).
    bandColor = Property(QColor, _get_band_color, notify=settingsChanged)

    def _get_accent_color(self) -> QColor:
        return QColor(self._colors().selection)

    accentColor = Property(QColor, _get_accent_color, notify=settingsChanged)

    def _get_link_color(self) -> QColor:
        return QColor(self._colors().link)

    linkColor = Property(QColor, _get_link_color, notify=settingsChanged)

    def _get_selection_color(self) -> QColor:
        """The wash drawn over a selected passage, already carrying its alpha (FR-070)."""
        return self._settings.text_selection_color()

    selectionColor = Property(QColor, _get_selection_color, notify=settingsChanged)

    # ---------------------------------------------------------------- settings

    def _get_font_size(self) -> float:
        return self._settings.font_size

    fontSize = Property(float, _get_font_size, notify=settingsChanged)

    def _get_line_height(self) -> float:
        return self._settings.line_height

    lineHeight = Property(float, _get_line_height, notify=settingsChanged)

    def _get_justify(self) -> bool:
        return self._settings.justify

    justify = Property(bool, _get_justify, notify=settingsChanged)

    def _get_theme_name(self) -> str:
        return self._settings.theme.value

    themeName = Property(str, _get_theme_name, notify=settingsChanged)

    def _get_theme_label(self) -> str:
        return {"light": "日间", "sepia": "米色", "dark": "夜间"}[self._settings.theme.value]

    themeLabel = Property(str, _get_theme_label, notify=settingsChanged)

    def _get_font_choice(self) -> str:
        return self._settings.font_choice.value

    fontChoice = Property(str, _get_font_choice, notify=settingsChanged)

    def _get_font_choice_label(self) -> str:
        return self._settings.font_choice.label

    fontChoiceLabel = Property(str, _get_font_choice_label, notify=settingsChanged)

    def _get_margin_label(self) -> str:
        return f"边距 {self._settings.margin_left}"

    marginLabel = Property(str, _get_margin_label, notify=settingsChanged)

    def _get_margin(self) -> int:
        """The margin on its own, for the menu row that shows the current value."""
        return int(self._settings.margin_left)

    margin = Property(int, _get_margin, notify=settingsChanged)

    # ---------------------------------------------------------------- selection

    def _get_has_selection(self) -> bool:
        return self._sel_end > self._sel_start

    #: Whether there is a passage to copy (FR-070) - what greys out the menu's 复制 row.
    hasSelection = Property(bool, _get_has_selection, notify=selectionChanged)

    def _get_selected_text(self) -> str:
        """The selected passage as plain text: what a copy hands to the clipboard.

        Read through the document rather than kept as a string, so that a passage
        selected before a re-layout still reads back the same *characters* (FR-070).
        Paragraph breaks become newlines and the layout-only word joiners go (FR-109).
        """
        document = self._document()
        if document is None or not self.hasSelection:
            return ""
        return selected_text(document, self._sel_start, self._sel_end)

    selectedText = Property(str, _get_selected_text, notify=selectionChanged)

    def _get_selection_rects(self) -> list[QRectF]:
        """The bands the selected passage covers, on the page, top to bottom (FR-070).

        Deliberately in *page* coordinates - the space the window image is rasterised in
        - rather than in the document's: the controller is the only thing that knows
        where the document's origin sits under the margins and the scroll offset, and
        ``PageItem`` then draws them through the very transform it draws the image with.
        Moving the window therefore moves them, which is why this is announced on
        ``selectionChanged`` as well as on a change of passage.
        """
        document = self._document()
        if document is None or not self.hasSelection:
            return []
        origin = self._view_geometry().content_origin
        top = origin.y() - self._scroll_offset
        return [
            QRectF(rect.x() + origin.x(), rect.y() + top, rect.width(), rect.height())
            for rect in selection_rects(document, self._sel_start, self._sel_end)
        ]

    selectionRects = Property(list, _get_selection_rects, notify=selectionChanged)

    # ---------------------------------------------------------------- word card

    def _get_dictionary_enabled(self) -> bool:
        return self._dictionary_enabled

    #: Whether pointing at a word raises its card (FR-095).  The right-click menu shows it
    #: as a tick; `DictionaryPopup.qml` needs nothing extra - with the switch off no card
    #: is ever put up, so its own `lookupVisible` binding already says "nothing here".
    dictionaryEnabled = Property(bool, _get_dictionary_enabled, notify=dictionaryChanged)

    def _get_lookup_visible(self) -> bool:
        return self._lookup is not None

    #: Whether the card is up (FR-095).  The one property ``DictionaryPopup.qml`` binds its
    #: ``visible`` to, so a card can never be drawn without content behind it.
    lookupVisible = Property(bool, _get_lookup_visible, notify=lookupChanged)

    def _get_lookup_form(self) -> str:
        """The word as it stood on the page - ``""`` when no card is up."""
        return self._lookup.form if self._lookup is not None else ""

    lookupForm = Property(str, _get_lookup_form, notify=lookupChanged)

    def _get_lookup_word(self) -> str:
        """The headword being explained: ``go`` after the pointer rested on ``went``."""
        return self._lookup.word if self._lookup is not None else ""

    lookupWord = Property(str, _get_lookup_word, notify=lookupChanged)

    def _get_lookup_phonetic(self) -> str:
        """The pronunciation in IPA, slashes and all; empty when the entry has none."""
        return self._lookup.phonetic if self._lookup is not None else ""

    lookupPhonetic = Property(str, _get_lookup_phonetic, notify=lookupChanged)

    def _get_lookup_senses(self) -> list[str]:
        """The gloss lines, already cut to what a small card holds (``glosses.py``)."""
        return list(self._lookup.senses) if self._lookup is not None else []

    lookupSenses = Property(list, _get_lookup_senses, notify=lookupChanged)

    def _get_lookup_anchor_x(self) -> float:
        """Where the card points, in the page area's own coordinates (FR-095).

        The page's rather than the window's: the shell puts the card inside the same item
        the pointer is in, so no margin or panel width has to be added twice.
        """
        return self._lookup_anchor[0]

    lookupAnchorX = Property(float, _get_lookup_anchor_x, notify=lookupChanged)

    def _get_lookup_anchor_y(self) -> float:
        return self._lookup_anchor[1]

    lookupAnchorY = Property(float, _get_lookup_anchor_y, notify=lookupChanged)

    @Slot(float, float)
    def hoverMoved(self, x: float, y: float) -> None:
        """The pointer moved over the page with no button held (FR-095).

        Deliberately the cheapest thing in this file: two fields, two branches and a timer
        restart.  It runs on every pointer event of a page-wide hover, so nothing that
        costs a hit test, a query or a signal happens here - all of that is left to
        :meth:`_on_lookup_dwell`, once the pointer has rested for
        :data:`_LOOKUP_DWELL_MS`.

        With the switch off the point is still remembered but no timer is started: the
        reader asked for no cards, so the dictionary is not consulted and there is nothing
        to take down.
        """
        self._hover_point = (float(x), float(y))
        if not self._dictionary_enabled:
            if self._lookup is not None:
                self._hide_lookup()
            return
        if self._page_covered():
            return
        self._lookup_timer.start()

    @Slot()
    def hoverLeft(self) -> None:
        """The pointer left the page: nothing is under it any more (FR-095).

        The card is *not* taken down here.  Leaving the page is also what happens when the
        pointer steps from the page onto the card itself, and a card that vanished as soon
        as the reader moved towards it to read it would be unusable.
        """
        self._hover_point = None
        self._lookup_timer.stop()
        self._lookup_asked = ""

    @Slot()
    def hideLookup(self) -> None:
        """Take the word card down (FR-095): the window lost the focus.

        A card still explaining a word in a window the reader has looked away from is an
        answer to a question nobody is asking any more.  Nothing else is touched - the
        page, the point the pointer last rested on and the switch all stay as they are, so
        coming back and moving the pointer a hair is enough to bring the card back.
        """
        self._hide_lookup()

    @Slot(bool)
    def setDictionaryEnabled(self, enabled: bool) -> None:
        """Turn 悬停查词 on or off, and remember the choice (FR-095).

        Off is not "hidden" but *off*: the dwell timer stops, the card goes away and no
        query is ever made, so a reader who would rather read undisturbed pays nothing for
        the feature existing.  On says so in the status bar - a switch whose only sign of
        life is a tick in a menu that has just closed leaves the reader unsure whether
        pointing at a word now does anything.

        Remembered across sessions, like the auto-scroll pace: this is a reading
        preference, not a mode of the current page (``SettingsStore.dictionary_enabled``).
        """
        enabled = bool(enabled)
        if enabled == self._dictionary_enabled:
            return
        self._dictionary_enabled = enabled
        self._store.set_dictionary_enabled(enabled)
        self._store.save()
        if not enabled:
            # Nothing may be left on screen, and no lookup may be in flight: `_hide_lookup`
            # stops the timer as well as clearing the card.
            self._hide_lookup()
        self.dictionaryChanged.emit()
        self.statusMessage.emit(
            "悬停查词已开启：把鼠标停在英文单词上即可看到释义"
            if enabled
            else "悬停查词已关闭"
        )
        _log.debug("dictionary enabled=%s", enabled)

    @Slot()
    def toggleDictionary(self) -> None:
        """The other end of the switch: on if it is off, off if it is on (the menu row)."""
        self.setDictionaryEnabled(not self._dictionary_enabled)

    def _on_lookup_dwell(self) -> None:
        """The pointer has rested: ask the dictionary about the word under it (FR-095)."""
        if not self._dictionary_enabled or self._hover_point is None or self._page_covered():
            return
        keys = lookup_keys(self._word_at(*self._hover_point))
        if not keys:
            self._hide_lookup()
            return
        if keys[0] == self._lookup_asked:
            return
        answer = self._lexicon.lookup(keys)
        if answer is None:
            # `_hide_lookup` forgets the key, so a miss sets it *after* clearing - that is
            # what stops a still pointer over an unknown word from querying on every event
            # while still letting the same word work again once the pointer has left it.
            self._hide_lookup()
            self._lookup_asked = keys[0]
            return
        self._lookup_asked = keys[0]
        self._lookup = answer
        self._lookup_anchor = self._hover_point
        self.lookupChanged.emit()

    def _hide_lookup(self) -> None:
        """Take the card down, if it is up, and forget which word it was about.

        Cheap enough to call from every hook that moves the page: with no card on screen
        it touches two fields and returns before any signal.
        """
        self._lookup_asked = ""
        self._lookup_timer.stop()
        if self._lookup is None:
            return
        self._lookup = None
        self.lookupChanged.emit()

    def _page_covered(self) -> bool:
        """Whether a panel is drawn *over* the page - no card while one is (FR-095).

        Only the settings drawer does that: it floats above the right-hand side of the
        text.  The contents and outline columns take room *beside* the page instead, so
        the word under the pointer is still fully visible and still worth explaining -
        and a reader who keeps the contents open (which is how most books open, FR-013)
        must not lose the dictionary over it.
        """
        return self._settings_visible

    def _word_at(self, x: float, y: float) -> str:
        """The word under a point on the page, as plain text (FR-095).

        The same two steps a double click takes - the point through Qt's hit test, then
        the run around it - because the card has to answer about exactly the word that
        double click would copy.
        """
        document = self._document()
        if document is None:
            return ""
        start, end = word_range(document, self._position_at_page(x, y))
        if start >= end:  # between two words, or out in the margin
            return ""
        return selected_text(document, start, end)

    # ------------------------------------------------------------------ panels

    def _get_toc_visible(self) -> bool:
        return self._toc_visible

    tocVisible = Property(bool, _get_toc_visible, notify=layoutChanged)

    def _get_toc_available(self) -> bool:
        """Whether the book brought a table of contents to show (FR-013).

        Two callers need this: the shell, which must not open a column with nothing
        in it, and the right-click menu, which greys its 目录 row out instead of
        offering an operation that would do nothing.
        """
        return bool(self._toc_flat)

    tocAvailable = Property(bool, _get_toc_available, notify=tocChanged)

    def _get_settings_visible(self) -> bool:
        return self._settings_visible

    settingsVisible = Property(bool, _get_settings_visible, notify=layoutChanged)

    def _get_panel_width(self) -> int:
        """The reader's own side-panel width, or ``0`` for the automatic one (FR-078).

        Read once by the shell when the controller arrives, so that a dragged width
        is the width the next session opens with (ADR-022).
        """
        return self._store.panel_width()

    panelWidth = Property(int, _get_panel_width, notify=layoutChanged)

    def _get_toc_items(self) -> list[dict[str, object]]:
        """The book's own rows, and nothing about the reader.

        The highlighted row used to be carried in these dicts as a ``current`` flag,
        which made the list a function of the reading position: every jump produced a
        *different* list, QML was handed a new model, and the view threw away its
        scroll position and started over at the first row (缺陷 25).  Which row is the
        reader's place is a property of the place, so it travels on its own signal -
        `currentTocRow` - and the rows stay a pure projection of `_toc_flat`.  The
        outline panel has worked this way from the start (FR-018).
        """
        return [
            {
                "title": entry.title,
                "level": entry.level,
                "section": section,
                "row": row,
            }
            for row, (entry, section) in enumerate(self._toc_flat)
        ]

    tocItems = Property(list, _get_toc_items, notify=tocChanged)

    def _get_current_toc_row(self) -> int:
        return self._toc_row_at(self._section_index, self._scroll_offset)

    # `tocRowChanged`, not `tocChanged`: the rows above have to stay untouched when
    # the highlight moves, or the view rebuilds its model and loses its place.
    currentTocRow = Property(int, _get_current_toc_row, notify=tocRowChanged)

    def _toc_row_at(self, section_index: int, offset: float) -> int:
        """The contents row the reader is at, or ``-1`` when the place has no row.

        A row is the reader's place when it points at an earlier document, or at the
        current one and no further in than where the reader is.  Comparing documents
        alone was enough while a document could hold only one row; the moment two rows
        share a file - which is what an anchor is for - that rule highlights the row
        *above* the one the reader is in, and the lower row can never be marked at all.

        Only rows of the current document ask for a layout, and that layout is the one
        already on screen, so this costs nothing to ask.
        """
        row_at = -1
        for row, (entry, target) in enumerate(self._toc_flat):
            if target > section_index:
                break
            if target < section_index:
                row_at = row
            elif self._toc_target_offset(entry, target) <= offset + _OFFSET_EPSILON:
                row_at = row
        return row_at

    def _toc_target_offset(self, entry: TocEntry, section_index: int) -> float:
        """Where inside its document a contents row lands, in pixels from its top."""
        if self._engine is None or not entry.fragment:
            return 0.0
        section = self._engine.section(section_index)
        block = section.anchor_block(entry.fragment)
        return 0.0 if block is None else section.block_offset(block)

    # ----------------------------------------------------------- position bar

    def _get_position_path(self) -> list[dict[str, object]]:
        """The levels the reader's place sits under, outermost first (FR-074).

        The bar shows a *path through the book's own tree*, not a file name: one
        document can carry many contents rows - that is what an anchor is for - so the
        file is not the unit a reader navigates by, and calling the whole file one
        name would be wrong everywhere except the top of it.  Taken from the row the
        highlight is on, so the bar and the contents column cannot disagree.
        """
        row = self._position_row()
        if row < 0:
            return []
        return [
            {
                "title": self._toc_flat[index][0].title,
                "level": self._toc_flat[index][0].level,
            }
            for index in self._ancestor_rows(row)
            if self._toc_flat[index][0].title
        ]

    positionPath = Property(list, _get_position_path, notify=tocRowChanged)

    def _get_position_title(self) -> str:
        """Name of the place the reader is in (FR-074).

        The contents row they are in; and where the book's own tree does not reach
        that far - a cover, an imprint page, or above the first anchor of a document
        its tree names - the section's own name (FR-012), the book's title as the last
        resort.  A bar that names nothing would be a band of colour across the top of
        the page.
        """
        row = self._position_row()
        if row >= 0 and self._toc_flat[row][0].title:
            return self._toc_flat[row][0].title
        return self._get_section_title()

    positionTitle = Property(str, _get_position_title, notify=tocRowChanged)

    def _position_row(self) -> int:
        """The contents row the reader's place answers to, or ``-1`` (FR-074).

        The row the highlight is on, and where there is none - the front matter above
        the first row of a book whose tree starts further in - the row that names the
        section itself, so the bar still says which part of the book the reader is in.
        """
        row = self._toc_row_at(self._section_index, self._scroll_offset)
        return row if row >= 0 else self._row_naming_section(self._section_index)

    def _ancestor_rows(self, row: int) -> list[int]:
        """The rows *row* sits under, outermost first (FR-074).

        A row is inside the nearest row above it of a shallower level, which is the
        shape the contents column draws as an indent (FR-017); the walk stops at the
        top level, which no row can be inside.  Levels are the book's own numbers,
        read at build time (ADR-020), so the bar and the column read one structure.
        """
        ancestors: list[int] = []
        level = self._toc_flat[row][0].level
        for index in range(row - 1, -1, -1):
            entry = self._toc_flat[index][0]
            if entry.level < level:
                ancestors.append(index)
                level = entry.level
                if level <= 0:
                    break
        ancestors.reverse()
        return ancestors

    # ----------------------------------------------------------------- outline

    def _get_outline_visible(self) -> bool:
        return self._outline_visible

    outlineVisible = Property(bool, _get_outline_visible, notify=layoutChanged)

    def _get_outline_available(self) -> bool:
        """Whether the current section has anything for the panel to show (FR-019).

        An empty outline column is worse than no column, so QML keeps the panel
        shut while this is false.  It now means "this section has at least one
        heading down to level 3" - the page map it used to fall back on has no
        meaning over a continuous column (ADR-016).
        """
        return bool(self._outline())

    outlineAvailable = Property(bool, _get_outline_available, notify=viewChanged)

    def _get_outline_items(self) -> list[dict[str, object]]:
        return self._outline()

    outlineItems = Property(list, _get_outline_items, notify=viewChanged)

    def _get_current_outline_row(self) -> int:
        """Row of the heading the reader is under, or ``-1`` before the first one.

        Kept apart from the rows themselves so that scrolling only recomputes an
        index instead of rebuilding the model (FR-018).
        """
        active = -1
        for row, item in enumerate(self._outline()):
            offset = item["offset"]
            if isinstance(offset, (int, float)) and float(offset) <= self._scroll_offset:
                active = row
        return active

    currentOutlineRow = Property(int, _get_current_outline_row, notify=viewChanged)

    # ------------------------------------------------------------ book loading

    @Slot(str)
    @Slot(str, int)
    def openBook(self, path: str, restore_section: int = -1) -> bool:
        """Open a book and show the remembered (or first) page (FR-001)."""
        self._error_message = ""
        started = time.perf_counter()
        try:
            book = EpubBook.open(path)
        except BookError as exc:
            self._error_message = str(exc)
            _log.warning("could not open %s: %s", path, exc)
            self.errorOccurred.emit(str(exc))
            self.layoutChanged.emit()
            return False

        # The book being closed still needs its place written - and before its archive
        # is shut, and before the swap, because the place belongs to *that* book.  The
        # drift itself is *not* stopped (FR-094): it is a mode the reader switched, and
        # opening another book does not switch it off; the next tick simply finds the
        # new book and carries on from its remembered place.
        if self._book is not None:
            self._save_state()
            self._book.close()
        self._book = book
        self._window_cache.clear()
        self._engine = LayoutEngine(
            book,
            self._settings,
            PageGeometry.from_settings(self._view_size, self._settings),
            device_ratio=self._device_ratio,
        )
        self._toc_flat = _flatten_toc(book.toc, book)
        self._last_open_ms = (time.perf_counter() - started) * 1000.0

        # The map comes up with the book (FR-013): a reader who does not know that
        # `T` exists should still be able to see that this book has a table of
        # contents.  A book without one keeps the column shut - an empty column is
        # worse than no column, the same rule the outline follows (FR-019).
        self._toc_visible = bool(self._toc_flat)

        state = self._store.book_state(path)
        if restore_section >= 0:
            self._section_index = min(max(0, restore_section), max(0, book.section_count - 1))
            self._scroll_offset = 0.0
        else:
            self._section_index = min(
                max(0, state.section), max(0, book.section_count - 1)
            )
            self._scroll_offset = self._offset_for_state(state)
        self._drop_outline()
        self._startup_ms = (time.perf_counter() - self._startup_started) * 1000.0

        self.bookChanged.emit()
        self.tocChanged.emit()
        # The columns are part of the layout: without this signal QML keeps the
        # table of contents the controller just opened hidden, because `tocVisible`
        # is only re-read when `layoutChanged` arrives.
        self.layoutChanged.emit()
        self._publish_view(set_section=True)
        self.statusMessage.emit(self.statusText)
        _log.info(
            "opened %r: %d sections, %d toc rows, parsed in %.0f ms, first window ready in %.0f ms",
            book.meta.title or book.path.name,
            book.section_count,
            len(self._toc_flat),
            self._last_open_ms,
            self._startup_ms,
        )
        return True

    @Slot()
    def closeBook(self) -> None:
        # First: the drift's last step would otherwise run against a book that is
        # being taken away (FR-094).
        self.stopAutoScroll()
        self._save_state()
        if self._book is not None:
            self._book.close()
        self._book = None
        self._engine = None
        self._window_cache.clear()
        self._scroll_offset = 0.0
        self._toc_flat = []
        self._toc_row = -1
        self._hide_lookup()
        # Columns belong to the book that was open, so they go with it.  The
        # settings drawer is book-independent and stays where the reader left it.
        self._toc_visible = False
        self._outline_visible = False
        self._drop_outline()
        # A passage belongs to the document it was chosen in (FR-070), so the mark goes
        # with the book - and QML has to be *told*, because what it draws from the mark
        # is read on `selectionChanged`: without this the item would go on washing bands
        # over a document that no longer exists.  The clipboard keeps what the reader
        # copied; putting the mark down has never been the same as undoing the copy.
        self._sel_anchor = None
        self._sel_start = 0
        self._sel_end = 0
        self.selectionChanged.emit()
        # There is no window any more.  The signal is not decoration: the outline
        # panel derives its availability from the section, so without it the binding
        # that decides whether the column exists keeps its old answer and the column
        # stays open over a book that has been closed.
        self.viewChanged.emit()
        self.bookChanged.emit()
        self.tocChanged.emit()
        # `tocChanged` no longer carries the highlight, and closing never goes through
        # `_publish_view`, so the answer "-1, there is no row" needs its own word.
        self.tocRowChanged.emit()
        self.layoutChanged.emit()

    def _offset_for_state(self, state: BookState) -> float:
        """Distance the remembered resume point is at in the current layout.

        The triple is a block index plus a fraction of that block's own height
        (ADR-011), which is what makes the place survive a font-size change: the
        block is the same paragraph, and the fraction means the same thing inside it
        - even for a block with no text at all, such as a full-page figure, where a
        character offset would have nothing to point at.
        """
        section = self._current_section()
        if section is None or not section.blocks:
            return 0.0
        block = max(0, min(int(state.block), len(section.blocks) - 1))
        fraction = max(0, min(1000, int(state.char_offset))) / 1000.0
        return section.block_offset(block) + section.block_height(block) * fraction

    # -------------------------------------------------------------- navigation

    @Slot(float)
    def scrollBy(self, distance: float) -> None:
        """Move the window by *distance* pixels; positive scrolls down (FR-073).

        This is what the wheel calls, and it is also what the arrow keys call with
        one line: one action, so the two can never drift apart.
        """
        self._set_offset(self._scroll_offset + float(distance))

    @Slot()
    def scrollUp(self) -> None:
        self.scrollBy(-self.lineStep)

    @Slot()
    def scrollDown(self) -> None:
        self.scrollBy(self.lineStep)

    @Slot()
    def scrollPageUp(self) -> None:
        self.scrollBy(-self._page_step())

    @Slot()
    def scrollPageDown(self) -> None:
        self.scrollBy(self._page_step())

    def _page_step(self) -> float:
        """One screen, less one line.

        The line that was at the top is still at the top afterwards, which is how
        every browser's Page Down behaves: the reader keeps their foothold instead
        of hunting for the line they were on (FR-073).
        """
        return max(1.0, self.viewportHeight - self.lineStep)

    @Slot()
    def scrollToTop(self) -> None:
        self._set_offset(0.0)

    @Slot()
    def scrollToBottom(self) -> None:
        self._set_offset(self.scrollMax)

    @Slot(float)
    def scrollToFraction(self, fraction: float) -> None:
        """Move the window to *fraction* of the scrollable distance (the scroll bar)."""
        self._set_offset(max(0.0, min(1.0, float(fraction))) * self.scrollMax)

    @Slot(float, float)
    def wheelScroll(self, angle_y: float, pixel_y: float) -> None:
        """Scroll by one wheel event, whatever shape the platform sent (FR-063).

        The arithmetic lives here rather than in QML, because the wheel, the keys and
        the menu have to move by the same units (FR-073), and because a test can drive
        this without a window.  QML forwards both deltas and nothing else; which of
        them counts is decided by :func:`_wheel_distance`.

        A phase event - the begin or end of a smooth gesture, where both deltas are
        zero - moves nothing and is ignored rather than repainting the window.
        """
        distance = _wheel_distance(angle_y, pixel_y, self.wheelStep)
        if not distance:
            return
        _log.debug("wheel angle=%.1f pixel=%.1f -> %.1f px", angle_y, pixel_y, distance)
        self.scrollBy(distance)

    @Slot(float, float, result=float)
    def mapWheelDistance(self, angle_y: float, pixel_y: float) -> float:
        """The distance one wheel event scrolls a *map column* by (FR-063 / FR-093).

        Same two shapes, same conversion, a different unit: three rows of the map
        instead of three lines of text.  The map columns hold their own ``contentY``
        (there is no controller state to move - the panels are QML lists, not the
        engine), so this returns the distance and QML applies it; what it must *not* do
        is work it out for itself, because that is where the delta conventions get
        mixed up (ADR-018 ③).  A phase event, with both deltas zero, comes back as
        0.0 and QML then moves nothing.
        """
        return _wheel_distance(angle_y, pixel_y, self.mapWheelStep)

    # ------------------------------------------------------------- auto-scroll

    @Slot()
    def startAutoScroll(self) -> None:
        """Let the text drift upwards by itself, at the chosen pace (FR-094).

        Two reasons to do nothing: no book is open, or it is already drifting (the
        menu row is a toggle, and this one is the state it reads).  A chapter that
        fits on one screen is the third, and it answers out loud - the reader who
        asked not to have to scroll by hand should not be left watching a still page
        and wondering whether the click registered.
        """
        if self._engine is None or self._book is None or self._auto_running:
            return
        if self.scrollMax <= _OFFSET_EPSILON:
            self.statusMessage.emit("本卷只有一屏，无需滚屏")
            return
        self._auto_running = True
        self._auto_pending = 0.0
        # The clock starts here rather than at the last tick, so the first step is
        # one interval long and not however long the reader sat in the menu.
        self._auto_last = time.perf_counter()
        self._auto_timer.start()
        self.autoScrollChanged.emit()
        _log.debug("auto-scroll started, pace=%s", self._auto_pace)

    @Slot()
    def stopAutoScroll(self) -> None:
        """Switch the drift off (FR-094).

        The reader's switch is the only thing that ends it: the menu row and `A` both
        arrive here through :meth:`toggleAutoScroll`, and nothing the reader's hand
        does to the page does - the wheel, the keys, the scroll bar, a pan, a jump, a
        chapter change all just move the place the text drifts *from*.  Three ends of
        the world call it themselves because there is nothing left to drift through:
        closing the book, quitting, and running out of book at the last line
        (:meth:`autoScrollStep`).
        """
        if not self._auto_running:
            return
        self._auto_running = False
        self._auto_timer.stop()
        self._auto_pending = 0.0
        # The reader stopped somewhere, and that is the place to come back to; the
        # periodic saves while it was drifting can be up to `_SAVE_THROTTLE_S` behind
        # - and the reader has just chosen this place, so it is worth a write now.
        self._save_state()
        self.autoScrollChanged.emit()
        _log.debug("auto-scroll stopped at %.1f px", self._scroll_offset)

    @Slot()
    def toggleAutoScroll(self) -> None:
        """On if it is off, off if it is on (the menu row and `A`)."""
        if self._auto_running:
            self.stopAutoScroll()
        else:
            self.startAutoScroll()

    @Slot(str)
    def setAutoScrollPace(self, pace: str) -> None:
        """Choose one of the three paces, and remember it (FR-094).

        Remembered rather than kept for the session: it is a reading preference like
        the panel width, and a reader who wants the slow pace wants it tomorrow too.
        Whether it is *drifting* is not remembered - a book that starts scrolling by
        itself on startup would be a page moving under a reader who never asked.
        """
        if pace not in _AUTO_SCROLL_PACES or pace == self._auto_pace:
            return
        self._auto_pace = pace
        self._store.set_auto_scroll_pace(pace)
        self._store.save()
        # Whatever was carried belonged to the old pace's step and is dropped, so a
        # change of pace takes effect on the next tick and not a fraction late.
        self._auto_pending = 0.0
        self.autoScrollChanged.emit()
        _log.debug("auto-scroll pace=%s", pace)

    @Slot(float)
    def autoScrollStep(self, elapsed: float) -> None:
        """Move the window by one tick's worth, and mind both ends of the book (FR-094).

        A slot of its own, and not hidden inside the timer's callback, because this
        *is* the feature: the timer only ever supplies the time.  A test drives it
        with whole seconds instead of waiting for them, and the one claim nobody can
        check by hand - that a minute of scrolling is a minute's worth of lines - is
        then an assertion rather than a stopwatch.
        """
        if not self._auto_running or self._engine is None or self._book is None:
            return
        # A hand on the page holds the drift still without switching it off (FR-094):
        # a drag recomputes its offset from where it started, so a tick in between
        # would be undone by the next pointer event, and a passage would slide out from
        # under the pointer selecting it.  `_on_auto_tick` has already moved the clock,
        # so the hold costs no catch-up step when the hand comes off.
        if self._drag_origin is not None or self._sel_anchor is not None:
            return
        # A section that fits on one screen has nowhere to drift *to*, and its end is
        # not the end of anything: taking it for the column end would walk the reader
        # off a cover page and into the first chapter, unasked (FR-094).  The reader is
        # told why when they switch the drift on there (:meth:`startAutoScroll`).
        if self.scrollMax <= _OFFSET_EPSILON:
            return
        step = _auto_scroll_step(elapsed, _auto_scroll_lines(self._auto_pace), self.lineStep)
        if step <= 0.0:
            return
        self._auto_pending += step
        # Below the threshold a move would not even repaint (`_set_offset` says so),
        # so the distance waits for the next tick rather than being lost.  At the
        # slow pace that is roughly every fourth tick - four times a second - and it
        # is invisible, which is the point of a drift.
        if self._auto_pending < _OFFSET_EPSILON:
            return
        move, self._auto_pending = self._auto_pending, 0.0
        if self._scroll_offset + move >= self.scrollMax - _OFFSET_EPSILON:
            if self._adjacent_section(1) < 0:
                # The end of the book.  Stop and say so, rather than scrolling
                # against a wall for the rest of the evening.  This is the one end
                # that is not a gesture and not the reader's switch, but the content
                # running out - there is nothing left to drift through.
                self._set_offset(self.scrollMax, remember=False)
                self.stopAutoScroll()
                self.statusMessage.emit("已到全书末尾，自动滚屏已停止")
                return
            # Riding into the next chapter is the whole point: the reader asked not
            # to have to turn pages (FR-094).  The last screenful of the chapter has
            # been in front of them for a while, so nothing goes unread - and the
            # same call keeps the book remembering where they are.  The drift is not
            # touched, so it carries on at the top of the new chapter.
            self._step_section(1)
            return
        self._set_offset(self._scroll_offset + move, remember=False)

    def _on_auto_tick(self) -> None:
        """One timer tick: how long since the last one, and move by that (FR-094)."""
        now = time.perf_counter()
        elapsed = _auto_scroll_elapsed(self._auto_last, now)
        self._auto_last = now
        if elapsed <= 0.0:
            return
        self.autoScrollStep(elapsed)

    def _set_offset(self, offset: float, *, remember: bool = True) -> None:
        """Move the window to *offset*, clamped to the section.

        Deliberately does *not* walk into the next section at the end (FR-074): the
        reader stays at the end of this chapter until they ask to leave, which is
        what a long web page does too.  The auto-scroll is the one caller that says
        otherwise, by stepping the section itself (FR-094).

        Moving the window by hand does *not* end the auto-scroll (FR-094): the drift
        is a mode the reader switches, not a mode any gesture may switch off, so a
        wheel notch under a drifting page simply moves the place it drifts *from*.
        ``remember=False`` is the drift's and the drag's, for the save throttle (see
        :data:`_SAVE_THROTTLE_S`): both arrive as a stream of moves, and neither may
        leave the position on disk behind the position on screen.
        """
        offset = max(0.0, min(float(offset), self.scrollMax))
        if abs(offset - self._scroll_offset) < _OFFSET_EPSILON:
            return
        # The word the pointer was resting on has moved with the page, so the card now
        # points at a place the reader is not pointing at (FR-095).
        self._hide_lookup()
        self._scroll_offset = offset
        self._publish_view(remember=remember)

    @Slot()
    def nextSection(self) -> None:
        self._step_section(1)

    @Slot()
    def previousSection(self) -> None:
        self._step_section(-1)

    @Slot()
    def goToNextSection(self) -> None:
        """Follow the "next chapter" line the window shows at the end (FR-074)."""
        target = self._adjacent_section(1)
        if target >= 0:
            self.goToSection(target)

    def _step_section(self, delta: int) -> None:
        if self._engine is None or self._book is None:
            return
        target = self._adjacent_section(delta)
        if target < 0:
            return
        self._save_state()
        self._section_index = target
        # Going back lands at the end of the previous chapter, which is where a
        # reader who mistook the next one for this one wants to be.
        self._scroll_offset = 0.0 if delta > 0 else self._engine.section(target).max_offset
        self._publish_view(set_section=True)

    @Slot()
    def scrollToSectionTop(self) -> None:
        """Back to the top of the current section (the menu's 篇首 row)."""
        self._set_offset(0.0)

    @Slot()
    def scrollToSectionEnd(self) -> None:
        """Down to the bottom of the current section (the menu's 篇末 row)."""
        self._set_offset(self.scrollMax)

    # ---------------------------------------------------------------- the hand

    @Slot(float)
    def beginDrag(self, y: float) -> None:
        """Take hold of the page, for a hand-pan drag (FR-079).

        Only ``y``: the text runs as one vertical column, so the pointer's horizontal
        position says nothing about where the page should be.
        """
        self._drag_origin = (float(y), self._scroll_offset)
        self._drag_moved = False
        # A press takes the word card down: the reader is about to move the page, and a
        # card that stayed put would be pointing at a line that is going elsewhere
        # (FR-095).
        self._hide_lookup()
        # A gesture is starting, so whatever the last one left behind is not this one's.
        # A double click's release is the case: it must not be told apart from a click by
        # a flag an earlier gesture set (FR-070).
        self._word_taken = False

    @Slot(float)
    def dragTo(self, y: float) -> None:
        """Move the page so the text stays under the pointer (FR-079).

        The offset is measured from where the drag *started*, not from the last move: a
        chain of deltas would accumulate every rounding error along the way, and it would
        walk into the clamp at either end of the section and stay there - the reader
        would have to drag back the distance they overshot before the page moved again.
        A distance from the anchor can do neither.

        The text follows the pointer, so dragging *down* moves the window back *up* the
        section: hence the minus sign.

        ``remember=False`` because a drag is a *stream* of moves - one per pointer
        event, sixty a second at best - and each of them used to write the whole
        configuration file for a position a few pixels further on (FR-094 / ADR-024).
        The throttle in ``_publish_view`` writes one every few seconds during a slow
        drag, and :meth:`endDrag` writes the place the reader let go at.
        """
        if self._drag_origin is None:
            return
        self._drag_moved = True
        origin_y, origin_offset = self._drag_origin
        self._set_offset(origin_offset - (float(y) - origin_y), remember=False)

    @Slot()
    def endDrag(self) -> None:
        """Let go of the page; the offset it reached is the place the reader is at (FR-079).

        A press and release that never moved is not a pan but a click, and a click is how
        the reader puts a marked passage down: the highlight goes, the clipboard keeps its
        text - it was copied when the marking gesture ended, and dismissing the mark is not
        the same as undoing the copy (FR-070).

        A double click is the exception, and it arrives looking exactly like a click: its
        release follows its own press with nothing in between.  The word it took is only
        there because of that press, so the release may not take it away again - which is
        what the flag ``selectWordAt`` leaves behind is for.
        """
        moved, word_taken = self._drag_moved, self._word_taken
        self._drag_origin = None
        self._drag_moved = False
        self._word_taken = False
        if not moved and not word_taken:
            self.clearSelection()
        elif moved:
            # The moves themselves did not write it (`dragTo`), so this is where the
            # place the reader let go at becomes the place to come back to (FR-094).
            self._save_state()

    @Slot(float, float)
    def beginSelection(self, x: float, y: float) -> None:
        """Start a passage at a point on the page, on a Shift press (FR-070).

        Both ends start on the character under the pointer, so a Shift *click* that
        never moves selects nothing yet; the word under the pointer is picked up on
        release (:meth:`endSelection`).  That is what tells a click from a drag without
        a timer, and it is also why one gesture can be both.
        """
        position = self._position_at_page(x, y)
        self._sel_anchor = position
        self._sel_start = position
        self._sel_end = position
        self._hide_lookup()
        self.selectionChanged.emit()

    @Slot(float, float)
    def extendSelection(self, x: float, y: float) -> None:
        """Drag the far end of the passage to a point on the page (FR-070).

        The end being dragged may pass the anchor - selecting upwards is the same
        gesture - so the two positions are kept sorted rather than assumed ordered, and
        what is highlighted never depends on which way the reader dragged.
        """
        if self._sel_anchor is None:
            return
        start, end = sorted((self._sel_anchor, self._position_at_page(x, y)))
        if (start, end) == (self._sel_start, self._sel_end):
            return
        self._sel_start, self._sel_end = start, end
        self.selectionChanged.emit()

    @Slot()
    def endSelection(self) -> None:
        """Finish the gesture: a click picks the word, and the passage is copied (FR-070).

        The copy happens here, on release, rather than on a further key: the reader
        asked for this passage and there is no editor to paste into, so making them
        press something else would be asking for the same thing twice.
        """
        if self._sel_anchor is None:
            return
        if not self.hasSelection:
            self._select_word(self._sel_anchor)
        self._sel_anchor = None
        self.copySelection()

    @Slot(float, float)
    def selectWordAt(self, x: float, y: float) -> None:
        """Select the word under a point and copy it - a double click (FR-070).

        The flag it leaves behind is read by :meth:`endDrag`: Qt sends this on the *press*
        of the second click of a double click, and the release that follows arrives
        indistinguishable from a plain click (see that method).
        """
        self._sel_anchor = None
        self._word_taken = True
        self._select_word(self._position_at_page(x, y))
        self.copySelection()

    @Slot()
    def clearSelection(self) -> None:
        """Drop the passage, leaving the page as it was (FR-070)."""
        self._sel_anchor = None
        if not self.hasSelection:
            return
        self._sel_start = 0
        self._sel_end = 0
        self.selectionChanged.emit()

    @Slot()
    def copySelection(self) -> None:
        """Put the selected passage on the system clipboard, and say so (FR-070).

        ``QGuiApplication.clipboard()`` rather than a ``TextEdit`` hidden in the item
        tree: the passage lives here, and a widget that existed only to hold the
        clipboard would be a second copy of it - in another coordinate system, with its
        own idea of what is selected.

        An empty passage is *not* copied, so ``Ctrl+C`` with nothing selected cannot
        take away whatever the reader had copied somewhere else.
        """
        text = self.selectedText
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:  # a platform plugin without one; not worth an error
            return
        clipboard.setText(text)
        self.statusMessage.emit(f"已复制 {len(text)} 字")

    def _select_word(self, position: int) -> None:
        """Select the word around *position*; an empty word changes nothing."""
        document = self._document()
        if document is None:
            return
        start, end = word_range(document, position)
        if start >= end:  # between two words, or out in the margin
            return
        self._sel_start, self._sel_end = start, end
        self.selectionChanged.emit()

    def _document(self) -> QTextDocument | None:
        """The document of the section on screen, or ``None`` with no book open."""
        section = self._current_section()
        return section.document if section is not None else None

    def _position_at_page(self, x: float, y: float) -> int:
        """Turn a point on the page - what a MouseArea reports - into a position.

        The inverse of the mapping ``selectionRects`` applies: the content margin comes
        off and the scroll offset goes back on, because a point's place *on the page*
        changes as the reader scrolls while its place *in the document* does not.
        """
        document = self._document()
        if document is None:
            return 0
        origin = self._view_geometry().content_origin
        return position_at(
            document,
            float(x) - origin.x(),
            float(y) + self._scroll_offset - origin.y(),
        )

    # ----------------------------------------------------------------- jumping

    @Slot(int)
    @Slot(int, str)
    def goToSection(self, index: int, fragment: str = "") -> None:
        """Jump to a section, optionally at an in-document anchor (FR-014)."""
        if self._engine is None or self._book is None:
            return
        index = max(0, min(index, self._book.section_count - 1))
        self._save_state()
        self._section_index = index
        self._scroll_offset = 0.0
        section = self._engine.section(index)
        if fragment:
            block = section.anchor_block(fragment)
            if block is not None:
                self._scroll_offset = section.block_offset(block)
        self._publish_view(set_section=True)

    @Slot(int)
    def goToTocRow(self, row: int) -> None:
        """Jump to a chapter and leave the map on screen (FR-013).

        The panel used to close here.  That rule came from the time it floated over
        the text and covering the page was the cost - FR-015 - and taking a column
        of its own made it wrong: following the contents through several chapters
        meant reopening the panel at every hop, with no way of seeing where the
        next one led.  FR-015 is superseded by ADR-014 and this is where the
        decision is kept; the outline behaves the same way (FR-018).  `T`, the
        button at the bottom left or `Esc` are the ways out.
        """
        if not 0 <= row < len(self._toc_flat):
            return
        entry, section = self._toc_flat[row]
        # The anchor travels on the entry rather than being parsed back out of the href
        # (FR-014).  Publishers put the *only* pointer to the place a row leads in the
        # fragment, and a row that quietly becomes "top of that file" is a row that
        # disagrees with the book; keeping the two apart is also what lets the href be
        # resolved against the spine while the anchor is resolved against the text.
        #
        # No signal of its own: `goToSection` republishes the window, which announces
        # the new highlight (`tocRowChanged`) - the rows themselves do not move, and it
        # is only the panel's idea of "you are here" that has to follow (缺陷 25).
        self.goToSection(section, entry.fragment)

    @Slot(int)
    def goToOutlineRow(self, row: int) -> None:
        """Jump to a heading of the current section (FR-018).

        Uses the offset the row was built with, which is exact for the layout it was
        built for - and the anchor is a heading, so the window lands with that
        heading at its top rather than somewhere inside the paragraph under it.

        Like the table of contents the panel stays open: it is a map you click
        through several times in a row, and it has a column of its own rather than
        covering the text.
        """
        items = self._outline()
        if not 0 <= row < len(items):
            return
        offset = items[row]["offset"]
        if not isinstance(offset, (int, float)):
            return
        self._set_offset(float(offset))

    @Slot()
    def toggleToc(self) -> None:
        """Show or hide the table of contents - the left column, and nothing else.

        A panel only steps aside for the panel it competes with for room.  The table
        of contents is on the left and has no rival there, so it no longer closes the
        settings drawer, and the drawer no longer closes it: pressing `S` used to
        take the map off the screen, which broke "the contents is always there"
        (FR-013) as soon as a reader changed the type size.
        """
        self._toc_visible = not self._toc_visible
        # A panel covers the page, and a card left on top of it would be a card about a
        # word the reader can no longer see (FR-095).
        self._hide_lookup()
        self.layoutChanged.emit()

    @Slot()
    def toggleOutline(self) -> None:
        """Show or hide the outline column (FR-019).

        Stays shut when there is nothing to fill it with, so the shortcut cannot
        open an empty column.
        """
        if not self._outline_visible and not self._get_outline_available():
            return
        self._outline_visible = not self._outline_visible
        self._hide_lookup()
        if self._outline_visible:
            self._settings_visible = False
        self.layoutChanged.emit()

    @Slot()
    def toggleSettings(self) -> None:
        """Show or hide the settings drawer.

        The drawer floats over the right-hand side, so the one column it yields is
        the outline - the column it shares that side with.  The table of contents is
        on the other side of the text and stays where it is (FR-013).
        """
        self._settings_visible = not self._settings_visible
        self._hide_lookup()
        if self._settings_visible:
            self._outline_visible = False
        self.layoutChanged.emit()

    @Slot()
    def closeSettings(self) -> None:
        """Dismiss the settings drawer, leaving the columns beside the text alone.

        This is what a click beside the drawer means: the reader wants the text
        column back to itself, not for the map to disappear with the drawer.
        """
        if self._settings_visible:
            self._settings_visible = False
            self.layoutChanged.emit()

    @Slot()
    def closePanels(self) -> None:
        """Close every panel at once (``Esc``).

        The one key that puts the window back the way it was: the panels go, and so does
        a selected passage - both are something the reader asked for and neither is part
        of the book (FR-070).  The clipboard keeps what was already copied.
        """
        if self._toc_visible or self._settings_visible or self._outline_visible:
            self._toc_visible = False
            self._settings_visible = False
            self._outline_visible = False
            self.layoutChanged.emit()
        self._hide_lookup()
        self.clearSelection()

    # ------------------------------------------------------------- view changes

    @Slot(int, int, float)
    def setViewSize(self, width: int, height: int, device_ratio: float = 1.0) -> None:
        """React to a window resize, keeping the reader's place (FR-051)."""
        size = QSizeF(max(120.0, float(width)), max(120.0, float(height)))
        ratio = device_ratio if device_ratio and device_ratio > 0 else 1.0
        if size == self._view_size and abs(ratio - self._device_ratio) < 1e-6:
            return
        self._view_size = size
        self._device_ratio = ratio
        # A re-layout moves every word on the page, so the card's anchor would be stale
        # and its word a different one (FR-095).
        self._hide_lookup()
        if self._engine is None:
            return

        anchor = self._anchor_block()
        self._window_cache.clear()
        self._drop_outline()
        self._engine.set_device_ratio(ratio)
        if self._engine.set_geometry(PageGeometry.from_settings(size, self._settings)):
            self._restore_block(anchor)
        self._publish_view(set_section=True)

    def _view_geometry(self) -> PageGeometry:
        """The box the text is laid out and rendered for.

        Before a book is open there is no engine yet, but QML still asks for the
        viewport height and the page size to size itself, so the same geometry is
        derived from the view size the window reported.
        """
        if self._engine is not None:
            return self._engine.geometry
        return PageGeometry.from_settings(self._view_size, self._settings)

    def _current_section(self) -> LaidOutSection | None:
        """The laid-out current section, or ``None`` when there is nothing to show."""
        if self._engine is None:
            return None
        try:
            return self._engine.section(self._section_index)
        except (BookError, IndexError):
            return None

    def _anchor_block(self) -> int | None:
        """Block at the top of the window: the place to come back to (ADR-011)."""
        section = self._current_section()
        if section is None:
            return None
        return section.block_at_offset(self._scroll_offset)

    def _restore_block(self, block: int | None) -> None:
        """Put the window back on *block* after the text was laid out again."""
        section = self._current_section()
        if section is None or block is None:
            return
        self._scroll_offset = section.block_offset(block)

    def _drop_outline(self) -> None:
        """Forget the outline rows; the layout moved, so their offsets are stale."""
        self._outline_key = None

    def _outline(self) -> list[dict[str, object]]:
        """Rows for the outline panel (FR-018).

        Memoised because one scroll step asks two or three times - the panel's model,
        the window's availability check and the highlight - and the rows carry
        offsets, so they only hold for one layout.  Rebuilding is cheap either way:
        0.8 ms for a 49-row outline over a 445-block section.
        """
        section = self._current_section()
        if section is None or self._book is None:
            return []
        key = (
            self._book.path,
            self._section_index,
            len(section.blocks),
            round(section.height, 1),
        )
        if self._outline_key != key:
            self._outline_cache = _build_outline(section)
            self._outline_key = key
        return self._outline_cache

    # --------------------------------------------------------- settings actions

    def _apply_settings(self, changes: dict[str, object]) -> None:
        """Persist a settings change and make the reader's view agree with it.

        Which settings live in the parsed text (kinsoku), which live in the laid-out
        documents (type, margins, theme) and which merely repaint is knowledge that
        belongs to the typesetting layer, so the controller hands the new settings over
        and the engine answers whether the layout is due (缺陷 29).
        """
        self._settings = self._settings.with_(**changes)
        self._store.set_settings(self._settings)
        self._store.save()
        if self._engine is None:
            self.settingsChanged.emit()
            return
        anchor = self._anchor_block()
        self._window_cache.clear()
        self._drop_outline()
        if self._engine.set_settings(self._settings):
            self._restore_block(anchor)
        self.settingsChanged.emit()
        self._publish_view(set_section=True)

    @Slot(float)
    def setFontSize(self, size: float) -> None:
        self._apply_settings({"font_size": float(size)})

    @Slot()
    def increaseFont(self) -> None:
        self.setFontSize(self._settings.font_size + _FONT_SIZE_STEP)

    @Slot()
    def decreaseFont(self) -> None:
        self.setFontSize(self._settings.font_size - _FONT_SIZE_STEP)

    @Slot(float)
    def setLineHeight(self, value: float) -> None:
        self._apply_settings({"line_height": float(value)})

    @Slot()
    def increaseLineHeight(self) -> None:
        self.setLineHeight(self._settings.line_height + _LINE_HEIGHT_STEP)

    @Slot()
    def decreaseLineHeight(self) -> None:
        self.setLineHeight(self._settings.line_height - _LINE_HEIGHT_STEP)

    @Slot(str)
    def setFontChoice(self, choice: str) -> None:
        try:
            self._apply_settings({"font_choice": FontChoice(choice)})
        except ValueError:
            pass

    @Slot()
    def cycleFontChoice(self) -> None:
        order = [FontChoice.SERIF, FontChoice.SANS, FontChoice.KAI]
        index = order.index(self._settings.font_choice)
        self._apply_settings({"font_choice": order[(index + 1) % len(order)]})

    @Slot(str)
    def setTheme(self, theme: str) -> None:
        try:
            self._apply_settings({"theme": Theme(theme)})
        except ValueError:
            pass

    @Slot()
    def cycleTheme(self) -> None:
        order = [Theme.LIGHT, Theme.SEPIA, Theme.DARK]
        index = order.index(self._settings.theme)
        self._apply_settings({"theme": order[(index + 1) % len(order)]})

    @Slot()
    def toggleJustify(self) -> None:
        self._apply_settings({"justify": not self._settings.justify})

    @Slot(int)
    def setMargin(self, margin: int) -> None:
        margin = max(24, min(240, int(margin)))
        self._apply_settings(
            {
                "margin_left": margin,
                "margin_right": margin,
                "margin_top": max(24, int(margin * 0.7)),
                "margin_bottom": max(24, int(margin * 0.7)),
            }
        )

    @Slot()
    def increaseMargin(self) -> None:
        self.setMargin(self._settings.margin_left + _MARGIN_STEP)

    @Slot()
    def decreaseMargin(self) -> None:
        self.setMargin(self._settings.margin_left - _MARGIN_STEP)

    @Slot(bool)
    def setKinsoku(self, enabled: bool) -> None:
        if enabled == self._settings.kinsoku:
            return
        self._apply_settings({"kinsoku": enabled})

    # --------------------------------------------------------------- bookkeeping

    def _overall_percent(self) -> float:
        """Progress across the whole book, weighted by section height.

        Only the current and the first section are measured: laying the whole book
        out just to draw a progress bar would defeat lazy layout (ADR-003).  The two
        measured heights are a poor average for a book whose chapters differ by a
        factor of forty, which is why the status bar shows it as an estimate beside
        the current section's own, exact percentage (FR-072).
        """
        if self._engine is None or self._book is None or self._book.section_count == 0:
            return 0.0
        sections = self._book.section_count
        measured = [self._engine.section(i).height for i in {0, self._section_index}]
        average = max(1.0, sum(measured) / len(measured))
        done = self._section_index * average + self._scroll_offset
        return max(0.0, min(100.0, done / (average * sections) * 100.0))

    def _publish_view(self, *, set_section: bool = False, remember: bool = True) -> None:
        if self._engine is not None:
            stats = self._engine.last_stats
            self._last_section_ms = stats.build_ms if stats else 0.0
            self._engine.prefetch(self._section_index, self._scroll_offset)
        if set_section:
            # A passage belongs to the document it was chosen in (FR-070).  Dropped
            # without a word - and without touching the clipboard, which holds what the
            # reader already copied.
            self._sel_anchor = None
            self._sel_start = 0
            self._sel_end = 0
            # The word card belongs to a document too, and to a place in it (FR-095).
            self._hide_lookup()
        self.viewChanged.emit()
        # The bands of a selection are drawn *on the page*, so moving the window moves
        # them: QML rebinds `selectionRects` from this signal too (FR-070).
        self.selectionChanged.emit()
        # Which row the panel highlights is a property of the place, not of the
        # document - a document can hold several rows (FR-013).  It is announced on a
        # signal of its own, so that moving it cannot republish the rows: while the two
        # travelled together, every jump handed QML a new model and the panel fell back
        # to its first row (缺陷 25).  And only when it *changes*: `_toc_row_at` walks
        # the rows of the current document and asks the section for their anchors, which
        # is not work to redo on every wheel notch (ADR-017 ④).  `viewChanged` would do
        # exactly that, which is why the highlight does not ride on it.
        row = self._get_current_toc_row()
        if set_section or row != self._toc_row:
            self._toc_row = row
            self.tocRowChanged.emit()
        # Remembering the place on every move is what makes a wheel notch, a jump or
        # a resize come back after a restart, and it is a file write each time - which
        # a reader's own move is worth.  A *stream* of moves is not (FR-094): the
        # auto-scroll's twenty a second and the drag's one per pointer event both pass
        # ``remember=False``, and are written every `_SAVE_THROTTLE_S` seconds instead
        # plus one final write when the gesture ends.
        if remember or time.perf_counter() - self._saved_at >= _SAVE_THROTTLE_S:
            self._save_state()

    def _save_state(self) -> None:
        """Remember the resume point: opening block plus a fraction of its height."""
        if self._book is None or self._engine is None:
            return
        section = self._current_section()
        if section is None:
            return
        self._store.set_book_state(
            self._book.path,
            BookState(
                section=self._section_index,
                block=section.block_at_offset(self._scroll_offset),
                char_offset=self._fraction_within_block(section),
                opened_at=time.time(),
            ),
        )
        self._store.save()
        self._saved_at = time.perf_counter()

    def _fraction_within_block(self, section: LaidOutSection) -> int:
        """How far into its opening block the window starts, in per mille.

        ``char_offset`` is the field the resume triple has always had (ADR-011) and
        a fraction of the block's height is what it now holds.  A distance is the
        right second coordinate for a continuous column: the reader can stop with a
        900 px figure filling the window, where no character of the block it belongs
        to is on screen at all, and a character index would have nothing to point
        at.  A fraction means the same thing inside the same paragraph whatever the
        type size is.
        """
        index = section.block_at_offset(self._scroll_offset)
        top = section.block_offset(index)
        height = section.block_height(index)
        if height <= 0.0:
            return 0
        return max(0, min(1000, round((self._scroll_offset - top) / height * 1000.0)))

    @Slot(int, int)
    def saveWindow(self, width: int, height: int) -> None:
        self._store.set_window(width, height)
        self._store.save()

    @Slot(int)
    def savePanelWidth(self, width: int) -> None:
        """Persist the side-panel width the reader dragged to (FR-078 / ADR-022).

        Called once per drag, on release - not per mouse move: the width is a
        preference, and writing the configuration file sixty times a second while the
        handle is held would be a disk write per event for no gain.  ``0`` means the
        reader asked for the automatic width back (a double click on the handle).
        """
        self._store.set_panel_width(width)
        self._store.save()

    @Slot()
    def shutdown(self) -> None:
        """Persist everything before the process exits."""
        # First the drift, then the place: `stopAutoScroll` writes the position the
        # reader was stopped at, which is the one that matters (FR-094).
        self.stopAutoScroll()
        self._hide_lookup()
        self._save_state()
        self._store.save()
        # The dictionary file holds a handle until the process ends; closing it here
        # keeps the file free for a rebuild without waiting for exit (ADR-026).
        self._lexicon.close()


def _flatten_toc(entries: tuple[TocEntry, ...], book: EpubBook) -> list[tuple[TocEntry, int]]:
    """Flatten the navigation tree, resolving each entry to a spine index.

    Entries pointing outside the spine are dropped rather than shown as dead
    links.  A table of contents is already a legitimate place to resolve hrefs,
    so this needs no extra parsing.
    """
    flat: list[tuple[TocEntry, int]] = []

    def walk(nodes: tuple[TocEntry, ...]) -> None:
        for node in nodes:
            if node.href:
                index = book.section_index_for_href(node.href)
                if index >= 0:
                    flat.append((node, index))
            walk(node.children)

    walk(entries)
    return flat




