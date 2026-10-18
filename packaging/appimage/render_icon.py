#!/usr/bin/env python3
"""Rasterise ``ebook-reader.svg`` to the 256×256 PNG an AppDir expects.

QtSvg ships with PySide6, which the bundle already requires, so this avoids
carrying a second image tool.  Running Qt offscreen keeps the build usable over
SSH without a display.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Must be set before PySide6 creates the platform integration.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

SIZE = 256


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"用法：{Path(sys.argv[0]).name} SOURCE.svg TARGET.png", file=sys.stderr)
        return 2

    source, target = (Path(value) for value in argv)
    # Keep the application object alive until the render finishes.
    app = QGuiApplication.instance() or QGuiApplication([])

    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        print(f"无法读取 SVG 图标：{source}", file=sys.stderr)
        return 1

    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, SIZE, SIZE))
    painter.end()

    target.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(target), "PNG"):
        print(f"无法写入 PNG：{target}", file=sys.stderr)
        return 1

    # Keep the Qt application object alive through the write above, and let
    # static analysis see that it is intentional.
    app.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
