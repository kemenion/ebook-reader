"""Build the navigation tree from EPUB3 ``nav.xhtml`` or EPUB2 ``toc.ncx``.

The reference books exercise both paths: 人生财富靠康波 ships a nav document,
币安人生 only an NCX.  Both parsers walk the XML by local name so that stray or
missing namespaces do not break navigation (FR-010 / FR-011).

Three shapes of publisher sloppiness are absorbed rather than reported, because a
navigation document is a convenience and a book without one is still a book:

* a document that is not well-formed XML at all - nav files with a raw ``&`` or an
  unclosed ``<li>`` do exist, and expat then refuses the whole file; those rows are
  recovered with the HTML parser, which is what a browser would do with the same
  bytes;
* a list that is not an ``<ol>`` - the EPUB 3 specification demands ``ol``, the
  EPUB 2 files converted by hand often carry ``ul``;
* no list at all - the rows are then read in document order as a flat list.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from ..models import Landmark, TocEntry
from .paths import join_href, split_fragment

__all__ = ["parse_landmarks", "parse_nav", "parse_ncx"]

_OPS_NS = "http://www.idpf.org/2007/ops"

#: What :func:`xml.etree.ElementTree.fromstring` raises on a document it cannot read.
#: ``ParseError`` is malformed XML; ``ValueError`` is a *declared* encoding that expat
#: does not implement - "multi-byte encodings are not supported" for ``gbk``, ``big5``
#: and the other Chinese and Japanese code pages that older EPUB 2 files declare as a
#: matter of course.  Both mean "read this the other way"; neither means "the book is
#: broken", and letting the second one through would be a traceback out of ``open()``
#: for a book whose every page a browser renders.
_UNREADABLE_XML = (ET.ParseError, ValueError)


def _resolve_target(base_dir: str, raw_href: str) -> tuple[str, str]:
    """Split an href into its spine path and its in-document anchor (FR-010 / FR-014).

    :func:`~ebook_reader.domain.epub.paths.join_href` drops the fragment on purpose -
    it is not part of the ZIP entry name - but it is not decoration either.  The
    fragment is the only part that says *where* in the document the row leads, and
    without it every anchored row lands at the top of its file, right beside the row
    above it.

    The anchor is percent-decoded here because that is the form the sanitizer reads
    out of the HTML: ``#%E4%B8%AD`` and ``#中`` name the same anchor.
    """
    if not raw_href:
        return "", ""
    _, fragment = split_fragment(unquote(raw_href))
    return join_href(base_dir, raw_href), fragment


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text_of(element: ET.Element) -> str:
    return " ".join("".join(element.itertext()).split())


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local_name(child.tag) == name]


def parse_nav(raw: bytes, base_dir: str) -> tuple[TocEntry, ...]:
    """Parse an EPUB3 navigation document into a tree of :class:`TocEntry`."""
    try:
        root = ET.fromstring(raw)
    except _UNREADABLE_XML:
        reader = _TolerantNavReader()
        reader.feed(_decode(raw))
        return _rows_to_entries(reader.rows, base_dir)

    nav = _find_nav(root, "toc", fallback_to_first=True)
    if nav is None:
        return ()

    list_element = _first_list(nav)
    if list_element is None:
        return _flat_links(nav, base_dir)
    return _walk_nav_list(list_element, base_dir, level=0)


def _first_list(nav: ET.Element) -> ET.Element | None:
    """The list holding the rows: a direct ``ol``/``ul``, or the first nested one."""
    for name in ("ol", "ul"):
        children = _children(nav, name)
        if children:
            return children[0]
    return next(
        (element for element in nav.iter() if _local_name(element.tag) in ("ol", "ul")),
        None,
    )


def _flat_links(nav: ET.Element, base_dir: str) -> tuple[TocEntry, ...]:
    """Read a nav document that never nests its rows in a list.

    The specification wants ``<ol>``; a document that puts the links straight into
    ``<nav>`` is still a legible contents, and every row is a first-level one.
    """
    rows = [
        (0, _text_of(element), element.get("href") or "")
        for element in nav.iter()
        if _local_name(element.tag) == "a"
    ]
    return _rows_to_entries(rows, base_dir)


def _find_nav(root: ET.Element, type_name: str, *, fallback_to_first: bool) -> ET.Element | None:
    """Find the ``<nav>`` that declares *type_name*.

    *fallback_to_first* decides what to do when no ``<nav>`` declares it.  For the
    contents the answer is the first ``<nav>``: a nav document whose only navigation
    carries no ``epub:type`` is still the contents, and refusing it would leave the
    reader with nothing where the book has a map.  For landmarks the answer is
    nothing, because guessing there would elect the contents document as the place
    the text begins.
    """
    fallback: ET.Element | None = None
    for element in root.iter():
        if _local_name(element.tag) != "nav":
            continue
        if fallback is None:
            fallback = element
        nav_type = element.get(f"{{{_OPS_NS}}}type") or element.get("type") or ""
        if type_name in nav_type.split():
            return element
    return fallback if fallback_to_first else None


def _walk_nav_list(ol: ET.Element, base_dir: str, level: int) -> tuple[TocEntry, ...]:
    entries: list[TocEntry] = []
    for li in _children(ol, "li"):
        anchor = next(
            (child for child in li if _local_name(child.tag) in ("a", "span")),
            None,
        )
        if anchor is None:
            continue
        title = _text_of(anchor)
        href, fragment = _resolve_target(base_dir, anchor.get("href") or "")
        nested = _children(li, "ol") or _children(li, "ul")
        children = _walk_nav_list(nested[0], base_dir, level + 1) if nested else ()
        if not title and not children:
            continue
        entries.append(
            TocEntry(title=title, href=href, level=level, fragment=fragment, children=children)
        )
    return tuple(entries)


def parse_ncx(raw: bytes, base_dir: str) -> tuple[TocEntry, ...]:
    """Parse an EPUB2 NCX document into a tree of :class:`TocEntry`."""
    try:
        root = ET.fromstring(raw)
    except _UNREADABLE_XML:
        reader = _TolerantNcxReader()
        reader.feed(_decode(raw))
        return _rows_to_entries(reader.rows, base_dir)

    nav_map = next(
        (element for element in root.iter() if _local_name(element.tag) == "navMap"),
        None,
    )
    if nav_map is None:
        return ()
    return _walk_nav_points(nav_map, base_dir, level=0)


def parse_landmarks(raw: bytes, base_dir: str) -> tuple[Landmark, ...]:
    """Parse the ``landmarks`` navigation: where the parts of the book begin (FR-009).

    Optional in the specification and absent from all three reference books, so the
    rows are only ever exercised by a book written in the tests.  The ``type`` is
    kept because it is the part with meaning: ``bodymatter`` names the document the
    text starts in, and a fallback layer that has to guess what is front matter is
    guessing about something the book may already have said.
    """
    try:
        root = ET.fromstring(raw)
    except _UNREADABLE_XML:
        return ()

    nav = _find_nav(root, "landmarks", fallback_to_first=False)
    if nav is None:
        return ()

    list_element = _first_list(nav)
    if list_element is None:
        return ()

    landmarks: list[Landmark] = []
    for li in _walk_items(list_element):
        anchor = next(
            (child for child in li if _local_name(child.tag) in ("a", "span")),
            None,
        )
        if anchor is None:
            continue
        href, fragment = _resolve_target(base_dir, anchor.get("href") or "")
        landmarks.append(
            Landmark(
                type=_landmark_type(anchor) or _landmark_type(li),
                title=_text_of(anchor),
                href=href,
                fragment=fragment,
            )
        )
    return tuple(landmarks)


def _landmark_type(element: ET.Element) -> str:
    value = element.get(f"{{{_OPS_NS}}}type") or element.get("type") or ""
    return value.strip()


def _walk_items(list_element: ET.Element) -> list[ET.Element]:
    """Every ``<li>`` under *list_element*, at any depth."""
    return [element for element in list_element.iter() if _local_name(element.tag) == "li"]


def _nest(rows: list[tuple[int, TocEntry]]) -> tuple[TocEntry, ...]:
    """Attach every row to the last row above it that is less deeply nested.

    The readers that produce flat rows - the tolerant one, and a nav document that
    nests nothing - do not know which row is whose child.  Indentation is the only
    thing that says so, and it says it the same way on paper.
    """
    cursor = 0

    def walk(level: int) -> list[TocEntry]:
        nonlocal cursor
        nodes: list[TocEntry] = []
        while cursor < len(rows) and rows[cursor][0] >= level:
            row_level, entry = rows[cursor]
            cursor += 1
            if cursor < len(rows) and rows[cursor][0] > row_level:
                entry = replace(entry, children=walk(row_level + 1))
            nodes.append(entry)
        return nodes

    return tuple(walk(0))


def _rows_to_entries(rows: list[tuple[int, str, str]], base_dir: str) -> tuple[TocEntry, ...]:
    """Turn flat ``(level, title, raw href)`` rows into a tree of entries."""
    resolved: list[tuple[int, TocEntry]] = []
    for level, title, raw_href in rows:
        if not title and not raw_href:
            continue
        href, fragment = _resolve_target(base_dir, raw_href)
        resolved.append(
            (level, TocEntry(title=title, href=href, level=level, fragment=fragment))
        )
    return _nest(resolved)


def _decode(raw: bytes) -> str:
    """Decode a navigation document, in the one case that needs text at all.

    Imported lazily: the sanitiser is a whole module of block-building machinery, and
    a navigation document that parses as XML never needs it - which is every book in
    the corpus.
    """
    from ..html.sanitizer import decode_text

    return decode_text(raw)


def _attribute(attributes: Sequence[tuple[str, str | None]], name: str) -> str:
    return next((value or "" for key, value in attributes if key.lower() == name), "")


class _TolerantNavReader(HTMLParser):
    """Recover the rows of a nav document that is not well-formed XML.

    A raw ``&`` in a title or an unclosed ``<li>`` makes expat refuse the whole file,
    and a document that a browser would show without complaint would be lost.  Every
    ``<a>`` is a row: the HTML parser cannot tell a ``<span>`` labelling an ``<li>``
    from one decorating a link, and inventing rows here would make the result depend
    on which path the document happened to take.

    A row is closed by whatever comes first - its own ``</a>``, the list item, or the
    next link - because a document this broken is exactly the place where the closing
    tag is the one that is missing.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[int, str, str]] = []
        self._depth = -1
        self._href: str | None = None
        self._text: list[str] = []

    def _flush(self) -> None:
        if self._href is None:
            return
        title = " ".join("".join(self._text).split())
        if title or self._href:
            self.rows.append((max(0, self._depth), title, self._href))
        self._href = None
        self._text = []

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag in ("ol", "ul"):
            self._depth += 1
        elif tag == "a":
            self._flush()
            self._href = _attribute(attributes, "href")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "li", "ol", "ul"):
            self._flush()
        if tag in ("ol", "ul"):
            self._depth = max(-1, self._depth - 1)

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


class _TolerantNcxReader(HTMLParser):
    """Recover the rows of an NCX that is not well-formed XML.

    A row is recorded the moment its ``navPoint`` opens and filled in as the label and
    the ``content`` go past, so the rows come out in document order with the nesting the
    file declares - a navPoint that is never closed keeps the parents it was opened
    under, and its title and target are still read.  Waiting for ``</navPoint>`` instead
    would lose exactly the rows a broken file is most likely to have.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._records: list[list] = []
        self._open: list[list] = []
        self._collecting = False

    @property
    def rows(self) -> list[tuple[int, str, str]]:
        """``(level, title, href)`` rows, in document order."""
        return [
            (level, " ".join(title.split()), src) for level, title, src in self._records
        ]

    def handle_starttag(self, tag: str, attributes: list[tuple[str, str | None]]) -> None:
        if tag == "navpoint":
            record = [len(self._open), "", ""]
            self._records.append(record)
            self._open.append(record)
            self._collecting = False
        elif tag == "text":
            self._collecting = True
        elif tag == "content" and self._open:
            self._open[-1][2] = _attribute(attributes, "src")

    def handle_endtag(self, tag: str) -> None:
        if tag == "text":
            self._collecting = False
        elif tag == "navpoint" and self._open:
            self._open.pop()

    def handle_data(self, data: str) -> None:
        if self._collecting and self._open:
            self._open[-1][1] += data



def _walk_nav_points(parent: ET.Element, base_dir: str, level: int) -> tuple[TocEntry, ...]:
    entries: list[TocEntry] = []
    for nav_point in parent:
        if _local_name(nav_point.tag) != "navPoint":
            continue
        label = next(
            (child for child in nav_point if _local_name(child.tag) == "navLabel"),
            None,
        )
        content = next(
            (child for child in nav_point if _local_name(child.tag) == "content"),
            None,
        )
        title = ""
        if label is not None:
            text_element = next(
                (child for child in label if _local_name(child.tag) == "text"),
                None,
            )
            if text_element is not None:
                title = _text_of(text_element)
        href, fragment = _resolve_target(
            base_dir, content.get("src") or "" if content is not None else ""
        )
        children = _walk_nav_points(nav_point, base_dir, level + 1)
        if not title and not children:
            continue
        entries.append(
            TocEntry(title=title, href=href, level=level, fragment=fragment, children=children)
        )
    return tuple(entries)
