"""CJK line-breaking rules (kin-soku / 禁則処理) enforced by U+2060 WORD JOINER.

Qt ships a full UAX#14 line-break table and even models justification
opportunities per glyph, but nothing in its public documentation promises that
CJK "no line start / no line end" punctuation rules are honoured.  Rather than
betting the typography on undocumented engine behaviour, we make the rule a
property of the *text itself* (ADR-005).

UAX#14 clause LB11 forbids a break on either side of U+2060 WORD JOINER, so
inserting one where a break would be illegal removes the illegal break by
construction.  This is deterministic: it does not depend on the Qt version, on
the font, or on the platform.  When a protected run no longer fits on a line,
the break simply moves earlier and carries the punctuation along - which is
exactly what proper kin-soku does.

WJ is a zero-width format character, so it is invisible; this matters because
reading positions store character offsets into this very text (ADR-011).
"""

from __future__ import annotations

from typing import Sequence

__all__ = [
    "NO_LINE_START",
    "NO_LINE_END",
    "WORD_JOINER",
    "protect_spans",
    "protect_text",
    "strip_word_joiners",
]

WORD_JOINER = "\u2060"

#: Punctuation that must never begin a line.
NO_LINE_START = frozenset("。，、；：！？）》」』〕】〗〙〛…—～·｡､｢｣")

#: Punctuation that must never end a line.
NO_LINE_END = frozenset("《（「『〔【〖〘〚｢")

#: Half-width punctuation, protected only when it follows CJK text so that
#: ordinary English ("hello, world") keeps its normal break opportunities.
NO_LINE_START_ASCII = frozenset(",.!?;:)]}»%")

_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x2E80, 0x2EFF),   # CJK radicals
    (0x3000, 0x303F),   # CJK symbols and punctuation
    (0x3040, 0x30FF),   # kana
    (0x3400, 0x4DBF),   # CJK extension A
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0xF900, 0xFAFF),   # compatibility ideographs
    (0xFF00, 0xFFEF),   # full-width forms
)


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _needs_protection(text: str) -> bool:
    return bool(
        NO_LINE_START & set(text)
        or NO_LINE_END & set(text)
        or NO_LINE_START_ASCII & set(text)
    )


def protect_text(text: str) -> str:
    """Insert word joiners so CJK line-breaking rules cannot be violated."""
    return protect_spans([text])[0]


def protect_spans(texts: Sequence[str]) -> list[str]:
    """Protect several text runs while treating them as one continuous line.

    Word joiners are attributed to the span that owns the character they glue
    to, so that inline styling boundaries (bold runs, footnote links) do not open
    a hole in the protection.
    """
    if not any(_needs_protection(text) for text in texts):
        return list(texts)

    result: list[str] = []
    previous_char = ""
    for text in texts:
        if not text:
            result.append(text)
            continue
        pieces: list[str] = []
        for index, char in enumerate(text):
            previous = text[index - 1] if index else previous_char
            if previous and previous != WORD_JOINER:
                if char in NO_LINE_START:
                    pieces.append(WORD_JOINER)
                elif char in NO_LINE_START_ASCII and _is_cjk(previous):
                    pieces.append(WORD_JOINER)
                elif previous in NO_LINE_END:
                    pieces.append(WORD_JOINER)
            pieces.append(char)
        result.append("".join(pieces))
        previous_char = text[-1]
    return result


def strip_word_joiners(text: str) -> str:
    """Remove injected word joiners, e.g. before matching a search query."""
    return text.replace(WORD_JOINER, "")
