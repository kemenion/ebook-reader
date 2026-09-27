"""Immutable data models shared by parsing, typesetting and the UI.

This module is intentionally free of any Qt import (ADR-012) so that parsing and
normalisation can be unit tested without a GUI.  Consequently images are
described by *path plus intrinsic size* rather than by a decoded ``QImage``;
decoding happens later, in the typesetting layer (ADR-006).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    "Block",
    "BlockAlign",
    "BlockKind",
    "BookMeta",
    "ImageRef",
    "Landmark",
    "ManifestItem",
    "Span",
    "SpineItem",
    "TocEntry",
]


class BlockKind(str, Enum):
    """Paragraph-level content kinds produced by the sanitiser (FR-021)."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    IMAGE = "image"
    RULE = "rule"
    QUOTE = "quote"
    LIST_ITEM = "list_item"
    PREFORMATTED = "preformatted"


class BlockAlign(str, Enum):
    """Horizontal alignment declared by the publisher (FR-025)."""

    INHERIT = "inherit"
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


@dataclass(frozen=True, slots=True)
class Span:
    """A run of inline text with a uniform character style (FR-022)."""

    text: str
    bold: bool = False
    italic: bool = False
    superscript: bool = False
    subscript: bool = False
    link_href: str | None = None


@dataclass(frozen=True, slots=True)
class ImageRef:
    """A reference to an image inside the archive (FR-039).

    ``width`` / ``height`` are the intrinsic pixel dimensions read from the file
    header without decoding pixels.  They may be ``0`` when probing failed.
    """

    src: str
    width: int = 0
    height: int = 0
    alt: str = ""

    @property
    def aspect_ratio(self) -> float | None:
        return self.width / self.height if self.width and self.height else None


@dataclass(frozen=True, slots=True)
class Block:
    """One paragraph-level unit of content.

    A block never holds decoded pixels or Qt objects, keeping it cheap to keep in
    memory and easy to reason about.
    """

    kind: BlockKind
    spans: tuple[Span, ...] = ()
    level: int = 0
    image: ImageRef | None = None
    align: BlockAlign = BlockAlign.INHERIT
    anchor_ids: tuple[str, ...] = ()
    list_ordered: bool = False

    @property
    def text(self) -> str:
        """Concatenated span text (word-joiner characters included)."""
        return "".join(span.text for span in self.spans)

    @property
    def is_heading(self) -> bool:
        return self.kind is BlockKind.HEADING

    @property
    def is_image(self) -> bool:
        return self.kind is BlockKind.IMAGE

    @property
    def is_empty(self) -> bool:
        return not self.spans and self.image is None


@dataclass(frozen=True, slots=True)
class BookMeta:
    """Dublin Core metadata subset actually used by the UI (FR-003)."""

    title: str = ""
    authors: tuple[str, ...] = ()
    language: str = ""
    publisher: str = ""
    identifier: str = ""
    cover_href: str | None = None

    @property
    def author_line(self) -> str:
        return " / ".join(self.authors)


@dataclass(frozen=True, slots=True)
class ManifestItem:
    """One ``<item>`` of the OPF manifest."""

    id: str
    href: str
    media_type: str
    properties: str = ""


@dataclass(frozen=True, slots=True)
class SpineItem:
    """One ``<itemref>`` of the OPF spine, resolved against the manifest.

    ``media_type`` is the authoritative way to tell a renderable document from
    anything else; the file name must not be inspected, because 25 of 28
    documents in the reference book carry no extension at all (I-1 / FR-004).

    ``linear`` is the publisher's answer to "does this document belong to the book
    proper"; a reading order that walks past a ``linear="no"`` document is the one
    the publisher asked for.  None of the three reference books uses it, so it is
    only exercised by a book written in the tests.
    """

    index: int
    idref: str
    href: str
    media_type: str
    linear: bool = True


@dataclass(frozen=True, slots=True)
class Landmark:
    """One row of a ``landmarks`` navigation document (FR-009).

    ``type`` is the EPUB structural semantic - ``bodymatter``, ``toc``,
    ``cover`` - and is the part worth keeping: it says which document the text
    actually starts in, which a table of contents full of front matter does not.
    """

    type: str
    title: str
    href: str = ""
    fragment: str = ""


@dataclass(frozen=True, slots=True)
class TocEntry:
    """A node of the navigation tree (FR-010 .. FR-013)."""

    title: str
    href: str
    level: int = 0
    #: The in-document anchor this row points at, without the ``#``; empty when the row
    #: points at the top of its document.  It sits beside ``href`` rather than inside it
    #: because ``href`` answers "which document" - the question that has to be resolved
    #: against the spine - while this answers "where in it" (FR-014).  Publishers use it
    #: heavily: in one reference book 143 of the contents' links name an anchor instead
    #: of a file, and a reader that dropped the fragment sent every one of them to the
    #: top of the document.
    fragment: str = ""
    children: tuple["TocEntry", ...] = field(default_factory=tuple)
