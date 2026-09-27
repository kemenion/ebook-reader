"""A QML item that displays one rendered page.

The page arrives as a fully rendered ``QImage``; this item only blits it (ADR-008).
QML can then wrap the item in a ``ShaderEffectSource`` to animate it on the GPU
without any further Python involvement.
"""

from __future__ import annotations

from PySide6.QtCore import Property, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickPaintedItem

__all__ = ["PageItem"]


class PageItem(QQuickPaintedItem):
    """Paints the current page image, letterboxed into the item's bounds."""

    imageChanged = Signal()
    backgroundChanged = Signal()

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._background = QColor("#ffffff")
        self.setAntialiasing(True)
        self.setSmooth(True)

    def _get_image(self) -> QImage:
        return self._image

    def _set_image(self, image: QImage | None) -> None:
        # QML may assign null before the controller is injected, so treat that as
        # "no page yet" rather than letting the conversion fail.
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

    def paint(self, painter: QPainter) -> None:
        bounds = self.contentsBoundingRect()
        painter.fillRect(bounds, self._background)
        if self._image.isNull():
            return
        source = QRectF(self._image.rect())
        target = self._fit(source.size(), bounds.size())
        painter.drawImage(target, self._image, source)

    def _fit(self, source: QSize, bounds: QSize) -> QRectF:
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
