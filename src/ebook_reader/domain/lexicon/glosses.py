"""How a dictionary row becomes the two lines the card shows (ADR-026).

ECDICT's phonetics are written in a transliteration that predates easy Unicode IPA
input: the schwa is Cyrillic ``ә``, primary stress is a typewriter apostrophe, secondary
stress a full stop, length a colon.  Printed as stored, that line reads as a typo rather
than as a pronunciation.

The correction runs at build time, over every row (``packaging/build-dicts.py``), so the
reader never pays for it and never sees the raw spelling.  *Which* gloss lines deserve a
place on a small card is the other half of the same question, and it is decided here too.
"""

from __future__ import annotations

__all__ = ["MAX_GLOSS_CHARS", "MAX_SENSES", "normalise_phonetic", "pick_senses"]

#: The substitutions that turn ECDICT's notation into the IPA it stands for.  One pass,
#: so no character can be rewritten twice (a ``:`` that became ``ː`` cannot then be read
#: as a secondary stress).
_PHONETIC_CHANGES = str.maketrans(
    {
        "\u04d9": "\u0259",  # ә U+04D9 -> ə U+0259, the schwa that was meant
        "'": "\u02c8",  # ' -> ˈ primary stress
        ".": "\u02cc",  # . -> ˌ secondary stress
        ":": "\u02d0",  # : -> ː length
    }
)

#: How many gloss lines the card carries.  Enough for a word with several senses, few
#: enough that the card never grows into the paragraph it is explaining.
MAX_SENSES = 3

#: Total characters of gloss, a whole-card cap rather than a per-line one: three short
#: lines and one long one are both fine, a card taller than the page it covers is not.
MAX_GLOSS_CHARS = 220

#: ECDICT marks machine-translated senses with this prefix.  They are usually a proper
#: noun or a mis-parsed phrase, and a reader asking "what does this word mean" is not
#: helped by them.
_NETWORK_PREFIX = "[网络]"

#: What a line that had to be cut ends in.
_ELLIPSIS = "…"


def normalise_phonetic(raw: str | None) -> str:
    """The pronunciation as IPA between slashes, or ``""`` when there is none.

    An empty result rather than a pair of empty slashes: the card then leaves the line
    out altogether, which is what a word without a recorded pronunciation should look
    like.
    """
    phonetic = " ".join((raw or "").strip().strip("/").translate(_PHONETIC_CHANGES).split())
    return f"/{phonetic}/" if phonetic else ""


def pick_senses(translation: str | None) -> tuple[str, ...]:
    """The gloss lines worth showing, at most :data:`MAX_SENSES` and
    :data:`MAX_GLOSS_CHARS` characters of them.

    Dropped first are the machine-translated ``[网络]`` lines - unless they are all
    there is, in which case the tag comes off and they are shown: a rough gloss beats an
    empty card.  The rest are kept in the order the dictionary lists them, which puts the
    everyday sense of a word first.

    ECDICT separates its senses with a *literal* ``\\n`` - two characters, not a line
    break - so the escape is unescaped here before the lines are counted.  Some rows do
    carry real newlines, hence both: whatever separated the senses, the result is the
    same list.
    """
    lines = [line.strip() for line in (translation or "").replace("\\n", "\n").splitlines()]
    lines = [line for line in lines if line]
    kept = [line for line in lines if not line.startswith(_NETWORK_PREFIX)]
    if not kept:
        kept = [line[len(_NETWORK_PREFIX) :].strip() for line in lines]
        kept = [line for line in kept if line]

    senses: list[str] = []
    used = 0
    for line in kept:
        if len(senses) >= MAX_SENSES:
            break
        room = MAX_GLOSS_CHARS - used
        if room <= 0:
            break
        if len(line) > room:
            line = line[: max(0, room - 1)].rstrip() + _ELLIPSIS
        senses.append(line)
        used += len(line)
    return tuple(senses)
