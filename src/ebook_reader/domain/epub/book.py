"""``EpubBook``: the single entry point the rest of the application uses.

The archive stays open and is read on demand, so opening an 85 MB book with 408
images costs almost nothing: nothing is extracted to disk and no pixels are
decoded here (FR-007 / I-3).
"""

from __future__ import annotations

import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Iterator

from ..errors import BookError, MissingResourceError, NotAnEpubError, PackageError
from ..html.kinsoku import strip_word_joiners
from ..models import Block, BookMeta, Landmark, ManifestItem, SpineItem, TocEntry
from . import container, images, toc
from .opf import Package, parse_package
from .paths import join_href, normalize, split_fragment

__all__ = ["EpubBook"]

_IMAGE_MEDIA_PREFIX = "image/"


def _base_dir_of(href: str) -> str:
    """The directory an href lives in, which relative hrefs resolve against."""
    return href.rsplit("/", 1)[0] if "/" in href else ""

#: How much of an image entry is decompressed to find its frame header.  Large
#: enough for JFIF/Exif application segments, small enough to stay cheap.
_IMAGE_HEAD_BYTES = 16 * 1024

#: How much of a section is read to find the line that names it (FR-012).
#:
#: The name of a section is the first thing it says, and in a book split by a
#: converter that line sits a few hundred bytes into the document.  Reading only the
#: heads of the reference omnibus costs 36-45 ms; parsing its 135 sections whole costs
#: 260 ms, a fifth of the cold start budget (NFR-001) spent before the reader has
#: asked to go anywhere.
_LABEL_SNIFF_BYTES = 1024

#: Longest unmarked first line that still reads as a name rather than as prose.
#: Chinese chapter titles run to about twenty-five characters; a photo caption is a
#: sentence and says nothing about where the reader is.
_LABEL_MAX_CHARS = 40

#: A line that ends like this is a sentence or a dangling label, not a name: a photo
#: caption («…每日通勤的自行车。») and a masthead («出版者：») both end here.
_LABEL_BAD_ENDING = "。！？；：，、,.;:!?"

#: A name does not contain sentence punctuation at all - a title with a full stop in
#: the middle is a sentence that happens to be short.
_LABEL_INNER_PROSE = ("。", "！", "？", "；")


def _clean(text: str) -> str:
    """Collapse whitespace and drop the layout-only word joiners (ADR-005 / FR-109)."""
    return " ".join(strip_word_joiners(text).split())


class EpubBook:
    """An opened EPUB archive with lazily parsed content."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._zip: zipfile.ZipFile | None = None
        self._package: Package | None = None
        self._toc: tuple[TocEntry, ...] | None = None
        self._landmarks: tuple[Landmark, ...] | None = None
        self._name_lookup: dict[str, str] | None = None
        self._block_cache: dict[int, tuple[Block, ...]] = {}
        self._probe_cache: dict[str, tuple[int, int] | None] = {}

    # ----------------------------------------------------------------- opening

    @classmethod
    def open(cls, path: str | Path) -> "EpubBook":
        """Open *path* and validate that it really is an EPUB package."""
        book = cls(path)
        book.package  # forces container.xml + OPF parsing, raising BookError early
        return book

    def _ensure_open(self) -> zipfile.ZipFile:
        if self._zip is not None:
            return self._zip
        if not self.path.is_file():
            raise BookError(f"file not found: {self.path}")
        try:
            archive = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise NotAnEpubError(f"{self.path.name} is not a ZIP archive") from exc
        self._zip = archive
        return archive

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def __enter__(self) -> "EpubBook":
        self._ensure_open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # --------------------------------------------------------------- resources

    @property
    def package(self) -> Package:
        """Parsed OPF, produced on first access."""
        if self._package is None:
            archive = self._ensure_open()
            opf_path = container.find_package_path(self._read_or_key_error)
            if opf_path not in archive.namelist():
                raise PackageError(f"package document not found: {opf_path}")
            self._package = parse_package(archive.read(opf_path), opf_path)
        return self._package

    @property
    def meta(self) -> BookMeta:
        return self.package.meta

    @property
    def spine(self) -> tuple[SpineItem, ...]:
        return self.package.spine

    @property
    def manifest(self) -> dict[str, ManifestItem]:
        return self.package.manifest

    @property
    def section_count(self) -> int:
        return len(self.spine)

    def _read_or_key_error(self, name: str) -> bytes:
        archive = self._ensure_open()
        return archive.read(self._resolve_entry(name))

    def _resolve_entry(self, name: str) -> str:
        """Map a normalised entry name to a real entry, tolerating case drift."""
        archive = self._ensure_open()
        if self._name_lookup is None:
            lookup: dict[str, str] = {}
            for entry in archive.infolist():
                lookup.setdefault(entry.filename, entry.filename)
                lookup.setdefault(entry.filename.lower(), entry.filename)
            self._name_lookup = lookup
        entry = self._name_lookup.get(name) or self._name_lookup.get(name.lower())
        if entry is None:
            raise MissingResourceError(name)
        return entry

    def resource(self, name: str) -> bytes:
        """Read one archive entry by its normalised path."""
        return self._read_or_key_error(normalize(name))

    def has_resource(self, name: str) -> bool:
        try:
            self._resolve_entry(normalize(name))
        except MissingResourceError:
            return False
        return True

    def image_names(self) -> tuple[str, ...]:
        return tuple(
            join_href(self.package.base_dir, item.href)
            for item in self.manifest.values()
            if item.media_type.startswith(_IMAGE_MEDIA_PREFIX)
        )

    # ---------------------------------------------------------------- sections

    def section_bytes(self, index: int) -> bytes:
        """Raw XHTML bytes of one spine document."""
        return self.resource(self.spine[index].href)

    def section_base_dir(self, index: int) -> str:
        """Directory that relative hrefs inside a section resolve against."""
        return _base_dir_of(self.spine[index].href)

    def resolve_in_section(self, index: int, href: str) -> str:
        return join_href(self.section_base_dir(index), href)

    def image_size(self, name: str) -> tuple[int, int] | None:
        """Intrinsic size of an archive image, probed from its header only.

        Only the beginning of each entry is decompressed.  Reading whole entries
        just to inspect a header costs about 24x more on the reference book
        (383 ms versus 16 ms for its 408 images, which total 85 MB), so a bounded
        head is read first and the full entry is touched only if the head was too
        short to contain the frame header.
        """
        if name not in self._probe_cache:
            self._probe_cache[name] = self._probe_image(name)
        return self._probe_cache[name]

    def _probe_image(self, name: str) -> tuple[int, int] | None:
        normalized = normalize(name)
        try:
            head = self._read_head(normalized, _IMAGE_HEAD_BYTES)
        except BookError:
            return None
        size = images.probe_size(head)
        if size is not None or len(head) < _IMAGE_HEAD_BYTES:
            return size
        # A huge EXIF block may push the frame header past the head window.
        try:
            return images.probe_size(self.resource(normalized))
        except BookError:
            return None

    def _read_head(self, name: str, limit: int) -> bytes:
        """Read at most *limit* bytes of one entry, without decompressing it all."""
        archive = self._ensure_open()
        entry = self._resolve_entry(name)
        info = archive.getinfo(entry)
        if info.file_size <= limit:
            return archive.read(entry)
        with archive.open(entry) as stream:
            return stream.read(limit)


    # ----------------------------------------------------------------- content

    def blocks(self, index: int) -> tuple[Block, ...]:
        """Normalised block list of one section, parsed once and cached."""
        cached = self._block_cache.get(index)
        if cached is None:
            cached = self._parse_blocks(index)
            self._block_cache[index] = cached
        return cached

    def _parse_blocks(self, index: int) -> tuple[Block, ...]:
        return self._sanitize(self.section_bytes(index), self.section_base_dir(index))

    def _sanitize(self, raw: bytes, base_dir: str) -> tuple[Block, ...]:
        # Imported lazily so that merely opening a book does not pay for it.
        from ..html.sanitizer import sanitize

        return sanitize(raw, base_dir=base_dir, probe=self.image_size)

    def drop_blocks_cache(self) -> None:
        self._block_cache.clear()

    def iter_blocks(self) -> Iterator[tuple[int, tuple[Block, ...]]]:
        for index in range(self.section_count):
            yield index, self.blocks(index)

    # --------------------------------------------------------------------- toc

    @property
    def toc(self) -> tuple[TocEntry, ...]:
        """The book's contents, with a row for every section it leaves unnamed (FR-012)."""
        if self._toc is None:
            self._toc = self._build_toc()
        return self._toc

    def _build_toc(self) -> tuple[TocEntry, ...]:
        """What the book says its parts are - EPUB 3 navigation first, NCX second.

        Whatever it says, the reading order is longer than that: the reference
        omnibus names three volumes in its NCX and is made of 135 sections, and 币安
        leaves 11 of its 35 unnamed.  ``]`` walks all of them, so the contents column
        shows all of them too - the same list, out of the same book, either named by
        the publisher or named by the section itself (see :meth:`_fill_unnamed`).
        """
        package = self.package
        for href, parser in ((package.nav_href, toc.parse_nav), (package.ncx_href, toc.parse_ncx)):
            if not href or not self.has_resource(href):
                continue
            entries = parser(self.resource(href), _base_dir_of(href))
            if entries:
                return self._fill_unnamed(entries)
        return self._fill_unnamed(())

    def _fill_unnamed(self, entries: tuple[TocEntry, ...]) -> tuple[TocEntry, ...]:
        """Add a row for every section of the reading order that no row names (FR-012).

        Each added row is spliced in between the rows that surround it, so the list
        stays in reading order - which is the property the highlight and 「下一章」both
        read it for - and the tree the book wrote keeps its shape: only the lines it
        left out are added, inside the part of the tree they belong to and at that
        part's own level.

        A ``linear="no"`` document is skipped: it is not part of the order the reader
        walks, which is also why 「下一章」steps over it (FR-016).

        ``entries`` is empty for a book with no navigation at all, and the result is
        then one row per nameable section - the fallback of FR-012, from the same rule
        as everything else rather than from a rule of its own.
        """
        named: set[int] = set()

        def note(rows: tuple[TocEntry, ...]) -> None:
            for row in rows:
                index = self.section_index_for_href(row.href)
                if index >= 0:
                    named.add(index)
                note(row.children)

        note(entries)
        pending: list[tuple[int, TocEntry]] = []
        for index in range(self.section_count):
            if index in named or not self.spine[index].linear:
                continue
            title = self._section_label(index)
            if title:
                pending.append((index, TocEntry(title=title, href=self.spine[index].href)))
        if not pending:
            return entries

        taken = 0

        def splice(
            rows: tuple[TocEntry, ...], level: int, bound: int
        ) -> tuple[TocEntry, ...]:
            """*rows* with the sections between them added at *level*; *bound* ends the list."""
            nonlocal taken
            out: list[TocEntry] = []
            for position, row in enumerate(rows):
                start = self._subtree_start(row)
                if start >= 0:
                    while taken < len(pending) and pending[taken][0] < start:
                        out.append(replace(pending[taken][1], level=level))
                        taken += 1
                if row.children:
                    # A part takes the sections up to where the row after it begins - a
                    # row without children takes none, or the last chapter of a part
                    # would collect every section that follows it inside the part.
                    after = (
                        self._subtree_start(rows[position + 1]) if position + 1 < len(rows) else -1
                    )
                    row = replace(
                        row,
                        children=splice(row.children, level + 1, after if after >= 0 else bound),
                    )
                out.append(row)
            while taken < len(pending) and pending[taken][0] < bound:
                out.append(replace(pending[taken][1], level=level))
                taken += 1
            return tuple(out)

        return splice(entries, 0, self.section_count)

    @property
    def landmarks(self) -> tuple[Landmark, ...]:
        """Where the book says its parts begin; empty when it does not say (FR-009).

        Only EPUB 3 books with a ``landmarks`` navigation have them, which is none of
        the three reference books.
        """
        if self._landmarks is None:
            self._landmarks = self._read_landmarks()
        return self._landmarks

    def _read_landmarks(self) -> tuple[Landmark, ...]:
        nav_href = self.package.nav_href
        if not nav_href or not self.has_resource(nav_href):
            return ()
        return toc.parse_landmarks(self.resource(nav_href), _base_dir_of(nav_href))

    def _subtree_start(self, row: TocEntry) -> int:
        """Where *row* begins in the spine, counting its children (-1 when nowhere)."""
        starts = [self.section_index_for_href(row.href)]
        starts.extend(self._subtree_start(child) for child in row.children)
        found = [start for start in starts if start >= 0]
        return min(found) if found else -1

    def _section_label(self, index: int) -> str:
        """The name a section gives itself, or ``""`` when it gives none (FR-012).

        A section is named by the first thing it says: a heading when the publisher
        marked one, and otherwise its first line of text - which is how a book split
        by a converter writes its chapter titles ("第1章 业余投资者比专业投资者业绩更好" is
        an ordinary ``<p>`` in 林奇).  That name is what the contents row shows, and
        what the 「下一章」line shows when the reader reaches the end of a section
        (FR-074).

        Only the head of the document is looked at (:data:`_LABEL_SNIFF_BYTES`), which
        is where a name lives.  The one case that pays for a whole parse is a head
        that ran out on the very block that would name the section - that block may
        have been cut in half by the reading window - and the document is then read in
        full, which is the parse the reader is about to need anyway: it is where they
        are being led.
        """
        head = self._read_head(self.spine[index].href, _LABEL_SNIFF_BYTES)
        if len(head) < _LABEL_SNIFF_BYTES:
            return self._first_label(self.blocks(index))
        blocks = self._sanitize(head, self.section_base_dir(index))
        position = next((at for at, block in enumerate(blocks) if block.text.strip()), -1)
        if 0 <= position < len(blocks) - 1:
            return self._label_of(blocks[position])
        return self._first_label(self.blocks(index))

    @classmethod
    def _first_label(cls, blocks: tuple[Block, ...]) -> str:
        """The name in *blocks*: the first line of theirs that says something."""
        for block in blocks:
            if block.text.strip():
                return cls._label_of(block)
        return ""

    @staticmethod
    def _label_of(block: Block) -> str:
        """*block* as a row title, or ``""`` when it reads as prose rather than a name.

        A heading is a name whatever it says - the publisher marked it as one.  A
        plain paragraph has to read like one: short, and without the punctuation of a
        sentence.  A photo caption («2017年10月，东京工作时期每日通勤的自行车。») and a masthead
        («出版者：») therefore leave their section unnamed instead of filling the column
        with lines that cannot help anyone find their place.

        The layout-only word joiners (ADR-005) come off here, because this text goes
        into a row in a list rather than into a line of the column, and an invisible
        character in a label is what defect 11 was about (FR-109).
        """
        if block.is_heading:
            return _clean(block.text)
        text = _clean(block.text)
        if not text or len(text) > _LABEL_MAX_CHARS:
            return ""
        if text[-1] in _LABEL_BAD_ENDING or any(stop in text for stop in _LABEL_INNER_PROSE):
            return ""
        return text

    def section_index_for_href(self, href: str, *, current: int = -1) -> int:
        """Find the spine index that owns *href*, ignoring any fragment, or ``-1``.

        The fragment is dropped rather than matched because it names a place *inside*
        the document, and this method answers which document that is; whether the row
        then lands at the top of it or at an anchor in it is a second question, asked of
        the laid-out section once it exists (FR-014).

        *current* answers the one href that names no document: ``#note3`` is a place in
        the document the link was written in, and a reader that knows which section it
        is showing can resolve it.  The default is ``-1`` because a caller without that
        context - the table of contents, whose rows live in the navigation document -
        has no place to send such a link, and guessing the first section would be worse
        than not following it.
        """
        path, fragment = split_fragment(href)
        target = normalize(path)
        if not target:
            return current if fragment and 0 <= current < self.section_count else -1
        for item in self.spine:
            if item.href == target:
                return item.index
        return -1

    def next_section(self, index: int) -> int:
        """Next section of the reading order after *index*, or ``-1``.

        A ``linear="no"`` document sits in the spine but not in the text - a cover, an
        advertisement, a fold-out map - and paging into one is paging into something
        the publisher said was not the book.  None of the three reference books marks a
        document this way, so this rule is exercised by a book written in the tests.
        """
        return self._step_section(index, 1)

    def previous_section(self, index: int) -> int:
        """Previous section of the reading order before *index*, or ``-1``."""
        return self._step_section(index, -1)

    def _step_section(self, index: int, delta: int) -> int:
        target = index + delta
        while 0 <= target < self.section_count:
            if self.spine[target].linear:
                return target
            target += delta
        return -1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        stored = self._package
        return f"<EpubBook {self.path.name!r} sections={len(stored.spine) if stored else 0}>"

