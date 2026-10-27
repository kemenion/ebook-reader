"""Which keys a word is worth asking the dictionary for, most promising first (ADR-026).

English is not a language of headwords.  The page says "went", "don't" and "e-books";
the dictionary has "go", "do" and "e-book".  Two of those gaps are closed here, without
touching the data:

* a contraction is expanded to the word it wraps - "don't" to "do", "won't" to "will";
* a hyphenated compound is also tried without its hyphen, because ECDICT spells some of
  them both ways ("e-book" and "ebook").

The third gap is the store's: inflections - "went", "better", "children" - cannot be
derived by any rule, so the dictionary's own ``exchange`` column is shipped as a
``form`` table and read there.  That is also why this module does not try suffixes: a
``-s`` rule would turn "is" into "i" and "bus" into "bu".

The *order* of the chain is the order the store tries, and the first hit wins: the word
itself comes first, so an entry for "don't" would be preferred over "do".
"""

from __future__ import annotations

import unicodedata

from .normalize import is_latin_token, strip_word

__all__ = ["lookup_keys"]

#: Contraction endings, and nothing else: a possessive ("reader's") and a contraction
#: ("he's") are spelled alike, and both are answered by the same base word.
_CONTRACTIONS = ("n't", "'re", "'ll", "'ve", "'d", "'m", "'s")

#: The contractions no suffix rule can produce - cutting "n't" off "can't" leaves "ca"
#: and off "won't" leaves "wo".  Their bases are supplied by hand.
_IRREGULAR_CONTRACTIONS = {
    "can't": "can",
    "won't": "will",
    "shan't": "shall",
}

#: A base shorter than this is a mis-split rather than a word ("s'" -> ""), so it is not
#: offered to the dictionary.
_MIN_BASE_CHARS = 2


def lookup_keys(text: str) -> tuple[str, ...]:
    """The keys to ask about for *text*, in the order they should be tried (FR-095).

    Empty when there is nothing worth asking: the pointer is on punctuation, on
    whitespace, or on a run this English dictionary cannot answer - the caller reads
    that as "be silent" rather than as an error (``normalize.is_latin_token``).
    """
    word = strip_word(text)
    if not word or not is_latin_token(word):
        return ()

    keys = [word]
    base = _contraction_base(word)
    if base:
        keys.append(base)
    if "-" in word:
        keys.append(word.replace("-", ""))

    # Diacritics are tried last: a novel prints "café" and "naïve", ECDICT files them as
    # "cafe" and "naive", but a file that does carry the accented spelling must win.
    for candidate in tuple(keys):
        folded = _fold(candidate)
        if folded != candidate:
            keys.append(folded)

    # `dict.fromkeys` rather than a set: the order is the contract, and "e-book" must be
    # tried before "ebook".
    return tuple(dict.fromkeys(keys))


def _fold(word: str) -> str:
    """*word* without its diacritics - "café" to "cafe", "naïve" to "naive"."""
    decomposed = unicodedata.normalize("NFKD", word)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _contraction_base(word: str) -> str:
    """The word a contraction wraps, or ``""`` when *word* is not one."""
    if word in _IRREGULAR_CONTRACTIONS:
        return _IRREGULAR_CONTRACTIONS[word]
    for ending in _CONTRACTIONS:
        if not word.endswith(ending):
            continue
        base = word[: -len(ending)]
        return base if len(base) >= _MIN_BASE_CHARS else ""
    return ""
