"""Rasterise one window of a continuously laid-out document to a ``QImage`` (ADR-008).

Everything expensive in the scroll path - text shaping, layout, image decoding - has
already happened by the time a window is rendered, so a render is a bounded,
predictable amount of work.  QML then treats the resulting image as a texture and
moves it on the GPU, with no Python callbacks per frame.

The image is one :data:`WINDOW_QUANTUM` taller than the page box.  That strip is what
makes scrolling within a quantum a translate in QML rather than a render here
(ADR-016 / NFR-005), and it is never seen on its own: the window is always drawn from
the top of the image, shifted up by at most one quantum.

Device pixel ratio is applied through ``QImage.setDevicePixelRatio`` rather than
by scaling the painter: Qt then keeps painter coordinates in logical units while
rasterising glyphs at full device resolution, so HiDPI text stays sharp (FR-057).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRectF
from PySide6.QtGui import QAbstractTextDocumentLayout, QImage, QPainter, QPalette, QTextDocument

from .settings import PageGeometry, TypographySettings

__all__ = ["WINDOW_QUANTUM", "quantise_offset", "render_window"]

#: Rendering quantum, in logical pixels (ADR-016).
#:
#: A window image is rasterised at a multiple of this distance and is drawn
#: shifted up by the remaining fraction, so scrolling inside one quantum costs the
#: compositor a translate instead of a Python render; the extra strip the image
#: carries covers the bottom of the window while that fraction grows (ADR-016).
WINDOW_QUANTUM = 32.0


def quantise_offset(offset: float) -> float:
    """Round *offset* down to the quantum the window is rendered at (ADR-016)."""
    return math.floor(max(0.0, offset) / WINDOW_QUANTUM) * WINDOW_QUANTUM


def render_window(
    document: QTextDocument,
    offset: float,
    geometry: PageGeometry,
    settings: TypographySettings,
    *,
    device_ratio: float = 1.0,
) -> QImage:
    """Draw the window that starts at *offset*, plus one quantum of headroom.

    *offset* is expected to be a multiple of :data:`WINDOW_QUANTUM`, which is what
    :func:`quantise_offset` is for; anything else still draws correctly, it just
    makes the caller's arithmetic harder to follow.
    """
    ratio = device_ratio if device_ratio and device_ratio > 0 else 1.0
    page_width = geometry.page_size.width()
    page_height = geometry.page_size.height()
    window_height = page_height + WINDOW_QUANTUM

    image = QImage(
        max(1, round(page_width * ratio)),
        max(1, round(window_height * ratio)),
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.setDevicePixelRatio(ratio)
    image.fill(settings.background())

    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setClipRect(QRectF(0.0, 0.0, page_width, window_height))
        painter.translate(geometry.content_origin)
        painter.translate(0.0, -offset)

        context = QAbstractTextDocumentLayout.PaintContext()
        context.palette.setColor(QPalette.ColorRole.Text, settings.foreground())
        context.palette.setColor(QPalette.ColorRole.Base, settings.background())
        # This is the important one.  `QTextDocumentLayout::draw` only skips the
        # blocks of other windows when it is told the clip in *document*
        # coordinates.  Relying on the painter's clip rect (set above, in device
        # coordinates, before the translation) does not work: Qt then walks every
        # block of the whole section, which on a 41-page chapter meant redrawing all
        # 28 of its images per window - measured at 212 ms per window instead of 9.
        context.clip = QRectF(
            0.0,
            offset,
            geometry.content_size.width(),
            geometry.content_size.height() + WINDOW_QUANTUM,
        )
        document.documentLayout().draw(painter, context)
    finally:
        painter.end()
    return image

