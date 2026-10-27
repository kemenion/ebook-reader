"""The bundled word list: one read-only SQLite file, opened the first time it is asked.

The data travels with the application for the same reason the Kai face does (ADR-025):
nothing about reading a book should need a network.  What ships is not ECDICT's 66 MB
CSV - ``packaging/build-dicts.py`` distils the words a novel is likely to use - but a
few megabytes, two tables, and one index seek per lookup (FR-096).

The schema is the contract between that script and this reader; no other module knows
it.  ``entry`` holds what is shown, ``form`` maps an inflected spelling to the headword
it belongs to, and ``meta`` records where the data came from, for whoever opens the file
with ``sqlite3`` decades from now.

A missing or unreadable file is a reader without a dictionary, never a reader that fails
to start (FR-008): the first lookup logs one warning, and every later one answers
``None``.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EN_DICT_FILE",
    "LICENCE_FILE",
    "SENSE_SEPARATOR",
    "Lexicon",
    "Lookup",
    "dict_dir",
    "open_bundled",
    "write_db",
]

_log = logging.getLogger(__name__)

#: The English list, shipped inside the package.
EN_DICT_FILE = "en.db"

#: The licence that must travel with the data, exactly as the OFL text does with the Kai
#: face (ADR-025): MIT requires the copyright notice to accompany every copy, the one
#: inside an AppImage included.
LICENCE_FILE = "LICENSE-ECDICT.txt"

#: Where the dictionaries live inside the package, and inside a frozen build's
#: ``_MEIPASS``.  Both layouts are probed rather than assumed, mirroring
#: ``typeset/fonts.py``: a freezer that changes its data layout should degrade to "no
#: dictionary" instead of resolving to some unrelated file elsewhere on the disk.
_DATA_PARTS = ("data", "dict")

#: Gloss lines in ``entry.senses`` are separated by this, so the column stays plain text
#: and a reader who opens the file with ``sqlite3`` can still read it.
SENSE_SEPARATOR = "\n"

_SCHEMA = """
CREATE TABLE entry (
    key      TEXT PRIMARY KEY,
    word     TEXT NOT NULL,
    phonetic TEXT NOT NULL,
    senses   TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE form (
    form  TEXT PRIMARY KEY,
    lemma TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;
"""

#: Page cache for a file of a few megabytes: a lookup is one index seek and one row, and
#: the words a reader meets in a minute are a handful, so a larger cache would only take
#: memory away from the page being rendered.
_CACHE_KIB = 1024


@dataclass(frozen=True, slots=True)
class Lookup:
    """One answered question: what the pointer was on, and what the dictionary says.

    *form* is the word as it stood on the page (``went``, ``don't``) and *word* the
    headword the entry is filed under (``go``, ``do``).  The card shows both when they
    differ, because a reader who pointed at one word and is shown another needs to know
    which one is being explained.  They are equal in the ordinary case where the page
    and the dictionary agree.
    """

    word: str
    phonetic: str
    senses: tuple[str, ...]
    form: str


class Lexicon:
    """A lazily opened, read-only dictionary file.

    Nothing is read until the first question: a reader who never points at a word must
    not pay for the file handle, and a test that builds a controller must not need the
    data file to be present.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._db: sqlite3.Connection | None = None
        self._opened = False

    @property
    def path(self) -> Path:
        """The file this lexicon reads - the one a failure message names."""
        return self._path

    def lookup(self, keys: Sequence[str]) -> Lookup | None:
        """The first of *keys* the dictionary can answer, or ``None``.

        *keys* is the chain from ``match.lookup_keys``, in its order, and the first hit
        wins: an entry for the word itself is preferred over the base of its
        contraction.  An inflected spelling is answered through the ``form`` table, in
        one extra seek.

        The answer's ``form`` is *keys*' first non-empty entry - the word as it stood on
        the page - whatever key actually hit, so a reader who pointed at "don't" is told
        that "do" is what is being explained.
        """
        db = self._open()
        if db is None:
            return None
        page_word = next((key for key in keys if key), "")
        for key in keys:
            if not key:
                continue
            row = self._entry(db, key)
            if row is not None:
                return _lookup(row, form=page_word)
            lemma = db.execute("SELECT lemma FROM form WHERE form = ?", (key,)).fetchone()
            if lemma is None:
                continue
            row = self._entry(db, lemma[0])
            if row is not None:
                return _lookup(row, form=page_word)
        return None

    def close(self) -> None:
        """Let go of the file; the next :meth:`lookup` reopens it."""
        if self._db is not None:
            self._db.close()
        self._db = None
        self._opened = False

    def _open(self) -> sqlite3.Connection | None:
        """The connection, opened once; ``None`` when this build carries no data."""
        if self._opened:
            return self._db
        self._opened = True
        try:
            # `mode=ro` rather than a plain connection: the file sits in the install
            # directory, may be read-only on disk, and a write attempt there has to fail
            # as a failed query instead of as a permission error during start-up.
            self._db = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True)
            self._db.execute(f"PRAGMA cache_size = -{_CACHE_KIB}")
        except sqlite3.Error as exc:
            _log.warning("查词数据打不开（%s）：%s；查词功能关闭", self._path, exc)
            self._db = None
        return self._db

    @staticmethod
    def _entry(db: sqlite3.Connection, key: str) -> tuple[str, str, str] | None:
        return db.execute(
            "SELECT word, phonetic, senses FROM entry WHERE key = ?", (key,)
        ).fetchone()


def _lookup(row: tuple[str, str, str], *, form: str) -> Lookup:
    word, phonetic, senses = row
    return Lookup(
        word=word,
        phonetic=phonetic,
        senses=tuple(line for line in senses.split(SENSE_SEPARATOR) if line),
        form=form,
    )


def dict_dir() -> Path:
    """Directory holding the dictionaries, both in-tree and when packaged."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        root = Path(base)
        candidates = (root.joinpath("ebook_reader", *_DATA_PARTS), root.joinpath(*_DATA_PARTS))
        for candidate in candidates:
            if candidate.is_dir():
                return candidate
    return Path(__file__).resolve().parents[2].joinpath(*_DATA_PARTS)


def open_bundled() -> Lexicon:
    """The English list that ships with this build - opened on paper, not read yet."""
    return Lexicon(dict_dir() / EN_DICT_FILE)


def write_db(
    path: Path,
    entries: Iterable[tuple[str, str, str, str]],
    forms: Iterable[tuple[str, str]] = (),
    meta: Iterable[tuple[str, str]] = (),
) -> None:
    """Write *path* from scratch, in the only schema :class:`Lexicon` reads.

    Used by ``packaging/build-dicts.py`` and by the tests, which build a handful of rows
    rather than a book's worth of them.  *entries* are ``(key, word, phonetic, senses)``
    and *forms* ``(form, lemma)``; both are written in the order given, so the same input
    produces the same file and a rebuilt dictionary shows up as a real change or not at
    all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(_SCHEMA)
        db.executemany(
            "INSERT INTO entry(key, word, phonetic, senses) VALUES (?, ?, ?, ?)",
            entries,
        )
        db.executemany("INSERT OR REPLACE INTO form(form, lemma) VALUES (?, ?)", forms)
        db.executemany("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", meta)
        db.commit()
        # Pages a growing table leaves behind would ship inside the package; VACUUM
        # writes the file out packed, which is worth one pass over a few hundred
        # thousand rows in a build script.
        db.execute("VACUUM")
        db.commit()
    finally:
        db.close()
