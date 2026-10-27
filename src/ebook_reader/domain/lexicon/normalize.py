"""What the pointer landed on, reduced to something the dictionary could know (ADR-026).

The running text is not text a dictionary recognises.  It carries the word joiner
kinsoku injected beside punctuation (FR-109), it ends in whatever mark closed the
sentence, its apostrophes are as likely to be typographic as ASCII, and a paragraph
break can be attached to either end when the word came out of a selection.  This module
is that cleaning and only that cleaning: *which* keys a cleaned word is worth asking
for is ``match.py``'s business, and answering them is ``store.py``'s.

Pure string work, deliberately (ADR-012): ``word_range`` has already found the run with
Qt's own text engine (``typeset/selection.py``), so nothing here needs a document, a
window or a display to be tested.
"""

from __future__ import annotations

from ..html.kinsoku import WORD_JOINER

__all__ = ["is_latin_token", "strip_word"]

#: Characters that never belong to a headword, however the pointer landed on them.
#: The word joiner is in the set as well as being replaced outright below: kinsoku puts
#: it *before* a closing mark, so the bare ``strip`` would leave the joiner behind.
_TRIM_CHARS = (
    WORD_JOINER
    + "\u00a0 \t\r\n"
    + ".,;:!?\"'`“”‘’«»‹›…()[]{}<>*_/\\|~^=+&%$#@"
)

#: Typographic apostrophes folded to ASCII before anything else looks at the token.  A
#: book set in the bundled Kai face carries U+2019 in "don’t", and a contraction rule
#: that only knows "'" would miss every one of them.
_APOSTROPHES = str.maketrans(
    {
        "\u2019": "'",  # ’ right single quotation mark
        "\u2018": "'",  # ‘ left single quotation mark
        "\u02bc": "'",  # ʼ modifier letter apostrophe
        "`": "'",
    }
)

#: The Unicode Han blocks, in the same three ranges ``typeset/selection.py`` uses.  Held
#: here as well rather than imported from there, because ``domain`` may not import
#: ``typeset`` (ADR-012); the ranges are a property of the script, not a list to keep in
#: step, so a copy is not a burden.
_HAN_RANGES = (
    ("\u3400", "\u4dbf"),
    ("\u4e00", "\u9fff"),
    ("\uf900", "\ufaff"),
)


def is_latin_token(text: str) -> bool:
    """Whether *text* is the kind of word this dictionary could answer (FR-095).

    The bundled list is an English one (ADR-026), so a run of Han script is not looked
    up at all rather than looked up and missed: the card stays away, which is what
    "nothing to show" should look like.  A *mixed* run - ``word_range`` hands back the
    clause when it holds Han script (FR-070) - is rejected as a whole; answering half of
    it would be worse than answering none of it.

    Letters outside ASCII are accepted: a novel prints "café" and "naïve", and ECDICT
    has entries for both.  What is required is that there is at least one ASCII letter,
    which is what keeps a run of digits or dashes out.
    """
    latin = False
    for char in text:
        if _is_han(char):
            return False
        if char.isascii() and char.isalpha():
            latin = True
    return latin


def strip_word(text: str) -> str:
    """The word alone, lower-cased - what a headword in the dictionary looks like.

    Everything around the letters goes: the joiner kinsoku injected, the mark that
    ended the sentence, a quote the pointer happened to land inside, and the newline
    ``selected_text`` gives a paragraph break (FR-109).  Hyphens and apostrophes *inside*
    the word stay, because they are part of how it is spelled - "well-known" and "don't"
    are headwords in their own right.

    Case is folded here rather than at the query, so a word capitalised only because it
    opened a sentence finds the same entry as the same word in the middle of one.
    """
    word = text.translate(_APOSTROPHES).replace(WORD_JOINER, "")
    # A paragraph break reaches a token as "\n"; collapsing whitespace also turns the
    # stray space of a mis-hit into an empty word, which the caller reads as "nothing".
    word = " ".join(word.split())
    return word.strip(_TRIM_CHARS).lower()


def _is_han(char: str) -> bool:
    """Whether *char* is Han script - the one thing that rules a lookup out entirely."""
    return any(low <= char <= high for low, high in _HAN_RANGES)
