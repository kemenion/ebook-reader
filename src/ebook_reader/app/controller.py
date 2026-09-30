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

from PySide6.QtCore import Property, QObject, QSizeF, Signal, Slot
from PySide6.QtGui import QColor, QImage

from ..domain import BookError, EpubBook
from ..domain.models import TocEntry
from ..typeset import (
    FontChoice,
    LayoutEngine,
    LaidOutSection,
    Theme,
    TypographySettings,
    quantise_offset,
)
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
    """State machine behind the QML user interface."""

    viewChanged = Signal()
    layoutChanged = Signal()
    settingsChanged = Signal()
    bookChanged = Signal()
    tocChanged = Signal()
    tocRowChanged = Signal()
    errorOccurred = Signal(str)
    statusMessage = Signal(str)

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

        if self._book is not None:
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
        self._save_state()
        if self._book is not None:
            self._book.close()
        self._book = None
        self._engine = None
        self._window_cache.clear()
        self._scroll_offset = 0.0
        self._toc_flat = []
        self._toc_row = -1
        # Columns belong to the book that was open, so they go with it.  The
        # settings drawer is book-independent and stays where the reader left it.
        self._toc_visible = False
        self._outline_visible = False
        self._drop_outline()
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

    def _set_offset(self, offset: float) -> None:
        """Move the window to *offset*, clamped to the section.

        Deliberately does *not* walk into the next section at the end (FR-074): the
        reader stays at the end of this chapter until they ask to leave, which is
        what a long web page does too.
        """
        offset = max(0.0, min(float(offset), self.scrollMax))
        if abs(offset - self._scroll_offset) < _OFFSET_EPSILON:
            return
        self._scroll_offset = offset
        self._publish_view()

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
        """Close every panel at once (``Esc``)."""
        if self._toc_visible or self._settings_visible or self._outline_visible:
            self._toc_visible = False
            self._settings_visible = False
            self._outline_visible = False
            self.layoutChanged.emit()

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

    def _apply_settings(self, changes: dict[str, object], *, needs_reparse: bool = False) -> None:
        self._settings = self._settings.with_(**changes)
        self._store.set_settings(self._settings)
        self._store.save()
        if self._engine is None:
            self.settingsChanged.emit()
            return
        if needs_reparse and self._book is not None:
            # kinsoku is baked into the parsed text, so the block cache must go.
            self._book.drop_blocks_cache()
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
        self._apply_settings({"kinsoku": enabled}, needs_reparse=True)

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

    def _publish_view(self, *, set_section: bool = False) -> None:
        if self._engine is not None:
            stats = self._engine.last_stats
            self._last_section_ms = stats.build_ms if stats else 0.0
            self._engine.prefetch(self._section_index, self._scroll_offset)
        self.viewChanged.emit()
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
        self._save_state()
        self._store.save()


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




