"""The contents tree: what each row points at, and where that is (FR-010 .. FR-014).

A row names two things, and they are not the same thing: a document, which is
resolved against the spine, and a place inside it, which is resolved against the laid
out text.  Reading the two as one string is what made four pairs of rows in the EPUB 2
reference book point at the very same place - both at the top of the file they shared -
and made every row whose href was an anchor rather than a file point at the top of its
document instead of at the passage it named.

The parsers are exercised on small documents written here rather than on the books, so
that a row that loses its anchor fails for the rule and not for the book that happens
to be installed; the reference book is used once, to say what the rule is worth.
"""

from __future__ import annotations

import pytest

from ebook_reader.app.controller import _flatten_toc
from ebook_reader.domain import EpubBook
from ebook_reader.domain.models import TocEntry
from ebook_reader.domain.epub.toc import parse_landmarks, parse_nav, parse_ncx

NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
        <li><a href="ch1.xhtml">第一章</a></li>
        <li><a href="ch1.xhtml#page_6">献词</a></li>
        <li><a href="ch2.xhtml#%E4%B8%AD%E6%96%87">中文锚点</a>
          <ol><li><a href="../other/ch3.xhtml#top">深层</a></li></ol>
        </li>
      </ol>
    </nav>
  </body>
</html>
"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章</text></navLabel>
      <content src="Text/ch1.xhtml"/></navPoint>
    <navPoint id="n2"><navLabel><text>第二节</text></navLabel>
      <content src="Text/ch1.xhtml#filepos1234"/></navPoint>
  </navMap>
</ncx>
"""


# ------------------------------------------------------------ one row, two targets


def test_a_nav_row_keeps_the_anchor_it_was_written_with() -> None:
    entries = parse_nav(NAV.encode(), "OEBPS/text")
    assert (entries[1].href, entries[1].fragment) == ("OEBPS/text/ch1.xhtml", "page_6")
    # And a row that names no anchor says so, rather than inheriting the one above.
    assert (entries[0].href, entries[0].fragment) == ("OEBPS/text/ch1.xhtml", "")


def test_an_ncx_row_keeps_the_anchor_it_was_written_with() -> None:
    entries = parse_ncx(NCX.encode(), "OEBPS")
    assert (entries[0].href, entries[0].fragment) == ("OEBPS/Text/ch1.xhtml", "")
    assert (entries[1].href, entries[1].fragment) == ("OEBPS/Text/ch1.xhtml", "filepos1234")


def test_a_percent_encoded_anchor_is_decoded() -> None:
    """The anchor has to match the ``id`` in the HTML, which arrives decoded."""
    entries = parse_nav(NAV.encode(), "OEBPS/text")
    assert entries[2].fragment == "中文"
    assert entries[2].children[0].fragment == "top"          # plain anchors keep working
    assert entries[2].children[0].href == "OEBPS/other/ch3.xhtml"


def test_an_entry_written_by_hand_still_has_a_fragment_field() -> None:
    """The default matters: entries are also built from headings, which never anchor."""
    assert TocEntry(title="章", href="a.xhtml").fragment == ""


def test_a_row_without_an_href_points_nowhere() -> None:
    nav = (
        '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
        "<ol><li><span>没有链接</span></li></ol></nav>"
    )
    entries = parse_nav(nav.encode(), "")
    assert (entries[0].href, entries[0].fragment) == ("", "")


# ------------------------------------------------------- resolving the two targets


def test_section_lookup_ignores_the_anchor(kangpo) -> None:
    """The path names the document; the anchor names a place inside it."""
    item = kangpo.spine[3]
    assert kangpo.section_index_for_href(item.href) == item.index
    assert kangpo.section_index_for_href(f"{item.href}#page_6") == item.index
    assert kangpo.section_index_for_href("EPUB/xhtml/Nonexistent#page_6") == -1


def test_a_row_can_be_both_scrolled_and_anchored(binan) -> None:
    """The reference case: two rows in one file, one of them anchored (FR-014).

    币安's NCX points 推薦語 and 獻詞 at the same document; before the anchor was kept,
    both rows landed on the top of it, so the map said the second chapter began where
    the first one did.
    """
    rows = _flatten_toc(binan.toc, binan)
    first, second = rows[0], rows[1]
    assert first[1] == second[1], "the fixture assumes one file holding both chapters"
    assert first[0].fragment == "" and second[0].fragment, "the second row is anchored"

    blocks = binan.blocks(second[1])
    owner = next(
        index for index, block in enumerate(blocks) if second[0].fragment in block.anchor_ids
    )
    assert owner > 0, "an anchor inside the file, not at the top of it"


def test_every_row_of_the_reference_book_keeps_its_own_place(binan) -> None:
    """No two rows may answer with the same target unless the book says so."""
    rows = _flatten_toc(binan.toc, binan)
    targets = [(section, entry.fragment) for entry, section in rows]
    assert len(targets) == len(rows)
    assert len(set(targets)) == len(rows), "two contents rows point at the same place"


@pytest.mark.parametrize("book", ["binan", "kangpo"])
def test_every_anchor_of_the_reference_books_resolves(book, request) -> None:
    """An anchor that resolves nowhere is a row that lies about where it leads."""
    epub = request.getfixturevalue(book)
    unresolved = []
    for entry, section in _flatten_toc(epub.toc, epub):
        if not entry.fragment:
            continue
        ids = {anchor for block in epub.blocks(section) for anchor in block.anchor_ids}
        if entry.fragment not in ids:
            unresolved.append((section, entry.fragment, entry.title))
    assert unresolved == []


# --------------------------------------------- what a sloppy document still says


UL_NAV = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <nav>
      <ul>
        <li><a href="ch1.xhtml">第一章</a></li>
        <li><a href="ch2.xhtml">第二章</a>
          <ul><li><a href="ch2.xhtml#note">注</a></li></ul>
        </li>
      </ul>
    </nav>
  </body>
</html>
"""

BROKEN_NAV = """<html xmlns="http://www.w3.org/1999/xhtml">
  <body>
    <nav epub:type="toc"><ol>
      <li><a href="ch1.xhtml">第一章 & 献词
      <li><a href="ch2.xhtml">第二章</a>
        <ul><li><a href="ch2.xhtml#note">注</a></ul>
    </ol></nav>
  </body>
</html>
"""

BROKEN_NCX = """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章 & 献词</text></navLabel>
      <content src="Text/ch1.xhtml"/>
      <navPoint id="n1a"><navLabel><text>第一节</text></navLabel>
        <content src="Text/ch1.xhtml#p1"/></navPoint>
    </navPoint>
    <navPoint id="n2"><navLabel><text>第二章</text></navLabel>
      <content src="Text/ch2.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def test_a_nav_list_may_be_a_ul() -> None:
    """The specification asks for ``ol``; hand-converted EPUB 2 files carry ``ul``."""
    entries = parse_nav(UL_NAV.encode(), "")
    assert [entry.title for entry in entries] == ["第一章", "第二章"]
    assert entries[1].children[0].href == "ch2.xhtml"
    assert entries[1].children[0].fragment == "note"


def test_a_nav_document_without_a_list_still_has_rows() -> None:
    """No ``ol``, no ``ul``, no indentation - the links are still a contents."""
    nav = (
        '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
        '<a href="a.xhtml">一</a><a href="b.xhtml">二</a></nav>'
    )
    entries = parse_nav(nav.encode(), "")
    assert [(entry.title, entry.href, entry.level) for entry in entries] == [
        ("一", "a.xhtml", 0),
        ("二", "b.xhtml", 0),
    ]


def test_a_nav_document_that_is_not_well_formed_keeps_its_rows() -> None:
    """A raw ``&`` and an unclosed ``<li>``: expat refuses the file, a browser shows it."""
    entries = parse_nav(BROKEN_NAV.encode(), "OEBPS")
    assert [(entry.title, entry.href, entry.level) for entry in entries] == [
        ("第一章 & 献词", "OEBPS/ch1.xhtml", 0),
        ("第二章", "OEBPS/ch2.xhtml", 0),
    ]
    assert [(child.title, child.fragment) for child in entries[1].children] == [("注", "note")]


def test_an_ncx_that_is_not_well_formed_keeps_its_rows_and_their_nesting() -> None:
    entries = parse_ncx(BROKEN_NCX.encode(), "")
    assert [entry.title for entry in entries] == ["第一章 & 献词", "第二章"]
    assert [(child.title, child.href) for child in entries[0].children] == [
        ("第一节", "Text/ch1.xhtml"),
    ]
    assert entries[0].children[0].fragment == "p1"


def test_an_ncx_without_closing_tags_keeps_the_rows_it_does_name() -> None:
    """Nothing is closed; the rows survive in document order, so the map is not empty."""
    raw = BROKEN_NCX.replace("&", "+").replace("</navPoint>", "")
    entries = parse_ncx(raw.encode(), "")
    assert _all_titles(entries) == ["第一章 + 献词", "第一节", "第二章"]

GBK_NAV = """<?xml version="1.0" encoding="gbk"?>
<html xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc"><ol>
      <li><a href="ch1.xhtml">第一章 獻詞</a>
      <li><a href="ch2.xhtml">第二章</a>
    </ol></nav>
  </body>
</html>
"""


def test_a_navigation_document_is_decoded_the_way_it_says_it_is_encoded() -> None:
    """A document that declares ``gbk`` is read as ``gbk``, not as broken UTF-8.

    Rows read as the wrong encoding are the wrong Chinese: the failure looks like a
    font problem and is not one.  The declaration is in the first bytes of the file,
    which is where the decoder looks for it.
    """
    entries = parse_nav(GBK_NAV.encode("gbk"), "")
    assert [entry.title for entry in entries] == ["第一章 獻詞", "第二章"]


def test_an_ncx_written_in_a_legacy_encoding_is_read_in_that_encoding() -> None:
    """The other parser refuses the same bytes for the same reason, and has to recover."""
    raw = (
        '<?xml version="1.0" encoding="gbk"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/"><navMap>'
        "<navPoint id='n1'><navLabel><text>第一章 獻詞</text></navLabel>"
        "<content src='Text/ch1.xhtml'/></navPoint></navMap></ncx>"
    ).encode("gbk")
    entries = parse_ncx(raw, "")
    assert [entry.title for entry in entries] == ["第一章 獻詞"]




def _all_titles(entries) -> list[str]:
    """Every title in the tree, parents before their children."""
    return [
        title
        for entry in entries
        for title in (entry.title, *_all_titles(entry.children))
    ]


# ------------------------------------------------ landmarks, and links without a path


NAV_WITH_LANDMARKS = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol><li><a href="text/ch1.xhtml">第一章</a></li></ol>
    </nav>
    <nav epub:type="landmarks">
      <ol>
        <li><a epub:type="cover" href="text/cover.xhtml">封面</a></li>
        <li><a epub:type="bodymatter" href="text/ch1.xhtml#page_1">正文</a></li>
      </ol>
    </nav>
  </body>
</html>
"""


def test_landmarks_are_read_with_the_type_the_book_gave_them() -> None:
    landmarks = parse_landmarks(NAV_WITH_LANDMARKS.encode(), "OEBPS")
    assert [(row.type, row.href, row.fragment) for row in landmarks] == [
        ("cover", "OEBPS/text/cover.xhtml", ""),
        ("bodymatter", "OEBPS/text/ch1.xhtml", "page_1"),
    ]


def test_a_nav_document_without_landmarks_has_none() -> None:
    """The contents document is not a landmark; taking it would name the wrong page."""
    assert parse_landmarks(NAV.encode(), "OEBPS/text") == ()


def test_a_book_reads_its_landmarks_and_keeps_them_out_of_the_contents(build_epub) -> None:
    path = build_epub(
        {
            "text/cover.xhtml": "<html><body><p>封面</p></body></html>",
            "text/ch1.xhtml": "<html><body><h1>第一章</h1></body></html>",
        },
        resources={"nav.xhtml": NAV_WITH_LANDMARKS},
        manifest={"nav.xhtml": ("application/xhtml+xml", "nav")},
    )
    book = EpubBook.open(path)
    assert [row.type for row in book.landmarks] == ["cover", "bodymatter"]
    assert [entry.title for entry in book.toc] == ["第一章"]


def test_a_book_that_says_nothing_about_its_parts_has_no_landmarks(binan) -> None:
    assert binan.landmarks == ()


def test_a_link_with_no_path_is_answered_by_the_document_it_was_written_in(binan) -> None:
    """``#note3`` names a place in the current document, not a document called ``#note3``."""
    assert binan.section_index_for_href("#note3") == -1
    assert binan.section_index_for_href("#note3", current=2) == 2
    assert binan.section_index_for_href("#note3", current=binan.section_count) == -1



def test_a_document_with_no_navigation_at_all_is_an_empty_tree_not_an_error() -> None:
    assert parse_nav(b"<html><body><p>\xe6\xad\xa3\xe6\x96\x87</p></body></html>", "") == ()
    assert parse_ncx(b"<ncx><navLabel><text>x</text></navLabel></ncx>", "") == ()

