"""Read image intrinsic dimensions from file headers, without decoding pixels.

Decoding all 408 images of the reference book would need roughly 2 GB of RAM
(``408 x 900 x 1400 x 4`` bytes), so layout only ever learns the aspect ratio
here and the pixels are decoded later, on demand (ADR-006 / I-3).

Only the first few hundred bytes of each file are inspected, which keeps probing
408 images in the tens of milliseconds.
"""

from __future__ import annotations

import struct

__all__ = ["probe_size"]

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_GIF_SIGNATURES = (b"GIF87a", b"GIF89a")
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def probe_size(data: bytes) -> tuple[int, int] | None:
    """Return ``(width, height)`` for *data*, or ``None`` if unrecognised.

    Never raises: a malformed header simply yields ``None`` so that a single
    broken image cannot abort opening a book (NFR-020).
    """
    try:
        if data.startswith(_PNG_SIGNATURE):
            return _probe_png(data)
        if data[:2] == b"\xff\xd8":
            return _probe_jpeg(data)
        if data[:6] in _GIF_SIGNATURES:
            return _probe_gif(data)
        if data[:2] == b"BM":
            return _probe_bmp(data)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return _probe_webp(data)
    except (struct.error, IndexError, ValueError):
        return None
    return None


def _probe_png(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return (width, height) if width and height else None


def _probe_jpeg(data: bytes) -> tuple[int, int] | None:
    """Walk the JPEG segment chain until a Start-Of-Frame marker is found."""
    offset = 2
    end = len(data)
    while offset + 4 <= end:
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        # Padding bytes and standalone markers carry no length field.
        if marker == 0xFF:
            offset += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:
            offset += 2
            continue
        segment_length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if segment_length < 2:
            return None
        if marker in _JPEG_SOF_MARKERS:
            if offset + 9 > end:
                return None
            height = int.from_bytes(data[offset + 5 : offset + 7], "big")
            width = int.from_bytes(data[offset + 7 : offset + 9], "big")
            return (width, height) if width and height else None
        offset += 2 + segment_length
    return None


def _probe_gif(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    width, height = struct.unpack("<HH", data[6:10])
    return (width, height) if width and height else None


def _probe_bmp(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26:
        return None
    width, height = struct.unpack("<ii", data[18:26])
    width, height = abs(width), abs(height)
    return (width, height) if width and height else None


def _probe_webp(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return (width, height)
    if chunk == b"VP8 " and len(data) >= 30:
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return (width, height) if width and height else None
    if chunk == b"VP8L" and len(data) >= 25:
        bits = int.from_bytes(data[21:25], "little")
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
        return (width, height)
    return None
