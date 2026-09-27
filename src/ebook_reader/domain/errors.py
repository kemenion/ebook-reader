"""Exception hierarchy for the domain layer.

All errors raised while reading a book derive from :class:`BookError` so the UI
can present a readable message instead of a traceback (FR-008 / NFR-020).
"""

from __future__ import annotations


class BookError(Exception):
    """Base class for every recoverable book problem."""


class NotAnEpubError(BookError):
    """The file is not a ZIP archive or has no META-INF/container.xml."""


class PackageError(BookError):
    """The OPF package document is missing or malformed."""


class MissingResourceError(BookError):
    """A manifest/spine entry points at a ZIP entry that does not exist."""

    def __init__(self, path: str) -> None:
        super().__init__(f"resource not found in archive: {path}")
        self.path = path


class UnsupportedFormatError(BookError):
    """The media type is known to be unsupported (e.g. MOBI)."""
