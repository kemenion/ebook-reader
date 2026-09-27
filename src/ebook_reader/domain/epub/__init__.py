"""EPUB container parsing (container.xml, OPF, NCX/nav, path and image probing)."""

from __future__ import annotations

from .book import EpubBook
from .opf import Package, parse_package

__all__ = ["EpubBook", "Package", "parse_package"]
