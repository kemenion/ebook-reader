"""A QML item that displays one rendered window of the text.

The window arrives as a fully rendered ``QImage``, one rendering quantum taller than
the page box (ADR-016); this item blits the visible part of it and nothing else.
Drawing a *sub-rectangle* is what keeps Python out of the scroll path: the image is
rasterised once per quantum, and moving within that quantum is a translate here, on
the GPU, with no callback per frame (ADR-008 / NFR-005).
"""

from __future__ import annotations

from PySide6.QtCore import Property, QRectF, QSize, QSizeF, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickPaintedItem

__all__ = ["PageItem"]


class PageItem(QQuickPaintedItem):
    """Paints the visible part of the current window image, letterboxed into bounds."""

    imageChanged = Signal()
    backgroundChanged = Signal()
    pageSizeChanged = Signal()
    panChanged = Signal()

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._background = QColor("#ffffff")
        self._page_size = QSizeF()
        self._pan = 0.0
        self.setAntialiasing(True)
        self.setSmooth(True)

    def _get_image(self) -> QImage:
        return self._image

    def _set_image(self, image: QImage | None) -> None:
        # QML may assign null before the controller is injected, so treat that as
        # "no window yet" rather than letting the conversion fail.
        if image is None:
            image = QImage()
        if image.cacheKey() == self._image.cacheKey():
            return
        self._image = image
        self.imageChanged.emit()
        self.update()

    image = Property(QImage, _get_image, _set_image, notify=imageChanged)

    def _get_background(self) -> QColor:
        return self._background

    def _set_background(self, color: QColor) -> None:
        if color == self._background:
            return
        self._background = color
        self.backgroundChanged.emit()
        self.update()

    background = Property(QColor, _get_background, _set_background, notify=backgroundChanged)

    def _get_page_size(self) -> QSizeF:
        return self._page_size

    def _set_page_size(self, size: QSizeF) -> None:
        """Logical size of the page box the image was rendered for.

        The image is taller than this - the extra strip is the rendering quantum the
        pan below moves through - so the item has to be told which rectangle of the
        image counts as one screen, or it would letterbox by the wrong aspect.
        """
        if size is None:
            size = QSizeF()
        if size == self._page_size:
            return
        self._page_size = QSizeF(size)
        self.pageSizeChanged.emit()
        self.update()

    pageSize = Property(QSizeF, _get_page_size, _set_page_size, notify=pageSizeChanged)

    def _get_pan(self) -> float:
        return self._pan

    def _set_pan(self, pan: float) -> None:
        """How far into the image the visible window starts, in logical pixels."""
        if abs(pan - self._pan) < 0.01:
            return
        self._pan = float(pan)
        self.panChanged.emit()
        self.update()

    pan = Property(float, _get_pan, _set_pan, notify=panChanged)

    def paint(self, painter: QPainter) -> None:
        bounds = self.contentsBoundingRect()
        painter.fillRect(bounds, self._background)
        if self._image.isNull():
            return

        ratio = self._image.devicePixelRatio() or 1.0
        page = self._page_box(ratio)
        headroom = max(0.0, self._image.height() / ratio - page.height())
        pan = max(0.0, min(self._pan, headroom))
        target = self._fit(page, bounds.size())
        # `drawImage()` reads an explicit source rectangle in *device* pixels - the
        # image's device pixel ratio does not apply to it - so the sub-rectangle is
        # scaled by the ratio here rather than by the painter.
        source = QRectF(
            0.0,
            pan * ratio,
            page.width() * ratio,
            page.height() * ratio,
        )
        painter.drawImage(target, self._image, source)

    def _page_box(self, ratio: float) -> QSizeF:
        """One screen's worth of the image, falling back to the image itself."""
        if not self._page_size.isEmpty():
            return self._page_size
        return QSizeF(self._image.width() / ratio, self._image.height() / ratio)

    def _fit(self, source: QSizeF, bounds: QSize) -> QRectF:
        """Scale to fit without changing the aspect ratio."""
        if source.isEmpty() or bounds.isEmpty():
            return QRectF(0, 0, 0, 0)
        scale = min(bounds.width() / source.width(), bounds.height() / source.height())
        width = source.width() * scale
        height = source.height() * scale
        return QRectF(
            (bounds.width() - width) / 2.0,
            (bounds.height() - height) / 2.0,
            width,
            height,
        )
