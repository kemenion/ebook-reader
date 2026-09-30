"""A QML item that displays one rendered window of the text.

The window arrives as a fully rendered ``QImage``, one rendering quantum taller than
the page box (ADR-016); this item blits the visible part of it and nothing else.
Drawing a *sub-rectangle* is what keeps Python out of the scroll path: the image is
rasterised once per quantum, and moving within that quantum is a translate here, on
the GPU, with no callback per frame (ADR-008 / NFR-005).

It also draws the selection (FR-070).  The bands arrive from the controller in *page*
coordinates - the space the window was rasterised in - so they go through the very
transform the image just went through, which is what keeps a passage on its own line
when the item letterboxes or rescales the page, and what saves a second mapping from
the document to the screen.  The wash is painted *over* the text because the text is a
bitmap by this point; that is why it carries an alpha and why the palette has a floor
to hold it to (FR-090).
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
    selectionRectsChanged = Signal()
    selectionColorChanged = Signal()

    def __init__(self, parent: object | None = None) -> None:
        super().__init__(parent)
        self._image = QImage()
        self._background = QColor("#ffffff")
        self._page_size = QSizeF()
        self._pan = 0.0
        self._selection_rects: list[QRectF] = []
        self._selection_color = QColor(0, 0, 0, 0)
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

    def _get_selection_rects(self) -> list[QRectF]:
        return self._selection_rects

    def _set_selection_rects(self, rects: object) -> None:
        """The bands of the current selection, in the page's own coordinates (FR-070).

        QML hands these over straight from the controller; an empty list - or ``null``
        before the controller is injected - simply means nothing is selected.
        """
        if rects is None:
            rects = []
        clean = [QRectF(rect) for rect in rects]  # type: ignore[union-attr]
        if clean == self._selection_rects:
            return
        self._selection_rects = clean
        self.selectionRectsChanged.emit()
        self.update()

    selectionRects = Property(list, _get_selection_rects, _set_selection_rects,
                              notify=selectionRectsChanged)

    def _get_selection_color(self) -> QColor:
        return self._selection_color

    def _set_selection_color(self, color: QColor) -> None:
        if color is None:
            color = QColor(0, 0, 0, 0)
        if color == self._selection_color:
            return
        self._selection_color = QColor(color)
        self.selectionColorChanged.emit()
        self.update()

    selectionColor = Property(QColor, _get_selection_color, _set_selection_color,
                              notify=selectionColorChanged)

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
        self._paint_selection(painter, target, page)

    def _paint_selection(self, painter: QPainter, target: QRectF, page: QSizeF) -> None:
        """Wash the selected bands over the text that has just been drawn (FR-070).

        The bands are in page coordinates, and the image has just been mapped from that
        space into ``target`` by a uniform scale - so the same scale and the same origin
        put a band where its line is, whether the page is at its natural size or has been
        letterboxed into a window the layout has not caught up with yet.
        """
        if not self._selection_rects or page.isEmpty() or self._selection_color.alpha() == 0:
            return
        scale = target.width() / page.width()
        painter.save()
        # Nothing of the page is outside the page box, so a band that runs to the edge of
        # the column is cut off there rather than drawn into the margin beside it.
        painter.setClipRect(target)
        for rect in self._selection_rects:
            painter.fillRect(
                QRectF(
                    target.left() + rect.x() * scale,
                    target.top() + rect.y() * scale,
                    rect.width() * scale,
                    rect.height() * scale,
                ),
                self._selection_color,
            )
        painter.restore()

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
