"""Pagination and page-break repair (ADR-004 / ADR-007).

Qt paginates a ``QTextDocument`` by slicing it at the page height.  That is
purely geometric: a 900x1400 figure straddling a page boundary is *cut in half*,
which is the one thing a paper book never does.

Qt exposes no keep-together or keep-with-next facility, so the repair works
geometrically: every block that straddles a boundary gets
``PageBreak_AlwaysBefore`` and the document is laid out again.  Each pass pushes
at least one block onto the next page, so the loop terminates; the iteration cap
is belt-and-braces against a pathological layout, and on hitting it we keep the
last usable result rather than failing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from PySide6.QtCore import QSizeF
from PySide6.QtGui import QTextBlockFormat, QTextCursor, QTextDocument

from ..domain.models import Block, BlockKind

__all__ = ["PageBreakRepair", "Pagination", "paginate"]

_log = logging.getLogger(__name__)

#: Block kinds that must not be split across a page boundary.
KEEP_TOGETHER = frozenset({BlockKind.IMAGE, BlockKind.HEADING, BlockKind.RULE})

#: A heading whose following block starts on another page looks orphaned, so it
#: is pushed along with its paragraph (FR-053).
KEEP_WITH_NEXT = frozenset({BlockKind.HEADING})

_MAX_ITERATIONS = 8


@dataclass(slots=True)
class PageBreakRepair:
    """Diagnostics for one pagination run."""

    iterations: int = 0
    pushed_blocks: tuple[int, ...] = ()
    converged: bool = True


@dataclass(slots=True)
class Pagination:
    page_count: int
    repair: PageBreakRepair = field(default_factory=PageBreakRepair)


def paginate(
    document: QTextDocument,
    blocks: tuple[Block, ...],
    page_size: QSizeF,
    *,
    max_iterations: int = _MAX_ITERATIONS,
) -> Pagination:
    """Lay *document* out on pages of *page_size*, repairing split blocks."""
    document.setPageSize(page_size)
    layout = document.documentLayout()
    page_height = max(1.0, page_size.height())

    pushed: set[int] = set()
    iterations = 0
    converged = False

    while iterations < max_iterations:
        iterations += 1
        layout = document.documentLayout()
        culprits: list[int] = []

        for index, block in enumerate(blocks):
            if index in pushed:
                continue
            if not _needs_geometry_check(block):
                continue
            text_block = document.findBlockByNumber(index)
            if not text_block.isValid():
                continue
            rect = layout.blockBoundingRect(text_block)
            if rect.height() > page_height:
                # Cannot fit on any page; pushing it would loop forever.
                continue
            first_page = int(rect.top() // page_height)
            last_page = int(max(rect.top(), rect.bottom() - 0.5) // page_height)
            if last_page > first_page:
                culprits.append(index)
            elif block.kind in KEEP_WITH_NEXT:
                following = document.findBlockByNumber(index + 1)
                if following.isValid() and layout.blockBoundingRect(following).top() // page_height > first_page:
                    culprits.append(index)

        if not culprits:
            converged = True
            break

        for index in culprits:
            _force_break_before(document, index)
            pushed.add(index)
        # Block formats were changed, so Qt relayouts on the next page query.
        document.pageCount()

    document.pageCount()  # settle the layout before reporting
    return Pagination(
        page_count=max(1, document.pageCount()),
        repair=PageBreakRepair(
            iterations=iterations,
            pushed_blocks=tuple(sorted(pushed)),
            converged=converged,
        ),
    )


def _needs_geometry_check(block: Block) -> bool:
    if block.kind in KEEP_TOGETHER:
        return True
    return block.kind in KEEP_WITH_NEXT


def _force_break_before(document: QTextDocument, index: int) -> None:
    block = document.findBlockByNumber(index)
    if not block.isValid():
        return
    cursor = QTextCursor(block)
    fmt = cursor.blockFormat()
    if fmt.pageBreakPolicy() & QTextBlockFormat.PageBreak_AlwaysBefore:
        return
    fmt.setPageBreakPolicy(fmt.pageBreakPolicy() | QTextBlockFormat.PageBreak_AlwaysBefore)
    cursor.setBlockFormat(fmt)
