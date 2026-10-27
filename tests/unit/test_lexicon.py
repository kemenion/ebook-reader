"""The rules behind the word card: what is asked, and what comes back (ADR-026).

The card itself is a QML element and is exercised through a real window
(``tests/integration/test_dictionary.py``).  What is asserted here is everything below
it, which is pure strings and one SQLite file: the pointer's punctuation is taken off
before the dictionary is asked, "don't" is asked as "do" as well as itself, "went" is
answered from the ``form`` table with the word that was actually pointed at still in
hand, and a build with no data answers "nothing" rather than raising (FR-008).

The last two tests look at the shipped file, which is the artefact this all exists for:
if it ever stops carrying "apple", every other test here would still pass.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

from ebook_reader.domain.html.kinsoku import WORD_JOINER
from ebook_reader.domain.lexicon import (
    EN_DICT_FILE,
    LICENCE_FILE,
    MAX_GLOSS_CHARS,
    MAX_SENSES,
    Lexicon,
    dict_dir,
    lookup_keys,
    normalise_phonetic,
    open_bundled,
    pick_senses,
    strip_word,
    write_db,
)
from ebook_reader.domain.lexicon.normalize import is_latin_token

#: A dictionary small enough to read in this file, in the shape the build script writes:
#: one entry, its phonetic, two gloss lines, and one inflected form pointing at it.
_ENTRIES = [
    ("go", "go", "/gəʊ/", "vi. 去, 走\nn. 尝试"),
    ("apple", "apple", "/ˈæpl/", "n. 苹果"),
]
_FORMS = [("went", "go")]


def _dictionary(path: Path) -> Lexicon:
    """A lexicon over a throwaway file built from :data:`_ENTRIES`."""
    write_db(path, _ENTRIES, _FORMS)
    return Lexicon(path)


def test_a_word_is_cleaned_before_the_dictionary_is_asked() -> None:
    """The page's punctuation, quotes and injected joiner are not part of the word.

    Kinsoku puts a word joiner beside the punctuation it protects (FR-109), so a word at
    the end of a sentence reaches the lookup as ``word\\u2060,`` - both have to go, or
    the word is silently never found.
    """
    assert strip_word(f"“Apple{WORD_JOINER},”") == "apple"
    assert strip_word("\n went.") == "went"
    assert strip_word("e-mail") == "e-mail"  # inner hyphen is part of the spelling
    assert strip_word("’tis") == "tis"
    assert strip_word(" ,. ") == ""


def test_only_latin_words_are_asked_about() -> None:
    """A Chinese run is not looked up at all, rather than looked up and missed.

    ``word_range`` hands back the whole clause when it holds Han script (FR-070), and the
    bundled list is an English one, so the card must stay away instead of opening empty.
    """
    assert lookup_keys("读书") == ()
    assert lookup_keys("读书reading") == ()
    assert not is_latin_token("读书")
    assert not is_latin_token("123 -- ")
    assert is_latin_token("naïve")


def test_a_contraction_is_asked_for_before_its_base() -> None:
    """The word itself first, then the verb it wraps - and typos in that rule are fatal.

    "can't" is the case no suffix rule gets right: cutting "n't" off leaves "ca", and
    "won't" leaves "wo".  Both are in the list of contractions supplied by hand.
    """
    assert lookup_keys("Don’t") == ("don't", "do")
    assert lookup_keys("can't") == ("can't", "can")
    assert lookup_keys("won't") == ("won't", "will")
    assert lookup_keys("children's") == ("children's", "children")
    assert lookup_keys("e-book") == ("e-book", "ebook")
    assert lookup_keys("café") == ("café", "cafe")


def test_an_inflected_form_is_answered_with_its_headword(tmp_path: Path) -> None:
    """No rule derives "went" from "go", so the form table is asked - and the card says
    both: the word on the page, and the entry that explains it (FR-095)."""
    lexicon = _dictionary(tmp_path / "en.db")
    try:
        answer = lexicon.lookup(lookup_keys("Went"))
    finally:
        lexicon.close()

    assert answer is not None
    assert (answer.form, answer.word) == ("went", "go")
    assert answer.senses == ("vi. 去, 走", "n. 尝试")


def test_the_phonetic_is_rewritten_in_the_ipa_it_stands_for() -> None:
    """ECDICT's typewriter notation reads as a typo until it is converted.

    The schwa is Cyrillic in the source, stress and length are an apostrophe and a colon;
    the card shows the IPA those characters were standing in for.
    """
    assert normalise_phonetic("'æpl") == "/ˈæpl/"
    assert normalise_phonetic("hau'evә") == "/hauˈevə/"
    assert normalise_phonetic("ˌfəutəuˈsinθəsis") == "/ˌfəutəuˈsinθəsis/"
    assert normalise_phonetic("") == ""
    assert normalise_phonetic(None) == ""


def test_the_gloss_keeps_three_lines_and_drops_the_machine_translations() -> None:
    """The card is a glance, not a dictionary page: three lines, and no ``[网络]`` noise.

    ECDICT separates its senses with a literal ``\\n`` - two characters - which is what
    the split has to see through, or every entry would arrive as one long line.
    """
    translation = "n. 苹果, 家伙\\n[网络] Apple Inc.\\n[医] 苹果\\n[化] 苹果酸"
    assert pick_senses(translation) == ("n. 苹果, 家伙", "[医] 苹果", "[化] 苹果酸")
    assert len(pick_senses("\\n".join(f"s{index}" for index in range(9)))) == MAX_SENSES

    long_line = pick_senses("x" * (MAX_GLOSS_CHARS + 50))[0]
    assert len(long_line) == MAX_GLOSS_CHARS
    assert long_line.endswith("…")


def test_a_missing_dictionary_answers_nothing(tmp_path: Path) -> None:
    """A missing data file is a reader without a word card, never a failed start-up."""
    lexicon = Lexicon(tmp_path / "absent.db")
    assert lexicon.lookup(("apple",)) is None
    # Asked twice: the failure is remembered rather than retried on every pointer move.
    assert lexicon.lookup(("apple",)) is None


def test_the_dictionary_is_found_inside_a_frozen_build(tmp_path: Path, monkeypatch) -> None:
    """PyInstaller unpacks the package under ``sys._MEIPASS`` - the layout no test runs in.

    The same probe the bundled font uses (ADR-025): a freezer that lays its data out
    differently must degrade to "no dictionary" rather than resolving to some unrelated
    file, and this is the only way to ask that question without building a 190 MB tree.
    """
    unpacked = tmp_path / "unpacked"
    target = unpacked / "ebook_reader" / "data" / "dict"
    target.mkdir(parents=True)
    monkeypatch.setattr(sys, "_MEIPASS", str(unpacked), raising=False)

    assert dict_dir() == target


def test_the_shipped_dictionary_answers_a_few_common_words() -> None:
    """The file this feature exists for: present, licensed, and able to answer (FR-096)."""
    assert (dict_dir() / EN_DICT_FILE).is_file()
    assert (dict_dir() / LICENCE_FILE).read_text(encoding="utf-8").startswith("MIT License")

    lexicon = open_bundled()
    try:
        apple = lexicon.lookup(lookup_keys("apple"))
        went = lexicon.lookup(lookup_keys("went"))
    finally:
        lexicon.close()

    assert apple is not None and "苹果" in " ".join(apple.senses)
    assert went is not None and went.word == "go" and went.form == "went"


def test_the_shipped_dictionary_records_where_it_came_from() -> None:
    """MIT asks for the notice to travel with the data; the file says so itself, for
    whoever opens it years later without this repository to hand."""
    db = sqlite3.connect(f"file:{dict_dir() / EN_DICT_FILE}?mode=ro", uri=True)
    try:
        meta = dict(db.execute("SELECT key, value FROM meta").fetchall())
    finally:
        db.close()

    assert "ECDICT" in meta["source"]
    assert meta["licence"] == "MIT"
    assert len(meta["source_sha256"]) == 64
    assert int(meta["entries"]) > 40_000
