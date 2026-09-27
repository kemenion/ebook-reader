"""Typography settings and theme colours, persisted as JSON (FR-032 .. FR-037).

Font stacks are ordered fallback chains rather than single names.  Verifying the
names against a real Qt font database paid off: ``Source Han Serif SC`` is *not*
a family name Qt recognises and asking for it alone silently resolves to
``WenQuanYi Zen Hei`` - a sans face, which would have destroyed the serif look
without any error.  Only the names below are known to resolve to the intended
face, and Qt keeps walking the chain when the first entry is absent (R-06).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Any, TypeVar

from PySide6.QtCore import QPointF, QSizeF
from PySide6.QtGui import QColor

__all__ = [
    "FontChoice",
    "KAI_STACK",
    "PageGeometry",
    "SANS_STACK",
    "SERIF_STACK",
    "THEMES",
    "Theme",
    "ThemeColors",
    "TypographySettings",
]

_E = TypeVar("_E", bound=Enum)

#: Verified on: Manjaro, Qt 6.11.2.  Chinese family names come first because they
#: are the ones the "Source Han" packages actually register.
SERIF_STACK: tuple[str, ...] = (
    "思源宋体 CN",
    "Noto Serif CJK SC",
    "Noto Serif SC",
    "Songti SC",
    "SimSun",
    "AR PL UMing CN",
    "serif",
)

SANS_STACK: tuple[str, ...] = (
    "思源黑体 CN",
    "Noto Sans CJK SC",
    "Microsoft YaHei",
    "PingFang SC",
    "文泉驿微米黑",
    "sans-serif",
)

KAI_STACK: tuple[str, ...] = (
    "AR PL UKai CN",
    "KaiTi",
    "STKaiti",
    "TW-Kai",
    "思源宋体 CN",
    "serif",
)


class FontChoice(str, Enum):
    """Which fallback chain the reader should use (FR-037)."""

    SERIF = "serif"
    SANS = "sans"
    KAI = "kai"

    @property
    def stack(self) -> tuple[str, ...]:
        return {
            FontChoice.SERIF: SERIF_STACK,
            FontChoice.SANS: SANS_STACK,
            FontChoice.KAI: KAI_STACK,
        }[self]

    @property
    def label(self) -> str:
        return {"serif": "宋体", "sans": "黑体", "kai": "楷体"}[self.value]


class Theme(str, Enum):
    LIGHT = "light"
    SEPIA = "sepia"
    DARK = "dark"


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """Physical page size plus the margins that define the content box.

    Qt's ``QTextDocument`` page size is the *content* box, not the full page:
    margins are applied by translating the painter in the renderer.  That is what
    makes four independent margins possible (FR-035), which a single
    ``setDocumentMargin`` cannot express.
    """

    page_size: QSizeF
    margin_top: float = 0.0
    margin_right: float = 0.0
    margin_bottom: float = 0.0
    margin_left: float = 0.0

    @classmethod
    def from_settings(cls, page_size: QSizeF, settings: "TypographySettings") -> "PageGeometry":
        return cls(
            page_size=page_size,
            margin_top=float(settings.margin_top),
            margin_right=float(settings.margin_right),
            margin_bottom=float(settings.margin_bottom),
            margin_left=float(settings.margin_left),
        )

    @property
    def content_size(self) -> QSizeF:
        return QSizeF(
            max(32.0, self.page_size.width() - self.margin_left - self.margin_right),
            max(32.0, self.page_size.height() - self.margin_top - self.margin_bottom),
        )

    @property
    def content_origin(self) -> QPointF:
        return QPointF(self.margin_left, self.margin_top)

    @property
    def content_height(self) -> float:
        return self.content_size.height()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PageGeometry):
            return NotImplemented
        return (
            self.page_size == other.page_size
            and self.margin_top == other.margin_top
            and self.margin_right == other.margin_right
            and self.margin_bottom == other.margin_bottom
            and self.margin_left == other.margin_left
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.page_size.width(),
                self.page_size.height(),
                self.margin_top,
                self.margin_right,
                self.margin_bottom,
                self.margin_left,
            )
        )


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Concrete colours for one theme, as hex strings so QML can bind directly."""

    background: str
    foreground: str
    heading: str
    muted: str
    link: str
    panel: str
    panel_text: str
    selection: str
    #: Multiplier applied to images so that white-background scans are not
    #: blinding in dark mode (FR-091).
    image_dim: float = 1.0


THEMES: dict[Theme, ThemeColors] = {
    Theme.LIGHT: ThemeColors(
        background="#ffffff",
        foreground="#1b1b1b",
        heading="#000000",
        muted="#8a8a8a",
        link="#1a5fb4",
        panel="#f4f4f4",
        panel_text="#1b1b1b",
        selection="#cfe1ff",
    ),
    Theme.SEPIA: ThemeColors(
        background="#f5ecd9",
        foreground="#3b3226",
        heading="#241d13",
        muted="#8d8271",
        link="#8a5a1f",
        panel="#ede1c8",
        panel_text="#3b3226",
        selection="#dfcfa8",
    ),
    Theme.DARK: ThemeColors(
        background="#1c1c1e",
        foreground="#c8c8c8",
        heading="#e6e6e6",
        muted="#787878",
        link="#7aa2f7",
        panel="#242428",
        panel_text="#c8c8c8",
        selection="#3a4a6b",
        image_dim=0.82,
    ),
}


@dataclass(slots=True)
class TypographySettings:
    """Everything that influences typesetting; serialisable to JSON (FR-083)."""

    font_size: float = 18.0
    font_choice: FontChoice = FontChoice.SERIF
    line_height: float = 1.75
    paragraph_spacing_em: float = 0.3
    first_line_indent_em: float = 2.0
    margin_top: int = 48
    margin_bottom: int = 48
    margin_left: int = 72
    margin_right: int = 72
    justify: bool = True
    kinsoku: bool = True
    theme: Theme = Theme.LIGHT

    # ---------------------------------------------------------------- derived

    @property
    def font_families(self) -> tuple[str, ...]:
        return self.font_choice.stack

    @property
    def colors(self) -> ThemeColors:
        return THEMES[self.theme]

    def foreground(self) -> QColor:
        return QColor(self.colors.foreground)

    def heading_color(self) -> QColor:
        return QColor(self.colors.heading)

    def link_color(self) -> QColor:
        return QColor(self.colors.link)

    def background(self) -> QColor:
        return QColor(self.colors.background)

    def selection_color(self) -> QColor:
        return QColor(self.colors.selection)

    # --------------------------------------------------------------- mutation

    def with_(self, **changes: Any) -> "TypographySettings":
        """Return a copy with *changes* applied, clamped to sane ranges."""
        updated = replace(self, **changes)
        updated.font_size = max(10.0, min(48.0, float(updated.font_size)))
        updated.line_height = max(1.0, min(3.0, float(updated.line_height)))
        updated.paragraph_spacing_em = max(0.0, min(2.0, float(updated.paragraph_spacing_em)))
        updated.first_line_indent_em = max(0.0, min(4.0, float(updated.first_line_indent_em)))
        for attribute in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
            setattr(updated, attribute, max(0, min(240, int(getattr(updated, attribute)))))
        return updated

    # ---------------------------------------------------------- serialisation

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["font_choice"] = self.font_choice.value
        data["theme"] = self.theme.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TypographySettings":
        """Build settings from JSON, ignoring unknown or malformed entries."""
        fields = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        clean: dict[str, Any] = {}
        for key, value in data.items():
            if key not in fields:
                continue
            try:
                if key == "font_choice":
                    clean[key] = FontChoice(value)
                elif key == "theme":
                    clean[key] = Theme(value)
                elif key in ("justify", "kinsoku"):
                    clean[key] = bool(value)
                elif key.startswith("margin_"):
                    clean[key] = int(value)
                else:
                    clean[key] = float(value)
            except (TypeError, ValueError):
                continue  # keep the default for a corrupted field
        return cls(**clean).with_()

