#!/usr/bin/env python3
"""Distil ECDICT into the small English dictionary the reader ships with (ADR-026).

ECDICT is an MIT-licensed English-Chinese dictionary of some 700 000 entries, published
as a 66 MB CSV.  Almost all of it is not what a novel needs: proper nouns, abbreviations,
typos, scientific binomials.  What a reader points at is the vocabulary of prose, so this
script keeps the words that are common enough to be ranked in a frequency list or that
carry a school or exam tag, and drops the rest.  The result is a few megabytes and two
tables, and it travels inside the package (FR-096).

Two conversions happen here rather than at lookup time, because they cost the same once
and nothing at all afterwards: the phonetics are rewritten from ECDICT's typewriter
notation into IPA, and the gloss is cut down to the lines a small card can hold
(``domain/lexicon/glosses.py``).  Inflections are read out of the ``exchange`` column
into the ``form`` table - no rule can derive "went" from "go", and without the table a
reader pointing at half the verbs in a book would get nothing.

The output is reproducible: the same CSV produces the same file, byte for byte, because
rows are written in CSV order and no timestamp goes into it.

Run from the repository root:

    .venv/bin/python packaging/build-dicts.py             # download if needed, then build
    .venv/bin/python packaging/build-dicts.py --refresh   # fetch a fresh copy first

The CSV is cached in ``build/dicts/`` (git-ignored); passing ``--csv`` uses a copy that
is already on the machine instead.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ebook_reader.domain.lexicon import (  # noqa: E402 - after the path is set up
    EN_DICT_FILE,
    SENSE_SEPARATOR,
    dict_dir,
    normalise_phonetic,
    pick_senses,
    write_db,
)

#: Upstream first, then the two mirrors: raw.githubusercontent is unreachable from some
#: networks, and both proxies serve the same bytes from the same revision.
SOURCE_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"
MIRRORS = (
    f"https://ghfast.top/{SOURCE_URL}",
    f"https://gh-proxy.com/{SOURCE_URL}",
)

#: Where the download is kept between builds.  Inside ``build/``, which git ignores.
CACHE_CSV = ROOT / "build" / "dicts" / "ecdict.csv"

#: A smaller file than this is an error page rather than the dictionary (the CDN mirrors
#: answer a 20 MB file with one sentence of text and no status code to notice).
MIN_CSV_BYTES = 50_000_000

#: Seconds to wait for a mirror before giving up on it.  Wire speed here measured at
#: roughly 800 kB/s, so 66 MB needs a minute and a half; five is slack, not a hang.
TIMEOUT_S = 300

#: Read in this many bytes at a time while downloading: big enough that the copy is not
#: a Python loop over 64 kB chunks, small enough that 66 MB never sits in memory.
CHUNK_BYTES = 1 << 20

#: Rank in the COCA or the BNC list, ``0`` when a word is on neither.  The top 60 000 of
#: either list is comfortably the vocabulary of fiction plus the words around it; the
#: tail below that is where the proper nouns and the technical terms start.
MAX_RANK = 60_000

#: A key longer than this cannot be reached by pointing at a word in a line of text
#: without the reader having to drag across it, so it is not worth the bytes.
MAX_KEY_CHARS = 32

#: ``exchange`` lists inflections as ``tag:value`` pairs separated by ``/``.  The ``0``
#: tag is the pointer from a form to its lemma (the row for "went" says ``0:go``); every
#: other tag is a form of this row's own word (``p:went`` on the row for "go").
_LEMMA_TAG = "0"


def fetch_csv(path: Path, *, refresh: bool = False) -> None:
    """Make sure *path* holds ECDICT's CSV, downloading it only when it is not there."""
    if path.is_file() and not refresh:
        print(f"[dict] 使用已下载的 {path}", flush=True)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    failure = ""
    for url in (*MIRRORS, SOURCE_URL):
        print(f"[dict] 下载 {url}", flush=True)
        partial = path.with_name(path.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT_S) as response:
                with partial.open("wb") as out:
                    shutil.copyfileobj(response, out, CHUNK_BYTES)
            if partial.stat().st_size < MIN_CSV_BYTES:
                failure = f"只有 {partial.stat().st_size} 字节，不是词典"
                print(f"[dict] 弃用：{failure}", flush=True)
                partial.unlink(missing_ok=True)
                continue
            partial.replace(path)
        except (OSError, urllib.error.URLError) as exc:
            failure = str(exc)
            print(f"[dict] 失败：{failure}", flush=True)
            continue
        return

    raise SystemExit(f"[dict] 所有源都下不下来：{failure}")


def build(csv_path: Path, out_path: Path) -> tuple[int, int]:
    """Write *out_path* from the CSV at *csv_path*; returns ``(entries, forms)``."""
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    entries: list[tuple[str, str, str, str]] = []
    kept: set[str] = set()
    #: Every (form, lemma) pair the CSV declares, from either side.  Filtered once all
    #: rows have been read: a form is only useful when its lemma made it into the file.
    declared: dict[str, str] = {}

    with csv_path.open(encoding="utf-8", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            word = (row.get("word") or "").strip()
            key = word.lower()
            if not key or len(key) > MAX_KEY_CHARS or " " in key:
                continue

            for form, lemma in _form_pairs(key, row.get("exchange")):
                declared.setdefault(form, lemma)

            if not _wanted(row):
                continue
            senses = pick_senses(row.get("translation"))
            if not senses:
                # A word with nothing to say is worse than no card at all: pointing at it
                # would raise a box that answers nothing.
                continue
            entries.append(
                (
                    key,
                    word,
                    normalise_phonetic(row.get("phonetic")),
                    SENSE_SEPARATOR.join(senses),
                )
            )
            kept.add(key)

    forms = [
        (form, lemma)
        for form, lemma in sorted(declared.items())
        # A form that is a headword in its own right is found without this row, and a
        # lemma that was filtered out has nothing to show.
        if lemma in kept and form not in kept and form != lemma
    ]
    meta = [
        ("source", "ECDICT - https://github.com/skywind3000/ECDICT"),
        ("source_sha256", digest),
        ("licence", "MIT"),
        ("policy", f"rank<={MAX_RANK} in COCA(BNC) or carries an exam tag"),
        ("entries", str(len(entries))),
        ("forms", str(len(forms))),
    ]
    write_db(out_path, entries, forms, meta)
    return len(entries), len(forms)


def _rank(row: dict[str, str], column: str) -> int:
    """A frequency rank from *column*, ``0`` when the word is not on that list.

    ECDICT writes an empty cell or a ``0`` for "not listed", and a few rows carry
    something that is not a number at all; all three mean the same thing here.
    """
    try:
        return int(row.get(column) or 0)
    except ValueError:
        return 0


def _wanted(row: dict[str, str]) -> bool:
    """Whether a word earns its place in the shipped file (FR-096).

    Either it is common enough to be ranked in the top :data:`MAX_RANK` of a frequency
    list, or it carries a school/exam tag (``zk`` 中考, ``gk`` 高考, ``cet4``, ``cet6``,
    ``ky`` 考研, ``toefl``, ``ielts``, ``gre``) - words a reader is likely to reach for
    the dictionary over precisely because they are rare in prose.
    """
    return (
        any(0 < _rank(row, column) <= MAX_RANK for column in ("frq", "bnc"))
        or bool((row.get("tag") or "").strip())
    )


def _form_pairs(key: str, exchange: str | None) -> Iterator[tuple[str, str]]:
    """The ``(form, lemma)`` pairs one row's ``exchange`` column declares.

    Both directions are read because both appear: the row for "went" points at its lemma
    (``0:go``) and the row for "go" lists its own inflections (``p:went``).  Which one a
    given pair of words carries varies, and reading only one side would leave holes.
    """
    for part in (exchange or "").split("/"):
        tag, _, value = part.partition(":")
        value = value.strip().lower()
        if not value or len(value) > MAX_KEY_CHARS or " " in value:
            continue
        yield (key, value) if tag == _LEMMA_TAG else (value, key)


def main(argv: list[str] | None = None) -> int:
    """Download what is missing, build the file, and say how big it came out."""
    parser = argparse.ArgumentParser(description="把 ECDICT 裁剪成随包发布的英汉词典")
    parser.add_argument("--csv", type=Path, default=CACHE_CSV, help="ECDICT 的 ecdict.csv")
    parser.add_argument(
        "--out",
        type=Path,
        default=dict_dir() / EN_DICT_FILE,
        help="输出的 SQLite 文件（默认写进包里）",
    )
    parser.add_argument("--refresh", action="store_true", help="忽略缓存，重新下载 CSV")
    args = parser.parse_args(argv)

    fetch_csv(args.csv, refresh=args.refresh)
    started = time.monotonic()
    entries, forms = build(args.csv, args.out)
    megabytes = args.out.stat().st_size / 1_000_000
    print(
        f"[dict] {args.out.name}：{entries} 条词条、{forms} 条屈折形式、"
        f"{megabytes:.1f} MB，用时 {time.monotonic() - started:.1f} s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

