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
that an item takes the event, is ``tests/integration/test_scroll_view.py``.
"""

from __future__ import annotations

import pytest

from ebook_reader.app.controller import _wheel_distance

#: Three lines of body text - the controller's ``wheelStep`` (FR-073).
STEP = 66.0


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
