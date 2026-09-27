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
    """

    index: int
    idref: str
    href: str
    media_type: str


@dataclass(frozen=True, slots=True)
class TocEntry:
    """A node of the navigation tree (FR-010 .. FR-013)."""

    title: str
    href: str
    level: int = 0
    children: tuple["TocEntry", ...] = field(default_factory=tuple)
