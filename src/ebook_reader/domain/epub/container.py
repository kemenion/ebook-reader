"""Locate the OPF package document via ``META-INF/container.xml`` (FR-002)."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from ..errors import NotAnEpubError, PackageError
from .paths import normalize

__all__ = ["find_package_path"]

CONTAINER_PATH = "META-INF/container.xml"
_CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"


def find_package_path(read_entry) -> str:
    """Return the normalised ZIP path of the OPF package document.

    *read_entry* is a callable ``name -> bytes`` that raises ``KeyError`` when the
    entry is absent; it is passed in so this module stays independent of the
    archive wrapper used by the caller.
    """
    try:
        raw = read_entry(CONTAINER_PATH)
    except KeyError as exc:  # pragma: no cover - exercised via EpubBook
        raise NotAnEpubError(f"missing {CONTAINER_PATH}") from exc

    try:
        root = ET.fromstring(raw)
    except (ET.ParseError, ValueError) as exc:
        # ``ValueError`` is a declared encoding expat does not implement.  This document
        # holds nothing but paths, so there is nothing to decode it for; what matters is
        # that the failure arrives as a readable error rather than a traceback (FR-008).
        raise NotAnEpubError(f"{CONTAINER_PATH} is not valid XML: {exc}") from exc

    rootfile = root.find(f".//{{{_CONTAINER_NS}}}rootfile")
    if rootfile is None:
        # Some hand-made archives omit the namespace; fall back to a tag scan.
        rootfile = next(
            (element for element in root.iter() if element.tag.endswith("rootfile")),
            None,
        )
    if rootfile is None:
        raise NotAnEpubError("container.xml declares no rootfile")

    full_path = rootfile.get("full-path") or ""
    if not full_path:
        raise NotAnEpubError("container.xml rootfile has no full-path")
    return normalize(full_path)


def package_base_dir(package_path: str) -> str:
    """Return the directory that relative hrefs are resolved against."""
    return package_path.rsplit("/", 1)[0] if "/" in package_path else ""
