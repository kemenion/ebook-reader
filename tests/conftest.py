"""Shared fixtures.

The reference books are the test data: they are the actual input the design was
built around, so testing against them catches far more than synthetic samples.
They are skipped rather than failed when absent, so the suite still runs in a
tree without the ``ebooks`` directory.

Books written here - see :func:`build_epub` - cover the rules the reference books
cannot show, because no real book in the corpus does those things: a navigation
document that is not well-formed XML, a ``<base href>``, a ``linear="no"``
document.  One rule per book, kept as small as the rule allows.
"""

from __future__ import annotations

import os
import sys
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

BOOKS = ROOT / "ebooks"


def _find(pattern: str) -> Path:
    matches = sorted(BOOKS.glob(pattern))
    if not matches:
        pytest.skip(f"reference book not present: {pattern}")
    return matches[0]


@pytest.fixture(scope="session")
def kangpo_path() -> Path:
    """EPUB 3 book: 27 sections, 408 images, 25 extension-less documents."""
    return _find("*康波*.epub")


@pytest.fixture(scope="session")
def binan_path() -> Path:
    """EPUB 2 book: 35 sections, an NCX table of contents, 53 grayscale images."""
    return _find("*币安*.epub")


@pytest.fixture(scope="session")
def kangpo(kangpo_path: Path):
    from ebook_reader.domain import EpubBook

    book = EpubBook.open(kangpo_path)
    yield book
    book.close()


@pytest.fixture(scope="session")
def binan(binan_path: Path):
    from ebook_reader.domain import EpubBook

    book = EpubBook.open(binan_path)
    yield book
    book.close()


@pytest.fixture(scope="session")
def linqi_path() -> Path:
    """EPUB 2 book: 135 sections whose own NCX names only three of them."""
    return _find("*林奇*.epub")


@pytest.fixture(scope="session")
def linqi(linqi_path: Path):
    from ebook_reader.domain import EpubBook

    book = EpubBook.open(linqi_path)
    yield book
    book.close()


@pytest.fixture(scope="session")
def largest_section(kangpo) -> int:
    """Index of the section with the most text, i.e. the worst case for layout."""
    return max(
        range(kangpo.section_count),
        key=lambda index: sum(len(block.text) for block in kangpo.blocks(index)),
    )


def _headings(book, index: int) -> list[int]:
    """Positions of the blocks that are headings with something to say (FR-018)."""
    return [
        position
        for position, block in enumerate(book.blocks(index))
        if block.is_heading and block.text.strip()
    ]


@pytest.fixture(scope="session")
def heading_section(kangpo) -> int:
    """The section of the EPUB 3 book carrying the most headings: the fullest outline available.

    The outline column is heading-only (ADR-016), so a test that needs rows has to
    be looking at a section that has headings - the book opens on its cover, which
    has none.
    """
    return max(
        range(kangpo.section_count),
        key=lambda index: (len(_headings(kangpo, index)), -index),
    )


@pytest.fixture(scope="session")
def headingless_section(binan) -> int:
    """The longest section of the EPUB 2 book with no headings: the one with nothing to list.

    the EPUB 2 book used to exercise the page-per-row fallback; with that gone
    it is the book that proves a section without headings gets no outline at all.
    """
    candidates = [
        index
        for index in range(binan.section_count)
        if len(binan.blocks(index)) > 100 and not _headings(binan, index)
    ]
    if not candidates:
        pytest.skip("reference book has no long section without headings")
    return max(candidates, key=lambda index: len(binan.blocks(index)))


@pytest.fixture(scope="session")
def qapp():
    """A single offscreen ``QGuiApplication`` for the whole session."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QGuiApplication

    application = QGuiApplication.instance() or QGuiApplication([])
    yield application


_CONTAINER_XML = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="{package_path}" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_PACKAGE_XML = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:sample</dc:identifier>
    <dc:title>{title}</dc:title>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
{items}
  </manifest>
  <spine>
{itemrefs}
  </spine>
</package>
"""


@pytest.fixture
def build_epub(tmp_path: Path):
    """Factory writing a minimal but valid EPUB and returning its path.

    *documents* maps an href relative to the package directory to its XHTML
    source and fixes the spine order.  *resources* adds archive entries that are
    not spine documents - a nav document, an NCX, a stylesheet, an image.
    *manifest* declares those extra items as ``href -> (media type, properties)``.
    *package_xml* replaces the generated package document, for the tests that are
    about the package itself (as bytes, when the test is about its encoding).
    """

    def build(
        documents: Mapping[str, str],
        *,
        resources: Mapping[str, str | bytes] | None = None,
        manifest: Mapping[str, tuple[str, str]] | None = None,
        package_xml: str | bytes | None = None,
        package_path: str = "OEBPS/content.opf",
        linear_no: Iterable[str] = (),
        name: str = "sample.epub",
        title: str = "样本",
    ) -> Path:
        base_dir = package_path.rsplit("/", 1)[0] if "/" in package_path else ""
        items: dict[str, tuple[str, str]] = {
            href: ("application/xhtml+xml", "") for href in documents
        }
        items.update(manifest or {})
        ids = {href: f"i{index}" for index, href in enumerate(items)}
        non_linear = set(linear_no)

        items_xml = "\n".join(
            f'    <item id="{ids[href]}" href="{href}" media-type="{media_type}"'
            + (f' properties="{properties}"' if properties else "")
            + "/>"
            for href, (media_type, properties) in items.items()
        )
        spine_xml = "\n".join(
            f'    <itemref idref="{ids[href]}"'
            + (' linear="no"' if href in non_linear else "")
            + "/>"
            for href in documents
        )
        package = package_xml or _PACKAGE_XML.format(
            title=title, items=items_xml, itemrefs=spine_xml
        )

        target = tmp_path / name
        with zipfile.ZipFile(target, "w") as archive:
            archive.writestr(
                zipfile.ZipInfo("mimetype"),
                "application/epub+zip",
                compress_type=zipfile.ZIP_STORED,
            )
            archive.writestr(
                "META-INF/container.xml", _CONTAINER_XML.format(package_path=package_path)
            )
            archive.writestr(package_path, package)
            for href, source in {**documents, **(resources or {})}.items():
                entry = f"{base_dir}/{href}" if base_dir else href
                archive.writestr(entry, source)
        return target

    return build
