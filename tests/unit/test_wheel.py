"""The wheel rule behind every scroll gesture (FR-063 / FR-073).

One function, one failure mode, and it is the one a reader reports as "the mouse
wheel does nothing": an event arrives in one of two shapes.  A mouse notch comes as
120 units of ``angleDelta``; a touchpad, a high-resolution wheel and a free-spinning
wheel come as screen ``pixelDelta`` with an angle of zero.  The reader used to read
the angle alone, so those gestures moved the text by exactly nothing - the event
arrived, was accepted, and the page stayed still.

The two deltas also carry opposite signs: an angle is positive when the wheel turns
*away* from the user (down is negative), while a pixel delta is a distance on screen
and positive when scrolling down.  So the tests below are written twice, once per
shape, and they check direction as carefully as distance: a fix that scrolled the
right amount the wrong way would be worse than the bug.

Pure arithmetic, so this needs no Qt.  That the QML side forwards both deltas, and
that an item takes the event, is ``tests/integration/test_scroll_view.py``.  The two
map columns ask the same rule for a distance of their own - three of *their* rows
(FR-093) - so the arithmetic is checked here too, and what a reader sees of it in
``tests/integration/test_toc_panel.py`` and ``test_outline.py``.
"""

from __future__ import annotations

import pytest

from ebook_reader.app.controller import (
    _MAP_ROW_HEIGHT,
    _MAP_WHEEL_ROWS,
    _wheel_distance,
)

#: Three lines of body text - the controller's ``wheelStep`` (FR-073).
STEP = 66.0

#: Three of the map's own rows - the controller's ``mapWheelStep`` (FR-093).
MAP_STEP = _MAP_ROW_HEIGHT * _MAP_WHEEL_ROWS


def test_a_notch_scrolls_three_lines_down() -> None:
    """A mouse away from the reader is 120 units, and it is one step, not 120 px."""
    assert _wheel_distance(-120.0, 0.0, STEP) == pytest.approx(STEP, abs=1e-9)


def test_a_notch_the_other_way_scrolls_three_lines_up() -> None:
    assert _wheel_distance(120.0, 0.0, STEP) == pytest.approx(-STEP, abs=1e-9)


def test_two_notches_scroll_twice_as_far() -> None:
    """Free-spinning wheels send several notches in one event on a fast flick."""
    assert _wheel_distance(-240.0, 0.0, STEP) == pytest.approx(2 * STEP, abs=1e-9)


def test_a_partial_notch_scrolls_proportionally() -> None:
    """A high-resolution wheel can stop between detents, and should follow."""
    assert _wheel_distance(-60.0, 0.0, STEP) == pytest.approx(STEP / 2, abs=1e-9)


def test_a_smooth_source_scrolls_by_its_own_pixels() -> None:
    """The regression: pixel-only events used to move the text by nothing at all."""
    assert _wheel_distance(0.0, 60.0, STEP) == pytest.approx(60.0, abs=1e-9)
    assert _wheel_distance(0.0, 3.0, STEP) == pytest.approx(3.0, abs=1e-9)


def test_a_smooth_source_up_scrolls_up() -> None:
    """A pixel delta is already a distance, so its sign is *not* flipped."""
    assert _wheel_distance(0.0, -60.0, STEP) == pytest.approx(-60.0, abs=1e-9)


def test_both_shapes_move_the_same_direction() -> None:
    """The two signs are opposite on purpose, which is the trap in this rule."""
    down = _wheel_distance(-120.0, 0.0, STEP)
    smooth_down = _wheel_distance(0.0, 120.0, STEP)
    assert down > 0 and smooth_down > 0
    assert _wheel_distance(120.0, 0.0, STEP) < 0
    assert _wheel_distance(0.0, -120.0, STEP) < 0


def test_the_angle_wins_when_an_event_carries_both() -> None:
    """``pixelDelta`` is driver-specific and unreliable on X11 (Qt's own words).

    An event with an angle is a wheel notch, and on X11 the distance that comes with
    it is not to be trusted; so it does not get to override the notch.
    """
    assert _wheel_distance(-120.0, 999.0, STEP) == pytest.approx(STEP, abs=1e-9)


def test_a_phase_event_moves_nothing() -> None:
    """ScrollBegin and ScrollEnd carry no distance; neither may be a jump."""
    assert _wheel_distance(0.0, 0.0, STEP) == 0.0


@pytest.mark.parametrize("step", [0.0, 1.0, 132.0])
def test_a_notch_is_always_one_step_whatever_the_type_size(step: float) -> None:
    """The step is the controller's, so the wheel follows a font-size change."""
    assert _wheel_distance(-120.0, 0.0, step) == pytest.approx(step, abs=1e-9)


def test_a_notch_scrolls_three_rows_of_the_map() -> None:
    """The maps count their own rows, and three of them are one notch (FR-093).

    Qt's own arithmetic for a ``ListView`` is a quarter of a screen of *text* lines,
    which came to about one of these 38 px rows: the contents of a 134 row book then
    took 134 turns of the wheel.
    """
    assert MAP_STEP == pytest.approx(114.0, abs=1e-9)
    assert _wheel_distance(-120.0, 0.0, MAP_STEP) == pytest.approx(MAP_STEP, abs=1e-9)


def test_the_map_step_is_three_rows_and_more_than_one() -> None:
    """The two numbers behind the step belong to the panels, not to the page."""
    assert _MAP_WHEEL_ROWS == 3
    assert MAP_STEP == pytest.approx(_MAP_WHEEL_ROWS * _MAP_ROW_HEIGHT, abs=1e-9)
    assert MAP_STEP > _MAP_ROW_HEIGHT, "a notch is more than a single row"


def test_the_map_leaves_a_smooth_deltas_pixels_alone() -> None:
    """A pixel delta is a distance already, so the map's step must not scale it.

    A touchpad moved the map by nothing at all before this (缺陷 30), because Qt's
    flickable reads the angle alone; a fix that *stepped* the pixels would move a
    touchpad by three rows a gesture, which is the old bug's mirror image.
    """
    assert _wheel_distance(0.0, 60.0, MAP_STEP) == pytest.approx(60.0, abs=1e-9)
    assert _wheel_distance(0.0, 3.0, MAP_STEP) == pytest.approx(3.0, abs=1e-9)
    assert _wheel_distance(0.0, -60.0, MAP_STEP) == pytest.approx(-60.0, abs=1e-9)
    assert _wheel_distance(0.0, 1000.0, MAP_STEP) == pytest.approx(1000.0, abs=1e-9)


def test_the_map_answers_a_half_notch_and_a_phase_event() -> None:
    """A high-resolution wheel follows at half a step, and a phase is not a jump."""
    assert _wheel_distance(-60.0, 0.0, MAP_STEP) == pytest.approx(MAP_STEP / 2, abs=1e-9)
    assert _wheel_distance(0.0, 0.0, MAP_STEP) == 0.0
