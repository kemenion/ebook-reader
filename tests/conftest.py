"""Shared fixtures.

The reference books are the test data: they are the actual input the design was
built around, so testing against them catches far more than synthetic samples.
They are skipped rather than failed when absent, so the suite still runs in a
tree without the ``ebooks`` directory.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BOOKS = ROOT / "ebooks"


def _find(pattern: str) -> Path:
    matches = sorted(BOOKS.glob(pattern))
    if not matches:
        pytest.skip(f"reference book not present: {pattern}")
    return matches[0]


@pytest.fixture(scope="session")
def kangpo_path() -> Path:
    """EPUB 3 book: 27 sections, 408 images, 25 extension-less documents."""
    return _find("*康波*.epub")


@pytest.fixture(scope="session")
def binan_path() -> Path:
    """EPUB 2 book: 35 sections, an NCX table of contents, 53 grayscale images."""
    return _find("*币安*.epub")


@pytest.fixture(scope="session")
def kangpo(kangpo_path: Path):
    from ebook_reader.domain import EpubBook

    book = EpubBook.open(kangpo_path)
    yield book
    book.close()


@pytest.fixture(scope="session")
def binan(binan_path: Path):
    from ebook_reader.domain import EpubBook

    book = EpubBook.open(binan_path)
    yield book
    book.close()


@pytest.fixture(scope="session")
def largest_section(kangpo) -> int:
    """Index of the section with the most text, i.e. the worst case for layout."""
    return max(
        range(kangpo.section_count),
        key=lambda index: sum(len(block.text) for block in kangpo.blocks(index)),
    )


def _headings(book, index: int) -> list[int]:
    """Positions of the blocks that are headings with something to say (FR-018)."""
    return [
        position
        for position, block in enumerate(book.blocks(index))
        if block.is_heading and block.text.strip()
    ]


@pytest.fixture(scope="session")
def heading_section(kangpo) -> int:
    """The 康波 section carrying the most headings: the fullest outline available.

    The outline column is heading-only (ADR-016), so a test that needs rows has to
    be looking at a section that has headings - the book opens on its cover, which
    has none.
    """
    return max(
        range(kangpo.section_count),
        key=lambda index: (len(_headings(kangpo, index)), -index),
    )


@pytest.fixture(scope="session")
def headingless_section(binan) -> int:
    """The longest 币安 section with no headings: the one with nothing to list.

    币安 is the book that used to exercise the page-per-row fallback; with that gone
    it is the book that proves a section without headings gets no outline at all.
    """
    candidates = [
        index
        for index in range(binan.section_count)
        if len(binan.blocks(index)) > 100 and not _headings(binan, index)
    ]
    if not candidates:
        pytest.skip("reference book has no long section without headings")
    return max(candidates, key=lambda index: len(binan.blocks(index)))


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen ``QGuiApplication`` for the whole session."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    application = QGuiApplication.instance() or QGuiApplication([])
    yield application
