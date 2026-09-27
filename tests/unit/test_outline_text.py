"""The label rule behind the outline panel (FR-018).

One function, one failure mode.  Kinsoku inserts U+2060 WORD JOINER to keep punctuation
off line starts; that is right for layout and wrong for the UI - the character survives
into a heading label, where it is invisible on screen but makes the label differ from
the same text read in the page (FR-109).  A label is also collapsed to single spaces,
because the panel wraps it in a column a few characters wide.

Pure text, so this needs no Qt.  What the panel does with the labels - one row per
heading, no rows at all without headings - is ``tests/integration/test_outline.py``.
"""

from __future__ import annotations

import pytest

from ebook_reader.app.controller import _clean_label

WORD_JOINER = "\u2060"


def test_clean_label_collapses_whitespace() -> None:
    assert _clean_label("  人生\n财富\t靠  康波 ") == "人生 财富 靠 康波"


def test_clean_label_drops_the_word_joiner() -> None:
    """Kinsoku inserts U+2060 for layout; it must not reach the reader's eye."""
    joined = "进了法院\u2060，我先去登记\u2060。"
    assert WORD_JOINER not in _clean_label(joined)
    assert _clean_label(joined) == "进了法院，我先去登记。"


def test_clean_label_keeps_a_long_heading_whole() -> None:
    """Nothing is cut off here: the column wraps, so a heading stays readable."""
    long = "字" * 200
    assert _clean_label(long) == long


@pytest.mark.parametrize("text", ["", "   ", WORD_JOINER, "\n\t", f" {WORD_JOINER} "])
def test_clean_label_of_a_heading_with_no_text_is_empty(text: str) -> None:
    """A label that would come out blank is empty, and the panel drops that row."""
    assert _clean_label(text) == ""

