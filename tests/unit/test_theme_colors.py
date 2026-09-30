"""The three palettes: one surface order, and type that can be read on it (FR-090).

Day, sepia and night are not three good-looking tables; they are one decision applied
three times.  The paper is always the brightest surface, the *band* the two strips on it
are drawn with is one step below the paper, and the chrome beside the book - contents
column, outline, menus, status bar - is a step further away again.  Night is that same
order with the lights off, which is what makes it a theme rather than a second design:
a grey deep enough to read on for an hour, without being a hole, and grey-white type all
the way through rather than light grey on black.

A rendering test cannot see any of this.  A palette whose secondary text sits at 2:1
still paints, it just hurts, and no integration test would notice - so the ratios are
asserted here, on the table itself, where the numbers are, and the QML simply binds to
whatever comes out of it (`bandColor`, FR-074).
"""

from __future__ import annotations

from ebook_reader.typeset.settings import THEMES, Theme, ThemeColors

#: Body text on its own surface: the WCAG AAA floor for normal text, which is what a
#: reading application should hold itself to (both the paper and the panels carry it).
_BODY_FLOOR = 7.0

#: The quiet tier - captions, hints, the levels above the reader's place.  It is
#: allowed to be quiet; it is not allowed to be invisible.
_MUTED_FLOOR = 3.0

#: A row's label on the colour that highlights it must still be a row's label.
_LABEL_ON_HIGHLIGHT_FLOOR = 4.5


def _luminance(color: str) -> float:
    """WCAG relative luminance of ``#rrggbb``: 0 is black, 1 is white."""
    weights = (0.2126, 0.7152, 0.0722)
    return sum(weight * _channel(color, index) for weight, index in zip(weights, (1, 3, 5)))


def _channel(color: str, index: int) -> float:
    value = int(color[index : index + 2], 16) / 255
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def _contrast(first: str, second: str) -> float:
    """The WCAG ratio between two colours: 1.0 is invisible, 21.0 is black on white."""
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _saturation(color: str) -> float:
    """HSV saturation: 0 is a pure grey, which is how "grey-white" is told from tinted."""
    channels = [int(color[index : index + 2], 16) for index in (1, 3, 5)]
    return (max(channels) - min(channels)) / max(channels)


def _surfaces(colors: ThemeColors) -> tuple[tuple[str, str], ...]:
    """Every surface of a theme, in the order the reader meets them."""
    return (
        ("paper", colors.background),
        ("band", colors.band),
        ("panel", colors.panel),
    )


def test_the_band_sits_between_the_paper_and_the_chrome() -> None:
    """One order in every theme: paper > band > panel, and never two of them alike.

    This is the rule that was broken before: the position bar and the 「下一章」 line were
    painted in the panel colour, so the column carried a strip that belonged to the map
    beside it, and the night theme's chrome was *lighter* than its paper - a different
    order per theme.  The steps are taste; the order is not, and it is what makes a page
    read as a page.
    """
    for theme in Theme:
        colors = THEMES[theme]
        paper, band, panel = (value for _, value in _surfaces(colors))
        assert _luminance(paper) > _luminance(band) > _luminance(panel), (
            f"{theme.value}: {paper} / {band} / {panel} are not paper > band > panel"
        )


def test_the_body_text_clears_the_floor_on_every_surface() -> None:
    """The page's ink is read on three surfaces, and each of them has to carry it."""
    for theme in Theme:
        colors = THEMES[theme]
        for name, surface in _surfaces(colors):
            ratio = _contrast(colors.foreground, surface)
            assert ratio >= _BODY_FLOOR, f"{theme.value}: body text on the {name} is {ratio:.2f}:1"
        # The panels carry their own text role; it is the same ink by another name.
        ratio = _contrast(colors.panel_text, colors.panel)
        assert ratio >= _BODY_FLOOR, f"{theme.value}: panel text on the panel is {ratio:.2f}:1"


def test_the_quiet_tier_stays_readable_on_every_surface() -> None:
    """Captions, hints and the levels above the reader's place - quiet, but legible.

    The muted colour is met on every surface: the outline's captions and the status
    bar's hints sit on a panel, the 「下一章」 line and the head of the position bar's path
    sit on the band, and the page's own quiet type sits on the paper.
    """
    for theme in Theme:
        colors = THEMES[theme]
        for name, surface in _surfaces(colors):
            ratio = _contrast(colors.muted, surface)
            assert ratio >= _MUTED_FLOOR, (
                f"{theme.value}: muted type on the {name} is {ratio:.2f}:1"
            )


def test_the_highlighted_row_keeps_its_label_readable() -> None:
    """A row's fill and its label are chosen together, in every theme.

    The accent is a tint of the surface it highlights rather than a contrast against it,
    so its own label is the pair that has to hold up - in the contents column, in the
    outline, and on the menu rows, all of which fill with the accent while current or
    hovered.  A fill equal to the panel underneath it would not read as a highlight at
    all, so that is asserted too.
    """
    for theme in Theme:
        colors = THEMES[theme]
        ratio = _contrast(colors.panel_text, colors.selection)
        assert ratio >= _LABEL_ON_HIGHLIGHT_FLOOR, (
            f"{theme.value}: a highlighted label is {ratio:.2f}:1 on its own fill"
        )
        assert colors.selection != colors.panel


def test_night_is_a_deep_grey_with_grey_white_type() -> None:
    """What "night" means here: a grey page, and type of no colour at all.

    Two reader-facing words are turned into numbers.  *Deep but not too deep* is a
    relative luminance well above black (#000000, 0.0) and far below a mid grey
    (#808080, 0.216).  *Grey-white type* is a light ink (luminance at least 0.7, i.e.
    around #d4d4d4 and up) with no hue worth the name; the link is the one colour night
    is allowed, because a link has to look like one.  The palette this pins away is the
    old one: #c8c8c8 ink on #1c1c1e paper, light grey on a hole.
    """
    colors = THEMES[Theme.DARK]
    assert 0.008 <= _luminance(colors.background) <= 0.06, (
        f"night paper {colors.background} is not a deep grey"
    )
    for role in ("foreground", "heading", "panel_text"):
        value = getattr(colors, role)
        assert _luminance(value) >= 0.7, f"night {role} {value} is not light"
        assert _saturation(value) <= 0.1, f"night {role} {value} is tinted, not grey-white"
    # The quiet tier is the one exception, and only in brightness: it is still grey, and
    # its 3:1 floor on every surface is asserted above.
    assert _saturation(colors.muted) <= 0.1
    assert _contrast(colors.foreground, colors.background) >= 9.0, "night ink is flat on its page"
    # FR-091: night is the only theme that dims images.
    assert colors.image_dim < 1.0
    assert THEMES[Theme.LIGHT].image_dim == 1.0
    assert THEMES[Theme.SEPIA].image_dim == 1.0
