"""``ReaderController``: the only surface QML talks to.

Holds all mutable application state (per C-013) and orchestrates the layers:
opening books, moving through pages, reacting to window resizes, and persisting
the reading position.  It contains no parsing and no typesetting logic - those
live behind :class:`~ebook_reader.typeset.engine.LayoutEngine`.
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
from ..typeset import FontChoice, LayoutEngine, Theme, TypographySettings
from ..typeset.settings import PageGeometry
from .settings_store import BookState, SettingsStore

__all__ = ["ReaderController"]

_log = logging.getLogger(__name__)

#: Rendered pages kept in memory.  A page image is a few megabytes, and keeping
#: the previous page makes backwards turns instant; more than a handful would
#: work against the memory budget (ADR-008 / NFR-002).
_PAGE_CACHE_SIZE = 5

#: How many pixels the margin control adds or removes per step.
_MARGIN_STEP = 12

_FONT_SIZE_STEP = 1.0
_LINE_HEIGHT_STEP = 0.05


class ReaderController(QObject):
    """State machine behind the QML user interface."""

    pageChanged = Signal()
    layoutChanged = Signal()
    settingsChanged = Signal()
    bookChanged = Signal()
    tocChanged = Signal()
    errorOccurred = Signal(str)
    statusMessage = Signal(str)

    def __init__(self, parent: QObject | None = None, store: SettingsStore | None = None) -> None:
        super().__init__(parent)
        self._store = store or SettingsStore()
        self._settings: TypographySettings = self._store.settings()
        self._book: EpubBook | None = None
        self._engine: LayoutEngine | None = None
        self._page_cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._section_index = 0
        self._page_index = 0
        self._view_size = QSizeF(900.0, 1300.0)
        self._device_ratio = 1.0
        self._toc_flat: list[tuple[TocEntry, int]] = []
        self._toc_visible = False
        self._settings_visible = False
        self._error_message = ""
        self._last_open_ms = 0.0
        self._last_section_ms = 0.0
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

    def _get_page_image(self) -> QImage:
        if self._engine is None:
            return QImage()
        key = (self._section_index, self._page_index)
        image = self._page_cache.get(key)
        if image is None:
            image = self._engine.render(*key)
            self._page_cache[key] = image
            while len(self._page_cache) > _PAGE_CACHE_SIZE:
                self._page_cache.popitem(last=False)
        return image

    pageImage = Property(QImage, _get_page_image, notify=pageChanged)

    def _get_page_index(self) -> int:
        return self._page_index

    pageIndex = Property(int, _get_page_index, notify=pageChanged)

    def _get_page_count(self) -> int:
        return self._section_page_count()

    pageCount = Property(int, _get_page_count, notify=pageChanged)

    def _get_section_index(self) -> int:
        return self._section_index

    sectionIndex = Property(int, _get_section_index, notify=pageChanged)

    def _get_section_count(self) -> int:
        return self._book.section_count if self._book else 0

    sectionCount = Property(int, _get_section_count, notify=bookChanged)

    def _get_section_title(self) -> str:
        for entry, section in self._toc_flat:
            if section == self._section_index and entry.title:
                return entry.title
        return self.bookTitle

    sectionTitle = Property(str, _get_section_title, notify=pageChanged)

    def _get_progress_text(self) -> str:
        if self._book is None:
            return ""
        return (
            f"第 {self._page_index + 1} / {self._section_page_count()} 页"
            f"   ·   全书 {self._overall_percent():.0f}%"
        )

    progressText = Property(str, _get_progress_text, notify=pageChanged)

    def _get_progress(self) -> float:
        return self._overall_percent() / 100.0

    progress = Property(float, _get_progress, notify=pageChanged)

    def _get_status_text(self) -> str:
        if self._error_message:
            return self._error_message
        if self._engine is None:
            return ""
        stats = self._engine.last_stats
        cache = self._engine.image_stats()
        parts = [f"打开 {self._last_open_ms:.0f} ms", f"排版 {self._last_section_ms:.0f} ms"]
        if stats is not None:
            parts.append(f"{stats.blocks} 块 / {stats.pages} 页")
            parts.append(f"分页修正 {max(0, stats.iterations - 1)} 次")
        parts.append(f"图片 {cache.items} 张 / {cache.bytes / 1e6:.1f} MB")
        return "   ·   ".join(parts)

    statusText = Property(str, _get_status_text, notify=pageChanged)

    def _get_startup_ms(self) -> float:
        """Wall-clock time from process start to the first page being ready.

        This is the number NFR-001 is about, so it is a first-class property
        rather than something only visible in a profiler.
        """
        return self._startup_ms

    startupMs = Property(float, _get_startup_ms, notify=pageChanged)

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

    # ------------------------------------------------------------------ panels

    def _get_toc_visible(self) -> bool:
        return self._toc_visible

    tocVisible = Property(bool, _get_toc_visible, notify=layoutChanged)

    def _get_settings_visible(self) -> bool:
        return self._settings_visible

    settingsVisible = Property(bool, _get_settings_visible, notify=layoutChanged)

    def _get_toc_items(self) -> list[dict[str, object]]:
        return [
            {
                "title": entry.title,
                "level": entry.level,
                "section": section,
                "row": row,
                "current": section == self._section_index,
            }
            for row, (entry, section) in enumerate(self._toc_flat)
        ]

    tocItems = Property(list, _get_toc_items, notify=tocChanged)

    def _get_current_toc_row(self) -> int:
        for row, (_, section) in enumerate(self._toc_flat):
            if section == self._section_index:
                return row
        return -1

    currentTocRow = Property(int, _get_current_toc_row, notify=tocChanged)

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
        self._page_cache.clear()
        self._engine = LayoutEngine(
            book,
            self._settings,
            PageGeometry.from_settings(self._view_size, self._settings),
            device_ratio=self._device_ratio,
        )
        self._toc_flat = _flatten_toc(book.toc, book)
        self._last_open_ms = (time.perf_counter() - started) * 1000.0

        state = self._store.book_state(path)
        if restore_section >= 0:
            self._section_index = min(max(0, restore_section), max(0, book.section_count - 1))
            self._page_index = 0
        else:
            self._section_index = min(
                max(0, state.section), max(0, book.section_count - 1)
            )
            self._page_index = self._page_for_state(state)
        self._startup_ms = (time.perf_counter() - self._startup_started) * 1000.0

        self.bookChanged.emit()
        self.tocChanged.emit()
        self._publish_page(set_section=True)
        self.statusMessage.emit(self.statusText)
        _log.info(
            "opened %r: %d sections, %d toc rows, parsed in %.0f ms, first page ready in %.0f ms",
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
        self._page_cache.clear()
        self._toc_flat = []
        self.bookChanged.emit()
        self.tocChanged.emit()
        self.layoutChanged.emit()

    def _page_for_state(self, state: BookState) -> int:
        if self._engine is None:
            return 0
        try:
            section = self._engine.section(state.section)
        except (BookError, IndexError):
            return 0
        return section.block_page(state.block)

    # -------------------------------------------------------------- navigation

    @Slot()
    def nextPage(self) -> None:
        if self._engine is None:
            return
        if self._page_index + 1 < self._section_page_count():
            self._page_index += 1
            self._publish_page()
            return
        self._step_section(1)

    @Slot()
    def previousPage(self) -> None:
        if self._engine is None:
            return
        if self._page_index > 0:
            self._page_index -= 1
            self._publish_page()
            return
        self._step_section(-1)

    @Slot()
    def nextSection(self) -> None:
        self._step_section(1)

    @Slot()
    def previousSection(self) -> None:
        self._step_section(-1)

    def _step_section(self, delta: int) -> None:
        if self._engine is None or self._book is None:
            return
        target = self._section_index + delta
        if not 0 <= target < self._book.section_count:
            return
        self._save_state()
        self._section_index = target
        # Going back should land on the last page of the previous section, which
        # is what a reader expects from a physical book.
        self._page_index = (
            0 if delta > 0 else max(0, self._engine.section(target).page_count - 1)
        )
        self._publish_page(set_section=True)

    @Slot()
    def firstPage(self) -> None:
        if self._page_index == 0:
            return
        self._page_index = 0
        self._publish_page()

    @Slot()
    def lastPage(self) -> None:
        last = max(0, self._section_page_count() - 1)
        if self._page_index == last:
            return
        self._page_index = last
        self._publish_page()

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
        self._page_index = 0
        section = self._engine.section(index)
        if fragment:
            block = section.anchor_block(fragment)
            if block is not None:
                self._page_index = section.block_page(block)
        self._publish_page(set_section=True)

    @Slot(int)
    def goToTocRow(self, row: int) -> None:
        if not 0 <= row < len(self._toc_flat):
            return
        entry, section = self._toc_flat[row]
        fragment = entry.href.partition("#")[2] if "#" in entry.href else ""
        self.goToSection(section, fragment)
        self._toc_visible = False
        self.layoutChanged.emit()

    @Slot()
    def toggleToc(self) -> None:
        self._toc_visible = not self._toc_visible
        if self._toc_visible:
            self._settings_visible = False
        self.layoutChanged.emit()

    @Slot()
    def toggleSettings(self) -> None:
        self._settings_visible = not self._settings_visible
        if self._settings_visible:
            self._toc_visible = False
        self.layoutChanged.emit()

    @Slot()
    def closePanels(self) -> None:
        if self._toc_visible or self._settings_visible:
            self._toc_visible = False
            self._settings_visible = False
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

        anchor = self._current_anchor_block()
        self._page_cache.clear()
        self._engine.set_device_ratio(ratio)
        if self._engine.set_geometry(PageGeometry.from_settings(size, self._settings)) and anchor is not None:
            self._page_index = self._engine.section(self._section_index).block_page(anchor)
        self._publish_page(set_section=True)

    def _current_anchor_block(self) -> int | None:
        if self._engine is None:
            return None
        try:
            return self._engine.section(self._section_index).page_start_block(self._page_index)
        except (BookError, IndexError):
            return None

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
        anchor = self._current_anchor_block()
        self._page_cache.clear()
        if self._engine.set_settings(self._settings) and anchor is not None:
            self._page_index = self._engine.section(self._section_index).block_page(anchor)
        self.settingsChanged.emit()
        self._publish_page(set_section=True)

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

    def _section_page_count(self) -> int:
        if self._engine is None:
            return 0
        try:
            return self._engine.section(self._section_index).page_count
        except (BookError, IndexError):
            return 0

    def _overall_percent(self) -> float:
        """Progress across the whole book, weighted by page counts.

        Only the current and the first section are measured: laying the whole book
        out just to draw a progress bar would defeat lazy layout (ADR-003).
        """
        if self._engine is None or self._book is None or self._book.section_count == 0:
            return 0.0
        sections = self._book.section_count
        measured = [self._engine.section(i).page_count for i in {0, self._section_index}]
        average = sum(measured) / len(measured)
        done = self._section_index * average + self._page_index
        return max(0.0, min(100.0, done / max(1.0, average * sections) * 100.0))

    def _publish_page(self, *, set_section: bool = False) -> None:
        if self._engine is not None:
            stats = self._engine.last_stats
            self._last_section_ms = stats.build_ms if stats else 0.0
            self._engine.prefetch(self._section_index, self._page_index + 1)
        self.pageChanged.emit()
        if set_section:
            self.tocChanged.emit()
        self._save_state()

    def _save_state(self) -> None:
        if self._book is None or self._engine is None:
            return
        try:
            section = self._engine.section(self._section_index)
        except (BookError, IndexError):
            return
        self._store.set_book_state(
            self._book.path,
            BookState(
                section=self._section_index,
                block=section.page_start_block(self._page_index),
                opened_at=time.time(),
            ),
        )
        self._store.save()

    @Slot(int, int)
    def saveWindow(self, width: int, height: int) -> None:
        self._store.set_window(width, height)
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




