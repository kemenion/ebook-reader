"""The section outline over a continuous column (FR-018, FR-019).

The panel used to be a page map with a heading-based fallback.  A column that is
scrolled rather than paged has no pages to map, and rows for equal slices of it would
move every time the window was resized - so what is left is the section's own headings
(ADR-016).  The cases worth testing are the ones the fallback used to hide: a section
with headings gets one row each, a section with none gets nothing at all, and a row is
a *scroll offset* that has to keep meaning the same heading after the text has been
laid out again.

These tests drive the controller directly (``conftest.controller``).  What the panel
draws, where its column sits and how a row takes a click are the shared shell's job,
in ``test_layout.py`` and ``test_scroll_view.py``.  Headless via
``QT_QPA_PLATFORM=offscreen``, like the other integration tests.
"""

from __future__ import annotations

from pathlib import Path

from ebook_reader.app.controller import ReaderController

#: A comfortable page box, and a narrow one that makes the text be set again.
PAGE = (900, 1200)
NARROW = (560, 1200)

#: The deepest heading the outline lists (``_OUTLINE_MAX_LEVEL`` in the controller).
MAX_LEVEL = 3

#: Exactly what a row holds - and no page: the page map is gone (ADR-016).
ROW_KEYS = {"title", "level", "offset", "block", "row"}

WORD_JOINER = "\u2060"


def _level(block) -> int:
    """The level the controller will record for *block* (it clamps to 1..6)."""
    return max(1, min(6, block.level))


def _rows(book, index: int) -> list[int]:
    """Block positions the outline of section *index* should list (FR-018)."""
    return [
        position
        for position, block in enumerate(book.blocks(index))
        if block.is_heading and _level(block) <= MAX_LEVEL and block.text.strip()
    ]


def _open(controller: ReaderController, path: Path, section: int) -> None:
    """Set the page box first, so the book is laid out at the size under test."""
    controller.setViewSize(*PAGE)
    assert controller.openBook(str(path))
    controller.goToSection(section)


def _scroll_to(controller: ReaderController, offset: float) -> None:
    """Put the window at *offset*, whatever it was at before.

    Through ``scrollBy`` - the same door the keys and the wheel use - and with a
    tolerance, because the controller adds the distance to the offset it holds and
    that is a floating-point sum, not an assignment.
    """
    controller.scrollBy(offset - controller.scrollOffset)
    assert abs(controller.scrollOffset - offset) < 0.01


# ------------------------------------------------------------------- heading rule


def test_heading_outline_mirrors_the_section(
    controller: ReaderController, kangpo, kangpo_path: Path, heading_section: int
) -> None:
    """One row per heading, with the heading's own place in the column behind it."""
    _open(controller, kangpo_path, heading_section)
    positions = _rows(kangpo, heading_section)
    assert len(positions) > 1, "this section has to carry more than one heading"

    items = controller.outlineItems
    blocks = kangpo.blocks(heading_section)

    assert [item["block"] for item in items] == positions
    assert [item["level"] for item in items] == [_level(blocks[i]) for i in positions]
    assert [item["title"] for item in items] == [
        " ".join(blocks[i].text.replace(WORD_JOINER, "").split()) for i in positions
    ]
    assert [item["row"] for item in items] == list(range(len(positions)))
    # A row is a place in the column, not a page: headings come in reading order, so
    # the offsets only ever increase, and every one of them is inside the section.
    offsets = [float(item["offset"]) for item in items]
    assert offsets == sorted(set(offsets))
    assert all(0.0 <= offset <= controller.scrollMax for offset in offsets)
    # Nothing deeper than the third level is listed, and a row holds no page.
    assert all(int(item["level"]) <= MAX_LEVEL for item in items)
    assert set(items[0]) == ROW_KEYS


def test_the_highlight_is_the_heading_the_reader_is_under(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """Walk the whole section: the highlight never skips a heading or lags behind."""
    _open(controller, kangpo_path, heading_section)
    items = controller.outlineItems
    assert len(items) > 1, "this section has to be worth walking"

    controller.scrollToTop()
    # A heading is a line like any other, so the first row is already the current one
    # when the section opens with a heading.
    starts_with_a_heading = float(items[0]["offset"]) == 0.0
    assert controller.currentOutlineRow == (0 if starts_with_a_heading else -1)

    for row, item in enumerate(items):
        _scroll_to(controller, float(item["offset"]))
        # The window is on the heading itself - that is what a row jumps to - and the
        # highlight is on that heading's row.
        assert controller._anchor_block() == int(item["block"])
        assert controller.currentOutlineRow == row

    # Past the last heading the highlight stays there rather than counting down.
    controller.scrollToSectionEnd()
    assert controller.currentOutlineRow == len(items) - 1


def test_a_row_jumps_to_its_heading(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    items = controller.outlineItems
    target = len(items) - 1
    assert target > 0

    controller.goToOutlineRow(target)
    assert controller.scrollOffset == float(items[target]["offset"])
    assert controller.currentOutlineRow == target
    # The anchor is a heading, so the window lands with it at the top and none of the
    # paragraph above it is left on screen.
    assert controller._anchor_block() == int(items[target]["block"])

    controller.goToOutlineRow(0)
    assert controller.currentOutlineRow == 0
    assert controller._anchor_block() == int(items[0]["block"])


def test_a_row_that_does_not_exist_is_ignored(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    controller.scrollToSectionEnd()
    landed = controller.scrollOffset

    controller.goToOutlineRow(9999)
    assert controller.scrollOffset == landed
    controller.goToOutlineRow(-1)
    assert controller.scrollOffset == landed


# ---------------------------------------------------------------------- page box


def test_the_same_headings_outlive_the_text_being_set_again(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """A narrower column moves the offsets; the rows and the reader's place hold.

    The rows are rebuilt for the new layout - which is why they carry an offset and a
    block rather than a page - and the place is a block (ADR-011), so the window comes
    back to the heading it was on, with the highlight on that heading's row.
    """
    _open(controller, kangpo_path, heading_section)
    before = controller.outlineItems
    target = len(before) // 2
    controller.goToOutlineRow(target)
    heading = int(before[target]["block"])

    controller.setViewSize(*NARROW)

    after = controller.outlineItems
    assert [item["block"] for item in after] == [item["block"] for item in before]
    assert [item["title"] for item in after] == [item["title"] for item in before]
    assert controller._anchor_block() == heading
    row = controller.currentOutlineRow
    assert row >= 0 and int(after[row]["block"]) == heading


# ------------------------------------------------------------ panel availability


def test_outline_is_unavailable_without_a_book(controller: ReaderController) -> None:
    assert not controller.outlineAvailable
    assert controller.outlineItems == []

    controller.toggleOutline()          # refused: there is nothing to fill it with
    assert not controller.outlineVisible


def test_a_section_without_headings_offers_no_outline(
    controller: ReaderController, binan_path: Path, headingless_section: int
) -> None:
    """The fallback is gone: no headings means no rows, and so no column (FR-019)."""
    _open(controller, binan_path, headingless_section)
    assert controller.outlineItems == []
    assert not controller.outlineAvailable
    assert controller.currentOutlineRow == -1

    controller.toggleOutline()
    assert not controller.outlineVisible


def test_open_book_makes_the_outline_available(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    assert controller.outlineAvailable

    controller.toggleOutline()
    assert controller.outlineVisible


def test_outline_leaves_with_the_book(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    controller.toggleOutline()
    assert controller.outlineVisible

    controller.closeBook()
    assert not controller.outlineAvailable
    assert not controller.outlineVisible
    assert controller.outlineItems == []


def test_jump_leaves_the_panel_open(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """The contents shut on a click once; the outline is a map you click through."""
    _open(controller, kangpo_path, heading_section)
    controller.toggleOutline()
    assert controller.outlineVisible

    controller.goToOutlineRow(0)
    assert controller.outlineVisible


# ----------------------------------------------------------------- columns


def test_outline_and_contents_share_the_screen(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    assert controller.tocVisible          # the map comes up with the book (FR-013)

    controller.toggleOutline()
    assert controller.tocVisible
    assert controller.outlineVisible


def test_opening_the_outline_closes_the_drawer(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """The drawer and the outline share a side, so one of them yields to the other."""
    _open(controller, kangpo_path, heading_section)
    controller.toggleSettings()
    controller.toggleOutline()
    assert controller.outlineVisible
    assert not controller.settingsVisible


def test_settings_yields_the_outline_column_only(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """The drawer floats over the right-hand side: it takes that column, no other."""
    _open(controller, kangpo_path, heading_section)
    controller.toggleOutline()
    assert controller.tocVisible and controller.outlineVisible

    controller.toggleSettings()
    assert controller.settingsVisible
    assert not controller.outlineVisible      # the column it shares a side with
    assert controller.tocVisible              # the map is on the other side (FR-013)

    # ... and dismissing the drawer leaves the map exactly where it was.
    controller.closeSettings()
    assert not controller.settingsVisible
    assert controller.tocVisible


def test_close_panels_closes_the_outline_too(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    _open(controller, kangpo_path, heading_section)
    controller.toggleOutline()
    controller.closePanels()
    assert not controller.outlineVisible


# ------------------------------------------------------------------------ labels


def test_heading_titles_carry_no_word_joiners(
    controller: ReaderController, kangpo_path: Path, heading_section: int
) -> None:
    """Kinsoku puts U+2060 into headings too, and a label is not laid-out text."""
    _open(controller, kangpo_path, heading_section)
    titles = [str(item["title"]) for item in controller.outlineItems]
    assert titles
    assert all(WORD_JOINER not in title for title in titles)
    # Collapsed and stripped: a label is one line in a narrow column.
    assert all(title and title == title.strip() for title in titles)
    assert all("  " not in title for title in titles)
    assert all(int(item["level"]) <= MAX_LEVEL for item in controller.outlineItems)

