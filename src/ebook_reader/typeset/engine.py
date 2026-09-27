"""Layout engine: the single façade the application layer talks to.

Responsibilities (and nothing else):

* own the per-section LRU of laid-out documents (ADR-003);
* rebuild everything when the viewport geometry or the typography changes;
* answer "how tall is section N", "where does block B start", "which block sits at
  distance Y", "give me the viewport at distance O as an image".

Parsing stays in the domain layer, and neither the UI nor the controller needs to
know that ``QTextDocument`` exists.

A section is laid out as **one continuous column** (ADR-016): the document is set
to the width of the text column with no height limit, Qt's pagination is never
used, and the reading position is a distance from the top of the column.  That is
what makes the reader scroll through a chapter the way a browser scrolls through a
page; block offsets still come from Qt's own layout, so the mapping between a
distance and a block index stays exact (ADR-011).
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QImage, QTextDocument

from ..domain.epub.book import EpubBook
from ..domain.models import Block
from .document import build_document, layout_continuous
from .images import ImageCache, ImageCacheStats
from .renderer import WINDOW_QUANTUM, quantise_offset, render_window
from .settings import PageGeometry, TypographySettings
from .style import StyleSet

__all__ = [
    "DEFAULT_SECTION_CACHE",
    "WINDOW_QUANTUM",
    "LaidOutSection",
    "LayoutEngine",
    "SectionStats",
    "make_geometry",
    "quantise_offset",
]

_log = logging.getLogger(__name__)

#: How many laid-out sections stay resident.  Each holds a QTextDocument plus the
#: images Qt keeps for it, so three hides section changes without inflating memory
#: (ADR-003 / NFR-002).
DEFAULT_SECTION_CACHE = 3


@dataclass(slots=True)
class LaidOutSection:
    """One section, laid out as a single continuous column (ADR-016).

    There is no page grid any more.  The document knows one height, and every
    navigation question - which block a distance lands in, where a block starts,
    how far the reader may scroll - is answered from the block boxes Qt computed
    while laying the column out.  ``height`` is the height of that column.
    """

    index: int
    blocks: tuple[Block, ...]
    document: QTextDocument
    height: float
    geometry: PageGeometry
    build_ms: float
    anchor_blocks: dict[str, int] = field(default_factory=dict)
    _block_tops: list[float] | None = None
    _block_heights: list[float] | None = None

    # ------------------------------------------------------------- navigation

    @property
    def max_offset(self) -> float:
        """Furthest the viewport may scroll and still show text.

        Past this point scrolling would only drag the last line up out of the
        content box and leave blank margin behind, which is not what "the end of
        the chapter" means to a reader.  A section shorter than one viewport never
        scrolls at all.
        """
        return max(0.0, self.height - self.geometry.content_height)

    def block_offset(self, block_index: int) -> float:
        """Distance from the top of the column to *block_index*."""
        tops = self._ensure_tops()
        if not tops:
            return 0.0
        block_index = max(0, min(block_index, len(tops) - 1))
        return max(0.0, min(tops[block_index], self.max_offset))

    def block_height(self, block_index: int) -> float:
        """Height of one block; the second coordinate of a resume point."""
        self._ensure_tops()
        heights = self._block_heights or []
        if not heights:
            return 0.0
        block_index = max(0, min(block_index, len(heights) - 1))
        return max(0.0, heights[block_index])

    def block_at_offset(self, offset: float) -> int:
        """Index of the last block that starts at or before *offset*."""
        tops = self._ensure_tops()
        if not tops:
            return 0
        low, high, result = 0, len(tops) - 1, 0
        while low <= high:
            middle = (low + high) // 2
            if tops[middle] <= offset:
                result = middle
                low = middle + 1
            else:
                high = middle - 1
        return result

    def anchor_block(self, fragment: str) -> int | None:
        """Block index owning an anchor id, or ``None``."""
        return self.anchor_blocks.get(fragment)

    def _ensure_tops(self) -> list[float]:
        if self._block_tops is None:
            layout = self.document.documentLayout()
            tops: list[float] = []
            heights: list[float] = []
            for index in range(len(self.blocks)):
                text_block = self.document.findBlockByNumber(index)
                rect = (
                    layout.blockBoundingRect(text_block) if text_block.isValid() else QRectF()
                )
                tops.append(rect.top())
                heights.append(rect.height())
            self._block_tops = tops
            self._block_heights = heights
        return self._block_tops

    def invalidate_geometry(self) -> None:
        self._block_tops = None
        self._block_heights = None


@dataclass(frozen=True, slots=True)
class SectionStats:
    """Per-section diagnostics, surfaced in the status bar and in tests."""

    index: int
    blocks: int
    height: float
    build_ms: float


class LayoutEngine:
    """Owns layout state and caches for one book at one configuration."""

    def __init__(
        self,
        book: EpubBook,
        settings: TypographySettings,
        geometry: PageGeometry,
        *,
        device_ratio: float = 1.0,
        section_cache_size: int = DEFAULT_SECTION_CACHE,
        image_cache: ImageCache | None = None,
    ) -> None:
        self._book = book
        self._settings = settings
        self._geometry = geometry
        self._device_ratio = device_ratio if device_ratio and device_ratio > 0 else 1.0
        self._section_cache_size = max(1, section_cache_size)
        self._image_cache = image_cache or ImageCache(
            book.resource,
            # Item count is a backstop; the byte budget is the real limit.  A
            # section may reference dozens of images, and an LRU smaller than the
            # working set thrashes: with 28 images in a section and room for 24,
            # every access missed because the item evicted was always the next one
            # needed.  The byte cap keeps memory honest either way (NFR-007).
            max_items=64,
            max_bytes=32 * 1024 * 1024,
            device_ratio=self._device_ratio,
        )
        self._style = StyleSet(settings, geometry.content_size)
        self._sections: OrderedDict[int, LaidOutSection] = OrderedDict()
        self._last_stats: SectionStats | None = None

    # ------------------------------------------------------------- properties

    @property
    def book(self) -> EpubBook:
        return self._book

    @property
    def settings(self) -> TypographySettings:
        return self._settings

    @property
    def geometry(self) -> PageGeometry:
        return self._geometry

    @property
    def image_cache(self) -> ImageCache:
        return self._image_cache

    @property
    def last_stats(self) -> SectionStats | None:
        return self._last_stats

    @property
    def line_step(self) -> float:
        """Height of one line of body text (FR-073).

        The controller scrolls by this and the wheel scrolls by a few of them, so
        it has to be the height Qt actually laid out rather than a constant.
        """
        return self._style.line_step()

    def image_stats(self) -> ImageCacheStats:
        return self._image_cache.stats()

    # --------------------------------------------------------------- mutation

    def set_geometry(self, geometry: PageGeometry) -> bool:
        """Change the page box; returns ``True`` when a relayout is required."""
        if geometry == self._geometry:
            return False
        self._geometry = geometry
        self._style = StyleSet(self._settings, geometry.content_size)
        self._sections.clear()
        return True

    def set_settings(self, settings: TypographySettings) -> bool:
        """Change typography and/or margins; returns ``True`` if a relayout is due.

        Margins live in the page box rather than in the text formats, so a margin
        change is turned into a geometry change here; the rest of the system only
        ever has to deal with "geometry changed, redo the layout".
        """
        previous = self._settings
        old_margins = (
            previous.margin_top,
            previous.margin_right,
            previous.margin_bottom,
            previous.margin_left,
        )
        new_margins = (
            settings.margin_top,
            settings.margin_right,
            settings.margin_bottom,
            settings.margin_left,
        )
        typography_changed = (
            settings.font_size != previous.font_size
            or settings.font_choice is not previous.font_choice
            or settings.line_height != previous.line_height
            or settings.paragraph_spacing_em != previous.paragraph_spacing_em
            or settings.first_line_indent_em != previous.first_line_indent_em
            or settings.justify != previous.justify
        )
        self._settings = settings
        if not typography_changed and old_margins == new_margins:
            # Only the theme (or kinsoku) changed, so no relayout is required.
            return False
        if old_margins != new_margins:
            self._geometry = PageGeometry.from_settings(self._geometry.page_size, settings)
        self._style = StyleSet(settings, self._geometry.content_size)
        self._sections.clear()
        return True

    def set_device_ratio(self, ratio: float) -> bool:
        """Change the rasterisation scale; returns ``True`` when a repaint is due."""
        ratio = ratio if ratio and ratio > 0 else 1.0
        if abs(ratio - self._device_ratio) < 1e-6:
            return False
        self._device_ratio = ratio
        self._image_cache.device_ratio = ratio
        return True

    def clear(self) -> None:
        self._sections.clear()

    # --------------------------------------------------------------- sections

    def section(self, index: int) -> LaidOutSection:
        """Return the laid-out section, building it on first use (ADR-003)."""
        cached = self._sections.get(index)
        if cached is not None:
            self._sections.move_to_end(index)
            return cached

        section = self._build_section(index)
        self._sections[index] = section
        while len(self._sections) > self._section_cache_size:
            _, evicted = self._sections.popitem(last=False)
            _log.debug("dropping laid-out section %d", evicted.index)
        return section

    def _build_section(self, index: int) -> LaidOutSection:
        started = time.perf_counter()
        blocks = self._book.blocks(index)
        document = build_document(blocks, self._style, self._image_cache)
        # The whole section, one column (ADR-016).  There is no pagination pass and
        # no page-break repair, because neither has a meaning when there are no
        # breaks: Qt lays the column out once and reports how tall it came out.
        height = layout_continuous(document, self._geometry.content_size.width())

        anchors: dict[str, int] = {}
        for block_index, block in enumerate(blocks):
            for anchor in block.anchor_ids:
                anchors.setdefault(anchor, block_index)

        elapsed = (time.perf_counter() - started) * 1000.0
        self._last_stats = SectionStats(
            index=index,
            blocks=len(blocks),
            height=height,
            build_ms=elapsed,
        )
        _log.debug(
            "laid out section %d: %d blocks, %.0f px tall in %.1f ms",
            index,
            len(blocks),
            height,
            elapsed,
        )

        return LaidOutSection(
            index=index,
            blocks=blocks,
            document=document,
            height=height,
            geometry=self._geometry,
            build_ms=elapsed,
            anchor_blocks=anchors,
        )

    # ----------------------------------------------------------------- render

    def render_window(self, section_index: int, offset: float) -> QImage:
        """Render the viewport at *offset* as an image (FR-056 / ADR-016).

        The offset is rounded down to :data:`WINDOW_QUANTUM` and the image comes
        out one quantum taller than the page box, so the caller draws the visible
        part of it with a translate and scrolling inside a quantum never reaches
        Python at all (NFR-005).
        """
        section = self.section(section_index)
        clamped = max(0.0, min(offset, section.max_offset))
        return render_window(
            section.document,
            quantise_offset(clamped),
            section.geometry,
            self._settings,
            device_ratio=self._device_ratio,
        )

    def prefetch(self, section_index: int, offset: float) -> None:
        """Decode the images the viewport at *offset* is about to need.

        Decoding costs roughly 10 ms per image, which a scroll cannot pay at paint
        time, so the images of the window *after* this one are decoded here.  A
        cache hit is a dictionary lookup and this is called on every scroll step,
        so the look-ahead is deliberately one viewport: far enough for a wheel,
        not so far that a jump decodes a screenful nobody will look at (NFR-005).
        """
        section = self.section(section_index)
        if not any(block.is_image for block in section.blocks):
            return
        viewport = max(1.0, section.geometry.content_height)
        top = max(0.0, offset)
        bottom = top + 2.0 * viewport
        layout = section.document.documentLayout()
        for index in range(section.block_at_offset(top), len(section.blocks)):
            block = section.blocks[index]
            if layout.blockBoundingRect(section.document.findBlockByNumber(index)).top() >= bottom:
                break
            if block.is_image and block.image is not None:
                self._image_cache.image(block.image.src, self._style.image_size_for(block))


def make_geometry(page_size: QSizeF, settings: TypographySettings) -> PageGeometry:
    """Build a :class:`PageGeometry` from a page size and the settings."""
    return PageGeometry.from_settings(page_size, settings)


