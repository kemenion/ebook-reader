"""Typesetting layer: block model to continuously laid-out, rasterised windows.

This is the only layer besides ``app`` that may import QtGui.
"""

from __future__ import annotations

from .engine import (
    DEFAULT_SECTION_CACHE,
    WINDOW_QUANTUM,
    LaidOutSection,
    LayoutEngine,
    quantise_offset,
)
from .images import ImageCache
from .settings import (
    FontChoice,
    PageGeometry,
    Theme,
    ThemeColors,
    TypographySettings,
)

__all__ = [
    "DEFAULT_SECTION_CACHE",
    "FontChoice",
    "ImageCache",
    "LaidOutSection",
    "LayoutEngine",
    "PageGeometry",
    "Theme",
    "ThemeColors",
    "TypographySettings",
    "WINDOW_QUANTUM",
    "quantise_offset",
]
