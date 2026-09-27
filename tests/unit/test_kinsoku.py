"""CJK line-breaking protection (ADR-005).

This is the cheapest place to prevent the most visible typography defect, so it
is tested thoroughly even though the implementation is small.
"""

from __future__ import annotations

import pytest

from ebook_reader.domain.html.kinsoku import (
    NO_LINE_END,
    NO_LINE_START,
    WORD_JOINER,
    protect_spans,
    protect_text,
    strip_word_joiners,
)


def test_closing_punctuation_is_glued_to_the_previous_character() -> None:
    protected = protect_text("他说。")
    assert protected == f"他说{WORD_JOINER}。"
    assert protected.count(WORD_JOINER) == 1


def test_opening_bracket_and_closing_bracket_are_both_protected() -> None:
    """Both rules apply: ``（`` must not end a line and ``）`` must not start one."""
    protected = protect_text("（一）")
    assert protected == f"（{WORD_JOINER}一{WORD_JOINER}）"
    assert protected.count(WORD_JOINER) == 2


def test_consecutive_punctuation_gets_one_joiner_each() -> None:
    protected = protect_text("好的，真的！")
    assert protected.count(WORD_JOINER) == 2
    assert strip_word_joiners(protected) == "好的，真的！"


@pytest.mark.parametrize("mark", sorted(NO_LINE_START))
def test_every_forbidden_line_start_is_protected(mark: str) -> None:
    protected = protect_text(f"字{mark}")
    assert protected == f"字{WORD_JOINER}{mark}", mark


@pytest.mark.parametrize("mark", sorted(NO_LINE_END))
def test_every_forbidden_line_end_is_protected(mark: str) -> None:
    protected = protect_text(f"{mark}字")
    assert protected == f"{mark}{WORD_JOINER}字", mark


def test_half_width_punctuation_is_left_alone_after_latin_text() -> None:
    """English must keep its normal break opportunities."""
    text = "the reader, and the book."
    assert protect_text(text) == text


def test_half_width_punctuation_is_glued_after_cjk_text() -> None:
    protected = protect_text("第一,第二")
    assert protected == f"第一{WORD_JOINER},第二"


def test_only_joiners_are_added() -> None:
    text = "人生财富靠康波（周金涛）。"
    assert strip_word_joiners(protect_text(text)) == text


def test_joiners_are_not_doubled() -> None:
    once = protect_text("他说。")
    assert protect_text(once) == once


def test_protection_works_across_span_boundaries() -> None:
    """Inline styling must not open a hole in the protection."""
    protected = protect_spans(["他强调", "。这是重点"])
    assert protected[0] == "他强调"
    assert protected[1] == f"{WORD_JOINER}。这是重点"


def test_plain_text_is_returned_unchanged() -> None:
    for text in ("", "hello world", "纯中文没有标点"):
        assert protect_text(text) == text


def test_spans_of_plain_text_are_copied_not_mutated() -> None:
    spans = ["abc", "def"]
    assert protect_spans(spans) == spans
    assert protect_spans(spans) is not spans
