"""The right-click menu: what opens it, what is in it, and that the rows work.

The point of this menu (FR-071) is that a reader finds everything without reading
documentation, so the tests treat it as a surface rather than as QML: a *real* right
button must open it - that is an event-routing question, and the answer is not
obvious once the page's own click zones are in the way - every operation must have a
row, and clicking a row must actually run the controller behind it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickItem

from conftest import at, centre, click, item, open_long_section, pump, set_window_size, visible

MENU = "readerMenu"

#: Every row the menu must offer.  An entry missing here means an operation exists
#: that a mouse cannot reach - which is exactly what FR-071 was asked for.
ROWS = (
    "打开文件…",
    "目录",
    "本节大纲",
    "设置",
    "复制",
    "向上滚动",
    "向下滚动",
    "上滚一屏",
    "下滚一屏",
    "本卷开头",
    "本卷结尾",
    "上一章",
    "下一章",
    "字号",
    "行距",
    "边距",
    "字体",
    "对齐",
    "主题",
    "关闭所有面板",
    "全屏",
    "退出",
)

#: The submenus behind the six rows whose meaning is the current value.
CHOICES = (
    "增大字号",
    "减小字号",
    "放宽行距",
    "收紧行距",
    "放宽边距",
    "收窄边距",
    "宋体",
    "黑体",
    "楷体",
    "两端对齐",
    "左对齐",
    "日间",
    "米色",
    "夜间",
)


def menu(window) -> QObject:
    found = window.findChild(QObject, MENU)
    assert found is not None, f"{MENU} is missing from Main.qml"
    return found


def rows(window) -> list[QQuickItem]:
    """Every row of the menu, found by the properties only a row has.

    Read off the objects rather than from the QML source, so a row that is *declared*
    but never actually added to the menu fails the test.
    """
    found = [
        child
        for child in menu(window).findChildren(QObject)
        if child.property("hint") is not None
    ]
    assert found, "the menu has no rows"
    return found


def labels(window) -> list[str]:
    return [row.property("text") for row in rows(window)]


def row(window, label: str) -> QQuickItem:
    """The row a reader would click, whose label *starts* with ``label``.

    Submenu rows carry their current value in the label (``字号 18``), so they are
    matched by prefix.
    """
    for child in rows(window):
        text = child.property("text")
        if text and text.startswith(label):
            return child
    raise AssertionError(f"no menu row labelled {label!r} in {labels(window)}")


def menu_item(window, name: str) -> QObject:
    """A row by object name - the way to reach a row inside a closed submenu."""
    for child in menu(window).findChildren(QObject):
        if child.objectName() == name:
            return child
    raise AssertionError(f"no menu row named {name!r}")


def open_menu(window, position: tuple[int, int] = (700, 400)) -> QObject:
    """A real right-click, and the menu that should come out of it."""
    click(window, position, right=True)
    pump(200)
    assert menu(window).property("visible"), "the right button did not open the menu"
    return menu(window)


def close_menu(window) -> None:
    QMetaObject.invokeMethod(menu(window), "close", Qt.ConnectionType.DirectConnection)
    pump(150)


def _open_scrollable_section(controller) -> int:
    """Show a section with room to scroll, so the scroll bar is there to click.

    Deliberately not the cover, which fits on one screen and has no bar at all.
    """
    for index in range(controller.sectionCount):
        controller.goToSection(index)
        if controller.scrollMax > 2 * controller.viewportHeight:
            controller.scrollToTop()
            pump(200)
            return index
    pytest.skip("reference book has no section long enough to scroll in")


# -------------------------------------------------------------------- opening it


def test_the_right_button_opens_the_menu_over_the_text(
    shell, kangpo_path: Path, warnings
) -> None:
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    # The documented default size, because this test is about where the menu lands:
    # a menu opened near the window's right edge is moved left to stay on screen, and
    # how far the edge is from the click depends on the display (FR-077).
    set_window_size(window)

    assert not menu(window).property("visible")

    opened = open_menu(window, (700, 400))
    assert (opened.property("x"), opened.property("y")) == (700, 400)
    assert warnings == []


def test_the_right_button_opens_the_menu_over_the_panels_and_the_status_bar(
    shell, kangpo_path: Path, warnings
) -> None:
    """Every corner of the window answers the right button, not only the page."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    assert visible(window, "tocPanel")          # the panel is where it is expected

    open_menu(window, (160, 300))
    assert menu(window).property("x") == 160
    close_menu(window)

    height = int(window.property("height"))
    open_menu(window, (700, height - 8))
    # The menu is taller than the space below the cursor, so it is moved up instead
    # of being allowed to hang off the window.
    assert menu(window).property("y") <= height - 8
    assert warnings == []


def test_the_right_button_only_opens_the_menu(shell, kangpo_path: Path) -> None:
    """The right-click layer is only interested in the right button (FR-062).

    The same spot under the left button is a control - here the scroll bar - so the
    pair of clicks says exactly what the layer takes and what it leaves alone.
    """
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    _open_scrollable_section(controller)

    bar = item(window, "scrollBar")
    assert visible(window, "scrollBar")
    field = at(bar, bar.width() / 2, bar.height() * 0.75)
    before = controller.scrollOffset

    click(window, field, right=True)
    pump(150)
    assert menu(window).property("visible")                 # the menu, and nothing else
    assert controller.scrollOffset == before
    close_menu(window)

    click(window, field)
    pump(240)
    assert controller.scrollOffset > before                 # the left button did act


# ----------------------------------------------------------------------- contents


def test_the_menu_offers_every_operation(shell, kangpo_path: Path) -> None:
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    open_menu(window)

    offered = labels(window)
    for expected in ROWS + CHOICES:
        assert any(text and text.startswith(expected) for text in offered), (
            f"{expected!r} is not in the menu: {offered}"
        )
    # ... and nothing else: a duplicate or an extra row is a row without a home in
    # the documentation, which is how a menu rots.
    assert len(offered) == len(ROWS) + len(CHOICES)


def test_the_menu_shows_which_columns_are_open(
    shell, kangpo_path: Path, heading_section: int
) -> None:
    """A tick means "this is on now", and it follows the controller both ways."""
    _, window, controller = shell

    # No book: the rows that need one are greyed out rather than doing nothing.
    assert not row(window, "目录").property("enabled")
    assert not row(window, "向下滚动").property("enabled")
    assert not row(window, "设置").property("enabled")
    assert not row(window, "复制").property("enabled")     # nothing marked either
    assert bool(row(window, "打开文件…").property("enabled"))    # always available

    assert controller.openBook(str(kangpo_path))
    pump(240)
    assert bool(row(window, "目录").property("marked"))       # it came up with the book
    assert not row(window, "本节大纲").property("marked")

    controller.toggleToc()
    assert not row(window, "目录").property("marked")

    # The outline needs a section with headings to be on offer at all (FR-019).
    controller.goToSection(heading_section)
    assert bool(row(window, "本节大纲").property("enabled"))
    controller.toggleOutline()
    assert bool(row(window, "本节大纲").property("marked"))
    controller.toggleOutline()
    assert not row(window, "本节大纲").property("marked")


def test_the_menu_knows_whether_the_book_has_a_table_of_contents(
    shell, kangpo_path: Path
) -> None:
    _, window, controller = shell
    assert not controller.tocAvailable              # nothing open, nothing to show
    assert controller.openBook(str(kangpo_path))
    assert controller.tocAvailable
    assert bool(row(window, "目录").property("enabled"))


def test_the_copy_row_is_greyed_until_there_is_something_to_copy(
    shell, kangpo_path: Path
) -> None:
    """The row that carries the page's own gesture, in both of its states (FR-070 / FR-071).

    A row that is always enabled invites the reader to click it and watch nothing happen,
    so it is greyed until a passage is marked - a book alone is not a passage - and then
    it does what its name says: the same thing ``Ctrl+C`` does, on the passage that is
    already on the page.
    """
    _, window, controller = shell
    assert not row(window, "复制").property("enabled")     # no book, so nothing to mark

    # A section with room to scroll in, so two points six lines apart are two positions.
    open_long_section(controller, kangpo_path)
    assert not row(window, "复制").property("enabled")     # a book is not a passage

    # A passage without the clipboard: the row has to be the thing that copies it.
    page = item(window, "pageView")
    x = int(page.width() / 2)
    controller.beginSelection(x, int(6 * controller.lineStep))
    controller.extendSelection(x, int(12 * controller.lineStep))
    assert controller.hasSelection
    assert bool(row(window, "复制").property("enabled"))

    clipboard = QGuiApplication.clipboard()
    clipboard.setText("别的东西")
    open_menu(window)
    click(window, centre(row(window, "复制")))
    pump(200)

    assert clipboard.text() == controller.selectedText
    assert not menu(window).property("visible")




# ------------------------------------------------------------------------ using it


def test_clicking_a_row_runs_the_operation_it_names(
    shell, kangpo_path: Path, warnings
) -> None:
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)

    open_menu(window)
    click(window, centre(row(window, "下一章")))
    pump(200)
    assert not menu(window).property("visible")            # it closes behind itself
    assert controller.sectionIndex == 1                    # the book opens on its cover

    open_menu(window)
    click(window, centre(row(window, "目录")))
    pump(200)
    assert not controller.tocVisible
    assert warnings == []


def test_a_submenu_opens_on_a_click_and_its_choice_sticks(
    shell, kangpo_path: Path, warnings
) -> None:
    """The value rows are submenus; both hops have to work with the mouse alone."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)
    assert controller.themeName == "light"

    open_menu(window)
    click(window, centre(row(window, "主题")))
    pump(300)

    submenu = menu(window).findChild(QObject, "menuTheme")
    assert submenu is not None and submenu.property("visible")
    click(window, centre(row(window, "夜间")))
    pump(200)

    assert controller.themeName == "dark"
    assert not menu(window).property("visible")
    assert not submenu.property("visible")
    assert warnings == []


def test_a_row_carries_the_shortcut_it_stands_for(shell, kangpo_path: Path) -> None:
    """The hint column is the only place the shortcuts are discoverable."""
    _, window, controller = shell
    assert controller.openBook(str(kangpo_path))
    pump(240)

    assert row(window, "目录").property("hint") == "T"
    assert row(window, "上一章").property("hint") == "["
    assert row(window, "向下滚动").property("hint") == "↓"
    assert row(window, "下滚一屏").property("hint") == "PgDn"
    assert menu_item(window, "menuSettings").property("hint") == "S"
    assert menu_item(window, "menuSectionTop").property("hint") == "Home"
    assert menu_item(window, "menuQuit").property("hint") == "Ctrl+Q"
    assert row(window, "字号").property("hint") == ""       # its value is in the label
