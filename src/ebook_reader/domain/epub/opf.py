"""Parse the OPF package document: metadata, manifest and spine (FR-003/FR-004)."""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from ..errors import PackageError
from ..models import BookMeta, ManifestItem, SpineItem
from .paths import join_href, normalize

__all__ = ["Package", "parse_package"]

_NCX_MEDIA_TYPE = "application/x-dtbncx+xml"
_NAV_PROPERTY = "nav"
_XML_DECLARATION_RE = re.compile(r"^\s*<\?xml[^>]*\?>")


class Package:
    """Parsed OPF content, already resolved against the archive layout."""

    __slots__ = ("base_dir", "meta", "manifest", "spine", "nav_href", "ncx_href", "opf_path")

    def __init__(
        self,
        *,
        opf_path: str,
        base_dir: str,
        meta: BookMeta,
        manifest: dict[str, ManifestItem],
        spine: tuple[SpineItem, ...],
        nav_href: str | None,
        ncx_href: str | None,
    ) -> None:
        self.opf_path = opf_path
        self.base_dir = base_dir
        self.meta = meta
        self.manifest = manifest
        self.spine = spine
        self.nav_href = nav_href
        self.ncx_href = ncx_href

    def resolve(self, href: str) -> str:
        """Resolve an href taken from the OPF against the archive layout."""
        return join_href(self.base_dir, href)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_all(root: ET.Element, name: str) -> list[ET.Element]:
    """Find elements by local name, tolerating a missing or unusual namespace."""
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _text_of(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


def _build_manifest(root: ET.Element) -> dict[str, ManifestItem]:
    items: dict[str, ManifestItem] = {}
    for element in _find_all(root, "item"):
        item_id = element.get("id")
        href = element.get("href")
        if not item_id or not href:
            continue
        items[item_id] = ManifestItem(
            id=item_id,
            href=href,
            media_type=element.get("media-type") or "",
            properties=element.get("properties") or "",
        )
    return items


def _build_spine(
    root: ET.Element,
    manifest: dict[str, ManifestItem],
    base_dir: str,
) -> tuple[SpineItem, ...]:
    """Resolve every itemref against the manifest.

    Documents are recognised solely through their manifest ``media-type``: the
    reference book stores 25 of its 28 documents with extension-less names such
    as ``xhtml/Chapter4_1`` (I-1 / FR-004).  A spine entry whose manifest item is
    missing is skipped rather than aborting the whole book.
    """
    spine: list[SpineItem] = []
    for element in _find_all(root, "itemref"):
        idref = element.get("idref")
        if not idref:
            continue
        item = manifest.get(idref)
        if item is None:
            continue
        spine.append(
            SpineItem(
                index=len(spine),
                idref=idref,
                href=join_href(base_dir, item.href),
                media_type=item.media_type,
                linear=(element.get("linear") or "yes").strip().lower() != "no",
            )
        )
    return tuple(spine)


def _spine_toc_href(
    root: ET.Element,
    manifest: dict[str, ManifestItem],
    base_dir: str,
) -> str | None:
    """The NCX the spine points at through ``<spine toc="…">`` (FR-011).

    An EPUB 2 package names its navigation twice - the manifest declares the media
    type, and the spine names the document - and the second one survives the mistakes
    the first one does not: a manifest item for the NCX with a wrong or missing
    media type is still named correctly here.
    """
    spine = next((element for element in _find_all(root, "spine")), None)
    if spine is None:
        return None
    item = manifest.get(spine.get("toc") or "")
    return join_href(base_dir, item.href) if item is not None else None



def _build_meta(
    root: ET.Element,
    manifest: dict[str, ManifestItem],
    base_dir: str,
) -> BookMeta:
    metadata = next((element for element in root if _local_name(element.tag) == "metadata"), None)
    if metadata is None:
        metadata = root

    def dc(name: str) -> str:
        candidates = [element for element in metadata if _local_name(element.tag) == name]
        return _text_of(candidates[0]) if candidates else ""

    authors = tuple(
        _text_of(element)
        for element in metadata
        if _local_name(element.tag) == "creator" and _text_of(element)
    )

    return BookMeta(
        title=dc("title"),
        authors=authors,
        language=dc("language"),
        publisher=dc("publisher"),
        identifier=dc("identifier"),
        cover_href=_find_cover_href(root, manifest, base_dir),
    )


def _find_cover_href(
    root: ET.Element,
    manifest: dict[str, ManifestItem],
    base_dir: str,
) -> str | None:
    """Locate the cover image via the EPUB3 ``cover-image`` property first, then
    via the EPUB2 ``<meta name="cover" content="id"/>`` convention."""
    for item in manifest.values():
        if "cover-image" in item.properties.split():
            return join_href(base_dir, item.href)

    for element in _find_all(root, "meta"):
        if element.get("name") == "cover":
            item = manifest.get(element.get("content") or "")
            if item is not None:
                return join_href(base_dir, item.href)
    return None


def _read_package_xml(raw: bytes, opf_path: str) -> ET.Element:
    """Read the package document, including books that declare a legacy encoding.

    expat implements UTF-8 and UTF-16 and refuses every other encoding *by name* - a
    ``gbk`` declaration raises ``ValueError``, not ``ParseError``, and it used to escape
    as a traceback out of ``EpubBook.open()``.  Older Chinese books write their package
    document that way as a matter of course, and the title and the author are exactly
    the text that needs decoding, so the document is decoded first and the declaration
    dropped (expat will not accept an encoding declaration on text it did not decode).
    """
    try:
        return ET.fromstring(raw)
    except ValueError:
        pass
    except ET.ParseError as exc:
        raise PackageError(f"{opf_path} is not valid XML: {exc}") from exc

    from ..html.sanitizer import decode_text

    try:
        return ET.fromstring(_XML_DECLARATION_RE.sub("", decode_text(raw), count=1))
    except ET.ParseError as exc:
        raise PackageError(f"{opf_path} is not valid XML: {exc}") from exc


def parse_package(raw: bytes, opf_path: str) -> Package:
    """Parse raw OPF bytes that live at *opf_path* inside the archive."""
    root = _read_package_xml(raw, opf_path)

    base_dir = opf_path.rsplit("/", 1)[0] if "/" in opf_path else ""
    manifest = _build_manifest(root)

    nav_href = next(
        (
            join_href(base_dir, item.href)
            for item in manifest.values()
            if _NAV_PROPERTY in item.properties.split()
        ),
        None,
    )
    ncx_href = next(
        (join_href(base_dir, item.href) for item in manifest.values() if item.media_type == _NCX_MEDIA_TYPE),
        None,
    ) or _spine_toc_href(root, manifest, base_dir)

    return Package(
        opf_path=normalize(opf_path),
        base_dir=base_dir,
        meta=_build_meta(root, manifest, base_dir),
        manifest=manifest,
        spine=_build_spine(root, manifest, base_dir),
        nav_href=nav_href,
        ncx_href=ncx_href,
    )
