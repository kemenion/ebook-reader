"""The three-column shell: the window, the column arithmetic, and what is in each.

The harness - building ``Main.qml`` for real, waiting for it, and reading items back
out of it - lives in ``conftest.py``, because the other integration modules need
exactly the same one.

The text column is the only column that is always there, and it is measured from the
window's own edges: a panel that overlaps the text, or a derived width that stops
re-evaluating, is invisible in a screenshot-sized assertion but shows up here.  The
scroll model itself - keys, wheel, scroll bar, the strip at the end of a chapter -
has its own module, ``test_scroll_view.py``.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QWindow

from conftest import (
    RESIZE_DEBOUNCE_MS,
    at,
    build_shell,
    centre,
    click,
    double_click,
    drag,
    item,
    on_screen,
    pump,
    set_window_size,
    visible,
)
from ebook_reader.app.settings_store import (
    _PANEL_WIDTH_MAX,
    _PANEL_WIDTH_MIN,
    SettingsStore,
)


def _column(window) -> tuple[float, float]:
    """Where the text column starts and how wide it is."""
    page = item(window, "pageArea")
    return page.x(), page.width()


# --------------------------------------------------------------------- the shell


def test_the_window_opens_wide_enough_for_three_columns(shell, warnings) -> None:
    _, window, _ = shell
    assert window.property("width") == 1400
    assert window.property("minimumWidth") == 720
    assert window.property("openColumns") == 0
    assert window.property("pageColumnWidth") == 1400
    assert warnings == []


def test_opening_a_book_takes_the_window_to_full_size(shell, kangpo_path: Path) -> None:
    """A reader who asked for a book asked for a page: the page gets the display (FR-077).

    Every book, not only the first one - picking another one from the menu is the same
    request - and the empty shell keeps the size it was built with, so the window is
    still usable for a file dialog on a small display.
    """
    _, window, controller = shell
    assert window.visibility() == QWindow.Visibility.Windowed
    assert window.property("width") == 1400

    assert controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert window.visibility() == QWindow.Visibility.Maximized

    # The page box follows the window, so nothing has to be resized by hand for the
    # text to use the room it was just given.
    assert controller._view_size.width() == window.property("pageColumnWidth")
    assert window.property("pageColumnWidth") == (
        window.property("width") - window.property("sidePanelWidth")
    )


def test_the_columns_tile_the_window(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    """No overlap and no gap: the page gets what the panels do not take.

    The width is read off the window instead of assumed: opening a book maximizes it
    (FR-077), so how much room there is depends on the display, and the panel width is
    derived from it (ADR-014 ②).  The arithmetic is the same at any size, which is
    what this test exists to say.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    # The outline column only takes space where the section has something to list
    # (FR-019), so the three-column arithmetic can only be measured on a section
    # that carries headings - this fixture is the one with the most.
    controller.goToSection(heading_section)
    pump(2 * RESIZE_DEBOUNCE_MS)
    full = float(window.property("width"))

    # The table of contents comes up with the book (ADR-015): a reader who has never
    # pressed `T` must still see that this book has one.
    assert visible(window, "tocPanel")
    assert not visible(window, "outlinePanel")
    panel = window.property("sidePanelWidth")
    toc = item(window, "tocPanel")
    assert (toc.x(), toc.width()) == (0.0, panel)
    assert _column(window) == (panel, full - panel)

    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    outline = item(window, "outlinePanel")
    assert visible(window, "outlinePanel")
    # Read again, not carried over: two columns share what one column took, and on a
    # window too narrow for two full panels the room comes out of the panels rather
    # than out of the text (ADR-014 ②).
    panel = window.property("sidePanelWidth")
    start, width = _column(window)
    assert (start, width) == (panel, full - 2 * panel)
    assert (outline.x(), outline.width()) == (start + width, panel)
    assert outline.x() + outline.width() == full

    # Closing it again hands the width back to the page.
    controller.toggleToc()
    assert not visible(window, "tocPanel")
    assert _column(window) == (0.0, full - window.property("sidePanelWidth"))


def test_the_page_is_set_for_the_column_not_the_window(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    """The point of occupying space: no panel is ever laid over the text."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    controller.goToSection(heading_section)
    controller.scrollBy(3000)                  # somewhere worth coming back to
    pump(2 * RESIZE_DEBOUNCE_MS)
    anchor = controller._anchor_block()

    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)

    # `_view_size` is the page box the sections are laid out for, and the point of
    # this test is precisely the seam between the two layers.
    assert controller._view_size.width() == window.property("pageColumnWidth")
    assert controller._view_size.width() < window.property("width")
    # Re-setting the section for the narrower column is what taking a column costs
    # (ADR-014).  It is paid in milliseconds, not in the reader's place (FR-051): the
    # block the window is on survives the re-set, because the place is a block index
    # plus a fraction of it rather than a page number (ADR-011).
    assert controller._anchor_block() == anchor


def test_the_outline_column_is_absent_when_it_has_nothing_to_show(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    """No content, no column - and the toggle that would open one does nothing.

    FR-019: the column only exists while there is something to fill it with.
    """
    _, window, controller = shell
    assert not visible(window, "outlinePanel")

    # Toggled with nothing to list: the column is not opened at all, rather than
    # standing empty beside the text.
    controller.toggleOutline()
    assert not controller.outlineVisible
    assert not visible(window, "outlinePanel")
    assert window.property("openColumns") == 0

    assert controller.openBook(str(kangpo_path))
    controller.goToSection(heading_section)
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.outlineAvailable
    assert not visible(window, "outlinePanel")      # the refusal still stands
    assert window.property("openColumns") == 1      # only the map is up

    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert visible(window, "outlinePanel")
    assert window.property("openColumns") == 2      # table of contents + outline

    controller.closeBook()
    assert not visible(window, "outlinePanel")
    assert not visible(window, "tocPanel")
    assert window.property("openColumns") == 0
    assert window.property("pageColumnWidth") == window.property("width")


def test_a_section_without_headings_gives_the_column_back(
    shell,
    kangpo_path: Path,
    binan_path: Path,
    heading_section: int,
    headingless_section: int,
) -> None:
    """The column leaves when the section has no heading, and nobody presses `O`.

    The outline is heading-only (ADR-016).  A section that carries no heading at all
    therefore has nothing to list, and the rule "no content, no column" (FR-019) has
    to be what puts the width back - not the reader noticing an empty strip.
    """
    _, window, controller = shell
    # Opened where there are headings to fill it, because the controller will not
    # open an empty column (FR-019) - and the toggle outlives the book change, so the
    # outline is still on when the headingless section arrives.
    assert controller.openBook(str(kangpo_path))
    controller.goToSection(heading_section)
    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.outlineVisible
    assert visible(window, "outlinePanel")

    assert controller.openBook(str(binan_path))
    controller.goToSection(headingless_section)
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.outlineVisible               # still toggled on, nothing to list
    assert not controller.outlineAvailable
    assert not visible(window, "outlinePanel")
    # The outline column claims no width at all: only the map is in the sum, whether
    # or not this book has one.
    assert window.property("openColumns") == (1 if controller.tocVisible else 0)
    assert _column(window)[1] == window.property("pageColumnWidth")


def test_a_narrow_window_keeps_both_columns_usable(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    controller.goToSection(heading_section)
    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)

    set_window_size(window, 720)

    panel = window.property("sidePanelWidth")
    start, width = _column(window)
    outline = item(window, "outlinePanel")
    assert panel == 200
    assert width >= 320
    assert (start, start + width) == (panel, outline.x())
    assert outline.x() + outline.width() == 720
    assert window.property("pageColumnWidth") == width


# ------------------------------------------------------------ the reader's width


def _divider_grab(window) -> tuple[int, int]:
    """Where to take hold of the divider: its middle, in window coordinates."""
    return centre(item(window, "tocDivider"))


def test_the_contents_column_can_be_dragged_wider_and_narrower(
    shell, kangpo_path: Path
) -> None:
    """The reader sets the width of the left column by dragging its edge (FR-078).

    Both directions, because each says something the automatic width does not.  Wider
    is what the automatic width withholds (it stops at 320 px on a wide window);
    narrower is what a reader who wants the text to have the room asks for.  The
    pointer's movement *is* the change in width - the edge stays under the pointer -
    which is why this is a drag and not a jump.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    full = float(window.property("width"))
    automatic = window.property("sidePanelWidth")
    assert window.property("panelWidth") == 0       # never dragged: the window decides
    assert visible(window, "tocDivider")

    grab = _divider_grab(window)
    drag(window, grab, (grab[0] + 80, grab[1]))
    panel = window.property("sidePanelWidth")
    assert panel == automatic + 80
    assert window.property("panelWidth") == panel
    toc = item(window, "tocPanel")
    assert (toc.x(), toc.width()) == (0.0, panel)
    assert _column(window) == (panel, full - panel)
    # And the page box follows the column, so the text is set for the width the reader
    # just made (FR-051) - one re-layout, after the divider was let go.
    assert controller._view_size.width() == window.property("pageColumnWidth")

    # Back the other way: narrower than the automatic width, which no other operation
    # can produce.
    grab = _divider_grab(window)
    drag(window, grab, (grab[0] - 140, grab[1]))
    panel = window.property("sidePanelWidth")
    assert panel == automatic - 60
    assert _column(window) == (panel, full - panel)
    assert controller._view_size.width() == window.property("pageColumnWidth")


def test_a_drag_stops_where_the_window_runs_out(shell, kangpo_path: Path) -> None:
    """A drag cannot squeeze the page out of the window (FR-078 / ADR-014 ②).

    The bounds are load-bearing rather than cosmetic: they are what keeps the columns
    tiling the window instead of overlapping it, and what keeps a 160 px floor under a
    title that still has to wrap legibly.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    full = float(window.property("width"))

    # Far past the right edge of the window: the column stops at what the window can
    # afford, and the text column still has its 320 px.
    grab = _divider_grab(window)
    drag(window, grab, (int(full) + 300, grab[1]))
    allowed = window.property("allowedPanelWidth")
    assert window.property("sidePanelWidth") == allowed
    assert _column(window) == (allowed, full - allowed)
    assert full - allowed >= 320

    # Far past the left edge: the floor, not a vanished column.
    grab = _divider_grab(window)
    drag(window, grab, (grab[0] - 400, grab[1]))
    assert window.property("sidePanelWidth") == 160
    assert _column(window) == (160.0, full - 160)


def test_the_dragged_width_is_what_the_next_session_opens_with(
    shell, kangpo_path: Path, tmp_path: Path
) -> None:
    """The width is a preference, not a session detail (FR-078 / ADR-022).

    Two sessions over one configuration file: the first drags, the second is built the
    way ``main()`` builds it and opens the same book.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    grab = _divider_grab(window)
    drag(window, grab, (grab[0] - 60, grab[1]))
    chosen = window.property("sidePanelWidth")
    assert chosen != window.property("autoPanelWidth")

    # Written once, on release - and what is written is the width that was on screen.
    assert SettingsStore(tmp_path / "config.json").panel_width() == chosen

    _, next_window, next_controller = build_shell(tmp_path / "config.json")
    assert next_controller.panelWidth == chosen
    assert next_window.property("panelWidth") == chosen
    assert next_controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert item(next_window, "tocPanel").width() == chosen
    assert (
        next_window.property("pageColumnWidth")
        == next_window.property("width") - chosen
    )


def test_a_double_click_on_the_divider_forgets_the_width(
    shell, kangpo_path: Path, tmp_path: Path
) -> None:
    """The way back to the window's own width, in one gesture (FR-078).

    Without it a reader who has dragged once is stuck with a number they chose for one
    display, and the only way out would be editing the configuration file by hand.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(2 * RESIZE_DEBOUNCE_MS)
    grab = _divider_grab(window)
    drag(window, grab, (grab[0] - 60, grab[1]))
    assert window.property("panelWidth") > 0

    double_click(window, _divider_grab(window))
    assert window.property("panelWidth") == 0
    assert window.property("sidePanelWidth") == window.property("autoPanelWidth")
    # Forgotten, not frozen at today's size: the file says "no preference" again.
    assert SettingsStore(tmp_path / "config.json").panel_width() == 0


def test_the_divider_bounds_are_the_ones_the_file_keeps(shell) -> None:
    """The two numbers live in two places, so they are compared instead of trusted.

    ``Main.qml`` has to clamp for itself while the drag is in flight (Python is not in
    that loop), and ``settings_store`` clamps again on read and on write - a drift
    between the two would show up as a width that changes on restart (ADR-022).
    """
    _, window, _ = shell
    assert window.property("minPanelWidth") == _PANEL_WIDTH_MIN
    assert window.property("maxPanelWidth") == _PANEL_WIDTH_MAX


def test_the_settings_drawer_leaves_the_columns_alone(
    shell, kangpo_path: Path, warnings
) -> None:
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    assert visible(window, "tocPanel")

    controller.toggleSettings()
    panel = item(window, "settingsPanel")
    assert visible(window, "settingsPanel")
    # `visible` says nothing here: the closed drawer is still `visible` and only slid
    # past the window's edge, so it has to be asked where it is instead.
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert on_screen(panel)
    # The drawer is the one panel that takes no column of its own - it floats over
    # the text.  The table of contents is on the other side of the text, so the
    # drawer has nothing to take from it and the map stays on screen (FR-013);
    # opening it costs no layout change at all.
    assert visible(window, "tocPanel")
    assert window.property("openColumns") == 1
    assert _column(window) == (
        window.property("sidePanelWidth"),
        window.property("pageColumnWidth"),
    )

    # The drawer slides in, so wait for it to arrive before asking where it is.
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert panel.x() == window.property("width") - panel.width()

    # A click on the text beside it dismisses the drawer - and only the drawer: the
    # shield that catches it sits on top of the reading area, so this must neither
    # move the reader nor put the map away.
    page = item(window, "pageArea")
    before = (controller.sectionIndex, controller.scrollOffset)
    click(window, at(page, 120, 400))
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert not controller.settingsVisible
    assert controller.tocVisible
    assert (controller.sectionIndex, controller.scrollOffset) == before
    assert warnings == []


def test_only_settings_shields_the_page_from_clicks(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    """A panel beside the text must not swallow a click meant for the text (FR-062)."""
    _, window, controller = shell
    assert not visible(window, "settingsShield")

    assert controller.openBook(str(kangpo_path))
    controller.goToSection(heading_section)
    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    # Back to the documented default size: the drawer is a fixed-width panel beside
    # the text, and "beside it" has to be a place on the page - on a display smaller
    # than the drawer plus a column, the middle of the text *is* the drawer.
    set_window_size(window)
    assert controller.tocVisible and controller.outlineVisible

    # Both columns stand, and neither of them is a shield: a click in the middle of
    # the text lands on the text.
    assert not visible(window, "settingsShield")
    page = item(window, "pageArea")
    click(window, at(page, page.width() / 2, 400))
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.tocVisible and controller.outlineVisible

    controller.toggleSettings()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert visible(window, "settingsShield")

    click(window, at(page, page.width() / 2, 400))
    pump(2 * RESIZE_DEBOUNCE_MS)
    # Dismissed the drawer; the map, which is not the drawer, is untouched.
    assert not controller.settingsVisible
    assert controller.tocVisible
