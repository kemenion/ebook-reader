"""``EpubBook``: the single entry point the rest of the application uses.

The archive stays open and is read on demand, so opening an 85 MB book with 408
images costs almost nothing: nothing is extracted to disk and no pixels are
decoded here (FR-007 / I-3).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Callable, Iterator

from ..errors import BookError, MissingResourceError, NotAnEpubError, PackageError
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
        # Imported lazily so that merely opening a book does not pay for it.
        from ..html.sanitizer import sanitize

        probe: Callable[[str], tuple[int, int] | None] = self.image_size
        return sanitize(
            self.section_bytes(index),
            base_dir=self.section_base_dir(index),
            probe=probe,
        )

    def drop_blocks_cache(self) -> None:
        self._block_cache.clear()

    def iter_blocks(self) -> Iterator[tuple[int, tuple[Block, ...]]]:
        for index in range(self.section_count):
            yield index, self.blocks(index)

    # --------------------------------------------------------------------- toc

    @property
    def toc(self) -> tuple[TocEntry, ...]:
        """Navigation tree: EPUB3 nav, then NCX, then a heading-based fallback."""
        if self._toc is None:
            self._toc = self._build_toc()
        return self._toc

    def _build_toc(self) -> tuple[TocEntry, ...]:
        package = self.package
        for href, parser in ((package.nav_href, toc.parse_nav), (package.ncx_href, toc.parse_ncx)):
            if not href or not self.has_resource(href):
                continue
            entries = parser(self.resource(href), _base_dir_of(href))
            if entries:
                return entries
        return self._toc_from_headings()

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

    def _toc_from_headings(self, max_level: int = 2) -> tuple[TocEntry, ...]:
        """Fallback navigation derived from the first heading of each section."""
        entries: list[TocEntry] = []
        for index in range(self.section_count):
            for block in self.blocks(index):
                if block.is_heading and block.level <= max_level:
                    title = " ".join(block.text.split())
                    if title:
                        entries.append(TocEntry(title=title, href=self.spine[index].href))
                    break
        return tuple(entries)

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

