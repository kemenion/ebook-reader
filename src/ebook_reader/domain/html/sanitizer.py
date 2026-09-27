"""Turn dirty publisher XHTML into our own explicit block model (ADR-002).

The reference book is hostile input: 4044 ``<b>``, 2303 ``<span>`` and 554
``<div>`` wrappers around plain paragraphs, 17 variants of inline style naming
fonts that do not exist on this machine (``PingFang SC``, ``FZFangSong-Z02``) and
images wrapped in ``<div style="display:block;text-align:center">`` shells.

Nothing here is delegated to Qt's HTML parser.  ``setHtml`` would silently keep
the publisher's ``font-family`` and only understands a small HTML4 subset, so the
document is rebuilt from scratch out of ``Block`` objects whose styling is fully
under our control (FR-020 .. FR-027).

Two invariants are enforced and unit tested:

* **Text conservation** - the concatenated block text differs from the source
  text only by injected word joiners and collapsed whitespace (R-09).
* **No font leakage** - ``font-family`` declarations are read by nobody.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Callable, Iterable, Sequence
from xml.etree import ElementTree as ET

from ..models import Block, BlockAlign, BlockKind, ImageRef, Span
from .kinsoku import protect_spans

__all__ = ["decode_text", "sanitize", "SizeProbe"]

SizeProbe = Callable[[str], "tuple[int, int] | None"]

# Elements whose content is never rendered.
_SKIP_TAGS = frozenset(
    {"head", "title", "meta", "link", "style", "script", "base", "template", "noscript",
     "rt", "rp"}  # ruby annotations are out of scope for v1
)

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}

_BLANK_TAGS = frozenset({"br", "hr", "img", "image", "wbr", "col", "input", "source"})

_WHITESPACE_RE = re.compile(r"[ \t\r\n\f\v\u00a0\u2000-\u200a\u2028\u2029]+")
_ALIGN_RE = re.compile(r"text-align\s*:\s*(left|center|right|justify)", re.IGNORECASE)
_ALIGN_ATTR = {
    "left": BlockAlign.LEFT,
    "center": BlockAlign.CENTER,
    "right": BlockAlign.RIGHT,
    "justify": BlockAlign.INHERIT,
}

_XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
_IMAGE_MEDIA_TYPE_HINT = "image"


@dataclass(frozen=True, slots=True)
class _Inline:
    """The character style active at a point inside a block."""

    bold: bool = False
    italic: bool = False
    superscript: bool = False
    subscript: bool = False
    link_href: str | None = None


PLAIN = _Inline()


class _BlockBuilder:
    """Accumulates inline runs until a block boundary is reached."""

    __slots__ = ("kind", "level", "align", "list_ordered", "anchor_ids", "_spans", "_texts")

    def __init__(
        self,
        kind: BlockKind,
        *,
        level: int = 0,
        align: BlockAlign = BlockAlign.INHERIT,
        list_ordered: bool = False,
        anchor_ids: Iterable[str] = (),
    ) -> None:
        self.kind = kind
        self.level = level
        self.align = align
        self.list_ordered = list_ordered
        self.anchor_ids = list(anchor_ids)
        self._spans: list[Span] = []
        self._texts: list[str] = []

    @property
    def is_empty(self) -> bool:
        return not self._spans

    def add_text(self, text: str, inline: _Inline, *, collapse: bool) -> None:
        if not text:
            return
        if collapse:
            text = _WHITESPACE_RE.sub(" ", text)
            if not text.strip() and not self._spans:
                return  # drop leading whitespace between block elements
        if text:
            self._spans.append(Span(text=text, **_inline_kwargs(inline)))
            self._texts.append(text)

    def mark(self) -> int:
        """Index of the next span, used by the tolerant parser to trim runs."""
        return len(self._spans)

    def trim_from(self, index: int, *, strip_trailing: bool = False) -> None:
        del self._spans[index:]
        del self._texts[index:]
        if strip_trailing and self._spans:
            trimmed = self._spans[-1].text.rstrip()
            if trimmed != self._spans[-1].text:
                self._spans[-1] = replace(self._spans[-1], text=trimmed)

    def build(self, *, protect: bool) -> Block | None:
        if not self._spans:
            return None
        texts = self._texts
        # Trim outer whitespace once, on the raw text, so that WJ insertion sees
        # the same string that survives.
        if texts:
            texts = list(texts)
            texts[0] = texts[0].lstrip()
            texts[-1] = texts[-1].rstrip()
        if protect:
            texts = protect_spans(texts)
        spans = tuple(
            replace(span, text=texts[index])
            for index, span in enumerate(self._spans)
            if texts[index]
        )
        if not spans:
            return None
        return Block(
            kind=self.kind,
            spans=spans,
            level=self.level,
            align=self.align,
            anchor_ids=tuple(self.anchor_ids),
            list_ordered=self.list_ordered,
        )


def _inline_kwargs(inline: _Inline) -> dict[str, object]:
    return {
        "bold": inline.bold,
        "italic": inline.italic,
        "superscript": inline.superscript,
        "subscript": inline.subscript,
        "link_href": inline.link_href,
    }


def _local_name(tag: object) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _align_from(element: ET.Element) -> BlockAlign:
    """Read alignment, deliberately ignoring every other declaration (FR-024)."""
    return _align_from_declarations(element.get("style") or "", element.get("align") or "")


def _align_from_declarations(style: str, align_attribute: str) -> BlockAlign:
    """Extract alignment from inline CSS or the legacy ``align`` attribute."""
    match = _ALIGN_RE.search(style)
    if match:
        return _ALIGN_ATTR.get(match.group(1).lower(), BlockAlign.INHERIT)
    return _ALIGN_ATTR.get(align_attribute.lower(), BlockAlign.INHERIT)


def _inline_from_attributes(tag: str, attributes: dict[str, str], parent: _Inline) -> _Inline:
    """Tag-and-attribute variant of :func:`_inline_from` for the tolerant path."""
    bold = parent.bold or tag in ("b", "strong", "th")
    italic = parent.italic or tag in ("i", "em", "cite", "var", "dfn")
    superscript = parent.superscript or tag == "sup"
    subscript = parent.subscript or tag == "sub"
    link = parent.link_href

    if tag == "a":
        href = attributes.get("href") or ""
        if href:
            link = href
    elif tag == "font":
        weight = attributes.get("weight", "").lower()
        if "bold" in weight or weight in ("600", "700", "800", "900"):
            bold = True

    if (
        bold == parent.bold
        and italic == parent.italic
        and superscript == parent.superscript
        and subscript == parent.subscript
        and link == parent.link_href
    ):
        return parent
    return _Inline(bold, italic, superscript, subscript, link)


def sanitize(
    raw: bytes | str,
    *,
    base_dir: str = "",
    probe: SizeProbe | None = None,
    protect: bool = True,
) -> tuple[Block, ...]:
    """Convert one XHTML document into our block model.

    *base_dir* is the archive directory of the document, used to resolve relative
    ``src`` attributes; a document that declares ``<base href>`` rebases itself
    against it first, exactly as a browser would.  *probe* reports intrinsic image
    sizes (see :mod:`ebook_reader.domain.epub.images`); when omitted, image blocks
    carry width and height ``0``.  *protect* enables CJK line-break protection.

    Malformed XML falls back to a tolerant HTML parse so that a single bad
    document cannot make a whole book unreadable (NFR-020).
    """
    if isinstance(raw, str):
        text = raw
        parsed: ET.Element | None = None
    else:
        text = ""
        try:
            parsed = ET.fromstring(raw)
        except (ET.ParseError, ValueError):
            # ``ValueError`` is a declared encoding expat does not implement (``gbk``,
            # ``big5``, …): the document is fine and has to be decoded first, which is
            # what the tolerant path below does.  Letting it through would be a
            # traceback for a chapter a browser shows in full.
            parsed = None
        if parsed is None:
            text = _decode(raw)

    if parsed is not None:
        return _TreeWalker(_declared_base(parsed, base_dir), probe, protect).run(parsed)

    tolerant = _TolerantParser(_declared_base_in_text(text, base_dir), probe, protect)
    tolerant.feed(text)
    tolerant.close()
    return tolerant.finish()


_BASE_HREF_RE = re.compile(r"""<base\b[^>]*?\bhref\s*=\s*["']([^"']*)["']""", re.IGNORECASE)


def _declared_base(root: ET.Element, base_dir: str) -> str:
    """The directory *root* declares for its own relative hrefs (FR-006).

    ``<base href>`` is rare, legal, and ignored by everything that reads a document as
    a string: a book that uses it puts its images and stylesheets somewhere other than
    beside the chapter, and a reader that does not look at the tag resolves every one of
    them to the wrong place.  A base naming a file rather than a directory
    (``../text/ch1.xhtml``) rebases against that file's directory, which is what a
    browser does with it.
    """
    for element in root.iter():
        if _local_name(element.tag) == "base":
            return _rebase(base_dir, element.get("href") or "")
    return base_dir


def _declared_base_in_text(text: str, base_dir: str) -> str:
    """The same rule for a document that had to be read as HTML."""
    match = _BASE_HREF_RE.search(text)
    return _rebase(base_dir, match.group(1)) if match else base_dir


def _rebase(base_dir: str, href: str) -> str:
    """Rebase *base_dir* on one ``<base href>`` value."""
    if not href:
        return base_dir
    from ..epub.paths import join_href

    resolved = join_href(base_dir, href)
    if href.endswith("/") or not resolved:
        return resolved
    return resolved.rsplit("/", 1)[0] if "/" in resolved else ""



def _attr(element: ET.Element, name: str) -> str:
    value = element.get(name)
    if value is not None:
        return value
    return element.get(_XLINK_HREF) or "" if name in ("href", "src") else ""


#: Block-level elements.  ``<div>`` counts as one because 554 of them wrap real
#: content in the reference book (I-5).
_BLOCK_TAGS = frozenset(
    {
        "p", "div", "section", "article", "main", "header", "footer", "nav",
        "figure", "figcaption", "body", "html", "td", "th", "caption", "dd", "dt",
        "center", "address", "form", "fieldset", "details", "summary",
    }
)

_QUOTE_TAGS = frozenset({"blockquote", "aside"})
_LIST_CONTAINER_TAGS = frozenset({"ol", "ul", "menu", "dl"})


class _Stack:
    """Context manager pushing a value for the duration of a block."""

    __slots__ = ("_stack", "_value")

    def __init__(self, stack: list, value: object) -> None:
        self._stack = stack
        self._value = value

    def __enter__(self) -> None:
        self._stack.append(self._value)

    def __exit__(self, *exc_info: object) -> None:
        self._stack.pop()


class _TreeWalker:
    """Depth-first conversion of an XML element tree into blocks."""

    def __init__(self, base_dir: str, probe: SizeProbe | None, protect: bool) -> None:
        self.base_dir = base_dir
        self.probe = probe
        self.protect = protect
        self.blocks: list[Block] = []
        self.current: _BlockBuilder | None = None
        self._aligns: list[BlockAlign] = [BlockAlign.INHERIT]
        self._lists: list[bool] = []
        #: Anchors seen on elements that produced no block of their own - an ``id`` on a
        #: ``<div>`` whose whole content is other blocks, on an empty ``<p>`` used as a
        #: marker, on an empty ``<a>``.  They still name a place in this document, and
        #: that place is the next block, so they are carried over to it (FR-014).
        self._orphans: list[str] = []

    # ------------------------------------------------------------ block output

    def run(self, root: ET.Element) -> tuple[Block, ...]:
        self._process(root, PLAIN)
        self._finish()
        self._attach_trailing_anchors()
        return tuple(self.blocks)

    def _current_align(self) -> BlockAlign:
        return self._aligns[-1]

    def _take_orphans(self) -> tuple[str, ...]:
        """Anchors left over by an element that produced nothing, for the next block."""
        orphans = tuple(self._orphans)
        self._orphans.clear()
        return orphans

    def _attach_trailing_anchors(self) -> None:
        """Give anchors that never met a following block to the last block there is.

        A marker at the very end of a document - ``<p id="filepos9999"/>`` - points at
        the end of what comes before it, and there is nothing after it to inherit it.
        """
        if not self._orphans or not self.blocks:
            return
        last = self.blocks[-1]
        self.blocks[-1] = replace(last, anchor_ids=tuple(self._orphans) + last.anchor_ids)
        self._orphans.clear()

    def _start(self, kind: BlockKind, **kwargs: object) -> _BlockBuilder:
        self._finish()
        orphans = self._take_orphans()
        if orphans:
            kwargs["anchor_ids"] = orphans + tuple(kwargs.get("anchor_ids") or ())
        builder = _BlockBuilder(kind, **kwargs)  # type: ignore[arg-type]
        if builder.align is BlockAlign.INHERIT:
            builder.align = self._current_align()
        self.current = builder
        return builder

    def _builder(self) -> _BlockBuilder:
        """Builder for inline content, created on demand as a paragraph."""
        if self.current is None:
            self.current = _BlockBuilder(
                BlockKind.PARAGRAPH,
                align=self._current_align(),
                anchor_ids=self._take_orphans(),
            )
        return self.current

    def _finish(self) -> None:
        if self.current is None:
            return
        builder = self.current
        block = builder.build(protect=self.protect)
        self.current = None
        if block is None:
            # Nothing to attach the anchors to *here*; the next block is where this place
            # is.  Dropping them instead is what made every link to them land at the top
            # of the file, beside every other link to the same file.
            self._orphans.extend(builder.anchor_ids)
            return
        self.blocks.append(block)

    # --------------------------------------------------------------- traversal

    def _emit_image(self, element: ET.Element) -> None:
        """Emit a block-level image, closing any text block first.

        The reference book wraps figures in ``<div style="display:block;
        text-align:center">`` shells, so the enclosing alignment is inherited
        through ``self._aligns`` (FR-025 / FR-039).
        """
        source = element.get("src") or element.get(_XLINK_HREF) or element.get("href")
        self._finish()
        if not source:
            return
        from ..epub.paths import join_href

        resolved = join_href(self.base_dir, source)
        size = self.probe(resolved) if self.probe is not None else None
        width, height = size if size else (0, 0)
        self.blocks.append(
            Block(
                kind=BlockKind.IMAGE,
                image=ImageRef(
                    src=resolved,
                    width=width,
                    height=height,
                    alt=element.get("alt") or "",
                ),
                align=self._current_align(),
                anchor_ids=self._take_orphans() + _anchor_ids(element),
            )
        )

    def _process(self, element: ET.Element, inline: _Inline) -> None:
        tag = _local_name(element.tag)
        if tag in _SKIP_TAGS:
            return

        if tag in ("img", "image"):
            self._emit_image(element)
            return
        if tag == "hr":
            self._start(BlockKind.RULE)
            self._finish()
            return
        if tag == "br":
            self._builder().add_text(" ", inline, collapse=True)
            return

        if tag in _LIST_CONTAINER_TAGS:
            self._lists.append(tag == "ol")
            with _Stack(self._aligns, _align_from(element)):
                self._add_content(element, inline, collapse=True)
            self._lists.pop()
            return

        if tag in _HEADING_TAGS:
            self._block_container(
                element,
                BlockKind.HEADING,
                inline,
                level=_HEADING_TAGS[tag],
                collapse=True,
            )
            return

        if tag in _QUOTE_TAGS:
            self._block_container(element, BlockKind.QUOTE, inline, collapse=True)
            return

        if tag == "pre":
            self._block_container(element, BlockKind.PREFORMATTED, inline, collapse=False)
            return

        if tag == "li":
            self._block_container(
                element,
                BlockKind.LIST_ITEM,
                inline,
                level=max(0, len(self._lists) - 1),
                list_ordered=bool(self._lists and self._lists[-1]),
                collapse=True,
            )
            return

        if tag in _BLOCK_TAGS:
            self._block_container(element, BlockKind.PARAGRAPH, inline, collapse=True)
            return

        # Inline element: widen the character style and stay inside the block.
        inner = _inline_from(element, inline)
        anchor = element.get("id")
        if anchor:
            self._builder().anchor_ids.append(anchor)
        if element.text:
            self._builder().add_text(element.text, inner, collapse=True)
        for child in element:
            self._process(child, inner)
            if child.tail:
                self._builder().add_text(child.tail, inner, collapse=True)

    def _block_container(
        self,
        element: ET.Element,
        kind: BlockKind,
        inline: _Inline,
        *,
        collapse: bool,
        **extra: object,
    ) -> None:
        align = _align_from(element)
        with _Stack(self._aligns, align):
            self._start(kind, align=align, anchor_ids=_anchor_ids(element), **extra)
            self._add_content(element, inline, collapse=collapse)
            self._finish()

    def _add_content(self, element: ET.Element, inline: _Inline, *, collapse: bool) -> None:
        if element.text:
            self._builder().add_text(element.text, inline, collapse=collapse)
        for child in element:
            self._process(child, inline)
            if child.tail:
                self._builder().add_text(child.tail, inline, collapse=collapse)


def _anchor_ids(element: ET.Element) -> tuple[str, ...]:
    anchor = element.get("id")
    return (anchor,) if anchor else ()


def _anchor_from(attributes: dict[str, str]) -> tuple[str, ...]:
    """The anchor id of a tag the HTML parser handed over as attributes.

    The tolerant path never read ids at all, so a document it had to parse for being
    malformed lost every one of its anchors and, with them, every contents row that
    points inside it (FR-008 / FR-014).
    """
    anchor = attributes.get("id") or ""
    return (anchor,) if anchor else ()


def _inline_from(element: ET.Element, parent: _Inline) -> _Inline:
    """Derive the inline style contributed by *element*.

    Only emphasis, vertical position and link targets are honoured; colour and
    font declarations are discarded on purpose (FR-022 / FR-024).
    """
    tag = _local_name(element.tag)
    bold = parent.bold or tag in ("b", "strong", "th")
    italic = parent.italic or tag in ("i", "em", "cite", "var", "dfn")
    superscript = parent.superscript or tag == "sup"
    subscript = parent.subscript or tag == "sub"
    link = parent.link_href

    if tag == "a":
        href = element.get(_XLINK_HREF) or element.get("href") or ""
        if href:
            link = href
    elif tag == "font":
        weight = (element.get("weight") or "").lower()
        if "bold" in weight or weight in ("600", "700", "800", "900"):
            bold = True

    if (
        bold == parent.bold
        and italic == parent.italic
        and superscript == parent.superscript
        and subscript == parent.subscript
        and link == parent.link_href
    ):
        return parent
    return _Inline(
        bold=bold,
        italic=italic,
        superscript=superscript,
        subscript=subscript,
        link_href=link,
    )


_VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "image", "input",
     "link", "meta", "param", "source", "track", "wbr"}
)
_ENCODING_RE = re.compile(rb"""encoding\s*=\s*["']([\w.-]+)["']""", re.IGNORECASE)


def _decode(raw: bytes) -> str:
    match = _ENCODING_RE.search(raw[:256])
    encoding = match.group(1).decode("ascii", "replace") if match else "utf-8"
    try:
        return raw.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")


#: Public alias: a document that is not well-formed XML has to be decoded before the
#: HTML parser can look at it, and the navigation parsers need the same rule the
#: sanitiser uses rather than a second one that could disagree with it.
decode_text = _decode


class _TolerantParser(HTMLParser):
    """Fallback for documents that are not well-formed XML (FR-008 / NFR-020).

    It produces the same block model as the XML path, with weaker structural
    guarantees: tags are balanced by a stack rather than by the parser, and an
    unclosed paragraph simply ends at the next block boundary.
    """

    def __init__(self, base_dir: str, probe: SizeProbe | None, protect: bool) -> None:
        super().__init__(convert_charrefs=True)
        self.base_dir = base_dir
        self.probe = probe
        self.protect = protect
        self.blocks: list[Block] = []
        self.current: _BlockBuilder | None = None
        self.inline = PLAIN
        self._inline_stack: list[tuple[str, _Inline]] = []
        self._aligns: list[BlockAlign] = [BlockAlign.INHERIT]
        self._lists: list[bool] = []
        self._skip = 0
        self._orphans: list[str] = []          # see _TreeWalker._orphans

    # The block plumbing deliberately mirrors _TreeWalker: same output model.

    def _take_orphans(self) -> tuple[str, ...]:
        orphans = tuple(self._orphans)
        self._orphans.clear()
        return orphans

    def _attach_trailing_anchors(self) -> None:
        if not self._orphans or not self.blocks:
            return
        last = self.blocks[-1]
        self.blocks[-1] = replace(last, anchor_ids=tuple(self._orphans) + last.anchor_ids)
        self._orphans.clear()

    def _finish(self) -> None:
        if self.current is None:
            return
        builder = self.current
        block = builder.build(protect=self.protect)
        self.current = None
        if block is None:
            self._orphans.extend(builder.anchor_ids)
            return
        self.blocks.append(block)

    def _builder(self) -> _BlockBuilder:
        if self.current is None:
            self.current = _BlockBuilder(
                BlockKind.PARAGRAPH,
                align=self._aligns[-1],
                anchor_ids=self._take_orphans(),
            )
        return self.current

    def _start(self, kind: BlockKind, **kwargs: object) -> None:
        self._finish()
        orphans = self._take_orphans()
        if orphans:
            kwargs["anchor_ids"] = orphans + tuple(kwargs.get("anchor_ids") or ())
        builder = _BlockBuilder(kind, **kwargs)  # type: ignore[arg-type]
        if builder.align is BlockAlign.INHERIT:
            builder.align = self._aligns[-1]
        self.current = builder

    # ------------------------------------------------------------ tag handling

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            # A void element - ``<base>``, ``<link>``, ``<meta>`` - has no end tag to
            # count down, and a document that has to be read as HTML writes them without
            # the slash.  Counting one anyway would leave the counter positive at
            # ``</head>`` and swallow the whole body: the fallback would return nothing
            # for a chapter that a browser shows in full.
            if tag not in _VOID_TAGS:
                self._skip += 1
            return
        if self._skip:
            return
        attributes = {name: (value or "") for name, value in attrs}
        align = _align_from_declarations(attributes.get("style", ""), attributes.get("align", ""))
        anchors = _anchor_from(attributes)

        if tag in ("img", "image"):
            self._emit_image(attributes)
            return
        if tag == "hr":
            self._start(BlockKind.RULE, anchor_ids=anchors)
            self._finish()
            return
        if tag == "br":
            self._builder().add_text(" ", self.inline, collapse=True)
            return

        if tag in _LIST_CONTAINER_TAGS:
            self._lists.append(tag == "ol")
            self._aligns.append(align)
            return
        if tag in _HEADING_TAGS:
            self._aligns.append(align)
            self._start(
                BlockKind.HEADING,
                level=_HEADING_TAGS[tag],
                align=align,
                anchor_ids=anchors,
            )
            return
        if tag in _QUOTE_TAGS:
            self._aligns.append(align)
            self._start(BlockKind.QUOTE, align=align, anchor_ids=anchors)
            return
        if tag == "pre":
            self._aligns.append(align)
            self._start(BlockKind.PREFORMATTED, align=align, anchor_ids=anchors)
            return
        if tag == "li":
            self._aligns.append(align)
            self._start(
                BlockKind.LIST_ITEM,
                level=max(0, len(self._lists) - 1),
                list_ordered=bool(self._lists and self._lists[-1]),
                align=align,
                anchor_ids=anchors,
            )
            return
        if tag in _BLOCK_TAGS:
            self._aligns.append(align)
            self._start(BlockKind.PARAGRAPH, align=align, anchor_ids=anchors)
            return

        self._inline_stack.append((tag, self.inline))
        self.inline = _inline_from_attributes(tag, attributes, self.inline)
        if anchors:
            # The same as the XML path: an id on an inline element names the place in
            # the paragraph the text around it lands in.
            self._builder().anchor_ids.append(anchors[0])

    def _emit_image(self, attributes: dict[str, str]) -> None:
        self._finish()
        source = attributes.get("src") or attributes.get("href") or ""
        if not source:
            return
        from ..epub.paths import join_href

        resolved = join_href(self.base_dir, source)
        size = self.probe(resolved) if self.probe is not None else None
        width, height = size if size else (0, 0)
        self.blocks.append(
            Block(
                kind=BlockKind.IMAGE,
                image=ImageRef(resolved, width, height, attributes.get("alt", "")),
                align=self._aligns[-1],
                anchor_ids=self._take_orphans() + _anchor_from(attributes),
            )
        )

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            if tag not in _VOID_TAGS and self._skip:
                self._skip -= 1
            return
        if self._skip or tag in _VOID_TAGS:
            return

        if (
            tag in _LIST_CONTAINER_TAGS
            or tag in _HEADING_TAGS
            or tag in _QUOTE_TAGS
            or tag in _BLOCK_TAGS
            or tag in ("pre", "li")
        ):
            self._finish()
            if tag in _LIST_CONTAINER_TAGS and self._lists:
                self._lists.pop()
            if len(self._aligns) > 1:
                self._aligns.pop()
            return

        for index in range(len(self._inline_stack) - 1, -1, -1):
            if self._inline_stack[index][0] == tag:
                self.inline = self._inline_stack[index][1] if index else PLAIN
                del self._inline_stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        collapse = self.current is None or self.current.kind is not BlockKind.PREFORMATTED
        self._builder().add_text(data, self.inline, collapse=collapse)

    def finish(self) -> tuple[Block, ...]:
        self._finish()
        self._attach_trailing_anchors()
        return tuple(self.blocks)




