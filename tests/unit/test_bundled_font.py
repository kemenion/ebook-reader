"""The Kai face is inside the package, and the kai choice asks for it first (ADR-025).

The defect this guards was invisible: ``KAI_STACK`` named only system families, so a
machine without a Chinese Kai installed showed 宋体 and reported nothing anywhere.  What
is asserted here is cheap and purely structural - the file exists, the licence that must
travel with it exists, and the stack names the bundled family before any system one.
Whether Qt *resolves* that family is a question for a real font database, and
``tests/integration/test_font_resolution.py`` asks it there.
"""

from __future__ import annotations

from pathlib import Path

from ebook_reader.typeset.fonts import (
    BUNDLED_KAI_FAMILY,
    BUNDLED_KAI_FILE,
    LICENSE_FILE,
    bundled_font_file,
    fonts_dir,
)
from ebook_reader.typeset.settings import FontChoice, KAI_STACK

#: A real CJK face is tens of megabytes; this floor only has to catch a placeholder -
#: an empty file, or the ~130-byte pointer a Git LFS checkout leaves behind when the
#: binary itself was never fetched.
_MIN_FONT_BYTES = 1_000_000


def test_the_bundled_kai_face_ships_with_the_package() -> None:
    """The reader must not depend on what the host has installed (FR-037)."""
    assert (fonts_dir() / BUNDLED_KAI_FILE).is_file()
    path = bundled_font_file()
    assert path is not None
    assert path.stat().st_size > _MIN_FONT_BYTES


def test_the_licence_travels_with_the_font() -> None:
    """OFL-1.1 requires the licence to accompany every copy, AppImage included."""
    text = (fonts_dir() / LICENSE_FILE).read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in text
    assert "With Reserved Font Names" in text
    assert "lxgw" in text


def test_a_build_without_the_font_degrades_instead_of_raising(tmp_path: Path) -> None:
    """A missing data directory is a logged warning, never a failed start."""
    assert bundled_font_file(tmp_path) is None


def test_the_kai_choice_asks_for_the_bundled_family_first() -> None:
    """First entry decides the face; the system names below it are the fallback."""
    assert KAI_STACK[0] == BUNDLED_KAI_FAMILY
    assert FontChoice.KAI.stack == KAI_STACK
