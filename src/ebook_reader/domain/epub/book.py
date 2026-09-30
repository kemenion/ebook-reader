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

#: How much of a document is read to decide whether it carries any text at all.
#:
#: A frontispiece - the page a chapter opens on, which holds one picture and nothing
#: else - is a few hundred bytes long, because the picture itself is a separate
#: archive entry.  A document longer than this window is taken to carry text in it and
#: is then left alone: what this answers is only whether a contents row is looking at a
#: page with nothing to read (FR-016 / ADR-021), never whether a page is readable.
_SILENT_DOCUMENT_BYTES = 4096

#: Deepest contents level the panel can show.  A row is indented 16 px per level
#: (``TocSidebar.qml``: ``leftPadding: 16 + rowLevel * 16``) inside a contents column that
#: is at most 320 px wide (``Main.qml``), so a row nested deeper than this would have
#: almost no room left for its own title; the outline column clamps its headings the same
#: way (:data:`ebook_reader.app.controller._OUTLINE_MAX_LEVEL`, FR-018).  Nothing in the
#: reference books comes near it: the deepest level read off their headings is 1.
_MAX_TOC_LEVEL = 5


def _clean(text: str) -> str:
    """Collapse whitespace and drop the layout-only word joiners (ADR-005 / FR-109)."""
    return " ".join(strip_word_joiners(text).split())


def _label_of(block: Block) -> str:
    """*block* as a row title, or ``""`` when it reads as prose rather than a name.

    A heading is a name whatever it says - the publisher marked it as one.  A plain
    paragraph has to read like one: short, and without the punctuation of a sentence.
    A photo caption («2017年10月，东京工作时期每日通勤的自行车。») and a masthead («出版者：»)
    therefore leave their section unnamed instead of filling the column with lines that
    cannot help anyone find their place.

    The layout-only word joiners (ADR-005) come off here, because this text goes into a
    row in a list rather than into a line of the column, and an invisible character in a
    label is what defect 11 was about (FR-109).
    """
    if block.is_heading:
        return _clean(block.text)
    text = _clean(block.text)
    if not text or len(text) > _LABEL_MAX_CHARS:
        return ""
    if text[-1] in _LABEL_BAD_ENDING or any(stop in text for stop in _LABEL_INNER_PROSE):
        return ""
    return text


def _walk_rows(rows: tuple[TocEntry, ...]) -> Iterator[TocEntry]:
    """Every row of a navigation tree, a parent before its children."""
    for row in rows:
        yield row
        yield from _walk_rows(row.children)


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
        self._document_cache: dict[int, tuple[Block, ...]] = {}
        self._spans: tuple[tuple[int, ...], ...] | None = None
        self._section_of_spine: dict[int, int] = {}
        self._own_rows: tuple[TocEntry, ...] | None = None
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
        return len(self._reading_sections())

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

    def section_documents(self, index: int) -> tuple[int, ...]:
        """Spine indexes behind one reading section, in reading order (FR-016).

        One document per section, except where the contents points at a page with
        nothing to read: that page is the chapter's frontispiece, and the material
        which follows it - as long as it says no name of its own - belongs to the same
        section, so that clicking the row leads to the chapter and not just to its
        title picture (ADR-021).
        """
        return self._reading_sections()[index]

    def _reading_sections(self) -> tuple[tuple[int, ...], ...]:
        """The reading order, split into sections; built once (FR-016)."""
        if self._spans is None:
            self._spans = self._split_spine()
            self._section_of_spine = {
                spine_index: section
                for section, span in enumerate(self._spans)
                for spine_index in span
            }
        return self._spans

    def _split_spine(self) -> tuple[tuple[int, ...], ...]:
        """Where one section ends and the next begins (FR-016 / ADR-021).

        The spine is one document per section, and that is what ``]`` walks.  The
        exception is a contents row that points at a document with nothing to read
        (:meth:`_says_nothing`): the row is the book's own statement that a part
        begins there, so the documents after it - while they are neither named by the
        contents nor able to name themselves - are part of that part rather than
        sections of their own.  A converted book that writes every chapter as a
        frontispiece plus its text is the shape this is for: without it the row lands
        on the picture, and the chapter itself is a section the contents never
        mentions, reachable only by paging into it.

        Only documents that would get no row of their own are absorbed - the ones
        :meth:`_fill_unnamed` has nothing to say about - so merging them cannot add,
        move or drop a row: the panel lists exactly what it listed before, and every
        row it lists now leads to something to read.
        """
        targets = {
            index
            for row in _walk_rows(self._book_rows())
            if (index := self._spine_index_of_href(row.href)) >= 0
        }
        spans: list[tuple[int, ...]] = []
        at = 0
        while at < len(self.spine):
            end = at + 1
            if at in targets and self._says_nothing(at):
                while end < len(self.spine) and self._is_absorbed(end, targets):
                    end += 1
            spans.append(tuple(range(at, end)))
            at = end
        return tuple(spans)

    def _is_absorbed(self, spine_index: int, targets: set[int]) -> bool:
        """Whether one document joins the section the row above it opened (FR-016).

        Three things have to hold, and together they are exactly「这一节没有自己的
        行」: it is in the reading order (a ``linear="no"`` document was taken out of
        the text by the publisher, FR-016), the contents does not name it, and it
        names nothing itself - not even its own first line.
        """
        return (
            spine_index not in targets
            and self.spine[spine_index].linear
            and not self._document_name(spine_index)[0]
        )

    def _says_nothing(self, spine_index: int) -> bool:
        """Whether one document holds no text at all: a page that is only a picture.

        Decided from a bounded head (:data:`_SILENT_DOCUMENT_BYTES`), which is exact
        whenever the document fits inside that window - the case the rule exists for,
        because a frontispiece is one ``<img>`` and nothing else.  A longer document is
        taken to carry text and left alone, so a chapter that opens with a full-page
        picture and then continues in words is not mistaken for a frontispiece on the
        strength of its first kilobytes.
        """
        head = self._read_head(self.spine[spine_index].href, _SILENT_DOCUMENT_BYTES)
        if len(head) >= _SILENT_DOCUMENT_BYTES:
            return False
        blocks = self._sanitize(head, self._document_base_dir(spine_index))
        return not any(block.text.strip() for block in blocks)

    def _section_of(self, spine_index: int) -> int:
        """Index of the reading section a spine document belongs to."""
        self._reading_sections()
        return self._section_of_spine[spine_index]

    def _in_reading_order(self, index: int) -> bool:
        """Whether a section belongs to the text the reader walks, not to a cover.

        The flag is per document (FR-016); a section answers for the documents it is
        made of, and one of them being part of the text is enough - a frontispiece the
        publisher marked ``linear="no"`` does not take the chapter with it.
        """
        return any(self.spine[spine_index].linear for spine_index in self.section_documents(index))

    def section_bytes(self, index: int) -> bytes:
        """Raw XHTML bytes of the document that opens one section (FR-016)."""
        return self.resource(self.spine[self.section_documents(index)[0]].href)

    def section_base_dir(self, index: int) -> str:
        """Directory that relative hrefs inside a section resolve against."""
        return self._document_base_dir(self.section_documents(index)[0])

    def _document_base_dir(self, spine_index: int) -> str:
        """Directory that relative hrefs inside one spine document resolve against."""
        return _base_dir_of(self.spine[spine_index].href)

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

    def blocks(self, index: int, *, protect: bool = True) -> tuple[Block, ...]:
        """Normalised block list of one section, parsed once and cached.

        A section that absorbed the material below its frontispiece reads as one list
        of blocks, which is what makes it one column on screen (ADR-016 / ADR-021):
        images already carry the archive path they resolve to, so the blocks of two
        documents behind different directories concatenate without a second pass.

        *protect* is CJK line-break protection (FR-031) and goes straight to the
        sanitiser: the joiners it injects are characters in the text, so they belong to
        the parse rather than to the drawing.  The caches below are keyed by section
        only, which means the flag in force is part of what they hold - a caller that
        changes it has to call :meth:`drop_blocks_cache` first, and the layout engine
        does exactly that (缺陷 29).
        """
        cached = self._block_cache.get(index)
        if cached is None:
            cached = tuple(
                block
                for spine_index in self.section_documents(index)
                for block in self._document_blocks(spine_index, protect=protect)
            )
            self._block_cache[index] = cached
        return cached

    def _document_blocks(self, spine_index: int, *, protect: bool = True) -> tuple[Block, ...]:
        """Normalised block list of one spine document, parsed once and cached."""
        cached = self._document_cache.get(spine_index)
        if cached is None:
            cached = self._sanitize(
                self.resource(self.spine[spine_index].href),
                self._document_base_dir(spine_index),
                protect=protect,
            )
            self._document_cache[spine_index] = cached
        return cached

    def _sanitize(self, raw: bytes, base_dir: str, *, protect: bool = True) -> tuple[Block, ...]:
        # Imported lazily so that merely opening a book does not pay for it.
        from ..html.sanitizer import sanitize

        return sanitize(raw, base_dir=base_dir, probe=self.image_size, protect=protect)

    def drop_blocks_cache(self) -> None:
        self._block_cache.clear()
        self._document_cache.clear()

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
        omnibus names three volumes in its NCX and is made of 135 sections, and the EPUB 2 book
        leaves 11 of its 35 unnamed.  ``]`` walks all of them, so the contents column
        shows all of them too - the same list, out of the same book, either named by
        the publisher or named by the section itself (see :meth:`_fill_unnamed`).

        Some books say *less* than that: they name every section and draw no hierarchy
        at all - the EPUB 3 book's navigation document is one flat ``<ol>`` of 26 ``<li>``.  The
        rows are all there and in order, but 第一部分 and the eighteen chapters inside
        it arrive as siblings, so the map stops showing what the book already knows.
        Such a tree is re-read from the text (:meth:`_nested_by_headings`) before the
        unnamed sections are filled in, so that the added rows land in the hierarchy
        too.

        The rows are resolved against the **reading sections** rather than against the
        spine, so a row whose target is a frontispiece points at the section that holds
        the frontispiece *and* the chapter below it (ADR-021).
        """
        return self._fill_unnamed(self._nested_by_headings(self._book_rows()))

    def _book_rows(self) -> tuple[TocEntry, ...]:
        """The rows the book wrote itself: EPUB 3 navigation first, NCX second (FR-010).

        Read once and kept: both the reading sections (which documents a contents row
        names, ADR-021) and the contents tree itself are built from them, and parsing a
        navigation document twice for one book would be paying for the same bytes
        twice.
        """
        if self._own_rows is None:
            package = self.package
            rows: tuple[TocEntry, ...] = ()
            for href, parser in (
                (package.nav_href, toc.parse_nav),
                (package.ncx_href, toc.parse_ncx),
            ):
                if not href or not self.has_resource(href):
                    continue
                rows = parser(self.resource(href), _base_dir_of(href))
                if rows:
                    break
            self._own_rows = rows
        return self._own_rows

    @staticmethod
    def _is_flat(entries: tuple[TocEntry, ...]) -> bool:
        """Whether *entries* declares no hierarchy at all: every row on the top level.

        The parsers give a nested ``<ol>`` / ``navPoint`` one level per step, so a tree
        whose rows are all at level 0 with no children is a book that wrote a list
        rather than a hierarchy.  That is what makes both the hierarchy of
        :meth:`_nested_by_headings` and the attribution of the filled rows in
        :meth:`_fill_unnamed` applicable - where the book drew a tree, nothing about it
        is second-guessed.
        """
        return all(not row.children and row.level == 0 for row in entries)

    def _nested_by_headings(self, entries: tuple[TocEntry, ...]) -> tuple[TocEntry, ...]:
        """Give a table of contents that has no hierarchy one, read off the text (FR-012).

        the EPUB 3 book's navigation lists 26 rows on one level: its eight front matter and part
        openings and its eighteen numbered chapters are drawn as siblings, so the panel
        shows a single column where the book has three parts (缺陷 26).  The hierarchy was
        not lost - it is in the chapter documents, written as headings: the front matter and
        the part openings are ``<h1>``, the numbered chapters are ``<h2>``.

        So a flat contents is read the way a person reads the book: a row whose own
        first heading is shallower than the next row's is its parent.  Levels are
        shifted so that the shallowest heading in the book is the top row - a book that
        writes everything as ``<h2>`` is not indented as a whole, and the EPUB 2 book, whose
        sections are not headings at all, keeps the flat list it declared.  The nesting
        stops at :data:`_MAX_TOC_LEVEL`.

        A row whose section does not name a heading says nothing about its own place, so
        it is given the level of the next row that does - or, when nothing after it does,
        the level of the row above.  It can therefore never be the parent of a row that
        did declare where it belongs, which is the one thing the text can be said *not*
        to imply: a chapter title written as a plain ``<p>`` is a chapter, not a part.

        Only a book whose contents declares no hierarchy of its own comes here: where
        the book said what its parts are, that answer is kept, whatever its headings
        look like - the text is read for the level the contents flattened, not to
        overrule the contents (FR-012).
        """
        if not entries or not self._is_flat(entries):
            return entries
        written: list[int] = []
        for row in entries:
            index = self.section_index_for_href(row.href)
            written.append(self._section_name(index)[1] if index >= 0 else 0)
        headings = [level for level in written if level > 0]
        if not headings:
            return entries
        base = min(headings)

        def normalized(level: int) -> int:
            return max(0, min(level - base, _MAX_TOC_LEVEL))

        # The level of the next row that named one, so that a row which named none can
        # take it without ever becoming the parent of a row that did.
        after: list[int | None] = [None] * len(entries)
        following: int | None = None
        for position in range(len(entries) - 1, -1, -1):
            if written[position] > 0:
                following = normalized(written[position])
            after[position] = following
        levels: list[int] = []
        for position, level in enumerate(written):
            if level > 0:
                levels.append(normalized(level))
            elif after[position] is not None:
                levels.append(after[position])
            else:
                levels.append(levels[-1] if levels else 0)
        children: list[list[int]] = [[] for _ in entries]
        roots: list[int] = []
        open_rows: list[int] = []
        for position, level in enumerate(levels):
            while open_rows and levels[open_rows[-1]] >= level:
                open_rows.pop()
            if open_rows:
                children[open_rows[-1]].append(position)
            else:
                roots.append(position)
            open_rows.append(position)
        # A row's children always follow it, so the tree is built back to front.
        built: list[TocEntry] = list(entries)
        for position in range(len(entries) - 1, -1, -1):
            built[position] = replace(
                entries[position],
                level=levels[position],
                children=tuple(built[child] for child in children[position]),
            )
        return tuple(built[root] for root in roots)

    def _fill_unnamed(self, entries: tuple[TocEntry, ...]) -> tuple[TocEntry, ...]:
        """Add a row for every section of the reading order that no row names (FR-012).

        Each added row is spliced in between the rows that surround it, so the list
        stays in reading order - which is the property the highlight and 「下一章」both
        read it for - and the tree the book wrote keeps its shape: only the lines it
        left out are added, inside the part of the tree they belong to and at that
        part's own level.

        A contents that declares no hierarchy at all has no such part to put them in,
        and a reader looking for a chapter the book forgot to list looks for it under
        the row it follows: in a book that wrote a flat list, an added row becomes a
        child of the row above it, and the rows before the first name of the book stay
        on the top level (that is the EPUB 2 book's 免责声明, which comes before everything the NCX
        names).  The omnibus is the case this is for: its three volume rows take the
        31, 66 and 32 sections that follow them, which is the book's own division.  It
        is an inference from reading order, and is recorded as one (ADR-020);
        where the book drew a hierarchy, nothing is inferred and the rows stay where it
        put them.

        A ``linear="no"`` document is skipped: it is not part of the order the reader
        walks, which is also why 「下一章」steps over it (FR-016).  It is also why the
        rows are built from the *sections* and not from the documents: a document that
        a frontispiece absorbed has no row of its own to add (ADR-021), so the list the
        panel shows is the same list it showed before that rule existed.

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
            if index in named or not self._in_reading_order(index):
                continue
            title = self._section_label(index)
            if title:
                opening = self.spine[self.section_documents(index)[0]].href
                pending.append((index, TocEntry(title=title, href=opening)))
        if not pending:
            return entries

        taken = 0
        adopt = self._is_flat(entries)

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
                elif adopt and start >= 0:
                    # A flat list has no part to belong to, so the sections between this
                    # row and the next one are put under it.  A row whose successor points
                    # nowhere is left alone rather than taking the rest of the book with
                    # it; a row that points nowhere itself takes nothing, because the
                    # panel would draw its children under a row that is not there.
                    after = (
                        self._subtree_start(rows[position + 1]) if position + 1 < len(rows) else -1
                    )
                    if after >= 0 or position + 1 == len(rows):
                        end = after if after >= 0 else bound
                        adopted: list[TocEntry] = []
                        while taken < len(pending) and pending[taken][0] < end:
                            adopted.append(
                                replace(pending[taken][1], level=min(level + 1, _MAX_TOC_LEVEL))
                            )
                            taken += 1
                        row = replace(row, children=tuple(adopted))
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
        an ordinary ``<p>`` in the omnibus).  That name is what the contents row shows, and
        what the 「下一章」line shows when the reader reaches the end of a section
        (FR-074).

        Only the head of the document is looked at (:data:`_LABEL_SNIFF_BYTES`), which
        is where a name lives.  The one case that pays for a whole parse is a head
        that ran out on the very block that would name the section - that block may
        have been cut in half by the reading window - and the document is then read in
        full, which is the parse the reader is about to need anyway: it is where they
        are being led.
        """
        return self._section_name(index)[0]

    def _section_name(self, index: int) -> tuple[str, int]:
        """The name a section gives itself, and the heading level it was written at.

        The name is what :meth:`_section_label` documents.  The level is the heading
        the name came from (``1`` for ``<h1>``, up to ``6``), and ``0`` when the block
        that named the section is not a heading at all - a converter's ``<p>`` chapter
        title, or a section that says nothing.

        A section says its name with the first thing it says, so a section that opens
        on a frontispiece is named by the first words of the chapter below it
        (:meth:`_document_name` is asked document by document, in reading order,
        ADR-021).  It is one sniff per document rather than two because the two answers
        come from the same block, and that block is already in hand: reading the head
        of a document is the whole cost of naming what it contains (36-45 ms for the
        omnibus's 135 sections), and the level is what a contents that declares no
        hierarchy is read from (:meth:`_nested_by_headings`).
        """
        for spine_index in self.section_documents(index):
            name, level = self._document_name(spine_index)
            if name:
                return name, level
        return "", 0

    def _document_name(self, spine_index: int) -> tuple[str, int]:
        """What one document says its name is, and the heading level it wrote it at.

        The name is read from the head of that one document
        (:data:`_LABEL_SNIFF_BYTES`), which is where a name lives.  The one case that
        pays for a whole parse is a head that ran out on the very block that would name
        it - that block may have been cut in half by the reading window - and the
        document is then read in full, which is the parse the reader is about to need
        anyway: it is where they are being led.

        An empty name means the document names nothing, which is the answer both a
        chapter written as plain prose and a page that is only a picture give (FR-012
        / ADR-021).
        """
        head = self._read_head(self.spine[spine_index].href, _LABEL_SNIFF_BYTES)
        if len(head) < _LABEL_SNIFF_BYTES:
            return self._first_name(self._document_blocks(spine_index))
        blocks = self._sanitize(head, self._document_base_dir(spine_index))
        position = next((at for at, block in enumerate(blocks) if block.text.strip()), -1)
        if 0 <= position < len(blocks) - 1:
            return self._name_of(blocks[position])
        return self._first_name(self._document_blocks(spine_index))

    @classmethod
    def _first_name(cls, blocks: tuple[Block, ...]) -> tuple[str, int]:
        """The name in *blocks* and the level of the heading that carried it."""
        for block in blocks:
            if block.text.strip():
                return cls._name_of(block)
        return "", 0

    @staticmethod
    def _name_of(block: Block) -> tuple[str, int]:
        """*block* as a row title and the level to give that row (FR-012)."""
        return _label_of(block), block.level if block.is_heading else 0

    def section_index_for_href(self, href: str, *, current: int = -1) -> int:
        """Find the reading section that owns *href*, ignoring any fragment, or ``-1``.

        The fragment is dropped rather than matched because it names a place *inside*
        the document, and this method answers which section that document belongs to;
        whether the row then lands at the top of it or at an anchor in it is a second
        question, asked of the laid-out section once it exists (FR-014).

        A document the contents names resolves to the section that holds it, and for a
        frontispiece that section is also the chapter below it (ADR-021).  Callers care
        about the section, not the document, because that is what they scroll and what
        the panel draws.

        *current* answers the one href that names no document: ``#note3`` is a place in
        the document the link was written in, and a reader that knows which section it
        is showing can resolve it.  The default is ``-1`` because a caller without that
        context - the table of contents, whose rows live in the navigation document -
        has no place to send such a link, and guessing the first section would be worse
        than not following it.
        """
        path, fragment = split_fragment(href)
        if not normalize(path):
            return current if fragment and 0 <= current < self.section_count else -1
        spine_index = self._spine_index_of_href(href)
        return -1 if spine_index < 0 else self._section_of(spine_index)

    def _spine_index_of_href(self, href: str) -> int:
        """The spine document *href* names, ignoring any fragment, or ``-1``."""
        target = normalize(split_fragment(href)[0])
        if not target:
            return -1
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
            if self._in_reading_order(target):
                return target
            target += delta
        return -1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        opened = self._package is not None
        return f"<EpubBook {self.path.name!r} sections={self.section_count if opened else 0}>"

