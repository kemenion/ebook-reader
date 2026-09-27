"""Build a ``QTextDocument`` from a block list (ADR-002).

The document is assembled programmatically with ``QTextCursor`` rather than by
feeding HTML to ``setHtml``.  Two reasons:

* Qt's HTML import accepts only a small HTML4 subset, so publisher
  ``<div style="...">`` shells would be honoured inconsistently;
* the publisher's ``font-family`` must be unable to influence the result.
  Building the document ourselves keeps styling entirely inside
  :class:`~ebook_reader.typeset.style.StyleSet`.

One block produces exactly one document block, so ``block_index`` addresses both
the domain block list and ``QTextDocument.findBlockByNumber`` - the invariant
that stable reading positions rely on (ADR-011).

Images are addressed through opaque ASCII resource keys (``epub-image:<n>``)
rather than archive paths, because those paths may contain spaces or
percent-encoded characters that would not survive a URL round trip.
"""

from __future__ import annotations

from typing import Iterable

from PySide6.QtCore import QSizeF, QUrl
from PySide6.QtGui import QTextCursor, QTextDocument

from ..domain.models import Block, BlockKind, Span
from .images import ImageCache
from .style import StyleSet

__all__ = ["IMAGE_KEY_PREFIX", "ReaderDocument", "build_document", "layout_continuous"]

IMAGE_KEY_PREFIX = "epub-image:"

_IMAGE_RESOURCE_TYPES = frozenset(
    {
        QTextDocument.ResourceType.ImageResource,
        QTextDocument.ResourceType.ImageResource.value,
    }
)

_EMPTY_SPAN = Span("")


class ReaderDocument(QTextDocument):
    """A text document that resolves its images from an archive on demand.

    ``loadResource`` is the single hook Qt offers for lazy content, so the image
    cache plugs in here; everything else keeps Qt's default behaviour (FR-054).
    """

    def __init__(self, image_cache: ImageCache, parent: object | None = None) -> None:
        super().__init__(parent)
        self._image_cache = image_cache
        self._image_paths: dict[str, str] = {}
        self._image_sizes: dict[str, QSizeF] = {}

    @property
    def image_cache(self) -> ImageCache:
        return self._image_cache

    def register_image(self, key: str, path: str, size: QSizeF) -> None:
        """Associate a resource key with an archive path and a display size."""
        self._image_paths[key] = path
        self._image_sizes[key] = size

    def image_path(self, key: str) -> str | None:
        """Reverse lookup, used by tests and by click handling."""
        return self._image_paths.get(key)

    def loadResource(self, resource_type: int, url: QUrl) -> object:
        """Resolve an image resource from the archive (FR-054)."""
        if resource_type in _IMAGE_RESOURCE_TYPES:
            key = url.toString()
            path = self._image_paths.get(key)
            if path:
                image = self._image_cache.image(path, self._image_sizes.get(key))
                if image is not None:
                    return image
        return super().loadResource(resource_type, url)


def build_document(
    blocks: Iterable[Block],
    style_set: StyleSet,
    image_cache: ImageCache,
) -> ReaderDocument:
    """Create a document for *blocks*.

    The document margin is zero because page margins are applied by translating
    the painter in the renderer, which is what makes asymmetric margins possible
    (FR-035).
    """
    blocks = tuple(blocks)
    doc = ReaderDocument(image_cache)
    doc.setDocumentMargin(0.0)
    doc.setDefaultFont(style_set.base_font())

    cursor = QTextCursor(doc)
    cursor.beginEditBlock()
    rule_format = style_set.char_format(_EMPTY_SPAN)
    for index, block in enumerate(blocks):
        if index:
            cursor.insertBlock()
        cursor.setBlockFormat(style_set.block_format(block))
        if block.kind is BlockKind.IMAGE:
            _insert_image(cursor, doc, style_set, block, index)
        elif block.kind is BlockKind.RULE:
            cursor.insertText(style_set.rule_text(), rule_format)
        else:
            for span in block.spans:
                if span.text:
                    cursor.insertText(span.text, style_set.char_format(span, level=block.level))
    cursor.endEditBlock()
    doc.setModified(False)
    return doc


def layout_continuous(document: QTextDocument, width: float) -> float:
    """Lay *document* out as one column *width* wide; return how tall it came out.

    The page height is ``-1``, which Qt reads as "no height limit" and lays the whole
    document out as a single page (ADR-016).  Passing ``0`` looks equivalent and is
    not: a zero-height page size means "the height has not been decided yet", and
    ``pageCount()`` then returns an uninitialised value.
    """
    document.setPageSize(QSizeF(max(32.0, width), -1.0))
    return max(0.0, document.size().height())


def _insert_image(
    cursor: QTextCursor,
    doc: ReaderDocument,
    style_set: StyleSet,
    block: Block,
    index: int,
) -> None:
    key = f"{IMAGE_KEY_PREFIX}{index}"
    fmt = style_set.image_format(key, block)
    if fmt is None:
        return
    doc.register_image(key, block.image.src if block.image else "", style_set.image_size_for(block))
    cursor.insertImage(fmt)

