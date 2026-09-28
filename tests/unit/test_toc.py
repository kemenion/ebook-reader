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
from ebook_reader.domain.epub.toc import parse_landmarks, parse_nav, parse_ncx
from ebook_reader.domain.html.kinsoku import strip_word_joiners
from ebook_reader.domain.models import TocEntry

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

    the EPUB 2 book's NCX points 推薦語 and 獻詞 at the same document; before the anchor was kept,
    both rows landed on the top of it, so the map said the second chapter began where
    the first one did.

    The pair is searched for rather than assumed to be the first two rows: the panel
    also carries the rows the book leaves unnamed (FR-012), and the EPUB 2 book's 免责声明 comes
    before that pair.
    """
    rows = _flatten_toc(binan.toc, binan)
    position = next(at for at in range(len(rows) - 1) if rows[at][1] == rows[at + 1][1])
    first, second = rows[position], rows[position + 1]
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
    # The cover's own first line is deliberately not what the landmark calls it, so a
    # row labelled 封面 could only have come from the landmarks leaking (FR-009); the
    # second row's empty fragment says the same about bodymatter's anchor.
    path = build_epub(
        {
            "text/cover.xhtml": "<html><body><p>卷首</p></body></html>",
            "text/ch1.xhtml": "<html><body><h1>第一章</h1></body></html>",
        },
        resources={"nav.xhtml": NAV_WITH_LANDMARKS},
        manifest={"nav.xhtml": ("application/xhtml+xml", "nav")},
    )
    book = EpubBook.open(path)
    assert [row.type for row in book.landmarks] == ["cover", "bodymatter"]
    assert [(entry.title, entry.fragment) for entry in book.toc] == [
        ("卷首", ""),
        ("第一章", ""),
    ]


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


# ------------------------------------- the sections the contents does not name (FR-012)

#: A book whose NCX names one section out of four - the shape the omnibus has, small
#: enough to reason about.  Section 0 says nothing, 1 names itself, 2 is a photo
#: caption and 3 is the one the book itself names.
SPARSE_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="n3"><navLabel><text>第三章 收尾</text></navLabel>
      <content src="text/ch3.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def _ncx_book(build_epub, documents, ncx: str = SPARSE_NCX, **kwargs):
    """A book whose only navigation is the NCX written here."""
    return build_epub(
        documents,
        resources={"toc.ncx": ncx},
        manifest={"toc.ncx": ("application/x-dtbncx+xml", "ncx")},
        **kwargs,
    )


def _contents(path) -> list[tuple[str, int]]:
    """The contents as the panel reads it: each row's title and the section it leads to."""
    book = EpubBook.open(path)
    return [(entry.title, section) for entry, section in _flatten_toc(book.toc, book)]


def test_a_section_the_contents_does_not_name_names_itself(build_epub) -> None:
    """The row a book leaves out is filled from the section's own first line (FR-012).

    A section that says nothing at all (a cover that is one image), and one whose
    first line is a caption rather than a name, stay out of the panel rather than
    turning up as rows that say nothing about where they lead.
    """
    path = _ncx_book(
        build_epub,
        {
            "text/cover.xhtml": "<html><body><p><img src='cover.png'/></p></body></html>",
            "text/ch1.xhtml": "<html><body><p>第一章 起步</p><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><p>1989年，温哥华的车库。</p></body></html>",
            "text/ch3.xhtml": "<html><body><h1>第三章 收尾</h1></body></html>",
        },
    )
    assert _contents(path) == [("第一章 起步", 1), ("第三章 收尾", 3)]


@pytest.mark.parametrize(
    "first_line",
    [
        "2017年10月，东京工作时期每日通勤的自行车。",          # a photo caption
        "出版者：",                                          # a masthead
        "最早阅读彼得·林奇的著作还是十几年前的事，当时我对这位美国富达公司麦哲伦基金经理印象最深的一点，就是他强调日常生活经验有助于股票投资。",
    ],
)
def test_a_first_line_that_reads_as_prose_is_not_a_name(build_epub, first_line: str) -> None:
    """A book with no contents and no names gets no rows, rather than rows of prose.

    A row has one job - telling the reader where it leads - and a sentence of the
    third chapter's first paragraph does not do that job.
    """
    path = build_epub({"text/ch1.xhtml": f"<html><body><p>{first_line}</p></body></html>"})
    assert EpubBook.open(path).toc == ()


def test_a_heading_is_a_name_however_long_it_says(build_epub) -> None:
    """A marked heading is the publisher's name, so length and punctuation do not count."""
    title = "第十二章 关于在一个所有人都在谈论股票的年代里如何保持冷静并继续长期投资的若干思考"
    path = build_epub(
        {"text/ch1.xhtml": f"<html><body><h3>{title}</h3><p>正文。</p></body></html>"}
    )
    book = EpubBook.open(path)
    assert [entry.title for entry in book.toc] == [title]


def test_a_document_outside_the_reading_order_gets_no_row(build_epub) -> None:
    """``linear="no"`` is not part of the order the reader walks (FR-016)."""
    path = build_epub(
        {
            "text/ch1.xhtml": "<html><body><p>第一章 起</p><p>正文。</p></body></html>",
            "text/ad.xhtml": "<html><body><p>广告页 五折促销</p><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><p>第二章 止</p><p>正文。</p></body></html>",
        },
        linear_no=["text/ad.xhtml"],
    )
    assert [entry.title for entry in EpubBook.open(path).toc] == ["第一章 起", "第二章 止"]


NESTED_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="p1"><navLabel><text>第一部分</text></navLabel>
      <content src="text/ch1.xhtml"/>
      <navPoint id="c2"><navLabel><text>第二章</text></navLabel>
        <content src="text/ch2.xhtml"/></navPoint>
    </navPoint>
    <navPoint id="p2"><navLabel><text>第二部分</text></navLabel>
      <content src="text/ch5.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def test_the_added_rows_keep_their_place_and_the_books_nesting(build_epub) -> None:
    """Filling the gaps adds rows; it does not rearrange what the book wrote.

    The unnamed sections land inside the part whose chapters surround them, at that
    part's level, and in reading order - the order the panel's highlight and 「下一章」
    both depend on.
    """
    path = _ncx_book(
        build_epub,
        {
            f"text/ch{number}.xhtml": (
                f"<html><body><p>第{number}节 标题</p><p>正文。</p></body></html>"
            )
            for number in range(1, 7)
        },
        ncx=NESTED_NCX,
    )
    book = EpubBook.open(path)
    assert book.toc[0].title == "第一部分"
    assert [child.title for child in book.toc[0].children] == [
        "第二章",
        "第3节 标题",
        "第4节 标题",
    ], "the part keeps its chapter and takes the sections that belong to it"
    assert [
        (entry.title, section, entry.level) for entry, section in _flatten_toc(book.toc, book)
    ] == [
        ("第一部分", 0, 0),
        ("第二章", 1, 1),
        ("第3节 标题", 2, 1),
        ("第4节 标题", 3, 1),
        ("第二部分", 4, 0),
        ("第6节 标题", 5, 0),
    ]


def test_a_name_the_reading_window_cut_in_half_is_read_again_in_full(build_epub) -> None:
    """A document is read whole when its head ended on the very block that names it.

    The name is looked for in the head of the file - that is what keeps naming 135
    sections cheap (see ``_LABEL_SNIFF_BYTES``).  The head that cannot answer is the
    one that ran out in the middle of the line, and the answer then has to come from
    the whole document rather than from the half of it that fitted.
    """
    from ebook_reader.domain.epub.book import _LABEL_SNIFF_BYTES

    title = "第九章 名字正好落在读窗口中间的那一章"
    opening, closing = "<html><head><!--", "--></head><body><p>"
    room = _LABEL_SNIFF_BYTES - len(opening.encode()) - len(closing.encode())
    padding = "x" * (room - len(title.encode()) // 2)
    document = f"{opening}{padding}{closing}{title}</p><p>正文。</p></body></html>"
    cut = document.encode()[:_LABEL_SNIFF_BYTES]
    assert title.encode()[:3] in cut and title.encode() not in cut, "the window misses the name"

    path = build_epub({"text/ch1.xhtml": document})
    assert [entry.title for entry in EpubBook.open(path).toc] == [title]


def test_the_omnibus_shows_every_chapter_the_keys_walk_through(linqi) -> None:
    """135 sections, three of them named by the NCX: the rest name themselves (FR-012).

    This is the book the gap was found on.  `]` walked 135 sections while the panel
    offered three rows, so the reader could move through the book but could not see
    where they were going - and the sections that were missing are exactly the ones
    the book never mentions.
    """
    rows = _flatten_toc(linqi.toc, linqi)
    targets = [section for _, section in rows]
    assert len(rows) >= 130
    assert targets == sorted(targets), "the panel reads the rows in reading order"
    assert 0 not in targets, "the title page says nothing, so it gets no row"

    contents = {entry.title: section for entry, section in rows}
    assert contents["战胜华尔街Beating the Street（珍藏版）"] == 3       # the book's own row
    assert contents["第1章 业余投资者比专业投资者业绩更好"] == 12       # a chapter's own name
    assert contents["后记 马里兰之旅的感悟"] == linqi.section_count - 1
    # The three rows the book wrote are the hierarchy; everything else is inside one of
    # them, so the panel draws a tree rather than one long list (ADR-020).
    assert {entry.level for entry, _ in rows} == {0, 1}


# ------------------------ a contents that draws no hierarchy of its own (FR-012 / ADR-020)


_NAV_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
  <body>
    <nav epub:type="toc">
      <ol>
{rows}
      </ol>
    </nav>
  </body>
</html>
"""


def _flat_nav_book(build_epub, rows, documents, **kwargs):
    """A book whose only navigation is a flat ``<ol>`` of *rows* - one ``<li>`` per row.

    ``rows`` are ``(title, href)`` pairs in reading order and all on the same level, which
    is the shape the EPUB 3 book's own ``nav.xhtml`` has: a list of names with no nesting in it,
    so the only hierarchy the reader can show is the one the documents were written with.
    """
    items = "\n".join(f'        <li><a href="{href}">{title}</a></li>' for title, href in rows)
    return build_epub(
        documents,
        resources={"nav.xhtml": _NAV_TEMPLATE.format(rows=items)},
        manifest={"nav.xhtml": ("application/xhtml+xml", "nav")},
        **kwargs,
    )


def test_a_flat_nav_takes_its_hierarchy_from_the_headings_of_its_sections(build_epub) -> None:
    """A list of names with no nesting is read as the shape the chapters were written in.

    The navigation document puts 第一部分, two chapters and 后记 on one level - which is
    what the EPUB 3 book's own ``nav.xhtml`` does with 26 rows - so the panel draws a part and its
    chapters in one column (缺陷 26).  The chapters were written with the difference the
    nav drops: the part opening is ``<h1>``, the chapters inside it are ``<h2>``.
    """
    path = _flat_nav_book(
        build_epub,
        [
            ("第一部分", "text/part.xhtml"),
            ("第一章", "text/ch1.xhtml"),
            ("第二章", "text/ch2.xhtml"),
            ("后记", "text/end.xhtml"),
        ],
        {
            "text/part.xhtml": "<html><body><h1>第一部分</h1><p>正文。</p></body></html>",
            "text/ch1.xhtml": "<html><body><h2>第一章</h2><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><h2>第二章</h2><p>正文。</p></body></html>",
            "text/end.xhtml": "<html><body><h1>后记</h1><p>正文。</p></body></html>",
        },
    )
    book = EpubBook.open(path)
    assert [(entry.title, entry.level) for entry in book.toc] == [
        ("第一部分", 0),
        ("后记", 0),
    ]
    assert [(child.title, child.level) for child in book.toc[0].children] == [
        ("第一章", 1),
        ("第二章", 1),
    ]
    # A shape, not a sort: the rows still read in the order the book is read in.
    assert [(entry.title, section) for entry, section in _flatten_toc(book.toc, book)] == [
        ("第一部分", 0),
        ("第一章", 1),
        ("第二章", 2),
        ("后记", 3),
    ]


def test_a_book_written_entirely_in_h2_is_not_indented_as_a_whole(build_epub) -> None:
    """Only the *difference* between heading levels is a hierarchy; the tag itself is not.

    A book converted to ``<h2>`` throughout has a flat contents and flat text as far as
    the reader is concerned.  Indenting every row by one because its headings happen to be
    ``<h2>`` would say something the book never said, which is why the shallowest heading
    in the book is the top row.
    """
    path = _flat_nav_book(
        build_epub,
        [
            ("第一部分", "text/part.xhtml"),
            ("第一章", "text/ch1.xhtml"),
            ("第二章", "text/ch2.xhtml"),
            ("后记", "text/end.xhtml"),
        ],
        {
            "text/part.xhtml": "<html><body><h2>第一部分</h2><p>正文。</p></body></html>",
            "text/ch1.xhtml": "<html><body><h2>第一章</h2><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><h2>第二章</h2><p>正文。</p></body></html>",
            "text/end.xhtml": "<html><body><h2>后记</h2><p>正文。</p></body></html>",
        },
    )
    book = EpubBook.open(path)
    assert [(entry.title, entry.level, entry.children) for entry in book.toc] == [
        ("第一部分", 0, ()),
        ("第一章", 0, ()),
        ("第二章", 0, ()),
        ("后记", 0, ()),
    ]


def test_a_section_that_is_not_a_heading_never_becomes_a_parent(build_epub) -> None:
    """A row whose section declares nothing takes the place of the row that declares one.

    The middle document opens with a plain ``<p>`` - a converter's chapter title, which is
    how most of the omnibus is written.  Read as "one level deeper than the row above it" it would
    become the parent of 第二章, which did declare itself: the one thing the text can be
    said *not* to imply.  It takes the level of the next row that names one instead, which
    is the part it sits in.
    """
    path = _flat_nav_book(
        build_epub,
        [
            ("第一部分", "text/part.xhtml"),
            ("第一章", "text/ch1.xhtml"),
            ("第二章", "text/ch2.xhtml"),
            ("后记", "text/end.xhtml"),
        ],
        {
            "text/part.xhtml": "<html><body><h1>第一部分</h1><p>正文。</p></body></html>",
            "text/ch1.xhtml": "<html><body><p>第一章 起步</p><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><h2>第二章</h2><p>正文。</p></body></html>",
            "text/end.xhtml": "<html><body><h1>后记</h1><p>正文。</p></body></html>",
        },
    )
    book = EpubBook.open(path)
    assert [(entry.title, entry.level) for entry in book.toc] == [("第一部分", 0), ("后记", 0)]
    assert [(child.title, child.level) for child in book.toc[0].children] == [
        ("第一章", 1),
        ("第二章", 1),
    ]


def test_a_section_that_is_not_a_heading_trails_the_level_above_it(build_epub) -> None:
    """With nothing after it to take a level from, such a row keeps the one above.

    A book whose last section opens with prose: nobody declared where it sits, and the row
    before it is the last word on the subject - so it stays beside that row rather than
    being lifted to the top level, and it takes nothing under it either.
    """
    path = _flat_nav_book(
        build_epub,
        [
            ("第一部分", "text/part.xhtml"),
            ("第一章", "text/ch1.xhtml"),
            ("余論 收尾的話", "text/tail.xhtml"),
        ],
        {
            "text/part.xhtml": "<html><body><h1>第一部分</h1><p>正文。</p></body></html>",
            "text/ch1.xhtml": "<html><body><h2>第一章</h2><p>正文。</p></body></html>",
            "text/tail.xhtml": "<html><body><p>余論 收尾的話</p><p>正文。</p></body></html>",
        },
    )
    book = EpubBook.open(path)
    assert [(entry.title, entry.level) for entry in book.toc] == [("第一部分", 0)]
    assert [(child.title, child.level) for child in book.toc[0].children] == [
        ("第一章", 1),
        ("余論 收尾的話", 1),
    ]


def test_a_book_that_draws_its_own_hierarchy_is_not_re_read(build_epub) -> None:
    """Where the book said what its parts are, that answer stands, headings or not.

    The two trees disagree on purpose here: the NCX puts 第二章 inside 第一部分, while both
    documents write their titles as ``<h1>`` - which, read off the headings, would make them
    siblings.  The book's own division wins, because it is the one that was written down
    rather than inferred (FR-012 / ADR-020).
    """
    path = _ncx_book(
        build_epub,
        {
            "text/ch1.xhtml": "<html><body><h1>第一部分</h1><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><h1>第二章</h1><p>正文。</p></body></html>",
            "text/ch5.xhtml": "<html><body><h1>第二部分</h1><p>正文。</p></body></html>",
        },
        ncx=NESTED_NCX,
    )
    book = EpubBook.open(path)
    assert [(entry.title, entry.level) for entry in book.toc] == [
        ("第一部分", 0),
        ("第二部分", 0),
    ]
    assert [(child.title, child.level) for child in book.toc[0].children] == [("第二章", 1)]
    assert book.toc[1].children == ()


VOLUMES_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="v1"><navLabel><text>上卷 起步</text></navLabel>
      <content src="text/ch1.xhtml"/></navPoint>
    <navPoint id="v2"><navLabel><text>下卷 收官</text></navLabel>
      <content src="text/ch4.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def test_the_sections_a_flat_contents_leaves_out_join_the_row_above_them(build_epub) -> None:
    """A book that declares no shape gets one from the order its rows are read in.

    Two volume rows and three sections they do not mention: an added section belongs to
    the row it follows, up to where the next row begins - which is how the omnibus's 131
    chapters end up inside its three volumes (ADR-020).  The one row before the book's
    first name stays on the top level, because there is no row above it to belong to.
    """
    path = _ncx_book(
        build_epub,
        {
            "text/ch0.xhtml": "<html><body><p>免责声明</p><p>正文。</p></body></html>",
            "text/ch1.xhtml": "<html><body><p>上卷 起步</p><p>正文。</p></body></html>",
            "text/ch2.xhtml": "<html><body><p>第2节 加仓</p><p>正文。</p></body></html>",
            "text/ch3.xhtml": "<html><body><p>第3节 减仓</p><p>正文。</p></body></html>",
            "text/ch4.xhtml": "<html><body><p>下卷 收官</p><p>正文。</p></body></html>",
            "text/ch5.xhtml": "<html><body><p>第5节 收尾</p><p>正文。</p></body></html>",
        },
        ncx=VOLUMES_NCX,
    )
    book = EpubBook.open(path)
    assert [(entry.title, len(entry.children)) for entry in book.toc] == [
        ("免责声明", 0),
        ("上卷 起步", 2),
        ("下卷 收官", 1),
    ]
    assert [
        (entry.title, section, entry.level) for entry, section in _flatten_toc(book.toc, book)
    ] == [
        ("免责声明", 0, 0),
        ("上卷 起步", 1, 0),
        ("第2节 加仓", 2, 1),
        ("第3节 减仓", 3, 1),
        ("下卷 收官", 4, 0),
        ("第5节 收尾", 5, 1),
    ]


# ---------------------- the row a title picture is named by (FR-012 / FR-016 / ADR-021)

#: Two chapters, each a title picture in a document of its own followed by its text.
FRONTISPIECE_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/">
  <navMap>
    <navPoint id="n1"><navLabel><text>第一章 标题图</text></navLabel>
      <content src="text/ch1.xhtml"/></navPoint>
    <navPoint id="n2"><navLabel><text>第二章 收尾</text></navLabel>
      <content src="text/ch2.xhtml"/></navPoint>
  </navMap>
</ncx>
"""


def test_a_row_that_points_at_a_title_picture_leads_into_the_chapter(build_epub) -> None:
    """The row keeps the title the book gave it, and gains the chapter (FR-016).

    A converted book writes each chapter as a title picture and then the text, and
    points its contents at the picture.  The picture is not a section of its own - it
    is the page the chapter opens on - so the row lands on the chapter; and nothing
    changes in the panel, because the documents it takes along are exactly the ones
    that would have had no row at all (缺陷 27 / ADR-021).
    """
    path = _ncx_book(
        build_epub,
        {
            "text/ch1.xhtml": "<html><body><h1><img src='title.png'/></h1></body></html>",
            "text/ch1txt.xhtml": "<html><body><p>第一章的正文，一句话。</p></body></html>",
            "text/ch2.xhtml": "<html><body><p>第二章 收尾</p></body></html>",
        },
        ncx=FRONTISPIECE_NCX,
    )
    book = EpubBook.open(path)
    # Two rows for three documents: the chapter's own first line is a sentence, so it
    # neither had a row before nor has one now - the row count is the book's business.
    assert _contents(path) == [("第一章 标题图", 0), ("第二章 收尾", 1)]
    # And the row that the reader clicks leads to the chapter's text, not to the
    # picture alone: the text is in the same section, at the top of which the picture
    # is still the first thing shown.
    assert book.section_documents(0) == (0, 1)
    assert "第一章的正文，一句话。" in "".join(
        strip_word_joiners(block.text) for block in book.blocks(0)
    )

