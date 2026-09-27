"""HTML normalisation: turn dirty publisher XHTML into our own block model.

The reference book is hostile input: 4044 ``<b>``, 2303 ``<span>``, 554 ``<div>``
wrappers, inline styles naming fonts that do not exist on Linux (``PingFang SC``,
``FZFangSong-Z02``) and images referenced from ``<div><img></div>`` pairs.  The
sanitiser therefore never defers to Qt's HTML parser; it builds an explicit,
inspectable block list instead (ADR-002).
"""

from __future__ import annotations

from .kinsoku import protect_text
from .sanitizer import sanitize

__all__ = ["protect_text", "sanitize"]
