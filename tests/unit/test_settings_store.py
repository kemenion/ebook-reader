"""The configuration file: what the reader's own layout choice survives (FR-078 .. FR-083).

The file is the only place a preference outlives the process, and it is meant to be
hand-editable, so every read here is a question about a file somebody else wrote: a
missing key, a key of the wrong type, a number outside the range the window can draw.
None of them may raise, and none of them may produce a layout that cannot be drawn -
which is why the bounds are asserted at the store, not only at the drag.

Only the width is covered; the settings, window and per-book sections have their rules
exercised through the controller and the shell (`tests/integration/`), and what this
file adds is the arithmetic the *next* session starts from.
"""

from __future__ import annotations

import json
from pathlib import Path

from ebook_reader.app.settings_store import (
    _PANEL_WIDTH_MAX,
    _PANEL_WIDTH_MIN,
    BookState,
    SettingsStore,
)


def test_a_file_that_was_never_dragged_asks_for_the_automatic_width(tmp_path: Path) -> None:
    """0 is "the window decides", which is how every existing file reads (FR-078)."""
    store = SettingsStore(tmp_path / "config.json")
    assert store.panel_width() == 0


def test_the_chosen_width_survives_a_restart(tmp_path: Path) -> None:
    """The point of the whole section: the next session opens with the same width."""
    path = tmp_path / "config.json"
    store = SettingsStore(path)
    store.set_panel_width(296)
    assert store.save()

    assert SettingsStore(path).panel_width() == 296
    # Human-readable and hand-editable, like everything else in the file (FR-083).
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["layout"]["panel_width"] == 296


def test_a_width_outside_the_range_is_clamped_rather_than_trusted(tmp_path: Path) -> None:
    """A hand-edited file must not be able to make the layout undrawable (ADR-022).

    Both directions: a panel of a few pixels and one that eats the page are the two
    ways a number in a text file can break a window.
    """
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"version": 1, "layout": {"panel_width": 5}}), encoding="utf-8"
    )
    assert SettingsStore(path).panel_width() == _PANEL_WIDTH_MIN

    path.write_text(
        json.dumps({"version": 1, "layout": {"panel_width": 100000}}), encoding="utf-8"
    )
    assert SettingsStore(path).panel_width() == _PANEL_WIDTH_MAX

    # And on the way out too, so the file cannot record what the window must ignore.
    store = SettingsStore(path)
    store.set_panel_width(9999)
    assert store.panel_width() == _PANEL_WIDTH_MAX


def test_a_broken_or_absent_layout_section_is_the_automatic_width(tmp_path: Path) -> None:
    """Every shape of "not a number there": no key, not a dict, not an int, negative."""
    path = tmp_path / "config.json"
    for raw in (
        {},
        {"layout": None},
        {"layout": []},
        {"layout": {"panel_width": "wide"}},
        {"layout": {"panel_width": -40}},
        {"layout": {"panel_width": None}},
    ):
        path.write_text(json.dumps(raw), encoding="utf-8")
        assert SettingsStore(path).panel_width() == 0, raw


def test_forgetting_the_width_is_not_the_same_as_zero_pixels(tmp_path: Path) -> None:
    """``0`` means "the window decides again", and it is stored as such (FR-078)."""
    path = tmp_path / "config.json"
    store = SettingsStore(path)
    store.set_panel_width(300)
    store.set_panel_width(0)
    assert store.panel_width() == 0
    assert store.save()
    assert SettingsStore(path).panel_width() == 0


def test_the_new_section_does_not_disturb_the_others(tmp_path: Path) -> None:
    """One more key in one more section, and nothing else in the file moves (FR-080)."""
    path = tmp_path / "config.json"
    store = SettingsStore(path)
    store.set_window(1200, 900)
    store.set_book_state("/tmp/book.epub", BookState(section=3, block=7, char_offset=250))
    store.set_panel_width(320)
    assert store.save()

    reopened = SettingsStore(path)
    assert reopened.window() == {"width": 1200, "height": 900}
    assert reopened.book_state("/tmp/book.epub").as_tuple() == (3, 7, 250)
    assert reopened.panel_width() == 320
    # Saving the window again - which happens on every close - keeps the width.
    reopened.set_window(1100, 800)
    assert reopened.save()
    assert SettingsStore(path).panel_width() == 320
