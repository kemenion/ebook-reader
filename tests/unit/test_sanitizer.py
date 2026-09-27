"""XHTML normalisation: the highest-risk transform in the project.

Two invariants matter more than any specific mapping:

* **text conservation** - no character may silently disappear (R-09);
* **no style leakage** - the publisher's ``font-family`` must never reach the
  layout (FR-024), because the reference book asks for ``PingFang SC`` and
  ``FZFangSong-Z02``, neither of which exists on Linux.
"""

from __future__ import annotations

import pytest

from ebook_reader.domain.html.kinsoku import strip_word_joiners
from ebook_reader.domain.html.sanitizer import sanitize
from ebook_reader.domain.models import BlockAlign, BlockKind


def _text_of(blocks) -> str:
    return " ".join(" ".join(block.text.split()) for block in blocks)


def test_paragraphs_and_headings_are_separated() -> None:
    blocks = sanitize("<html><body><h1>标题</h1><p>第一段</p><p>第二段</p></body></html>")
    assert [block.kind for block in blocks] == [
        BlockKind.HEADING,
        BlockKind.PARAGRAPH,
        BlockKind.PARAGRAPH,
    ]
    assert blocks[0].level == 1
    assert blocks[1].text == "第一段"


def test_head_content_never_leaks_into_the_body() -> None:
    blocks = sanitize(
        "<html><head><title>书名</title><style>p{color:red}</style>"
        "<script>var x=1</script></head><body><p>正文</p></body></html>"
    )
    text = _text_of(blocks)
    assert "书名" not in text
    assert "color" not in text
    assert "var x" not in text
    assert text == "正文"


def test_publisher_font_family_is_not_reachable() -> None:
    blocks = sanitize(
        '<p style="font-size:16px;font-family:\'PingFang SC\'">中文</p>'
    )
    assert "PingFang" not in repr(blocks)
    assert "font-family" not in repr(blocks)
    assert blocks[0].text == "中文"


def test_alignment_survives_but_nothing_else_does() -> None:
    blocks = sanitize('<div style="display:block;text-align:center"><img src="i.jpg"/></div>')
    assert blocks[0].kind is BlockKind.IMAGE
    assert blocks[0].align is BlockAlign.CENTER
    assert blocks[0].image is not None
    assert blocks[0].image.src == "i.jpg"


def test_images_are_emitted_as_their_own_blocks() -> None:
    blocks = sanitize('<div><p>文字</p><p><img src="a.png" alt="图"/></p></div>')
    # The paragraph that holds only a figure becomes the figure: no empty
    # paragraph is left behind.
    assert [block.kind for block in blocks] == [BlockKind.PARAGRAPH, BlockKind.IMAGE]
    assert blocks[1].image is not None
    assert blocks[1].image.src == "a.png"
    assert blocks[1].image.alt == "图"


def test_inline_emphasis_is_preserved() -> None:
    blocks = sanitize("<p>普通<b>粗体<i>又斜</i></b>普通</p>")
    spans = blocks[0].spans
    assert spans[0].bold is False
    assert spans[1].bold is True and spans[1].italic is False
    assert spans[2].bold is True and spans[2].italic is True
    assert "".join(span.text for span in spans) == "普通粗体又斜普通"


def test_superscript_and_links_are_preserved() -> None:
    blocks = sanitize('<p>注<a href="#n1"><sup>1</sup></a></p>')
    spans = blocks[0].spans
    assert spans[1].link_href == "#n1"
    assert spans[1].superscript is True


def test_nested_divs_do_not_lose_text() -> None:
    blocks = sanitize("<div><div><div>深层文字</div></div></div>")
    assert "深层文字" in _text_of(blocks)


def test_list_items_are_recognised_with_their_ordering() -> None:
    blocks = sanitize("<ol><li>甲</li><li>乙</li></ol><ul><li>丙</li></ul>")
    assert [block.kind for block in blocks] == [BlockKind.LIST_ITEM] * 3
    assert [block.list_ordered for block in blocks] == [True, True, False]


def test_entities_and_nbsp_are_resolved() -> None:
    blocks = sanitize("<p>a&nbsp;b&amp;c&lt;d</p>")
    text = blocks[0].text
    assert "&amp;" not in text and "&lt;" not in text
    assert "b&c<d" in text


def test_broken_html_falls_back_instead_of_failing() -> None:
    """Not well-formed XML must still produce usable blocks (NFR-020)."""
    blocks = sanitize("<html><body><p>第一段<p>第二段<img src='x.jpg'><p>第三段")
    text = _text_of(blocks)
    assert "第一段" in text and "第二段" in text and "第三段" in text
    assert any(block.kind is BlockKind.IMAGE for block in blocks)


# ----------------------------------------------------------------------- anchors
#
# An anchor is how a contents row says *where* inside a document it leads (FR-014).
# It is not part of the text, so it can never change what is on the page - but it is
# the whole difference between a row that lands on the passage it names and one that
# lands on the top of the file, beside every other row that names the same file.


def test_an_anchor_on_a_wrapper_belongs_to_the_block_inside_it() -> None:
    """``<div id="chapter2">`` around paragraphs is a habit, not an edge case.

    The wrapper contributes no text of its own, so it produces no block - and an
    anchor that vanished with it took the reader to the top of the document.
    """
    blocks = sanitize('<div id="chapter2"><p>第一段</p><p>第二段</p></div>')
    assert blocks[0].anchor_ids == ("chapter2",)
    assert blocks[1].anchor_ids == ()


def test_an_empty_marker_hands_its_anchor_to_the_next_block() -> None:
    """``<p id="page_6"></p>`` and ``<a id="filepos12"></a>`` mark a place, not text."""
    assert sanitize('<p id="page_6"></p><p>正文</p>')[0].anchor_ids == ("page_6",)
    assert sanitize('<p><a id="filepos12"></a>正文</p>')[0].anchor_ids == ("filepos12",)


def test_anchors_on_a_wrapper_and_on_its_first_block_are_both_kept() -> None:
    blocks = sanitize('<div id="part1"><p id="page_6">正文</p></div>')
    assert blocks[0].anchor_ids == ("part1", "page_6")


def test_an_anchor_at_the_very_end_goes_to_the_last_block() -> None:
    """The end marker has no following block to inherit it; the text before it is it."""
    assert sanitize('<p>正文</p><p id="end"></p>')[-1].anchor_ids == ("end",)


def test_an_anchor_carries_into_the_image_block_it_wraps() -> None:
    blocks = sanitize('<div id="plate"><img src="i.jpg"/></div>')
    assert blocks[0].kind is BlockKind.IMAGE
    assert blocks[0].anchor_ids == ("plate",)
    # A wrapper and the image itself can each carry one; both are places in the text.
    both = sanitize('<div id="plate"><img id="fig1" src="i.jpg"/></div>')
    assert both[0].anchor_ids == ("plate", "fig1")


def test_anchors_survive_a_document_that_needed_the_tolerant_parser() -> None:
    """Malformed HTML used to lose every anchor, and with them every link into it."""
    blocks = sanitize('<html><body><div id="part1"><p>第一段<p id="page_6">第二段')
    ids = [anchor for block in blocks for anchor in block.anchor_ids]
    assert ids == ["part1", "page_6"]


def test_preformatted_keeps_whitespace() -> None:
    blocks = sanitize("<pre>a  b\n  c</pre>")
    assert blocks[0].kind is BlockKind.PREFORMATTED
    assert "a  b" in blocks[0].text


def test_empty_document_yields_no_blocks() -> None:
    assert sanitize("<html><body></body></html>") == ()
    assert sanitize("") == ()


def test_text_conservation_on_the_largest_real_section(kangpo, largest_section) -> None:
    """The guard against silently dropping content (R-09)."""
    from xml.etree import ElementTree as ET

    raw = kangpo.section_bytes(largest_section)
    blocks = kangpo.blocks(largest_section)

    root = ET.fromstring(raw)
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() in ("head", "style", "script", "title"):
            element.text = None
            for child in element:
                child.tail = None
    source = " ".join("".join(root.itertext()).split())
    model = " ".join(strip_word_joiners(_text_of(blocks)).split())

    assert len(model) > 20_000
    drift = abs(len(source) - len(model)) / len(source)
    assert drift < 0.01, f"text drift {drift:.2%} ({len(source)} -> {len(model)})"


def test_real_section_produces_the_expected_structure(kangpo, largest_section) -> None:
    blocks = kangpo.blocks(largest_section)
    kinds = [block.kind for block in blocks]
    assert kinds.count(BlockKind.PARAGRAPH) > 50
    assert all(block.spans or block.image for block in blocks)


def test_word_joiners_are_present_in_real_chinese_text(kangpo) -> None:
    joined = sum(block.text.count("\u2060") for index in range(4) for block in kangpo.blocks(index))
    assert joined > 0


# ------------------------------------------- a document that rebases itself (FR-006)


def test_a_base_href_moves_every_relative_href_in_the_document() -> None:
    """``<base href>`` is legal, rare, and rebases images as well as links.

    A document that carries it keeps its files somewhere other than beside itself, so a
    reader that ignores the tag resolves every one of them to a place nothing lives.
    """
    blocks = sanitize(
        '<html><head><base href="../assets/"/></head><body><img src="pic.png"/></body></html>',
        base_dir="EPUB/text",
    )
    assert blocks[0].image is not None
    assert blocks[0].image.src == "EPUB/assets/pic.png"


def test_a_base_href_naming_a_file_rebases_on_that_files_directory() -> None:
    """The browser rule: a base is a URL, and only a trailing ``/`` makes it a directory."""
    blocks = sanitize(
        '<html><head><base href="assets/index.html"/></head><body><img src="pic.png"/></body></html>',
        base_dir="EPUB/text",
    )
    assert blocks[0].image.src == "EPUB/text/assets/pic.png"


def test_a_base_href_is_read_even_when_the_document_is_read_as_html() -> None:
    """Otherwise a broken document would also lose the answer to where its files are."""
    blocks = sanitize(
        "<html><head><base href='assets/'></head><body><p>正文<b>粗</p>"
        "<img src='pic.png'></body></html>",
        base_dir="EPUB/text",
    )
    images = [block for block in blocks if block.is_image]
    assert [block.image.src for block in images] == ["EPUB/text/assets/pic.png"]


def test_a_document_without_a_base_still_resolves_beside_itself() -> None:
    blocks = sanitize('<html><body><img src="pic.png"/></body></html>', base_dir="EPUB/text")
    assert blocks[0].image.src == "EPUB/text/pic.png"


def test_base_is_a_head_tag_and_never_becomes_content() -> None:
    blocks = sanitize(
        '<html><head><base href="assets/"/><title>书名</title></head><body><p>正文</p></body></html>'
    )
    assert [block.text for block in blocks] == ["正文"]


def test_void_head_tags_written_without_a_slash_do_not_swallow_the_body() -> None:
    """``<meta charset>`` unclosed is the commonest way a chapter stops being XML.

    The head tags that never close have to stay uncounted, or the tolerant fallback
    would drop the body of exactly the documents it exists to rescue (R-09).
    """
    blocks = sanitize(
        "<html><head><meta charset='utf-8'><link rel='stylesheet' href='a.css'></head>"
        "<body><p>正文</p><p>第二段<b>粗体</p></body></html>"
    )
    assert [block.text for block in blocks] == ["正文", "第二段粗体"]
    assert blocks[1].spans[-1].bold is True


def test_a_chapter_written_in_a_legacy_encoding_is_read_in_that_encoding() -> None:
    """Older Chinese books declare ``gbk``, and expat refuses the name it is given.

    ``ValueError: multi-byte encodings are not supported`` is not a ``ParseError``, so it
    used to leave ``open()`` as a traceback - for a chapter whose own declaration says
    how to read it.  Decoded as UTF-8 the text is the wrong Chinese, which is the failure
    that looks like a font problem and is not one.
    """
    raw = (
        '<?xml version="1.0" encoding="gbk"?>\n'
        "<html><body><p>中文正文</p></body></html>"
    ).encode("gbk")
    blocks = sanitize(raw, base_dir="EPUB/text")
    assert _text_of(blocks) == "中文正文"


