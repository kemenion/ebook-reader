"""Lazy, bounded image decoding (ADR-006).

Decoding all 408 images of the reference book at full resolution needs about
2 GB, so three rules apply:

1. images are decoded only when a page that shows them is actually painted;
2. they are decoded **at the size they will be displayed** (via
   ``QImageReader.setScaledSize``, which lets libjpeg use its DCT scaling instead
   of decoding at full size and shrinking afterwards);
3. the result sits in an LRU cache bounded by both item count and byte size.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize
from PySide6.QtGui import QImage, QImageReader

__all__ = ["ImageCache", "ImageCacheStats"]


@dataclass(frozen=True, slots=True)
class ImageCacheStats:
    items: int
    bytes: int
    hits: int
    misses: int
    decodes: int
    failures: int


class ImageCache:
    """LRU cache of decoded images, keyed by whatever identifier the caller uses."""

    __slots__ = (
        "_loader",
        "_max_items",
        "_max_bytes",
        "_device_ratio",
        "_entries",
        "_bytes",
        "_hits",
        "_misses",
        "_decodes",
        "_failures",
        "last_error",
    )

    def __init__(
        self,
        loader: Callable[[str], bytes],
        *,
        max_items: int = 24,
        max_bytes: int = 32 * 1024 * 1024,
        device_ratio: float = 1.0,
    ) -> None:
        self._loader = loader
        self._max_items = max(1, max_items)
        self._max_bytes = max(1, max_bytes)
        self._device_ratio = device_ratio if device_ratio and device_ratio > 0 else 1.0
        self._entries: OrderedDict[str, QImage] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._decodes = 0
        self._failures = 0
        #: Diagnostic for the most recent failed decode; surfaced by tests.
        self.last_error: str = ""

    # ------------------------------------------------------------------ config

    @property
    def device_ratio(self) -> float:
        return self._device_ratio

    @device_ratio.setter
    def device_ratio(self, value: float) -> None:
        value = value if value and value > 0 else 1.0
        if abs(value - self._device_ratio) > 1e-6:
            self._device_ratio = value
            self.clear()

    # ------------------------------------------------------------------ access

    def image(self, key: str, display_size: QSizeF | None = None) -> QImage | None:
        """Return the image for *key*, decoded for *display_size* logical pixels.

        Returns ``None`` when the resource is missing or cannot be decoded, so a
        single broken image never breaks a page (NFR-020).
        """
        cached = self._entries.get(key)
        if cached is not None:
            self._entries.move_to_end(key)
            self._hits += 1
            return cached

        self._misses += 1
        image = self._decode(key, display_size)
        if image is None:
            self._failures += 1
            return None

        self._entries[key] = image
        self._bytes += _image_bytes(image)
        self._evict()
        return image

    def forget(self, key: str) -> None:
        image = self._entries.pop(key, None)
        if image is not None:
            self._bytes -= _image_bytes(image)

    def clear(self) -> None:
        self._entries.clear()
        self._bytes = 0

    def stats(self) -> ImageCacheStats:
        return ImageCacheStats(
            items=len(self._entries),
            bytes=self._bytes,
            hits=self._hits,
            misses=self._misses,
            decodes=self._decodes,
            failures=self._failures,
        )

    # ----------------------------------------------------------------- internals

    def _decode(self, key: str, display_size: QSizeF | None) -> QImage | None:
        try:
            raw = self._loader(key)
        except Exception as exc:  # noqa: BLE001 - a missing image must not break a page
            self.last_error = f"{key}: {exc}"
            return None
        if not raw:
            self.last_error = f"{key}: empty payload"
            return None

        self._decodes += 1
        payload = QByteArray(raw)
        buffer = QBuffer()
        # ``setData`` copies, whereas ``QBuffer(QByteArray)`` would take a pointer
        # to a temporary that Python frees immediately (a classic PySide pitfall
        # that shows up as "every image fails to decode").
        buffer.setData(payload)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setAutoTransform(True)  # honour EXIF orientation

        if display_size is not None and display_size.isValid() and not display_size.isEmpty():
            source = reader.size()
            target = QSize(
                max(1, round(display_size.width() * self._device_ratio)),
                max(1, round(display_size.height() * self._device_ratio)),
            )
            if source.isValid() and source.width() > 0 and source.height() > 0:
                # Never scale up: a blurry upscale is worse than a smaller bitmap.
                if target.width() > source.width() or target.height() > source.height():
                    target = source
                if target != source:
                    reader.setScaledSize(target)

        image = reader.read()
        if image.isNull():
            return None
        image.setDevicePixelRatio(self._device_ratio)
        return image

    def _evict(self) -> None:
        while self._entries and (
            len(self._entries) > self._max_items or self._bytes > self._max_bytes
        ):
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= _image_bytes(evicted)


def _image_bytes(image: QImage) -> int:
    return max(1, image.sizeInBytes())
