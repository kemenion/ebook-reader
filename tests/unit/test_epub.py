"""EPUB container parsing against the two reference books.

The traps these tests guard are specific and were found by inspecting the real
files, not imagined: the EPUB 3 book stores 25 of its 28 documents under names
with no extension at all, and it is the *manifest media type* - never the file
name - that says what a document is (I-1 / FR-004).
"""

from __future__ import annotations

import pytest

from ebook_reader.domain import EpubBook
from ebook_reader.domain.epub.paths import join_href, normalize, split_fragment


def test_opens_both_books(kangpo, binan) -> None:
    assert kangpo.section_count == 27
    assert binan.section_count == 35


def test_metadata_is_read(kangpo, binan) -> None:
    assert "康波" in kangpo.meta.title
    assert kangpo.meta.authors == ("周金涛",)
    assert "币安" in binan.meta.title
    assert binan.meta.language.startswith("zh")


def test_extensionless_documents_are_recognised(kangpo) -> None:
    """The single most important parsing rule in this project."""
    extensionless = [
        item for item in kangpo.spine if "." not in item.href.rsplit("/", 1)[-1]
    ]
    assert len(extensionless) == 25
    assert all(item.media_type == "application/xhtml+xml" for item in extensionless)
    # And they really are readable documents, not empty placeholders.
    for item in extensionless:
        assert len(kangpo.section_bytes(item.index)) > 100


def test_every_spine_document_parses(kangpo, binan) -> None:
    for book in (kangpo, binan):
        for index in range(book.section_count):
            assert book.section_bytes(index), (book.path.name, index)
            assert book.blocks(index), (book.path.name, index)


def test_every_image_resource_resolves(kangpo, binan) -> None:
    assert len(kangpo.image_names()) == 408
    assert len(binan.image_names()) == 53
    for book in (kangpo, binan):
        for name in book.image_names():
            assert book.has_resource(name), name


def test_relative_image_hrefs_are_normalised(kangpo) -> None:
    """Chapter documents live in ``EPUB/xhtml`` and point at ``../images/...``."""
    sources = {
        block.image.src
        for index in range(kangpo.section_count)
        for block in kangpo.blocks(index)
        if block.image is not None
    }
    assert sources
    assert all(source.startswith("EPUB/") for source in sources)
    assert all(".." not in source for source in sources)
    assert all(kangpo.has_resource(source) for source in sources)


def test_toc_comes_from_nav_for_epub3(kangpo) -> None:
    assert kangpo.package.nav_href is not None
    assert len(kangpo.toc) >= 20
    assert any(entry.title for entry in kangpo.toc)


def test_toc_comes_from_ncx_for_epub2(binan) -> None:
    assert binan.package.nav_href is None
    assert binan.package.ncx_href is not None
    assert len(binan.toc) >= 10


def test_nested_toc_entries_are_preserved(kangpo, binan) -> None:
    def depth(entries) -> int:
        return 1 + max((depth(entry.children) for entry in entries), default=0)

    assert depth(kangpo.toc) >= 1
    assert depth(binan.toc) >= 1


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("Chapter1", "EPUB/xhtml/Chapter1"),
        ("../images/a.jpg", "EPUB/images/a.jpg"),
        ("/absolute/x.xhtml", "absolute/x.xhtml"),
        ("./same.xhtml", "EPUB/xhtml/same.xhtml"),
        ("has%20space.xhtml", "EPUB/xhtml/has space.xhtml"),
        ("frag.xhtml#note1", "EPUB/xhtml/frag.xhtml"),
    ],
)
def test_href_resolution(href: str, expected: str) -> None:
    assert join_href("EPUB/xhtml", href) == expected


def test_path_helpers() -> None:
    assert normalize("a/./b/../c") == "a/c"
    assert normalize("/leading") == "leading"
    assert split_fragment("a.xhtml#frag") == ("a.xhtml", "frag")
    assert split_fragment("a.xhtml") == ("a.xhtml", "")


def test_bad_input_raises_a_readable_error(tmp_path) -> None:
    from ebook_reader.domain import BookError

    broken = tmp_path / "broken.epub"
    broken.write_bytes(b"this is not a zip file")
    with pytest.raises(BookError):
        EpubBook.open(broken)

    missing = tmp_path / "missing.epub"
    with pytest.raises(BookError):
        EpubBook.open(missing)


def test_section_lookup_by_href(kangpo) -> None:
    for item in kangpo.spine[:5]:
        assert kangpo.section_index_for_href(item.href) == item.index
    assert kangpo.section_index_for_href("EPUB/xhtml/Nonexistent") == -1
