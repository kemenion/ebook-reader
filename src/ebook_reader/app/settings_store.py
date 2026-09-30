"""Persist settings, window state and per-book reading positions (FR-080 .. FR-083).

The file is plain JSON under the XDG config directory, so it stays readable,
hand-editable and easy to debug.  Every read is defensive: a corrupted or
partially written file must degrade to defaults rather than prevent startup.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

from ..typeset.settings import TypographySettings

__all__ = ["BookState", "SettingsStore", "config_dir", "config_path"]

_log = logging.getLogger(__name__)

_VERSION = 1
_FILE_NAME = "config.json"

#: Bounds of the reader's own side-panel width, in px (FR-078 / ADR-022).
#:
#: The window clamps what it can actually afford on top of these (the text column
#: keeps at least 320 px), but these two are the bounds that survive a restart - and
#: they are also what protects the layout from a hand-edited `config.json`: a width
#: of zero or of 100000 must not be able to produce a panel that is invisible or one
#: that leaves no room for a page.  `Main.qml` mirrors the same two numbers for the
#: drag itself, and `test_layout.py` pins the mirror in place.
_PANEL_WIDTH_MIN = 160
_PANEL_WIDTH_MAX = 480


def config_dir() -> Path:
    """Directory for our configuration, honouring ``XDG_CONFIG_HOME``."""
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    if not location:
        location = str(Path.home() / ".config" / "ebook-reader")
    path = Path(location)
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / _FILE_NAME


@dataclass(slots=True)
class BookState:
    """Per-book resume point: section index, block index, character offset.

    Deliberately not a page number: page numbers change with the window size and
    the font size, whereas this triple survives both (ADR-011).
    """

    section: int = 0
    block: int = 0
    char_offset: int = 0
    opened_at: float = 0.0

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.section, self.block, self.char_offset)


class SettingsStore:
    """Reads and writes the single JSON configuration file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self._data: dict[str, Any] = {
            "version": _VERSION,
            "settings": {},
            "window": {},
            "layout": {},
            "books": {},
        }
        self.load()

    # ------------------------------------------------------------------ access

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def settings(self) -> TypographySettings:
        raw = self._data.get("settings")
        if not isinstance(raw, dict):
            return TypographySettings()
        return TypographySettings.from_dict(raw)

    def set_settings(self, settings: TypographySettings) -> None:
        self._data["settings"] = settings.to_dict()

    def window(self) -> dict[str, int]:
        raw = self._data.get("window")
        if not isinstance(raw, dict):
            return {}
        result: dict[str, int] = {}
        for key in ("width", "height", "x", "y"):
            value = raw.get(key)
            if isinstance(value, int):
                result[key] = value
        return result

    def set_window(
        self, width: int, height: int, x: int | None = None, y: int | None = None
    ) -> None:
        window: dict[str, int] = {"width": int(width), "height": int(height)}
        if x is not None and y is not None:
            window["x"] = int(x)
            window["y"] = int(y)
        self._data["window"] = window

    def panel_width(self) -> int:
        """The reader's own side-panel width, or ``0`` for the automatic one (FR-078).

        ``0`` is the answer for a file that has never been dragged, and it is what
        makes the automatic width (``Main.qml``: a share of the window) the default
        until the reader says otherwise.  A stored width outside the bounds is
        clamped rather than rejected: the file is meant to be hand-editable.
        """
        value = _safe_int(self._layout().get("panel_width"))
        if value <= 0:
            return 0
        return max(_PANEL_WIDTH_MIN, min(_PANEL_WIDTH_MAX, value))

    def set_panel_width(self, width: int) -> None:
        """Remember the reader's own panel width; ``0`` forgets it (FR-078).

        Whether the number fits the window it will be shown in is the window's
        question, not this file's: a width chosen on a large display is kept in full
        so that it comes back on a large display (ADR-022).
        """
        value = int(width)
        self._layout()["panel_width"] = (
            0 if value <= 0 else max(_PANEL_WIDTH_MIN, min(_PANEL_WIDTH_MAX, value))
        )

    def book_state(self, book_path: str | os.PathLike[str]) -> BookState:
        entry = self._books().get(self._book_key(book_path))
        if not isinstance(entry, dict):
            return BookState()
        return BookState(
            section=_safe_int(entry.get("section")),
            block=_safe_int(entry.get("block")),
            char_offset=_safe_int(entry.get("char_offset")),
            opened_at=float(entry.get("opened_at") or 0.0),
        )

    def set_book_state(self, book_path: str | os.PathLike[str], state: BookState) -> None:
        self._books()[self._book_key(book_path)] = {
            "section": int(state.section),
            "block": int(state.block),
            "char_offset": int(state.char_offset),
            "opened_at": float(state.opened_at),
        }

    def forget_book(self, book_path: str | os.PathLike[str]) -> None:
        self._books().pop(self._book_key(book_path), None)

    def recent_books(self, limit: int = 10) -> list[tuple[str, float]]:
        entries = [
            (key, float(value.get("opened_at") or 0.0))
            for key, value in self._books().items()
            if isinstance(value, dict)
        ]
        entries.sort(key=lambda item: item[1], reverse=True)
        return entries[:limit]

    # ------------------------------------------------------------------------ io

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("ignoring unreadable configuration %s: %s", self.path, exc)
            return
        if not isinstance(raw, dict):
            return
        self._data = {
            "version": _safe_int(raw.get("version")) or _VERSION,
            "settings": raw.get("settings") if isinstance(raw.get("settings"), dict) else {},
            "window": raw.get("window") if isinstance(raw.get("window"), dict) else {},
            "layout": raw.get("layout") if isinstance(raw.get("layout"), dict) else {},
            "books": raw.get("books") if isinstance(raw.get("books"), dict) else {},
        }

    def save(self) -> bool:
        """Write atomically, so an interrupted save cannot corrupt the file."""
        self._data["version"] = _VERSION
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".config-", suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(self._data, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            _log.warning("could not write configuration %s: %s", self.path, exc)
            return False
        return True

    # ---------------------------------------------------------------- internals

    def _books(self) -> dict[str, Any]:
        books = self._data.get("books")
        if not isinstance(books, dict):
            books = {}
            self._data["books"] = books
        return books

    def _layout(self) -> dict[str, Any]:
        """The window-layout section, created on demand (FR-078)."""
        layout = self._data.get("layout")
        if not isinstance(layout, dict):
            layout = {}
            self._data["layout"] = layout
        return layout

    @staticmethod
    def _book_key(book_path: str | os.PathLike[str]) -> str:
        try:
            return str(Path(book_path).expanduser().resolve())
        except OSError:  # pragma: no cover - only on exotic filesystems
            return str(book_path)


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

