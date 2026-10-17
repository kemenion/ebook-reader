"""自动滚屏: the text moving by itself, and the one switch that stops it (FR-094).

The feature makes three claims a reader would notice being wrong, and each of them is
asked here through the shell rather than through a call: that a *key* and a *menu row*
really start it (a shortcut Qt cannot parse is registered and never fires), that the
timer really moves the text at the pace it announces, and that **nothing but the
switch stops it** - a wheel notch, a key, a pan, a chapter jump all change the place
the text drifts *from* and leave it drifting, because a reader who wheels back half a
page to re-read a paragraph has not asked to be handed the page back.  The one thing a
hand does do is hold it still for as long as the button is down; that is a hold, not a
stop, and the text carries on when the hand comes off.

The arithmetic of one step, and the shape of a hand-edited pace name, are
``tests/unit/test_autoscroll_pace.py``: they need no window, and none of them would be
clearer for having one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QObject

from ebook_reader.app.controller import _AUTO_SCROLL_DEFAULT_PACE, _OFFSET_EPSILON

from conftest import (
    at,
    build_shell,
    centre,
    click,
    drag,
    item,
    open_long_section,
    press,
    pump,
    set_window_size,
    wheel,
)

#: The right-click that opens the menu, in a place the text column certainly covers
#: (``test_menu.py`` opens it in the same one).
MENU_CLICK = (700, 400)


def menu(window) -> QObject:
    found = window.findChild(QObject, "readerMenu")
    assert found is not None, "readerMenu is missing from Main.qml"
    return found


def menu_row(window, name: str) -> QObject:
    """A row of the menu by object name - reachable even with its submenu shut."""
    for child in menu(window).findChildren(QObject):
        if child.objectName() == name:
            return child
    raise AssertionError(f"no menu row named {name!r}")


def open_menu(window) -> None:
    """A real right-click, so the rows below are laid out where they can be clicked."""
    click(window, MENU_CLICK, right=True)
    pump(200)
    assert menu(window).property("visible"), "the right button did not open the menu"


def messages(controller) -> list[str]:
    """Everything the controller says to the reader from now on (FR-070 / FR-094)."""
    collected: list[str] = []
    controller.statusMessage.connect(collected.append)
    return collected


# ---------------------------------------------------------- starting and stopping


def test_the_key_starts_the_drift_and_the_same_key_ends_it(
    shell, kangpo_path: Path
) -> None:
    """`A`, through Qt's own key delivery: the halfway state is the reader's to undo."""
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    assert not controller.autoScrollRunning

    press(window, "A")
    assert controller.autoScrollRunning

    press(window, "A")
    assert not controller.autoScrollRunning


def test_the_menu_row_starts_the_drift_and_takes_the_stop_in_the_same_place(
    shell, kangpo_path: Path, warnings
) -> None:
    """The row a reader who never read the documentation can see and click (FR-071)."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    # The documented default size: where the menu lands relative to the click depends
    # on the window, and this test clicks a row inside the popup it just opened.
    set_window_size(window)
    open_long_section(controller, kangpo_path)

    assert bool(menu_row(window, "menuAutoScroll").property("enabled"))
    assert not bool(menu_row(window, "menuAutoScroll").property("marked"))

    open_menu(window)
    click(window, centre(menu_row(window, "menuAutoScroll")))
    pump(200)
    assert not menu(window).property("visible")        # it closes behind itself
    assert controller.autoScrollRunning
    assert bool(menu_row(window, "menuAutoScroll").property("marked"))

    open_menu(window)
    click(window, centre(menu_row(window, "menuAutoScroll")))
    pump(200)
    assert not controller.autoScrollRunning
    assert not bool(menu_row(window, "menuAutoScroll").property("marked"))
    assert warnings == []


def test_the_row_is_greyed_out_until_there_is_a_book(shell, kangpo_path: Path) -> None:
    """A greyed row answers "why did nothing happen"; a silent no-op does not."""
    _, window, controller = shell
    assert not bool(menu_row(window, "menuAutoScroll").property("enabled"))
    assert controller.autoScrollStatus == ""

    controller.startAutoScroll()                       # without a book: nothing to move
    assert not controller.autoScrollRunning


def test_a_chapter_that_fits_on_one_screen_says_so_instead_of_staring_blank(
    shell, kangpo_path: Path
) -> None:
    """The cover has nowhere to go, and the reader is told rather than left waiting."""
    _, _, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    controller.goToSection(0)                          # the cover fits on one screen
    pump(120)
    if controller.scrollMax > 0.5:  # pragma: no cover - the reference book's cover
        pytest.skip("the reference book's first section can scroll")

    said = messages(controller)
    controller.startAutoScroll()

    assert not controller.autoScrollRunning
    assert said and "只有一屏" in said[-1]


@pytest.mark.parametrize(
    "gesture",
    [
        pytest.param(lambda c: c.scrollDown(), id="down-key"),
        pytest.param(lambda c: c.scrollPageDown(), id="page-down-key"),
        pytest.param(lambda c: c.scrollBy(120.0), id="wheel"),
        pytest.param(lambda c: c.scrollToTop(), id="home-key"),
        pytest.param(lambda c: c.scrollToBottom(), id="end-key"),
        pytest.param(lambda c: c.scrollToFraction(0.5), id="scroll-bar"),
        pytest.param(lambda c: c.scrollToSectionEnd(), id="section-end"),
        pytest.param(lambda c: c.nextSection(), id="next-chapter"),
        pytest.param(lambda c: c.previousSection(), id="previous-chapter"),
        pytest.param(lambda c: c.goToNextSection(), id="next-chapter-line"),
        pytest.param(lambda c: c.goToSection(6), id="contents-row"),
        pytest.param(lambda c: c.scrollToFraction(0.0), id="a-move-that-moves-nothing"),
    ],
)
def test_no_gesture_the_reader_makes_ends_the_drift(
    controller, kangpo_path: Path, gesture
) -> None:
    """Every way of moving the window leaves the switch where the reader put it.

    The mode is a *mode*: a reader who wheels back to re-read a paragraph, or jumps to
    another chapter, is still being read to, and only the row (or the key) that started
    it may end it.  A hand on the page holds the text still for as long as it is down -
    see ``test_the_page_holds_still_while_a_hand_is_on_it`` - but these are all moves
    that have already finished, so the drift has to survive each of them.
    """
    open_long_section(controller, kangpo_path)
    controller.startAutoScroll()
    assert controller.autoScrollRunning

    gesture(controller)

    assert controller.autoScrollRunning, "a gesture switched the drift off"


def test_a_wheel_notch_on_the_page_leaves_it_running(shell, kangpo_path: Path) -> None:
    """The same claim through the shell, where the notch has to reach the controller.

    Two things at once: the notch moved the page, and half a second later the *timer*
    is still moving it - further along than where the notch left it, which is a
    stronger statement than the flag being true.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    controller.startAutoScroll()
    before = controller.scrollOffset

    page = item(window, "pageView")
    wheel(window, at(page, page.width() / 2, 400), notches=1)

    assert controller.scrollOffset > before                # the notch did scroll
    assert controller.autoScrollRunning, "the notch switched the drift off"
    nudged = controller.scrollOffset
    pump(500)
    assert controller.scrollOffset > nudged                # ... and on it goes


def test_the_page_holds_still_while_a_hand_is_on_it(
    shell, kangpo_path: Path
) -> None:
    """Held, not switched off: the text waits for the hand to come off (FR-094).

    Two gestures need this and both would be broken without it: a pan recomputes its
    offset from where it started, so a tick in between would be undone by the next
    pointer event, and a passage would slide out from under the pointer marking it.
    The hold costs no catch-up step either - the clock is advanced by the tick that
    does nothing.
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)
    controller.startAutoScroll()

    # ... a pan (FR-079)
    controller.beginDrag(300.0)
    held_at = controller.scrollOffset
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset == held_at
    assert controller.autoScrollRunning                    # held, not stopped
    controller.endDrag()
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset > held_at               # and off it goes again

    # ... and a passage being marked (FR-070)
    controller.beginSelection(200.0, 300.0)
    marked_at = controller.scrollOffset
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset == marked_at
    controller.endSelection()
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset > marked_at
    assert controller.autoScrollRunning


def test_opening_another_book_keeps_the_drift_going(
    shell, kangpo_path: Path, binan_path: Path
) -> None:
    """The mode belongs to the reader, not to the file they happen to have open.

    The old book's place is written before the swap (a drifting page's periodic saves
    can be seconds behind), and the drift itself carries on in the new book from
    wherever *it* was remembered - not from the top, and not switched off.
    """
    _, _, controller = shell
    first = open_long_section(controller, kangpo_path)
    controller.startAutoScroll()
    controller.autoScrollStep(1.0)
    assert controller.autoScrollRunning
    assert controller.scrollOffset > 0.0

    assert controller.openBook(str(binan_path))
    pump(240)

    assert controller.autoScrollRunning, "opening another book switched the drift off"
    assert controller._store.book_state(str(kangpo_path)).section == first
    assert controller.sectionIndex != first, "the second book opened on the first's page"
    for index in range(controller.sectionCount):
        controller.goToSection(index)
        if controller.scrollMax > controller.viewportHeight:
            break
    else:  # pragma: no cover - the second reference book has long chapters
        pytest.skip("the second reference book has no section long enough to scroll in")
    assert controller.autoScrollRunning, "a chapter jump switched the drift off"
    controller.scrollToTop()
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset > 0.0                   # drifting in the new book
    controller.stopAutoScroll()


# ------------------------------------------------------------------- how fast it goes


def test_a_minute_of_drifting_is_the_pace_the_menu_promises(shell, kangpo_path: Path) -> None:
    """The three paces, measured in the lines the page is actually drawn with.

    The distance is asked for as a whole minute rather than waited for, which is the
    reason :meth:`autoScrollStep` is a slot of its own: what the reader chooses is a
    speed in lines a minute, and this is that sentence as an assertion.
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)

    for pace, lines in (("slow", 6.0), ("normal", 12.0), ("fast", 24.0)):
        controller.scrollToTop()
        pump(60)
        controller.setAutoScrollPace(pace)
        assert controller.autoScrollPace == pace
        controller.startAutoScroll()

        controller.autoScrollStep(60.0)                # one minute of reading, now

        assert controller.scrollOffset == pytest.approx(
            lines * controller.lineStep, rel=1e-6
        ), f"the {pace} pace is not {lines} lines a minute"
        controller.stopAutoScroll()


def test_the_timer_really_moves_the_text_at_about_that_speed(
    shell, kangpo_path: Path
) -> None:
    """The one thing only the shell can answer: the clock is wired to the page.

    A second of real time is one sixtieth of the middle pace - about nine pixels - and
    the tolerance is generous because a timer is a timer: what is being ruled out is a
    page that stands still, or one that jumps a screen.
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)
    per_second = controller.autoScrollLinesPerMinute * controller.lineStep / 60.0

    controller.startAutoScroll()
    pump(1000)
    moved = controller.scrollOffset
    controller.stopAutoScroll()

    assert moved == pytest.approx(per_second, rel=0.25), (
        f"a second of drifting moved {moved:.1f} px, not about {per_second:.1f} px"
    )
    # ... and stopping it is stopping it: nothing moves on afterwards.
    pump(300)
    assert controller.scrollOffset == moved


def test_the_slow_pace_still_moves_even_though_a_tick_is_a_quarter_of_a_pixel(
    shell, kangpo_path: Path
) -> None:
    """The remainder a tick cannot use is carried, not dropped (the 缺 for a "stuck" page).

    At the slow pace one 50 ms tick grants a quarter of a pixel and the offset refuses
    moves below half a pixel, so a tick-by-tick step would leave the slow pace doing
    nothing at all - the failure a reader would report as "the slow setting is broken".
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)
    controller.setAutoScrollPace("slow")
    tick = 0.05 * 6.0 * controller.lineStep / 60.0
    # The layout this test assumes: a 50 ms tick of the slow pace is under a pixel, and
    # four of them are over one - otherwise the paragraph below is about a different page.
    assert 0.0 < tick < _OFFSET_EPSILON
    assert 4 * tick > _OFFSET_EPSILON

    controller.startAutoScroll()

    controller.autoScrollStep(0.05)                    # one tick on its own ...
    assert controller.scrollOffset == 0.0              # ... is under what the window shows

    for _ in range(3):                                 # three more, and it is a pixel
        controller.autoScrollStep(0.05)
    assert controller.scrollOffset > 0.0
    assert controller.scrollOffset <= 4 * tick         # and no further than four are worth
    controller.stopAutoScroll()


# ------------------------------------------------------------------- the two ends


def test_the_drift_rides_on_into_the_next_chapter(shell, kangpo_path: Path) -> None:
    """The end of a chapter is not the end of the drift: it is what the reader asked for.

    Turning the page by hand is exactly what this mode exists to save them from, so the
    next chapter starts by itself - and the drift has to survive its own chapter change,
    which is the interesting half: ``_step_section`` is also the path the reader's own
    `]` and `[` take, and the mode has to come out the other side of it still running.
    """
    _, _, controller = shell
    index = open_long_section(controller, kangpo_path)
    assert controller.hasNextSection, "the reference book's long section is the last one"
    controller.scrollToBottom()

    controller.startAutoScroll()
    controller.autoScrollStep(1.0)                     # one second, at the end of the chapter

    assert controller.autoScrollRunning, "the drift stopped itself at the chapter end"
    assert controller.sectionIndex == index + 1
    assert controller.scrollOffset < controller.lineStep   # the new chapter's top

    # ... and it really is still drifting: a second later it has moved on again.
    controller.autoScrollStep(1.0)
    assert controller.scrollOffset > 0.0
    controller.stopAutoScroll()


def test_a_one_screen_chapter_does_not_walk_the_reader_off_it(
    shell, kangpo_path: Path
) -> None:
    """A cover with the drift switched on is not the end of a chapter.

    With the switch no longer cleared by gestures, a reader can carry it into a section
    that fits on one screen (the cover, a title page).  There its "end" is also its
    beginning, so a drift that took it for the column end would turn to chapter one by
    itself, unasked - which is why a section with nowhere to go is skipped rather than
    stepped over.
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)
    controller.startAutoScroll()
    controller.goToSection(0)                          # the cover fits on one screen
    pump(120)
    if controller.scrollMax > _OFFSET_EPSILON:  # pragma: no cover - the reference cover
        pytest.skip("the reference book's first section can scroll")

    controller.autoScrollStep(5.0)

    assert controller.sectionIndex == 0, "the drift walked off the cover"
    assert controller.scrollOffset == pytest.approx(0.0)
    assert controller.autoScrollRunning                # still on: there is just nowhere to go
    controller.stopAutoScroll()


def test_the_end_of_the_book_stops_and_says_so(shell, kangpo_path: Path) -> None:
    """The last section has nothing behind it, so the drift ends rather than grinding on."""
    _, _, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    last = controller.sectionCount - 1
    controller.goToSection(last)
    pump(120)
    assert not controller.hasNextSection
    if controller.scrollMax <= _OFFSET_EPSILON:  # pragma: no cover - the afterword is long
        pytest.skip("the reference book's last section fits on one screen")
    controller.scrollToBottom()

    said = messages(controller)
    controller.startAutoScroll()
    controller.autoScrollStep(1.0)

    assert not controller.autoScrollRunning
    assert controller.scrollOffset == pytest.approx(controller.scrollMax)
    assert said and "全书末尾" in said[-1]


# --------------------------------------------------------------------- what it shows


def test_the_status_bar_says_the_text_is_moving(shell, kangpo_path: Path) -> None:
    """The state is on screen while it lasts, and the diagnostics come back after it.

    Without it the reader has no way to tell "drifting very slowly" from "stuck", and
    the mode's whole point is that it is quiet enough to mistake for either (FR-094).
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    label = item(window, "messageLabel")

    controller.startAutoScroll()
    assert "自动滚屏" in controller.autoScrollStatus
    assert "12 行/分" in controller.autoScrollStatus
    # The open just said something of its own, and a passing word outranks the state
    # for a few seconds (FR-070) - so the label is read once it has had its turn.
    pump(2600)
    assert "自动滚屏" in label.property("text")

    controller.stopAutoScroll()
    pump(120)
    assert "自动滚屏" not in label.property("text")
    assert "打开" in label.property("text")            # the layout diagnostics again


def test_the_pace_is_remembered_and_the_drifting_is_not(
    shell, tmp_path: Path, kangpo_path: Path
) -> None:
    """Tomorrow starts at the same speed and with the page still (FR-094).

    Two statements in one, and they are deliberately opposite: the pace is a reading
    preference and belongs in the file, while "it was moving when I closed it" does
    not - a page that starts drifting on startup would move under a reader who asked
    for nothing.
    """
    _, _, controller = shell
    open_long_section(controller, kangpo_path)

    assert controller.autoScrollPace == _AUTO_SCROLL_DEFAULT_PACE
    assert controller.autoScrollPaceLabel == "中"

    controller.setAutoScrollPace("fast")
    assert controller.autoScrollPace == "fast"
    assert controller.autoScrollPaceLabel == "快"
    controller.startAutoScroll()
    assert controller.autoScrollRunning

    engine, window, next_session = build_shell(tmp_path / "config.json")
    assert next_session.autoScrollPace == "fast"
    assert next_session.autoScrollPaceLabel == "快"
    assert not next_session.autoScrollRunning
    assert next_session.autoScrollStatus == ""
    assert window.findChild(QObject, "readerMenu") is not None
    engine.deleteLater()


# --------------------------------------------------------------- what it writes


def test_a_drag_writes_the_position_once_instead_of_per_pointer_event(
    shell, kangpo_path: Path
) -> None:
    """A stream of moves is not a stream of file writes (FR-094 / ADR-024).

    A hand-pan drag used to write `config.json` on every pointer event - sixty atomic
    writes a second for a position a few pixels further on, each of them fsync'd.  The
    place is now written when the reader lets go (and by the throttle while the hand is
    still moving), which is the same rule the auto-scroll needed: the file may lag the
    page by a few seconds, but never by a gesture.
    """
    _, window, controller = shell
    open_long_section(controller, kangpo_path)
    page = item(window, "pageView")

    original = controller._store.save
    written: list[float] = []

    def counting() -> bool:
        written.append(controller.scrollOffset)
        return original()

    controller._store.save = counting  # type: ignore[method-assign]
    # A pointer moving *up* the page pulls the text up with it (FR-079's direction):
    # 200 px of pointer is 200 px of column.
    drag(window, at(page, page.width() / 2, 600), at(page, page.width() / 2, 400))

    moved_to = controller.scrollOffset
    assert moved_to > 0.0                                  # the drag did move the page
    assert len(written) == 1, f"the drag wrote the file {len(written)} times"
    # ... and what it wrote is where the hand let go, not where it started.
    assert written == [moved_to]
