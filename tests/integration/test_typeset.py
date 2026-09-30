"""End-to-end checks on the typesetting layer, including the performance budget.

These are the tests that decide whether the architecture works at all: they assert
the measured numbers from the design document, not just functional behaviour.  All
of them run headless via ``QT_QPA_PLATFORM=offscreen``.

A section is one continuous column and one *window* of it is the unit that gets
rasterised - the page box plus one rendering quantum of headroom (ADR-016) - so where
the paged version of this module asked about pages, this one asks about the column:
how tall it came out, which block a distance lands in, and what a window at that
distance contains.  The two guarantees that only made sense against a page boundary
(ADR-007) are kept in the form the column gives them: a figure is never squeezed or
overlapped by the layout, and nothing is inserted between a heading and its text.
"""

from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QSizeF

from ebook_reader.domain.html.kinsoku import NO_LINE_START, NO_LINE_START_ASCII, WORD_JOINER
from ebook_reader.typeset import (
    WINDOW_QUANTUM,
    LayoutEngine,
    TypographySettings,
    quantise_offset,
)
from ebook_reader.typeset.images import ImageCache
from ebook_reader.typeset.settings import PageGeometry, Theme
from ebook_reader.typeset.style import StyleSet

from conftest import dominant_colour

PAGE = QSizeF(1000.0, 1346.0)

#: NFR-003: 30 ms target / 50 ms floor for a 22 000 character section with warm
#: glyph caches.  The first section in a fresh process pays a one-off shaping
#: warm-up of roughly 60 ms, which belongs to the start-up budget instead, so the
#: budget is asserted on the steady state.
SECTION_BUDGET_MS = 50.0

#: NFR-004: 15 ms target / 20 ms floor for rasterising one window.
WINDOW_BUDGET_MS = 20.0

#: How many windows the render budget is measured over, one viewport apart.
WINDOWS_MEASURED = 8

#: Room the style set may leave between a heading and the text under it, in line
#: steps.  Two is generous - the measured worst case over the reference book is 1.45,
#: which is the heading's own bottom margin - while a page break used to leave up to
#: a page.
HEADING_GAP_LINE_STEPS = 2.0

#: A figure's rect inside the column is the display size the style set asked for;
#: half a pixel of rounding is Qt's (measured), not a difference in room.
FIGURE_SIZE_TOLERANCE = 1.0


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
    """Warm the glyph caches the way opening a book does, then measure.

    The column is checked to be a long one first, because the budget only means
    something if the section really is the worst case it claims to be: the reference
    book's largest section comes out at thirty screens of text (measured), and a
    section that fits on one screen would pass whatever the code did.
    """
    engine.section(0)
    engine.section(largest_section)

    engine.clear()
    section, elapsed = _timed(lambda: engine.section(largest_section))
    assert section.height > 2.0 * section.geometry.content_height
    assert elapsed < SECTION_BUDGET_MS, f"section took {elapsed:.1f} ms"


def test_window_rendering_stays_within_budget(engine: LayoutEngine, largest_section: int) -> None:
    """The unit of rasterisation is a window of the column, one viewport apart.

    Every window costs the same as any other one: what used to be "render page n" is
    now "render what is on screen at this distance down the column".
    """
    section = engine.section(largest_section)
    engine.render_window(largest_section, 0.0)  # warm the first paint

    worst = 0.0
    for index in range(WINDOWS_MEASURED):
        offset = min(
            quantise_offset(index * section.geometry.content_height), section.max_offset
        )
        image, elapsed = _timed(
            lambda offset=offset: engine.render_window(largest_section, offset)
        )
        assert not image.isNull()
        worst = max(worst, elapsed)
    assert worst < WINDOW_BUDGET_MS, f"slowest window took {worst:.1f} ms"


def test_rendered_window_has_the_expected_geometry(
    engine: LayoutEngine, largest_section: int
) -> None:
    """A window is the page box plus one quantum of headroom (ADR-016, NFR-005).

    The extra strip is what makes scrolling inside a quantum a bitmap translate
    rather than a repaint, so its height is part of the contract, not an accident.
    """
    image = engine.render_window(largest_section, 0.0)
    assert image.width() == round(PAGE.width())
    assert image.height() == round(PAGE.height() + WINDOW_QUANTUM)


def test_rendered_window_contains_ink(engine: LayoutEngine, largest_section: int) -> None:
    """Guards against the silent failure mode of drawing nothing at all.

    Checked at three distances: the top of the column, one viewport down, and the
    deepest offset the section allows - the last window is the one an off-by-one in
    the offset clamp would leave blank.
    """
    section = engine.section(largest_section)
    offsets = (0.0, quantise_offset(section.geometry.content_height), section.max_offset)
    for offset in offsets:
        image = engine.render_window(largest_section, offset)
        dark = sum(
            1
            for y in range(0, image.height(), 11)
            for x in range(0, image.width(), 11)
            if image.pixelColor(x, y).lightness() < 200
        )
        assert dark > 100, f"window at {offset:.1f} looks blank ({dark} dark samples)"


def test_device_ratio_scales_the_bitmap(engine: LayoutEngine, largest_section: int) -> None:
    engine.set_device_ratio(2.0)
    image = engine.render_window(largest_section, 0.0)
    assert image.width() == round(PAGE.width() * 2)
    assert image.height() == round((PAGE.height() + WINDOW_QUANTUM) * 2)
    assert image.devicePixelRatio() == pytest.approx(2.0)


def test_no_figure_is_squeezed_or_overlapped_by_the_column(
    engine: LayoutEngine, kangpo
) -> None:
    """FR-052 in column coordinates: a figure gets exactly the room it asked for.

    The page-boundary form of the guarantee went with paging itself (ADR-016): over a
    continuous column there is no boundary for a figure to be split across.  What can
    still go wrong is the figure being clipped by the layout, or given filler room it
    does not fill - which is precisely what the page-break repair used to do - so the
    assertion is that a figure's rect in the column is the display size the style set
    chose, and that the block after it starts where the figure ends.
    """
    style = StyleSet(engine.settings, engine.geometry.content_size)
    checked = 0
    for index in range(kangpo.section_count):
        section = engine.section(index)
        layout = section.document.documentLayout()
        for block_index, block in enumerate(section.blocks):
            if not block.is_image:
                continue
            checked += 1
            rect = layout.blockBoundingRect(section.document.findBlockByNumber(block_index))
            assert rect.height() == pytest.approx(
                style.image_size_for(block).height(), abs=FIGURE_SIZE_TOLERANCE
            ), f"figure {index}/{block_index} is {rect.height():.1f}px tall"
            following = section.document.findBlockByNumber(block_index + 1)
            if following.isValid():
                assert (
                    layout.blockBoundingRect(following).top()
                    >= rect.bottom() - FIGURE_SIZE_TOLERANCE
                ), f"text overlaps figure {index}/{block_index}"
    assert checked > 100, "expected the reference book to contain many figures"


def test_a_heading_has_its_text_right_below_it(engine: LayoutEngine, kangpo) -> None:
    """FR-053 in column coordinates: nothing is inserted after a heading.

    Over a column there is no bottom of a page for a heading to be orphaned on, and
    the pass that used to break before one is gone (ADR-016).  Its absence is
    observable: the gap between a heading and the block that follows it is the
    ordinary spacing the style set asks for - the heading's own bottom margin, a
    little over one body line (measured) - and not the rest of a page.
    """
    step = engine.line_step
    checked = 0
    for index in range(kangpo.section_count):
        section = engine.section(index)
        layout = section.document.documentLayout()
        for block_index, block in enumerate(section.blocks):
            if not block.is_heading or block_index + 1 >= len(section.blocks):
                continue
            checked += 1
            here = layout.blockBoundingRect(section.document.findBlockByNumber(block_index))
            following = layout.blockBoundingRect(
                section.document.findBlockByNumber(block_index + 1)
            )
            gap = following.top() - here.bottom()
            assert -0.5 <= gap < HEADING_GAP_LINE_STEPS * step, (
                f"heading {index}/{block_index} is followed by a {gap / step:.1f} line gap"
            )
    assert checked > 20, f"expected many headings with text, saw {checked}"


def test_the_column_height_is_one_layout_pass_and_reproducible(
    engine: LayoutEngine, kangpo
) -> None:
    """ADR-016: the height is Qt's own answer for one column, not a repaired total.

    Paging ran a repair loop that pushed blocks off page boundaries and reported how
    many iterations it took (ADR-007).  No such pass exists any more, so the claim
    worth pinning is that the reported height is the height the document itself came
    out at - and that laying the same column out twice gives the same number, which is
    what lets an offset be remembered as a reading position.
    """
    for index in range(kangpo.section_count):
        section = engine.section(index)
        assert section.height == pytest.approx(section.document.size().height(), abs=0.5)
        assert section.build_ms >= 0.0

    first = engine.section(0)
    engine.clear()
    rebuilt = engine.section(0)
    assert rebuilt.height == pytest.approx(first.height, abs=0.1)
    assert rebuilt.blocks == first.blocks


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
    """ADR-011: the position is a block, and the block still addresses the same text.

    A reading position is a distance down the column now (ADR-016), but the block
    index is what survives a relayout, so the promise being tested is the one the
    reader can see: after changing the type size, the same block index is the same
    paragraph - and it has moved a long way down the column, because the type is
    bigger.
    """
    engine = LayoutEngine(kangpo, settings, geometry)
    section = engine.section(11)
    block = min(5, len(section.blocks) - 1)
    text = section.blocks[block].text
    before = section.block_offset(block)

    assert engine.set_settings(settings.with_(font_size=24.0)) is True
    restored = engine.section(11)
    assert restored.blocks[block].text == text
    assert restored.block_at_offset(restored.block_offset(block)) >= block
    assert restored.block_offset(block) > before
    assert 0.0 <= restored.block_offset(block) <= restored.height


def test_the_block_lookup_is_the_last_block_at_or_before_an_offset(
    engine: LayoutEngine, largest_section: int
) -> None:
    """The inverse of a reading position: a distance down the column names a block.

    Two blocks can share a top (an empty one before a figure, say), so the contract
    is "the last block that starts at or before this offset" - which is what makes
    scrolling to a remembered offset land on the same text again.  The next block's
    top is what proves it really is the last one.
    """
    section = engine.section(largest_section)
    tops = [section.block_offset(index) for index in range(len(section.blocks))]
    assert all(later >= earlier for earlier, later in zip(tops, tops[1:]))

    step = section.geometry.content_height / 4.0
    offset = 0.0
    samples = 0
    while offset <= section.max_offset:
        index = section.block_at_offset(offset)
        assert 0 <= index < len(section.blocks)
        assert section.block_offset(index) <= offset + 0.01
        if index + 1 < len(section.blocks):
            assert section.block_offset(index + 1) > offset - 0.01
        offset += step
        samples += 1
    assert samples > 8, f"only sampled {samples} offsets"


def test_a_theme_change_re_inks_the_pages(qapp, kangpo, settings, geometry, largest_section) -> None:
    """The palette is not a property of the window: it is inside the documents (缺陷 28).

    ``StyleSet`` puts the theme's colours into every char format and a laid-out section
    keeps its own blocks, so an engine that answered "only colours changed, nothing to
    relayout" kept the old ink while the paper was re-filled in the new theme.  Measured
    on the reference book, 米色 → 夜间 left #3b3226 (sepia's brown) on #27272b - 1.4:1,
    the reader's words as a barely visible stain rather than text.  A theme change is
    therefore a relayout, and this asserts it the way the reader sees it: the colour the
    page is actually drawn in.
    """
    sepia = settings.with_(theme=Theme.SEPIA)
    engine = LayoutEngine(kangpo, sepia, geometry)
    engine.section(largest_section)
    assert (
        dominant_colour(engine.render_window(largest_section, 0.0), sepia.colors.background)
        == sepia.colors.foreground
    ), "the first paint is not the theme it was asked for"

    dark = sepia.with_(theme=Theme.DARK)
    assert engine.set_settings(dark) is True, "a theme change left the documents alone"
    assert (
        dominant_colour(engine.render_window(largest_section, 0.0), dark.colors.background)
        == dark.colors.foreground
    ), "the words kept the old theme's ink on the new theme's paper"


def test_turning_avoid_head_tail_off_reaches_the_pages(
    kangpo, settings, geometry, largest_section
) -> None:
    """FR-031: kinsoku is in the text, so the toggle has to reach the laid-out page.

    The joiners are inserted *into* the parsed text, and a laid-out section keeps its own
    copy of the blocks - so dropping the book's cache alone left this engine handing back
    the very same document, joiners and all, and the row did nothing until the reader
    changed section (measured before the fix: same document object, joiner still in it).
    The drop is the engine's, because "how a setting reaches a document" is what the
    engine knows; the test walks the toggle the way the controller does.
    """
    engine = LayoutEngine(kangpo, settings, geometry)
    before = engine.section(largest_section)
    assert any(WORD_JOINER in block.text for block in before.blocks), "kinsoku is on by default"

    assert engine.set_settings(settings.with_(kinsoku=False)) is False, "no relayout is due"
    after = engine.section(largest_section)
    assert after is not before, "the documents outlived the text they were built from"
    assert not any(WORD_JOINER in block.text for block in after.blocks)


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
            assert section.blocks
            assert section.height > 0.0
            assert section.max_offset >= 0.0
            assert section.block_at_offset(0.0) == 0


