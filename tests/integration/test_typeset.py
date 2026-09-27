"""End-to-end checks on the typesetting layer, including the performance budget.

These are the tests that decide whether the architecture works at all: they assert
the measured numbers from the design document, not just functional behaviour.  All
of them run headless via ``QT_QPA_PLATFORM=offscreen``.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QSizeF

from ebook_reader.domain.html.kinsoku import NO_LINE_START, NO_LINE_START_ASCII
from ebook_reader.typeset import LayoutEngine, TypographySettings
from ebook_reader.typeset.images import ImageCache
from ebook_reader.typeset.settings import PageGeometry

PAGE = QSizeF(1000.0, 1346.0)

#: NFR-003: 30 ms target / 50 ms floor for a 22 000 character section with warm
#: glyph caches.  The first section in a fresh process pays a one-off shaping
#: warm-up of roughly 60 ms, which belongs to the start-up budget instead, so the
#: budget is asserted on the steady state.
SECTION_BUDGET_MS = 50.0

#: NFR-004: 15 ms target / 20 ms floor for rasterising one page.
PAGE_BUDGET_MS = 20.0


@pytest.fixture
def settings() -> TypographySettings:
    return TypographySettings()


@pytest.fixture
def geometry(settings: TypographySettings) -> PageGeometry:
    return PageGeometry.from_settings(PAGE, settings)


@pytest.fixture
def engine(qapp, kangpo, settings, geometry) -> LayoutEngine:
    return LayoutEngine(kangpo, settings, geometry)


def _timed(callable_) -> tuple[object, float]:
    started = time.perf_counter()
    result = callable_()
    return result, (time.perf_counter() - started) * 1000.0


def test_page_geometry_deducts_margins(
    geometry: PageGeometry, settings: TypographySettings
) -> None:
    assert geometry.content_size.width() == pytest.approx(
        PAGE.width() - settings.margin_left - settings.margin_right
    )
    assert geometry.content_size.height() == pytest.approx(
        PAGE.height() - settings.margin_top - settings.margin_bottom
    )


def test_section_build_stays_within_budget(
    engine: LayoutEngine, largest_section: int
) -> None:
    """Warm the glyph caches the way opening a book does, then measure."""
    engine.section(0)
    engine.section(largest_section)

    engine.clear()
    section, elapsed = _timed(lambda: engine.section(largest_section))
    assert section.page_count >= 2
    assert elapsed < SECTION_BUDGET_MS, f"section took {elapsed:.1f} ms"


def test_page_rendering_stays_within_budget(engine: LayoutEngine, largest_section: int) -> None:
    section = engine.section(largest_section)
    engine.render(largest_section, 0)  # warm the first paint

    worst = 0.0
    for page in range(min(8, section.page_count)):
        image, elapsed = _timed(lambda page=page: engine.render(largest_section, page))
        assert not image.isNull()
        worst = max(worst, elapsed)
    assert worst < PAGE_BUDGET_MS, f"slowest page took {worst:.1f} ms"


def test_rendered_page_has_the_expected_geometry(engine: LayoutEngine, largest_section: int) -> None:
    image = engine.render(largest_section, 0)
    assert image.width() == round(PAGE.width())
    assert image.height() == round(PAGE.height())


def test_rendered_page_contains_ink(engine: LayoutEngine, largest_section: int) -> None:
    """Guards against the silent failure mode of drawing nothing at all."""
    image = engine.render(largest_section, 0)
    dark = sum(
        1
        for y in range(0, image.height(), 11)
        for x in range(0, image.width(), 11)
        if image.pixelColor(x, y).lightness() < 200
    )
    assert dark > 100, f"page looks blank ({dark} dark samples)"


def test_device_ratio_scales_the_bitmap(engine: LayoutEngine, largest_section: int) -> None:
    engine.set_device_ratio(2.0)
    image = engine.render(largest_section, 0)
    assert image.width() == round(PAGE.width() * 2)
    assert image.devicePixelRatio() == pytest.approx(2.0)


def test_no_image_block_is_split_across_a_page_boundary(engine: LayoutEngine, kangpo) -> None:
    """FR-052 / ADR-007: pages may be short, but a figure is never cut in half."""
    split = 0
    checked = 0
    for index in range(kangpo.section_count):
        section = engine.section(index)
        if not any(block.is_image for block in section.blocks):
            continue
        height = section.geometry.content_height
        layout = section.document.documentLayout()
        for block_index, block in enumerate(section.blocks):
            if not block.is_image:
                continue
            checked += 1
            rect = layout.blockBoundingRect(section.document.findBlockByNumber(block_index))
            first = int(rect.top() // height)
            last = int(max(rect.top(), rect.bottom() - 0.5) // height)
            if first != last:
                split += 1
    assert checked > 100, "expected the reference book to contain many figures"
    assert split == 0


def test_headings_are_not_left_alone_at_the_bottom_of_a_page(
    engine: LayoutEngine, kangpo
) -> None:
    """FR-053: a heading must not be the last thing on a page."""
    height = engine.geometry.content_height
    orphans = 0
    for index in range(kangpo.section_count):
        section = engine.section(index)
        layout = section.document.documentLayout()
        for block_index, block in enumerate(section.blocks):
            if not block.is_heading or block_index + 1 >= len(section.blocks):
                continue
            here = layout.blockBoundingRect(section.document.findBlockByNumber(block_index))
            following = layout.blockBoundingRect(
                section.document.findBlockByNumber(block_index + 1)
            )
            if int(following.top() // height) > int(here.top() // height):
                orphans += 1
    assert orphans == 0


def test_page_break_repair_converges(engine: LayoutEngine, kangpo) -> None:
    """The iteration cap must never be reached in practice."""
    for index in range(kangpo.section_count):
        section = engine.section(index)
        assert section.repair.converged, index
        assert section.repair.iterations <= 8
    assert engine.last_stats is not None


def test_no_forbidden_character_starts_a_line(
    engine: LayoutEngine, largest_section: int
) -> None:
    """FR-031 / ADR-005, asserted on the lines Qt actually produced.

    This is the test that turns "the typography looks right" into something a
    machine checks: it inspects every wrapped line of the largest section.
    """
    forbidden = NO_LINE_START | NO_LINE_START_ASCII
    section = engine.section(largest_section)
    violations: list[str] = []
    lines = 0
    for block_index in range(len(section.blocks)):
        text_block = section.document.findBlockByNumber(block_index)
        if not text_block.isValid():
            continue
        layout = text_block.layout()
        raw = text_block.text()
        for line_index in range(1, layout.lineCount()):
            start = layout.lineAt(line_index).textStart()
            if start >= len(raw):
                continue
            lines += 1
            if raw[start] in forbidden:
                violations.append(raw[max(0, start - 6) : start + 4])
    assert lines > 200, f"expected many wrapped lines, saw {lines}"
    assert not violations, f"{len(violations)} line(s) start with punctuation: {violations[:3]}"


def test_prefetch_populates_the_image_cache(qapp, kangpo, settings, geometry) -> None:
    """Section 0 is the cover: a single figure, so a prefetch must decode it."""
    engine = LayoutEngine(kangpo, settings, geometry)
    assert engine.image_stats().decodes == 0
    engine.prefetch(0, 0)
    assert engine.image_stats().decodes > 0
    assert engine.image_stats().failures == 0


def test_image_cache_stays_within_its_budget(qapp, kangpo) -> None:
    """ADR-006: the byte cap is a hard limit, whatever the working set."""
    cache = ImageCache(kangpo.resource, max_items=3, max_bytes=3 * 1024 * 1024)
    size = QSizeF(756.0, 1000.0)
    for name in kangpo.image_names()[:16]:
        assert cache.image(name, size) is not None

    stats = cache.stats()
    assert stats.failures == 0
    assert stats.items <= 3
    assert stats.bytes <= 3 * 1024 * 1024


def test_image_cache_serves_hits_when_the_working_set_fits(qapp, kangpo) -> None:
    """The other half of the same story: give it room and it does serve hits.

    An LRU smaller than the working set thrashes - with a section's images and
    room for fewer, every access evicts the item that is needed next, so the hit
    count stays at zero.  This test pins the positive case so that a future change
    to the budget knobs cannot silently reintroduce thrashing.
    """
    cache = ImageCache(kangpo.resource, max_items=64, max_bytes=64 * 1024 * 1024)
    names = kangpo.image_names()[:6]
    size = QSizeF(756.0, 1000.0)
    for name in names:
        assert cache.image(name, size) is not None

    hits_before = cache.stats().hits
    for name in names:
        cache.image(name, size)

    assert cache.stats().hits == hits_before + len(names)


def test_image_cache_never_scales_up(qapp, kangpo) -> None:
    name = kangpo.image_names()[0]
    intrinsic = kangpo.image_size(name)
    assert intrinsic is not None

    image = ImageCache(kangpo.resource).image(name, QSizeF(4000.0, 4000.0))
    assert image is not None
    assert image.width() <= intrinsic[0]
    assert image.height() <= intrinsic[1]


def test_image_cache_reports_a_missing_resource(qapp, kangpo) -> None:
    cache = ImageCache(kangpo.resource)
    assert cache.image("EPUB/images/does-not-exist.jpg") is None
    assert cache.stats().failures == 1


def test_section_cache_evicts_old_sections(qapp, kangpo, settings, geometry) -> None:
    engine = LayoutEngine(kangpo, settings, geometry, section_cache_size=2)
    for index in range(5):
        engine.section(index)
    assert len(engine._sections) == 2


def test_reading_position_survives_a_font_size_change(qapp, kangpo, settings, geometry) -> None:
    """ADR-011: a block-based position still addresses the same paragraph."""
    engine = LayoutEngine(kangpo, settings, geometry)
    section = engine.section(11)
    block = section.page_start_block(min(5, section.page_count - 1))
    text = section.blocks[block].text

    assert engine.set_settings(settings.with_(font_size=24.0)) is True
    restored = engine.section(11)
    assert restored.blocks[block].text == text
    assert 0 <= restored.block_page(block) < restored.page_count


def test_theme_change_does_not_force_relayout(engine: LayoutEngine, settings) -> None:
    """Only colours change, so no re-layout should be triggered."""
    engine.section(0)
    assert engine.set_settings(settings.with_(theme=settings.theme.DARK)) is False


def test_margin_change_is_a_geometry_change(engine: LayoutEngine, settings) -> None:
    assert engine.set_settings(settings.with_(margin_left=140, margin_right=140)) is True
    assert engine.geometry.content_size.width() == pytest.approx(PAGE.width() - 280)


def test_geometry_change_reports_whether_it_matters(
    qapp, kangpo, settings, geometry
) -> None:
    engine = LayoutEngine(kangpo, settings, geometry)
    engine.section(0)
    smaller = PageGeometry.from_settings(QSizeF(700.0, 900.0), settings)
    assert engine.set_geometry(smaller) is True
    assert engine.set_geometry(smaller) is False


def test_every_section_of_both_books_can_be_laid_out(
    qapp, kangpo, binan, settings, geometry
) -> None:
    for book in (kangpo, binan):
        engine = LayoutEngine(book, settings, geometry)
        for index in range(book.section_count):
            section = engine.section(index)
            assert section.page_count >= 1
            assert section.blocks


