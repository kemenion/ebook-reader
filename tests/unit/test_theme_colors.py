"""The three palettes: one surface order, and type that can be read on it (FR-090).

Day, sepia and night are not three good-looking tables; they are one decision applied
three times.  The paper is always the brightest surface, the *band* the two strips on it
are drawn with is one step below the paper, and the chrome beside the book - contents
column, outline, menus, status bar - is a step further away again.  Night is that same
order with the lights off, which is what makes it a theme rather than a second design:
a grey deep enough to read on for an hour, without being a hole, and type that is light
grey rather than white.

A rendering test cannot see any of this.  A palette whose secondary text sits at 2:1
still paints, it just hurts, and no integration test would notice - so the ratios are
asserted here, on the table itself, where the numbers are, and the QML simply binds to
whatever comes out of it (`bandColor`, FR-074).

The selection wash is the same kind of decision and is held to the same place.  It is
painted *over* the rasterised page rather than recolouring any glyph (FR-070), so the
colours a reader actually sees are the wash mixed with the theme's own paper - and those
mixtures, not the table's raw values, are what the floors below are measured on.
"""

from __future__ import annotations

from ebook_reader.typeset.settings import (
    SELECTION_ALPHA,
    THEMES,
    Theme,
    ThemeColors,
    TypographySettings,
)

#: Body text on its own surface: the WCAG AAA floor for normal text, which is what a
#: reading application should hold itself to (both the paper and the panels carry it).
_BODY_FLOOR = 7.0

#: The quiet tier - captions, hints, the levels above the reader's place.  It is
#: allowed to be quiet; it is not allowed to be invisible.
_MUTED_FLOOR = 3.0

#: A row's label on the colour that highlights it must still be a row's label.
_LABEL_ON_HIGHLIGHT_FLOOR = 4.5

#: A marked passage has to be tellable from the paper it is marked on.  The floor sits
#: well below the 3:1 WCAG asks of a non-text boundary, and deliberately: the wash is a
#: large flat area with type on it rather than a line beside something, and the two goals
#: pull against each other - a fill strong enough to reach 3:1 would start to eat the ink
#: drawn under it, which is the other floor here.  Browsers' own selection fills sit at
#: about this ratio for the same reason.
_WASH_OFF_PAPER_FLOOR = 1.5

#: Body ink read *through* the wash: the WCAG AA floor for normal text, one step below
#: the 7:1 the paper itself is held to.  Drawn over the type rather than behind it, the
#: wash costs some contrast - that is the price of marking a passage in a reader that has
#: no glyphs of its own to recolour, and it is paid where it can best be afforded.
_INK_THROUGH_WASH_FLOOR = 4.5

#: Night's body ink: a light grey, not a white page turned inside out.  The floor is what
#: keeps the words readable on the deep paper (the 9:1 asserted below); the ceiling is
#: the reader's own account of it - "浅灰，有点偏白，但不要太白" - because near-white type on
#: a dark page glares.  Two palettes bracket the window and are named here so the next
#: person can see what moving it does: #c8c8c8 ink (0.58) was the light-grey-on-a-hole
#: that started the last revision, and #e6e6ea (0.80) was that revision's overshoot.
_NIGHT_INK_FLOOR = 0.6

#: See above.  Headings are exempt: they are rare on the page and are meant to be seen
#: from across the room.
_NIGHT_INK_CEILING = 0.75

#: ``SELECTION_ALPHA`` as the value that actually reaches the painter: a ``QColor`` is
#: eight bits per channel, so 0.35 of a colour arrives as 89/255 and the mixes below are
#: measured on that rather than on the float behind it.
_WASH_ALPHA = round(SELECTION_ALPHA * 255) / 255


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


def _over(fill: str, paper: str, alpha: float) -> str:
    """*fill* laid over *paper* at *alpha*, as ``#rrggbb``: what a translucent wash shows.

    A plain per-channel mix, which is what a painter's alpha blend does on an opaque
    surface - no gamma correction by hand, because Qt's own compositing does not do any
    either at these values.
    """
    mixed = "#"
    for index in (1, 3, 5):
        above = int(fill[index : index + 2], 16)
        below = int(paper[index : index + 2], 16)
        mixed += f"{round(alpha * above + (1.0 - alpha) * below):02x}"
    return mixed


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


def test_night_is_a_deep_grey_with_light_grey_type() -> None:
    """What "night" means here: a grey page, and type of no colour at all.

    Two reader-facing words are turned into numbers.  *Deep but not too deep* is a
    relative luminance well above black (#000000, 0.0) and far below a mid grey
    (#808080, 0.216).  *Light grey type* is a two-sided window rather than a floor: the
    ink has to be light enough to read on that page, and grey enough not to glare, and
    the body and the panel text are the same grey (FR-090).  *No hue worth the name* is
    the saturation bound; the link is the one colour night is allowed, because a link
    has to look like one.
    """
    colors = THEMES[Theme.DARK]
    assert 0.008 <= _luminance(colors.background) <= 0.06, (
        f"night paper {colors.background} is not a deep grey"
    )
    for role in ("foreground", "panel_text"):
        value = getattr(colors, role)
        assert _NIGHT_INK_FLOOR <= _luminance(value) <= _NIGHT_INK_CEILING, (
            f"night {role} {value} is not a light grey"
        )
        assert _saturation(value) <= 0.1, f"night {role} {value} is tinted, not grey"
    # The headings are the louder tier, and the only one allowed past the ceiling.
    assert _luminance(colors.heading) >= _NIGHT_INK_FLOOR + 0.1, "headings are not louder"
    assert _luminance(colors.heading) > _luminance(colors.foreground)
    assert _saturation(colors.heading) <= 0.1
    # The quiet tier is the one exception, and only in brightness: it is still grey, and
    # its 3:1 floor on every surface is asserted above.
    assert _saturation(colors.muted) <= 0.1
    assert _contrast(colors.foreground, colors.background) >= 9.0, "night ink is flat on its page"
    # FR-091: night is the only theme that dims images.
    assert colors.image_dim < 1.0
    assert THEMES[Theme.LIGHT].image_dim == 1.0
    assert THEMES[Theme.SEPIA].image_dim == 1.0


def test_the_selection_wash_is_visible_without_swallowing_the_type() -> None:
    """A marked passage: told apart from the paper, and readable through (FR-070).

    Two floors with one number each between them on the same scale of alpha, and they
    pull in opposite directions - which is why the number is not left to the eye.  A
    wash light enough to leave the ink untouched would not read as a mark on the page; a
    strong one would leave a highlighted passage harder to read than the page around it,
    which is the wrong way round.  0.35 of a theme's own colour is where those two meet.
    """
    for theme in Theme:
        colors = THEMES[theme]
        washed = _over(colors.text_selection, colors.background, _WASH_ALPHA)
        apart = _contrast(washed, colors.background)
        assert apart >= _WASH_OFF_PAPER_FLOOR, (
            f"{theme.value}: a marked passage is {apart:.2f}:1 against its own paper"
        )
        through = _contrast(colors.foreground, washed)
        assert through >= _INK_THROUGH_WASH_FLOOR, (
            f"{theme.value}: body text through the wash is {through:.2f}:1"
        )


def test_the_wash_is_the_theme_colour_at_one_alpha() -> None:
    """The hue belongs to the theme, the alpha to the reader's experience of marking.

    One alpha for all three themes, applied where the colour is handed out: a theme that
    named its own would be a second place to change the strength of a highlight, and the
    reader would meet a differently emphatic mark depending on the paper under it.  The
    eight-bit channel is the only rounding between the constant and the painter.
    """
    assert 0.0 < SELECTION_ALPHA < 1.0
    for theme in Theme:
        wash = TypographySettings(theme=theme).text_selection_color()
        assert (wash.name(), wash.alpha()) == (
            THEMES[theme].text_selection,
            round(SELECTION_ALPHA * 255),
        ), f"{theme.value}: the wash is {wash.name()} at {wash.alpha()}"
