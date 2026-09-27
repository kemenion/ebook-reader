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
