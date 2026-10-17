"""The arithmetic behind 自动滚屏: the speed, the tick, and the two ends of the day.

The auto-scroll (FR-094) is three decisions and a timer.  The decisions are here,
because each of them is a way the feature can be *wrong* that no eye catches:

* **The unit is the line, not the pixel.**  A pace in px/s would scroll twice as fast
  in words the moment the reader asked for a bigger type - the same number of lines
  puts twice the distance behind it - so the pace is lines per minute and the pixel
  step is derived from ``lineStep``, exactly the way the wheel and the arrow keys
  derive theirs (FR-073 / FR-093).
* **The distance follows the clock, not the tick count.**  Qt is free to fire a timer
  late, and a machine under load will; counting ticks turns that into text that
  crawls, which is the one thing a reader would notice.
* **A gap is not reading time.**  A suspended machine comes back with minutes missing
  from the clock, and moving by them would teleport the reader through pages they
  never saw.

Pure arithmetic, so this needs no Qt and no book.  That the timer really moves the
text, that a key or a wheel notch really stops it, and that the menu and the status
bar show it are ``tests/integration/test_autoscroll.py``.
"""

from __future__ import annotations

import pytest

from ebook_reader.app.controller import (
    _AUTO_SCROLL_DEFAULT_PACE,
    _AUTO_SCROLL_MAX_GAP,
    _AUTO_SCROLL_PACES,
    _AUTO_SCROLL_TIMER_MS,
    _OFFSET_EPSILON,
    _auto_scroll_elapsed,
    _auto_scroll_lines,
    _auto_scroll_pace,
    _auto_scroll_pace_label,
    _auto_scroll_step,
)

#: A line of body text at the default type size, measured on the reference book's
#: layout.  Only its *ratio* to the pace matters here.
LINE = 45.0


def test_the_three_paces_are_slow_middle_and_fast() -> None:
    """The numbers the menu offers, in lines a minute (FR-094)."""
    assert _auto_scroll_lines("slow") == pytest.approx(6.0)
    assert _auto_scroll_lines("normal") == pytest.approx(12.0)
    assert _auto_scroll_lines("fast") == pytest.approx(24.0)
    # Three paces, and the middle one is what a reader who never chose gets: any
    # faster and following the text is work, any slower and the page looks stuck.
    assert _AUTO_SCROLL_DEFAULT_PACE == "normal"
    assert len(_AUTO_SCROLL_PACES) == 3


def test_a_minute_of_scrolling_is_that_many_lines_of_text() -> None:
    """The pace read as a distance: twelve lines a minute is twelve ``lineStep``s."""
    assert _auto_scroll_step(60.0, 12.0, LINE) == pytest.approx(12 * LINE)


def test_the_speed_does_not_depend_on_how_the_time_was_cut_up() -> None:
    """Twenty ticks of 50 ms and one call of a second come to the same place.

    This is the whole reason the distance is derived from the clock: the timer fires
    when Qt gets to it, which means the ticks *will* be uneven.
    """
    whole = _auto_scroll_step(1.0, 12.0, LINE)
    pieces = sum(_auto_scroll_step(_AUTO_SCROLL_TIMER_MS / 1000.0, 12.0, LINE) for _ in range(20))
    assert pieces == pytest.approx(whole)


def test_a_bigger_type_scrolls_the_same_number_of_lines() -> None:
    """The unit is the line: a pace must survive the reader asking for larger text.

    Which is the point of the unit - the words per minute stay put while ``lineStep``
    changes, so the same setting feels the same at every type size.
    """
    assert _auto_scroll_step(60.0, 12.0, 36.0) / 36.0 == pytest.approx(12.0)
    assert _auto_scroll_step(60.0, 12.0, 60.0) / 60.0 == pytest.approx(12.0)
    assert _auto_scroll_step(60.0, 12.0, 60.0) > _auto_scroll_step(60.0, 12.0, 36.0)


def test_one_tick_is_smaller_than_the_window_can_notice() -> None:
    """Why a tick's distance is carried rather than applied: it is under a pixel.

    At the slow pace a single 50 ms tick is a quarter of a pixel, and ``_set_offset``
    ignores anything below ``_OFFSET_EPSILON`` - so a step that was simply *applied*
    every tick would never move the page at all.  The remainder is kept until it is
    worth a repaint, which at the slow pace is about four ticks.
    """
    tick = _auto_scroll_step(_AUTO_SCROLL_TIMER_MS / 1000.0, 6.0, LINE)
    assert 0.0 < tick < _OFFSET_EPSILON
    assert 4 * tick > _OFFSET_EPSILON


def test_nothing_moves_for_no_time_or_a_nonsense_pace() -> None:
    """Every zero in the sum means a still page, and none of them may raise."""
    assert _auto_scroll_step(0.0, 12.0, LINE) == 0.0
    assert _auto_scroll_step(-1.0, 12.0, LINE) == 0.0        # a clock that went back
    assert _auto_scroll_step(1.0, 0.0, LINE) == 0.0          # no pace at all
    assert _auto_scroll_step(1.0, -12.0, LINE) == 0.0
    assert _auto_scroll_step(1.0, 12.0, 0.0) == 0.0          # a section with no lines


def test_a_sleep_is_not_time_the_reader_was_reading() -> None:
    """The gap guard, both ways round (the machine slept, or the clock jumped back)."""
    assert _auto_scroll_elapsed(0.0, 0.05) == pytest.approx(0.05)
    assert _auto_scroll_elapsed(0.0, _AUTO_SCROLL_MAX_GAP) == pytest.approx(_AUTO_SCROLL_MAX_GAP)
    assert _auto_scroll_elapsed(0.0, _AUTO_SCROLL_MAX_GAP + 0.001) == 0.0
    assert _auto_scroll_elapsed(0.0, 300.0) == 0.0            # suspended overnight
    assert _auto_scroll_elapsed(10.0, 10.0) == 0.0            # two ticks in one instant
    assert _auto_scroll_elapsed(10.0, 9.0) == 0.0


def test_a_pace_name_this_version_does_not_know_is_the_default_one() -> None:
    """A hand-edited file must not be able to produce a speed nobody chose (FR-083).

    The page still moves, at the pace a reader who never chose one gets, and the menu
    still ticks one row - rather than no row, which would be a feature that looks
    broken without being one.
    """
    for unknown in ("turbo", "", "FAST", "fastest", "0"):
        assert _auto_scroll_pace(unknown) == _AUTO_SCROLL_DEFAULT_PACE
        assert _auto_scroll_lines(unknown) == pytest.approx(12.0)
        assert _auto_scroll_pace_label(unknown) == "中"
    for known in ("slow", "normal", "fast"):
        assert _auto_scroll_pace(known) == known


def test_each_pace_has_the_reader_word_that_goes_with_it() -> None:
    """What the menu's label and the status bar print (FR-094)."""
    assert [_auto_scroll_pace_label(name) for name in ("slow", "normal", "fast")] == [
        "慢",
        "中",
        "快",
    ]
