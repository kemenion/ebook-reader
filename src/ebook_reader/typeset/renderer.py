"""Rasterise one page of a laid-out document to a ``QImage`` (ADR-008).

Everything expensive in the animation path (text shaping, pagination, image
decoding) has already happened by the time a page is rendered, so a page render
is a bounded, predictable amount of work.  QML then treats the resulting image as
a texture and animates it on the GPU, with no Python callbacks per frame.

Device pixel ratio is applied through ``QImage.setDevicePixelRatio`` rather than
by scaling the painter: Qt then keeps painter coordinates in logical units while
rasterising glyphs at full device resolution, so HiDPI text stays sharp (FR-057).
"""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QAbstractTextDocumentLayout, QImage, QPainter, QPalette, QTextDocument

from .settings import PageGeometry, TypographySettings

__all__ = ["render_page"]


def render_page(
    document: QTextDocument,
    page_index: int,
    geometry: PageGeometry,
    settings: TypographySettings,
    *,
    device_ratio: float = 1.0,
) -> QImage:
    """Draw page *page_index* onto a new image of the full page size."""
    ratio = device_ratio if device_ratio and device_ratio > 0 else 1.0
    page_width = geometry.page_size.width()
    page_height = geometry.page_size.height()

    image = QImage(
        max(1, round(page_width * ratio)),
        max(1, round(page_height * ratio)),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(settings.background())

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setClipRect(QRectF(0.0, 0.0, page_width, page_height))
        painter.translate(geometry.content_origin)
        painter.translate(0.0, -page_index * geometry.content_height)

        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, settings.foreground())
        context.palette.setColor(QPalette.ColorRole.Base, settings.background())
        # This is the important one.  `QTextDocumentLayout::draw` only skips the
        # blocks of other pages when it is told the clip in *document*
        # coordinates.  Relying on the painter's clip rect (set above, in device
        # coordinates, before the translation) does not work: Qt then walks every
        # block of every page, which on a 41-page section meant redrawing all 28
        # of its images per page - measured at 212 ms per page instead of 9 ms.
        context.clip = QRectF(
            0.0,
            page_index * geometry.content_height,
            geometry.content_size.width(),
            geometry.content_size.height(),
        )
        document.documentLayout().draw(painter, context)
    finally:
        painter.end()
    return image
