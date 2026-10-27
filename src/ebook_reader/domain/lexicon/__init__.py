"""The dictionary the reader consults without leaving the page (ADR-026).

Ctrl over an English word shows what it means: a pronunciation and a line or two of
gloss, from a list that ships inside the package.  Everything needed to answer is here
and nothing else is: the words are distilled at build time (``packaging/build-dicts.py``)
from ECDICT, an MIT-licensed English-Chinese dictionary, into one SQLite file.

Three questions, three modules:

* ``normalize`` - what did the pointer actually land on, once the punctuation and the
  joiner kinsoku injected are taken off;
* ``match`` - which keys is that word worth asking for ("went", "don't", "e-book");
* ``store`` - what does the file say, and what happens when it is missing.

``glosses`` holds the two conversions the build applies once, so the card's lines and
its IPA are already what the reader should see.

Nothing here imports Qt (ADR-012), and nothing here downloads anything (FR-096).
"""

from __future__ import annotations

from .glosses import MAX_GLOSS_CHARS, MAX_SENSES, normalise_phonetic, pick_senses
from .match import lookup_keys
from .normalize import is_latin_token, strip_word
from .store import (
    EN_DICT_FILE,
    LICENCE_FILE,
    SENSE_SEPARATOR,
    Lexicon,
    Lookup,
    dict_dir,
    open_bundled,
    write_db,
)

__all__ = [
    "EN_DICT_FILE",
    "LICENCE_FILE",
    "MAX_GLOSS_CHARS",
    "MAX_SENSES",
    "SENSE_SEPARATOR",
    "Lexicon",
    "Lookup",
    "dict_dir",
    "is_latin_token",
    "lookup_keys",
    "normalise_phonetic",
    "open_bundled",
    "pick_senses",
    "strip_word",
    "write_db",
]
