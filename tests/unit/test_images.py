"""Image header probing (ADR-006).

The 24x speed-up of head-only probing rests on it returning exactly the same
dimensions as a full read, so that equivalence is asserted against the real
archive rather than a fixture.
"""

from __future__ import annotations

import struct
import zlib

from ebook_reader.domain.epub.images import probe_size


def _png(width: int, height: int) -> bytes:
    header = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    chunk = struct.pack(">I", len(ihdr)) + b"IHDR" + ihdr + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    return header + chunk + b"\x00" * 32


def _jpeg_with_app0(width: int, height: int) -> bytes:
    """Minimal JPEG: SOI, a JFIF APP0 segment, then a baseline SOF0."""
    soi = b"\xff\xd8"
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00" + struct.pack(">HH", 72, 72) + b"\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", height, width) + b"\x03" + b"\x00" * 9
    return soi + app0 + sof0 + b"\xff\xd9"


def test_png_dimensions_are_read() -> None:
    assert probe_size(_png(1234, 567)) == (1234, 567)


def test_jpeg_dimensions_are_read_past_an_app0_segment() -> None:
    assert probe_size(_jpeg_with_app0(900, 1400)) == (900, 1400)


def test_gif_dimensions_are_read() -> None:
    data = b"GIF89a" + struct.pack("<HH", 640, 480) + b"\x00" * 16
    assert probe_size(data) == (640, 480)


def test_bmp_dimensions_are_read() -> None:
    data = b"BM" + b"\x00" * 16 + struct.pack("<ii", 800, 600) + b"\x00" * 8
    assert probe_size(data) == (800, 600)


def test_progressive_jpeg_is_understood() -> None:
    sof2 = b"\xff\xc2" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", 300, 200) + b"\x03" + b"\x00" * 9
    assert probe_size(b"\xff\xd8" + sof2) == (200, 300)


def test_garbage_returns_none_instead_of_raising() -> None:
    for blob in (b"", b"not an image", b"\x89PNG\r\n\x1a\nshort", b"\xff\xd8" + b"\xff\xc0"):
        assert probe_size(blob) is None


def test_head_probe_matches_full_read_for_every_archive_image(kangpo) -> None:
    """The optimisation must be exactly equivalent, not approximately."""
    from ebook_reader.domain.epub.images import probe_size as probe

    names = kangpo.image_names()
    assert len(names) == 408
    for name in names:
        assert kangpo.image_size(name) == probe(kangpo.resource(name)), name
