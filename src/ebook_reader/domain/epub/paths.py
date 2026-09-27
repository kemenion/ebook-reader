"""Package-relative path resolution inside an EPUB archive (FR-005 / FR-006).

EPUB hrefs are URIs: they may be percent-encoded, may carry a fragment or query,
and are relative to the directory holding the OPF package document.  ZIP entry
names, by contrast, are plain (already decoded) POSIX paths.  Every conversion
between the two happens here.
"""

from __future__ import annotations

import posixpath
from urllib.parse import unquote

__all__ = ["href_path", "join_href", "normalize", "split_fragment"]


def split_fragment(href: str) -> tuple[str, str]:
    """Split ``"chapter.xhtml#note1"`` into ``("chapter.xhtml", "note1")``."""
    path, _, fragment = href.partition("#")
    return path, fragment


def normalize(path: str) -> str:
    """Normalise a ZIP entry name so lookups are stable.

    Handles ``./``, ``../``, backslashes and repeated slashes.  A leading ``/``
    is dropped because ZIP entry names are relative.
    """
    path = path.replace("\\", "/")
    path = path.lstrip("/")
    if not path:
        return ""
    normalized = posixpath.normpath(path)
    return "" if normalized == "." else normalized


def join_href(base_dir: str, href: str) -> str:
    """Resolve *href* relative to *base_dir* and percent-decode it.

    ``base_dir`` is the directory of the OPF package document (``""`` when the
    package sits at the archive root).  Fragments and query strings are dropped
    because they are not part of the ZIP entry name.
    """
    if not href:
        return ""
    decoded = unquote(href)
    path, _ = split_fragment(decoded)
    path = path.split("?", 1)[0]
    if not path:
        return ""
    if path.startswith("/"):
        return normalize(path)
    if not base_dir:
        return normalize(path)
    return normalize(posixpath.join(base_dir, path))


def href_path(href: str) -> str:
    """Return the path part of an href, percent-decoded, without normalising."""
    decoded = unquote(href)
    path, _ = split_fragment(decoded)
    return path.split("?", 1)[0]
