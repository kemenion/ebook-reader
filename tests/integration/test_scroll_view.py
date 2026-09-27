"""Scrolling one continuous column: the keys, the wheel, and the scroll bar (ADR-016).

Paging is gone, so the questions these tests ask are the ones the new model raises:
does every shortcut Qt is given actually *fire* (a sequence Qt cannot parse is
registered and never fires - the scroll keys were silently dead that way), does each
key move the text by the amount the documentation claims, does the wheel move the
text and nothing else, does the view keep the offset/image relationship that makes
scrolling cheap (NFR-005), and does the end of a chapter offer the way into the next
one?

Everything here goes through real events - ``QTest`` key clicks and hand-built wheel
events - rather than through the controller, because "the key reaches the shortcut"
is a different claim from "the controller scrolls", and only the first one can be
broken by QML.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ebook_reader.typeset.renderer import WINDOW_QUANTUM, quantise_offset

from conftest import (
    RESIZE_DEBOUNCE_MS,
    at,
    centre,
    click,
    item,
    on_screen,
    press,
    pump,
    shortcut_keys,
    shortcut_sequences,
    visible,
    wheel,
)

#: Float arithmetic over layout pixels: a twentieth of a pixel is far below anything
#: a reader could see and far above the noise in the sums.
TOLERANCE = 0.05

#: Every shortcut the shell registers, in the spelling ``QKeySequence`` prints for it
#: (``Escape`` is written that way in Main.qml and canonicalises to ``Esc``).
DOCUMENTED = frozenset(
    {
        "Down",
        "Up",
        "PgDown",
        "Space",
        "Right",
        "PgUp",
        "Backspace",
        "Left",
        "Home",
        "End",
        "]",
        "[",
        "T",
        "O",
        "S",
        "Esc",
        "Ctrl+O",
        "Ctrl+Q",
        "Ctrl++",
        "Ctrl+=",
        "Ctrl+-",
        "F11",
    }
)


def _open_long_section(controller, path: Path, *, screens: float = 2.0) -> int:
    """Open *path* on a section with room to scroll, and return its index.

    Deliberately *not* section 0: both books open on a cover that fits on one screen,
    where every scroll assertion would hold trivially.
    """
    assert controller.openBook(str(path))
    pump(240)
    for index in range(controller.sectionCount):
        controller.goToSection(index)
        if controller.scrollMax > screens * controller.viewportHeight:
            break
    else:
        pytest.skip("reference book has no section long enough to scroll in")
    controller.scrollToTop()
    pump(120)
    assert controller.scrollOffset == 0.0
    return index


def _page_step(controller) -> float:
    """One screen, less one line - the arithmetic ``scrollPageDown`` uses."""
    return max(1.0, controller.viewportHeight - controller.lineStep)

# ------------------------------------------------------------------- registration


def test_the_shell_registers_every_documented_shortcut(shell, warnings) -> None:
    """The list itself, because a missing entry is a key the reader cannot press."""
    _, window, _ = shell
    declared = shortcut_sequences(window)

    assert shortcut_keys(window) == DOCUMENTED
    # ... and no two of them are the same sequence, which would be one shortcut
    # quietly shadowing another.
    assert len(set(declared)) == len(declared) == len(DOCUMENTED)
    assert warnings == []


def test_every_registered_sequence_is_one_qt_can_parse(shell) -> None:
    """A name Qt does not know has no key at all, and a keyless shortcut never fires.

    ``QKeySequence.fromString("PageDown")`` gives a sequence that prints as nothing but
    still *counts* as one - so ``isEmpty()`` says no and the mistake stays invisible.
    ``fromString("BracketRight")`` is the same, which is how `PgDown` and `]` came to
    be registered-but-dead.
    """
    from PySide6.QtGui import QKeySequence

    _, window, _ = shell
    broken = [
        text
        for text in shortcut_sequences(window)
        if QKeySequence.fromString(text).toString() != text.replace("Escape", "Esc")
    ]
    assert broken == [], f"Main.qml registers sequences Qt cannot read: {broken}"

    dead = QKeySequence.fromString("PageDown")            # the old spelling
    assert dead.count() == 1 and dead.toString() == ""     # one key, and it is nothing
    assert not dead.isEmpty()                              # ... which is why it hid
    assert QKeySequence.fromString("BracketRight").toString() == ""  # and the other one


# ---------------------------------------------------------------------- the keys


def test_the_arrow_keys_move_one_line(shell, kangpo_path: Path) -> None:
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)

    press(window, "Down")
    assert controller.scrollOffset == pytest.approx(controller.lineStep, abs=TOLERANCE)
    press(window, "Down")
    assert controller.scrollOffset == pytest.approx(
        2 * controller.lineStep, abs=TOLERANCE
    )
    press(window, "Up")
    assert controller.scrollOffset == pytest.approx(controller.lineStep, abs=TOLERANCE)


def test_the_page_keys_move_one_screen(shell, kangpo_path: Path) -> None:
    """`PgDown`, `Space` and `Right` are one action; so are the three keys up."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    step = _page_step(controller)

    for key in ("PgDown", "Space", "Right"):
        before = controller.scrollOffset
        press(window, key)
        moved = pytest.approx(before + step, abs=TOLERANCE)
        assert controller.scrollOffset == moved, key

    for key in ("PgUp", "Backspace", "Left"):
        before = controller.scrollOffset
        press(window, key)
        moved = pytest.approx(before - step, abs=TOLERANCE)
        assert controller.scrollOffset == moved, key


def test_the_ends_of_the_chapter_are_reachable(shell, kangpo_path: Path) -> None:
    """`End` stops where the last line does, and `Home` comes back (FR-074)."""
    _, window, controller = shell
    index = _open_long_section(controller, kangpo_path)

    press(window, "End")
    assert controller.scrollOffset == pytest.approx(controller.scrollMax, abs=TOLERANCE)
    assert controller.atSectionEnd
    assert controller.hasNextSection
    assert controller.nextSectionTitle
    assert visible(window, "nextChapterStrip")

    press(window, "Home")
    assert controller.scrollOffset == 0.0
    assert not controller.atSectionEnd
    assert not visible(window, "nextChapterStrip")
    assert controller.sectionIndex == index


def test_the_bracket_keys_step_one_chapter(shell, kangpo_path: Path) -> None:
    """`]` and `[` change chapter: the reader lands at the start of the next one."""
    _, window, controller = shell
    index = _open_long_section(controller, kangpo_path)
    assert index > 0, "this test needs a chapter before the one it starts in"

    press(window, "]")
    assert controller.sectionIndex == index + 1
    assert controller.scrollOffset == 0.0

    press(window, "[")
    assert controller.sectionIndex == index
    # Backwards lands at the end of the chapter: the reader wants what they just came
    # from, not the start of the chapter before it.
    assert controller.scrollOffset == pytest.approx(controller.scrollMax, abs=TOLERANCE)


def test_the_navigation_keys_do_nothing_without_a_book(shell) -> None:
    """No book, no text: the keys must be inert rather than an error."""
    _, window, controller = shell
    for key in ("Down", "PgDown", "End", "]", "["):
        press(window, key)
    assert (controller.sectionIndex, controller.scrollOffset) == (0, 0.0)

# --------------------------------------------------------------------- the panels


def test_the_panel_keys_toggle_the_columns(
    shell, kangpo, kangpo_path: Path, heading_section: int
) -> None:
    """`T`, `O`, `S` and `Esc`, through real key events (FR-013 / FR-019)."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    assert controller.tocVisible          # the map comes up with the book (FR-013)

    press(window, "T")
    assert not controller.tocVisible
    press(window, "T")
    assert controller.tocVisible

    # The outline only opens where there is something to list, so jump to a section
    # that has headings - the book opens on its cover, which has none.
    controller.goToSection(heading_section)
    pump(240)
    assert controller.outlineAvailable
    press(window, "O")
    assert controller.outlineVisible and visible(window, "outlinePanel")
    press(window, "O")
    assert not controller.outlineVisible

    press(window, "S")
    assert controller.settingsVisible
    drawer = item(window, "settingsPanel")
    assert on_screen(drawer)

    # `Esc` closes what is open, whichever panels those are.
    assert controller.tocVisible and controller.settingsVisible
    press(window, "Esc")
    panels = (
        controller.tocVisible,
        controller.outlineVisible,
        controller.settingsVisible,
    )
    assert not any(panels)
    pump(240)                    # the drawer slides out over 180 ms
    assert not on_screen(drawer)


def test_the_settings_drawer_yields_the_right_hand_column(
    shell, kangpo, kangpo_path: Path, heading_section: int
) -> None:
    """One column on the right, so the drawer closes the outline, not the map."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    controller.goToSection(heading_section)
    pump(240)

    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.outlineVisible and visible(window, "outlinePanel")

    controller.toggleSettings()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert controller.settingsVisible and not controller.outlineVisible
    assert not visible(window, "outlinePanel")
    assert controller.tocVisible and visible(window, "tocPanel")   # untouched



# --------------------------------------------------------------------- the wheel


def test_the_wheel_scrolls_the_text_by_the_wheel_step(shell, kangpo_path: Path) -> None:
    """One notch is three lines, down or up, whichever way it is turned (FR-073)."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    page = item(window, "pageArea")
    assert controller.wheelStep == pytest.approx(3 * controller.lineStep, abs=TOLERANCE)

    wheel(window, at(page, page.width() / 2, 400))
    assert controller.scrollOffset == pytest.approx(controller.wheelStep, abs=TOLERANCE)

    wheel(window, at(page, page.width() / 2, 400), -2)
    assert controller.scrollOffset == 0.0


def test_a_click_on_the_text_does_not_move_it(shell, kangpo_path: Path) -> None:
    """The click zones are gone with the pages; a click on the text is not a turn."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    page = item(window, "pageArea")

    click(window, at(page, page.width() * 0.25, 400))
    click(window, at(page, page.width() * 0.75, 800))
    pump(200)
    assert controller.scrollOffset == 0.0


def test_the_wheel_over_a_panel_leaves_the_text_alone(
    shell, kangpo, kangpo_path: Path, heading_section: int
) -> None:
    """Each panel has a column of its own, and the wheel over it belongs to the panel.

    The columns are laid out beside the text rather than over it, so this is what
    makes a panel readable while it is open: turning the wheel above the contents
    does not secretly move the text underneath (FR-062).
    """
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    controller.goToSection(heading_section)
    pump(240)
    controller.toggleOutline()
    pump(2 * RESIZE_DEBOUNCE_MS)
    assert visible(window, "tocPanel") and visible(window, "outlinePanel")

    for name in ("tocPanel", "outlinePanel"):
        panel = item(window, name)
        before = controller.scrollOffset
        wheel(window, at(panel, panel.width() / 2, 400))
        assert controller.scrollOffset == before, name


def test_a_scroll_inside_one_quantum_reuses_the_bitmap(
    shell, kangpo_path: Path
) -> None:
    """ADR-016 / NFR-005: Python rasterises once per quantum, QML pans the rest.

    The window is rendered at the quantised offset and drawn shifted up by the
    remainder, so the invariant is ``windowOffset + pan == scrollOffset`` - and the
    image identity only changes when the quantised offset does.
    """
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    assert controller.scrollOffset == 0.0
    assert not controller.viewImage.isNull()
    first = controller.viewImage.cacheKey()

    controller.scrollBy(WINDOW_QUANTUM / 4)
    pump(150)
    assert controller.windowOffset == 0.0
    assert controller.pan == pytest.approx(WINDOW_QUANTUM / 4, abs=TOLERANCE)
    assert controller.viewImage.cacheKey() == first        # still the same bitmap

    controller.scrollBy(WINDOW_QUANTUM)
    pump(150)
    assert controller.windowOffset == pytest.approx(WINDOW_QUANTUM, abs=TOLERANCE)
    assert controller.windowOffset + controller.pan == pytest.approx(
        controller.scrollOffset, abs=TOLERANCE
    )
    assert controller.viewImage.cacheKey() != first        # a new quantum, a new image


def test_the_window_offset_is_the_scroll_offset_on_the_quantum_grid(
    shell, kangpo_path: Path
) -> None:
    """Every key step keeps the two coordinates consistent, whatever it moves by."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)

    for key in ("Down", "PgDown", "Down", "PgUp", "End", "Home"):
        press(window, key)
        assert controller.windowOffset == quantise_offset(controller.scrollOffset), key
        assert controller.windowOffset + controller.pan == pytest.approx(
            controller.scrollOffset, abs=TOLERANCE
        )


# ---------------------------------------------------------------- the next chapter


def test_the_line_at_the_end_opens_the_next_chapter(shell, kangpo_path: Path) -> None:
    """The strip is a real button: one click and the reader is in the next part."""
    _, window, controller = shell
    index = _open_long_section(controller, kangpo_path)
    controller.scrollToBottom()
    pump(200)

    assert visible(window, "nextChapterStrip")
    click(window, centre(item(window, "nextChapterStrip")))
    pump(240)

    assert controller.sectionIndex == index + 1
    assert controller.scrollOffset == 0.0
    assert not visible(window, "nextChapterStrip")


def test_the_last_chapter_announces_nothing(shell, kangpo_path: Path) -> None:
    """There is no next chapter to name, so the strip stays away (FR-074)."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)

    controller.goToSection(controller.sectionCount - 1)
    controller.scrollToBottom()
    pump(240)
    assert not controller.hasNextSection
    assert not visible(window, "nextChapterStrip")


# --------------------------------------------------------------- the scroll bar


def test_the_scroll_bar_moves_the_window(shell, kangpo_path: Path) -> None:
    """A click on the bar is a jump to that fraction of the chapter (FR-076)."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    bar = item(window, "scrollBar")
    handle = item(window, "scrollHandle")
    assert visible(window, "scrollBar")

    # The handle is drawn where the reader is, along the bar's own usable length.
    assert handle.height() == pytest.approx(bar.property("handleHeight"), abs=TOLERANCE)
    usable = bar.height() - handle.height()
    # A click lands on a whole pixel, and one pixel of this bar is worth this much
    # text: the reached fraction can be that far off the requested one, and no more.
    per_pixel = controller.scrollMax / usable

    click(window, at(bar, bar.width() / 2, usable / 2 + handle.height() / 2))
    pump(240)
    assert controller.scrollOffset == pytest.approx(
        0.5 * controller.scrollMax, abs=per_pixel
    )
    assert handle.y() == pytest.approx(0.5 * usable, abs=1.0)

    click(window, at(bar, bar.width() / 2, usable * 0.75 + handle.height() / 2))
    pump(240)
    assert controller.scrollOffset == pytest.approx(
        0.75 * controller.scrollMax, abs=per_pixel
    )
    assert handle.y() == pytest.approx(0.75 * usable, abs=1.0)


def test_the_scroll_bar_says_how_long_the_chapter_is(shell, kangpo_path: Path) -> None:
    """The handle is one screenful of text: the bar reports how far the end is."""
    _, window, controller = shell
    _open_long_section(controller, kangpo_path)
    bar = item(window, "scrollBar")
    page = item(window, "pageArea")

    expected = max(
        30.0,
        bar.height() * min(1.0, controller.viewportHeight / controller.contentHeight),
    )
    assert bar.property("handleHeight") == pytest.approx(expected, abs=TOLERANCE)
    # It is drawn inside the text column, over the right margin (FR-076).
    assert at(bar, bar.width(), 0)[0] <= at(page, page.width(), 0)[0]


def test_the_scroll_bar_stays_away_when_there_is_nothing_to_scroll(
    shell, kangpo_path: Path
) -> None:
    """A chapter that fits on one screen has no distance to scroll, so no bar either."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)

    controller.goToSection(0)             # the cover: one image, one screenful
    pump(240)
    assert controller.scrollMax == 0.0
    assert not visible(window, "scrollBar")

