"""The bundled Kai face really resolves inside Qt, not just in the file system (ADR-025).

``tests/unit/test_bundled_font.py`` proves the TTF and its licence ship and that
``KAI_STACK`` names the bundled family first.  That is not the reader-facing claim: Qt
may still fail to load a file, and a family name can be a near-miss of what the font
database actually registers.  Only a live ``QGuiApplication`` can answer it, so the
question is asked once here - on the same ``QFont`` the engine builds (``style.py``:
``setFamilies``), because that is where a broken chain would silently turn 楷体 into 宋体.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase, QFontInfo

from ebook_reader.typeset.fonts import BUNDLED_KAI_FAMILY, load_bundled_fonts
from ebook_reader.typeset.settings import KAI_STACK


def test_the_kai_choice_resolves_to_the_bundled_face(qapp) -> None:
    """楷体 must be the shipped face even on a machine with no Kai installed (FR-037)."""
    families = load_bundled_fonts()
    assert BUNDLED_KAI_FAMILY in families, "Qt did not register the bundled font"
    assert BUNDLED_KAI_FAMILY in QFontDatabase.families()

    font = QFont()
    font.setFamilies(list(KAI_STACK))
    assert QFontInfo(font).family() == BUNDLED_KAI_FAMILY
