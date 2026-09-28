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
    assert len(kangpo.toc) == 8, "four front matter rows, three parts and the afterword"
    assert len(_rows(kangpo.toc)) == 26
    assert any(entry.title for entry in _rows(kangpo.toc))


def test_toc_comes_from_ncx_for_epub2(binan) -> None:
    assert binan.package.nav_href is None
    assert binan.package.ncx_href is not None
    assert len(binan.toc) >= 10


def _rows(entries) -> list:
    """Every row of a contents tree, parents before their children."""
    return [row for entry in entries for row in (entry, *_rows(entry.children))]


def test_a_flat_contents_is_given_the_hierarchy_of_its_headings(kangpo) -> None:
    """The nav document lists its 26 rows on one level; the chapters say what they are.

    the EPUB 3 book's navigation document is a single flat ``<ol>`` - every ``<li>`` at the same
    level - so 第一部分 and the eighteen numbered chapters inside it arrived as siblings
    and the panel drew the front matter, the three parts and the chapters in one column
    (缺陷 26).  Nothing about the hierarchy is missing from the book: it is written in
    the chapter documents, where the front matter and the part openings are ``<h1>``
    and the numbered chapters are ``<h2>``.
    """
    rows = _rows(kangpo.toc)
    levels = [entry.level for entry in rows]
    assert len(rows) == 26 and len(rows) == kangpo.section_count - 1
    assert levels.count(0) == 8, "the four front matter rows, three parts, one afterword"
    assert levels.count(1) == 18, "every numbered chapter"

    parts = [entry for entry in kangpo.toc if entry.children]
    assert [entry.title for entry in parts] == [
        "第一部分 涛动周期论",
        "第二部分 大类资产配置",
        "第三部分 先生言谈",
    ]
    assert [len(entry.children) for entry in parts] == [8, 5, 5]
    assert [child.level for child in parts[0].children] == [1] * 8
    assert parts[0].children[0].title.startswith("01 ")


def test_the_volumes_of_the_omnibus_take_the_chapters_inside_them(linqi) -> None:
    """Three declared rows and 135 sections: each chapter sits under its own volume.

    The NCX names three volumes and nothing else, and the other 131 rows are filled in
    from the sections themselves (FR-012) - inside the volume they follow rather than
    beside it, because the omnibus is three books and the map has to say which.  This is
    the sweep of the omnibus: 31 + 66 + 32, one row per section from the first chapter
    to 后记.
    """
    assert [entry.title for entry in linqi.toc] == [
        "彼得林奇投资经典全集（共3册）",
        "目录",
        "战胜华尔街Beating the Street（珍藏版）",
        "彼得·林奇教你理财",
        "彼得·林奇的成功投资（珍藏版）",
    ]
    assert [len(entry.children) for entry in linqi.toc] == [0, 0, 31, 66, 32]
    assert linqi.toc[4].children[-1].title == "后记 马里兰之旅的感悟"
    assert len(_rows(linqi.toc)) == linqi.section_count - 1, "one row per nameable section"


def test_a_flat_contents_whose_text_is_flat_stays_flat(binan) -> None:
    """Nothing is inferred where nothing was written: the EPUB 2 book's sections are not headings.

    Its NCX lists its rows on one level and its sections open with prose, so reading a
    hierarchy off the headings finds none and the list the book declared is kept as it
    is - including the two rows filled in before the NCX's first name (sections 1 and
    3), which stay on the top level because there is no row before them to own them.
    """
    rows = _rows(binan.toc)
    assert {entry.level for entry in rows} == {0}
    assert all(not entry.children for entry in rows)
    assert [entry.title for entry in binan.toc[:2]] == ["币安人生", "免责声明"]


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


GBK_PACKAGE = """<?xml version="1.0" encoding="gbk"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:sample</dc:identifier>
    <dc:title>書名</dc:title>
    <dc:creator>作者</dc:creator>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="i0" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="i0"/></spine>
</package>
"""


def test_a_package_written_in_a_legacy_encoding_is_still_read(build_epub) -> None:
    """A declared ``gbk`` makes expat raise ``ValueError``, which is not a ``ParseError``.

    The title and the author are exactly the text that has to be decoded, so refusing the
    document would mean a traceback out of ``open()`` for a book that names its own
    encoding correctly.
    """
    path = build_epub(
        {"text/ch1.xhtml": "<html><body><p>正文</p></body></html>"},
        package_xml=GBK_PACKAGE.encode("gbk"),
    )
    book = EpubBook.open(path)
    assert book.meta.title == "書名"
    assert book.meta.authors == ("作者",)
    assert book.meta.language == "zh"


def test_section_lookup_by_href(kangpo) -> None:
    for item in kangpo.spine[:5]:
        assert kangpo.section_index_for_href(item.href) == item.index
    assert kangpo.section_index_for_href("EPUB/xhtml/Nonexistent") == -1


# --------------------------------------------- the package's own pointers (FR-011)


SPINE_NAMES_ITS_NCX = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:sample</dc:identifier>
    <dc:title>样本</dc:title>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="i0" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="map" href="toc.ncx" media-type="text/plain"/>
  </manifest>
  <spine toc="map">
    <itemref idref="i0"/>
  </spine>
</package>
"""

WRONG_MEDIA_TYPE_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章 引言</text></navLabel>
      <content src="text/ch1.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def test_the_spine_names_the_ncx_when_the_manifest_does_not(build_epub) -> None:
    """EPUB 2 points at its NCX twice; a wrong media type only breaks one of the two.

    The row title is deliberately *not* the chapter's own heading, so that a reader
    falling back to the heading outline cannot pass this test by accident.
    """
    path = build_epub(
        {"text/ch1.xhtml": "<html><body><h1>第一章</h1></body></html>"},
        resources={"toc.ncx": WRONG_MEDIA_TYPE_NCX},
        manifest={"toc.ncx": ("text/plain", "")},
        package_xml=SPINE_NAMES_ITS_NCX,
    )
    book = EpubBook.open(path)
    assert [entry.title for entry in book.toc] == ["第一章 引言"]


def test_a_document_marked_non_linear_is_not_paged_into(build_epub) -> None:
    """``linear="no"`` is in the spine but not in the text: a cover, an advertisement."""
    path = build_epub(
        {
            "text/cover.xhtml": "<html><body><p>封面</p></body></html>",
            "text/ch1.xhtml": "<html><body><h1>第一章</h1></body></html>",
            "text/ad.xhtml": "<html><body><p>广告</p></body></html>",
            "text/ch2.xhtml": "<html><body><h1>第二章</h1></body></html>",
        },
        linear_no=["text/cover.xhtml", "text/ad.xhtml"],
    )
    book = EpubBook.open(path)
    assert book.section_count == 4  # the documents are still readable, and still indexed
    assert [item.linear for item in book.spine] == [False, True, False, True]
    assert book.next_section(0) == 1
    assert book.next_section(1) == 3
    assert book.next_section(3) == -1
    assert book.previous_section(3) == 1  # backwards across the advertisement
    assert book.previous_section(2) == 1
    assert book.previous_section(1) == -1  # the cover before it is not part of the text
    assert book.previous_section(0) == -1


def test_paging_the_reference_books_never_skips_a_section(kangpo, binan) -> None:
    """A linear spine is what the reference books have; the walk must be a plain walk."""
    for book in (kangpo, binan):
        assert all(item.linear for item in book.spine)
        for index in range(book.section_count - 1):
            assert book.next_section(index) == index + 1
        assert book.previous_section(0) == -1

