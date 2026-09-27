"""Domain layer: EPUB parsing and content normalisation.

Nothing in this package may import Qt (ADR-012).
"""

from __future__ import annotations

from .errors import BookError, MissingResourceError, NotAnEpubError, PackageError
from .models import (
    Block,
    BlockAlign,
    BlockKind,
    BookMeta,
    ImageRef,
    ManifestItem,
    Span,
    SpineItem,
    TocEntry,
)

__all__ = [
    "Block",
    "BlockAlign",
    "BlockKind",
    "BookError",
    "BookMeta",
    "ImageRef",
    "ManifestItem",
    "MissingResourceError",
    "NotAnEpubError",
    "PackageError",
    "Span",
    "SpineItem",
    "TocEntry",
]


def __getattr__(name: str) -> object:
    """Expose ``EpubBook`` lazily so the package import stays cheap."""
    if name == "EpubBook":
        from .epub.book import EpubBook

        return EpubBook
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

