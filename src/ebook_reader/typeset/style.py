"""Map domain blocks onto Qt text formats.

All styling originates here.  Publisher declarations never reach this layer, so a
publisher ``font-family`` naming a face that does not exist cannot leak into the
layout (FR-024 / I-4).

Formats are cached per distinct style signature: the reference book has 2693
blocks whose text is spread over thousands of inline runs, and building a fresh
``QTextCharFormat`` per run would dominate the layout budget (NFR-003).
"""

from __future__ import annotations

from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import QFont, QTextBlockFormat, QTextCharFormat, QTextImageFormat

from ..domain.models import Block, BlockAlign, BlockKind, Span
from .settings import TypographySettings

__all__ = ["StyleSet", "heading_scale"]

_ALIGNMENT = {
    BlockAlign.LEFT: Qt.AlignmentFlag.AlignLeft,
    BlockAlign.CENTER: Qt.AlignmentFlag.AlignHCenter,
    BlockAlign.RIGHT: Qt.AlignmentFlag.AlignRight,
}

#: Heading sizes as a multiple of the body size, indexed by level 1..6.
_HEADING_SCALE = {1: 1.9, 2: 1.55, 3: 1.35, 4: 1.2, 5: 1.1, 6: 1.05}

#: Fraction of the content height one image may occupy.  Leaving a little room
#: matters because a figure that fills the page exactly leaves no space for its
#: caption and would force an extra page (ADR-007).
_IMAGE_HEIGHT_FRACTION = 0.94

#: Fallback aspect ratio (width / height) for an image whose header could not be
#: parsed.  Only the width is pinned in that case, so distortion is impossible.
_PORTRAIT_ASPECT = 0.72


def heading_scale(level: int) -> float:
    return _HEADING_SCALE.get(max(1, min(6, level)), 1.05)


class StyleSet:
    """Pre-computed formats for one (settings, page geometry) combination."""

    __slots__ = ("settings", "content_size", "_block_cache", "_char_cache", "_font_cache")

    def __init__(self, settings: TypographySettings, content_size: QSizeF) -> None:
        self.settings = settings
        self.content_size = content_size
        self._block_cache: dict[tuple[object, ...], QTextBlockFormat] = {}
        self._char_cache: dict[tuple[object, ...], QTextCharFormat] = {}
        self._font_cache: dict[int, QFont] = {}

    # ------------------------------------------------------------------ fonts

    def base_font(self, scale: float = 1.0) -> QFont:
        """Body font, optionally scaled for a heading level.

        Pixel sizes are used rather than point sizes so ``font_size`` means the
        same thing on every machine, matching the CSS-like units the reference
        books themselves use.
        """
        pixel_size = max(6, round(self.settings.font_size * scale))
        font = self._font_cache.get(pixel_size)
        if font is None:
            font = QFont()
            font.setFamilies(list(self.settings.font_families))
            font.setPixelSize(pixel_size)
            font.setStyleStrategy(
                QFont.StyleStrategy.PreferDefault | QFont.StyleStrategy.PreferAntialias
            )
            self._font_cache[pixel_size] = font
        return font

    # ----------------------------------------------------------- char formats

    def char_format(self, span: Span, level: int = 0) -> QTextCharFormat:
        """Character format for one inline run."""
        key = (
            level,
            span.bold,
            span.italic,
            span.superscript,
            span.subscript,
            span.link_href is not None,
        )
        cached = self._char_cache.get(key)
        if cached is not None:
            return cached

        fmt = QTextCharFormat()
        font = self.base_font(heading_scale(level) if level else 1.0)
        if span.bold:
            font.setWeight(QFont.Weight.Bold)
        if span.italic:
            font.setItalic(True)
        fmt.setFont(font)
        fmt.setForeground(self.settings.heading_color() if level else self.settings.foreground())
        if span.superscript:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSuperScript)
        elif span.subscript:
            fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript)
        if span.link_href is not None:
            fmt.setAnchor(True)
            fmt.setAnchorHref(span.link_href)
            fmt.setForeground(self.settings.link_color())
            fmt.setFontUnderline(True)

        self._char_cache[key] = fmt
        return fmt

    # ---------------------------------------------------------- block formats

    def block_format(self, block: Block) -> QTextBlockFormat:
        key = (block.kind, block.level, block.align, block.list_ordered)
        cached = self._block_cache.get(key)
        if cached is not None:
            return cached
        fmt = self._build_block_format(block)
        self._block_cache[key] = fmt
        return fmt

    def _build_block_format(self, block: Block) -> QTextBlockFormat:
        settings = self.settings
        em = settings.font_size
        fmt = QTextBlockFormat()
        fmt.setAlignment(self._alignment(block))
        if block.kind is BlockKind.IMAGE:
            # Images must not inherit the body line height.  `ProportionalHeight`
            # multiplies the natural line height, which *includes* the image, so a
            # 1.75 line height turned an 800 px figure into a 1400 px line and
            # pushed it onto a second page.  SingleHeight uses the font height,
            # which for an image-only line leaves the image size alone.
            fmt.setLineHeight(0.0, int(QTextBlockFormat.SingleHeight.value))
        else:
            fmt.setLineHeight(
                float(settings.line_height * 100.0),
                int(QTextBlockFormat.ProportionalHeight.value),
            )

        if block.kind is BlockKind.PARAGRAPH:
            # A centred paragraph is a caption: centring plus a hanging indent
            # looks wrong, so the indent is dropped (FR-025 / FR-032).
            fmt.setTextIndent(
                0.0 if block.align is BlockAlign.CENTER else settings.first_line_indent_em * em
            )
            fmt.setTopMargin(0.0)
            fmt.setBottomMargin(settings.paragraph_spacing_em * em)

        elif block.kind is BlockKind.HEADING:
            fmt.setTextIndent(0.0)
            fmt.setTopMargin(em * (1.6 if block.level <= 2 else 1.1))
            fmt.setBottomMargin(em * (0.9 if block.level <= 2 else 0.6))

        elif block.kind is BlockKind.QUOTE:
            fmt.setTextIndent(0.0)
            fmt.setLeftMargin(em * 1.4)
            fmt.setRightMargin(em * 0.8)
            fmt.setTopMargin(em * 0.3)
            fmt.setBottomMargin(em * 0.6)

        elif block.kind is BlockKind.LIST_ITEM:
            fmt.setTextIndent(0.0)
            fmt.setLeftMargin(em * (1.2 + 1.1 * block.level))
            fmt.setTopMargin(0.0)
            fmt.setBottomMargin(em * 0.15)

        elif block.kind is BlockKind.PREFORMATTED:
            fmt.setTextIndent(0.0)
            fmt.setLeftMargin(em * 0.8)
            fmt.setTopMargin(em * 0.2)
            fmt.setBottomMargin(em * 0.2)

        elif block.kind is BlockKind.RULE:
            fmt.setTextIndent(0.0)
            fmt.setTopMargin(em * 0.8)
            fmt.setBottomMargin(em * 0.8)

        elif block.kind is BlockKind.IMAGE:
            fmt.setTextIndent(0.0)
            fmt.setTopMargin(em * 0.4)
            fmt.setBottomMargin(em * 0.4)

        return fmt

    def _alignment(self, block: Block) -> Qt.AlignmentFlag:
        if block.align is not BlockAlign.INHERIT:
            return _ALIGNMENT[block.align]
        if block.kind is BlockKind.RULE:
            return Qt.AlignmentFlag.AlignHCenter
        if self.settings.justify and block.kind in (
            BlockKind.PARAGRAPH,
            BlockKind.LIST_ITEM,
            BlockKind.QUOTE,
        ):
            # Qt distributes justification over inter-character gaps for Han
            # script, which is exactly the Chinese convention (ADR-001).
            return Qt.AlignmentFlag.AlignJustify
        return Qt.AlignmentFlag.AlignLeft

    # ------------------------------------------------------------------ images

    def image_format(self, name: str, block: Block) -> QTextImageFormat | None:
        """Size an image so that it fits inside the content box (FR-039).

        *name* is the resource key the document will be asked to resolve, not the
        archive path: opaque ASCII keys keep URL round-tripping unambiguous even
        when the real file name contains spaces or percent-encodings.
        """
        reference = block.image
        if reference is None or not reference.src:
            return None

        max_width = max(1.0, self.content_size.width())
        max_height = max(1.0, self.content_size.height() * _IMAGE_HEIGHT_FRACTION)
        width = float(reference.width)
        height = float(reference.height)

        fmt = QTextImageFormat()
        fmt.setName(name)
        if width > 0 and height > 0:
            scale = min(1.0, max_width / width, max_height / height)
            fmt.setWidth(width * scale)
            fmt.setHeight(height * scale)
        else:
            # Unknown intrinsic size: pin the width only; the height then comes
            # from the decoded image, so nothing can be distorted.
            fmt.setWidth(max_width)
            fmt.setHeight(max_height * _PORTRAIT_ASPECT)
        fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignMiddle)
        return fmt

    def image_size_for(self, block: Block) -> QSizeF:
        """Display size chosen for *block*, or the whole box when unsizeable."""
        fmt = self.image_format("", block)
        if fmt is None:
            return QSizeF(self.content_size.width(), self.content_size.height())
        return QSizeF(fmt.width(), fmt.height())

    def rule_text(self) -> str:
        """A centred rule line occupying about a quarter of the content width."""
        count = max(3, int(self.content_size.width() * 0.28 / max(1.0, self.settings.font_size)))
        return "─" * count

