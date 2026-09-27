"""Build the navigation tree from EPUB3 ``nav.xhtml`` or EPUB2 ``toc.ncx``.

The reference books exercise both paths: 人生财富靠康波 ships a nav document,
币安人生 only an NCX.  Both parsers walk the XML by local name so that stray or
missing namespaces do not break navigation (FR-010 / FR-011).
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from ..models import TocEntry
from .paths import join_href

__all__ = ["parse_nav", "parse_ncx"]

_OPS_NS = "http://www.idpf.org/2007/ops"


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
    except ET.ParseError:
        return ()

    nav = _find_toc_nav(root)
    if nav is None:
        return ()

    list_elements = _children(nav, "ol")
    if not list_elements:
        list_elements = [element for element in nav.iter() if _local_name(element.tag) == "ol"]
    if not list_elements:
        return ()
    return _walk_nav_list(list_elements[0], base_dir, level=0)


def _find_toc_nav(root: ET.Element) -> ET.Element | None:
    """Prefer ``<nav epub:type="toc">``, fall back to the first ``<nav>``."""
    fallback: ET.Element | None = None
    for element in root.iter():
        if _local_name(element.tag) != "nav":
            continue
        if fallback is None:
            fallback = element
        nav_type = element.get(f"{{{_OPS_NS}}}type") or element.get("type") or ""
        if "toc" in nav_type.split():
            return element
    return fallback


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
        href = join_href(base_dir, anchor.get("href") or "") if anchor.get("href") else ""
        nested = _children(li, "ol")
        children = _walk_nav_list(nested[0], base_dir, level + 1) if nested else ()
        if not title and not children:
            continue
        entries.append(TocEntry(title=title, href=href, level=level, children=children))
    return tuple(entries)


def parse_ncx(raw: bytes, base_dir: str) -> tuple[TocEntry, ...]:
    """Parse an EPUB2 NCX document into a tree of :class:`TocEntry`."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return ()

    nav_map = next(
        (element for element in root.iter() if _local_name(element.tag) == "navMap"),
        None,
    )
    if nav_map is None:
        return ()
    return _walk_nav_points(nav_map, base_dir, level=0)


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
        href = join_href(base_dir, content.get("src") or "") if content is not None else ""
        children = _walk_nav_points(nav_point, base_dir, level + 1)
        if not title and not children:
            continue
        entries.append(TocEntry(title=title, href=href, level=level, children=children))
    return tuple(entries)
