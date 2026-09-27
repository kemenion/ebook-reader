"""Layout engine: the single façade the application layer talks to.

Responsibilities (and nothing else):

* own the per-section LRU of laid-out documents (ADR-003);
* rebuild everything when the page geometry or the typography changes;
* answer "how many pages does section N have", "which page is block B on",
  "give me page P of section N as an image".

Parsing stays in the domain layer, and neither the UI nor the controller needs to
know that ``QTextDocument`` exists.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from PySide6.QtCore import QSizeF
from PySide6.QtGui import QImage, QTextDocument

from ..domain.epub.book import EpubBook
from ..domain.models import Block
from .document import build_document
from .images import ImageCache, ImageCacheStats
from .paginator import PageBreakRepair, paginate
from .renderer import render_page
from .settings import PageGeometry, TypographySettings
from .style import StyleSet

__all__ = ["LaidOutSection", "LayoutEngine", "SectionStats", "make_geometry"]

_log = logging.getLogger(__name__)

#: How many laid-out sections stay resident.  Each holds a QTextDocument plus the
#: images Qt keeps for it, so three hides section changes without inflating memory
#: (ADR-003 / NFR-002).
DEFAULT_SECTION_CACHE = 3


@dataclass(slots=True)
class LaidOutSection:
    """One section, laid out and paginated."""

    index: int
    blocks: tuple[Block, ...]
    document: QTextDocument
    page_count: int
    geometry: PageGeometry
    repair: PageBreakRepair
    build_ms: float
    anchor_blocks: dict[str, int] = field(default_factory=dict)
    _block_tops: list[float] | None = None

    # ------------------------------------------------------------- navigation

    def block_page(self, block_index: int) -> int:
        """Page that contains *block_index*, clamped to the section."""
        tops = self._ensure_tops()
        if not tops:
            return 0
        block_index = max(0, min(block_index, len(tops) - 1))
        page = int(tops[block_index] // max(1.0, self.geometry.content_height))
        return max(0, min(page, self.page_count - 1))

    def page_start_block(self, page_index: int) -> int:
        """Index of the last block that starts at or before *page_index*."""
        tops = self._ensure_tops()
        if not tops:
            return 0
        target = page_index * max(1.0, self.geometry.content_height)
        low, high, result = 0, len(tops) - 1, 0
        while low <= high:
            middle = (low + high) // 2
            if tops[middle] <= target:
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
            for index in range(len(self.blocks)):
                text_block = self.document.findBlockByNumber(index)
                tops.append(
                    layout.blockBoundingRect(text_block).top() if text_block.isValid() else 0.0
                )
            self._block_tops = tops
        return self._block_tops

    def invalidate_geometry(self) -> None:
        self._block_tops = None


@dataclass(frozen=True, slots=True)
class SectionStats:
    """Per-section diagnostics, surfaced in the status bar and in tests."""

    index: int
    pages: int
    blocks: int
    build_ms: float
    iterations: int
    converged: bool
    pushed_blocks: int


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
        pagination = paginate(document, blocks, self._geometry.content_size)

        anchors: dict[str, int] = {}
        for block_index, block in enumerate(blocks):
            for anchor in block.anchor_ids:
                anchors.setdefault(anchor, block_index)

        elapsed = (time.perf_counter() - started) * 1000.0
        self._last_stats = SectionStats(
            index=index,
            pages=pagination.page_count,
            blocks=len(blocks),
            build_ms=elapsed,
            iterations=pagination.repair.iterations,
            converged=pagination.repair.converged,
            pushed_blocks=len(pagination.repair.pushed_blocks),
        )
        if not pagination.repair.converged:
            _log.warning("page-break repair did not converge for section %d", index)

        return LaidOutSection(
            index=index,
            blocks=blocks,
            document=document,
            page_count=pagination.page_count,
            geometry=self._geometry,
            repair=pagination.repair,
            build_ms=elapsed,
            anchor_blocks=anchors,
        )

    # ----------------------------------------------------------------- render

    def render(self, section_index: int, page_index: int) -> QImage:
        """Render one page as an image (FR-056)."""
        section = self.section(section_index)
        return render_page(
            section.document,
            max(0, min(page_index, section.page_count - 1)),
            section.geometry,
            self._settings,
            device_ratio=self._device_ratio,
        )

    def prefetch(self, section_index: int, page_index: int) -> None:
        """Decode the images a page needs before it is painted.

        Decoding costs roughly 10 ms per image, so doing it ahead of the paint
        keeps page turns inside the "cache hit" budget (NFR-005).
        """
        section = self.section(section_index)
        if not any(block.is_image for block in section.blocks):
            return
        height = max(1.0, section.geometry.content_height)
        top = page_index * height
        bottom = top + height
        layout = section.document.documentLayout()
        for index in range(section.page_start_block(page_index), len(section.blocks)):
            block = section.blocks[index]
            if layout.blockBoundingRect(section.document.findBlockByNumber(index)).top() >= bottom:
                break
            if block.is_image and block.image is not None:
                self._image_cache.image(block.image.src, self._style.image_size_for(block))


def make_geometry(page_size: QSizeF, settings: TypographySettings) -> PageGeometry:
    """Build a :class:`PageGeometry` from a page size and the settings."""
    return PageGeometry.from_settings(page_size, settings)


