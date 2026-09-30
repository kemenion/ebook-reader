import QtQuick
import QtQuick.Controls

// Every operation the reader has, on the right mouse button (FR-071).
//
// Named `ReaderMenu`, not `ContextMenu`: Qt's FluentWinUI3 style registers a
// `ContextMenu` singleton in an implicitly imported module, and that name wins over
// this file - the type then resolves to something the engine refuses to create
// ("Type cannot be created in QML").  The object name is what the tests look for.
//
// The point of this menu is discoverability: the shortcuts exist, but a reader who
// has never opened the settings drawer has no way of knowing that `T` means 目录 or
// that a section outline exists at all.  So the menu is deliberately **flat** -
// grouped by separators rather than hidden in submenus - with one exception: the six
// rows whose meaning *is* the current value (字号 / 行距 / 边距 / 字体 / 对齐 / 主题)
// open a short submenu and carry that value in their own label, so nothing is
// hidden behind a row that does not say what it does.
//
// Three more decisions worth recording:
//
// * **Rows are enabled only while they can do something.**  Panel rows need a book;
//   目录 additionally needs the book to have brought a table of contents, and
//   本节大纲 a section with something to list (FR-019).  A greyed-out row answers
//   "why did nothing happen" in a way a silent no-op cannot.
// * **The tick is decoration, not state.**  A row is not `checkable`: a checkable
//   QQC2 row toggles its own `checked` before firing `triggered`, which would fight
//   the binding that mirrors the controller.  The controller stays the single source
//   of truth and the row only shows what it says (`marked`).
// * **The menu paints itself.**  A popup is not part of the item tree the panels
//   take their colours from, so the frame, the rows, the separator and the arrow are
//   drawn here in the reader's colours; the palette is set too, for the pieces the
//   style still draws on its own.
Menu {
    id: root
    objectName: "readerMenu"

    // Injected by Main.qml, as for every other component here.
    property var ctl: null

    // Told the window's state rather than asking for it: a popup is not in the
    // window's item tree to read `visibility` from.
    property bool fullScreen: false

    // The two operations that belong to the window, not to the controller.
    signal openFileRequested()
    signal toggleFullScreenRequested()

    readonly property bool cHasBook: ctl ? ctl.hasBook : false
    readonly property bool cHasPanels: ctl
                                      ? (ctl.tocVisible || ctl.outlineVisible
                                         || ctl.settingsVisible)
                                      : false
    readonly property color cPanel: ctl ? ctl.panelColor : "#eaeaea"
    readonly property color cText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#808080"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"

    // The style Qt picks here (Fusion) paints a menu with `palette.base` - white -
    // which would drop a white rectangle into the middle of the dark theme, so the
    // roles its remaining pieces use are set as well.
    palette.window: cPanel
    palette.base: cPanel
    palette.text: cText
    palette.windowText: cText
    palette.highlight: cAccent
    palette.highlightedText: cText
    palette.mid: cMuted
    palette.dark: cMuted

    // The style's frame is 200 px wide, which leaves the rows too little room once a
    // tick gutter and a value column have been taken out of it.  The list view hands
    // its own width down to every row, so the width is decided here.
    implicitWidth: 268

    background: MenuSurface { }

    // The parent row of a submenu is not one of the items declared below: nesting a
    // Menu creates that row for us, through this delegate.  Pointing it at MenuAction
    // is what keeps those six rows aligned with the rest instead of falling back to
    // the style's own padding.
    delegate: MenuAction { }


    // One row: label on the left, shortcut or current value on the right.
    //
    // The Basic style shows a tick only on checkable rows and has nowhere to put a
    // hint, so its labels start at different x depending on the row - which reads as
    // a ragged column.  This component gives every row the same left gutter and its
    // own content item, so the menu has two clean columns.
    component MenuAction: MenuItem {
        id: action

        //: Shortcut, or the current value on the rows that open a submenu.
        property string hint: ""
        //: Shows a tick.  Driven by the controller, never toggled by the click.
        property bool marked: false

        implicitHeight: 30
        // The gutter holds the tick on every row, ticked or not.
        leftPadding: 30
        // Submenu rows keep room for the arrow the style draws on the right.
        rightPadding: action.subMenu ? 26 : 12

        indicator: Text {
            x: 10
            y: action.topPadding + (action.availableHeight - height) / 2
            text: "\u2713"
            color: root.cText
            font.pixelSize: 13
            opacity: action.enabled ? 1.0 : 0.45
            visible: action.marked
        }

        // Hovered and pressed rows take the same accent the panels use for the
        // current row, so the whole window highlights the same way.
        background: Rectangle {
            color: action.highlighted ? root.cAccent : "transparent"
            opacity: action.down ? 0.85 : (action.highlighted ? 0.6 : 1.0)
        }

        // The style draws a bitmap; a plain triangle follows the row's text colour.
        arrow: Text {
            x: action.width - width - action.padding
            y: action.topPadding + (action.availableHeight - height) / 2
            text: "\u25b8"
            color: root.cText
            font.pixelSize: 12
            visible: action.subMenu
        }

        contentItem: Item {
            readonly property real hintWidth: hintLabel.implicitWidth > 0
                                               ? hintLabel.implicitWidth + 26 : 0

            implicitWidth: Math.max(210, label.implicitWidth + hintWidth)
            implicitHeight: label.implicitHeight

            Text {
                id: label
                anchors.left: parent.left
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: Math.min(implicitWidth, parent.width - parent.hintWidth)
                text: action.text
                color: root.cText
                opacity: action.enabled ? 1.0 : 0.45
                font.pixelSize: 14
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }

            Text {
                id: hintLabel
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: action.hint
                color: root.cText
                opacity: 0.55
                font.pixelSize: 12
            }
        }
    }

    // A submenu: the same frame, because it is a popup of its own and would otherwise
    // fall back to the style's colours - white in the dark theme.  The rows inside it
    // are created through this menu's own delegate, so the delegate has to be set
    // again here; separators, rows and frame all take their colours from the reader,
    // so no palette is needed (a menu of two or three rows never scrolls, and the
    // scroll indicator is the only piece that would have used one).
    component ValueMenu: Menu {
        implicitWidth: 268
        background: MenuSurface { }
        delegate: MenuAction { }
    }

    // The frame around a menu, in the reader's colours rather than the style's.
    component MenuSurface: Rectangle {
        color: root.cPanel
        border.color: root.cMuted
        border.width: 1
    }

    // The style's separator is 3 px of a fixed grey that ignores the theme; nine
    // pixels with the muted colour reads as a break between groups.
    component MenuDivider: MenuSeparator {
        implicitHeight: 9

        contentItem: Rectangle {
            implicitWidth: 200
            implicitHeight: 1
            color: root.cMuted
            opacity: 0.45
        }
    }

    // ----------------------------------------------------------- opening a book

    MenuAction {
        objectName: "menuOpenFile"
        text: "打开文件…"
        hint: "Ctrl+O"
        onTriggered: root.openFileRequested()
    }

    MenuDivider { }

    // --------------------------------------------------------------- the columns

    MenuAction {
        objectName: "menuToc"
        text: "目录"
        hint: "T"
        marked: ctl ? ctl.tocVisible : false
        enabled: root.cHasBook && (ctl ? ctl.tocAvailable : false)
        onTriggered: ctl && ctl.toggleToc()
    }

    MenuAction {
        objectName: "menuOutline"
        text: "本节大纲"
        hint: "O"
        marked: ctl ? (ctl.outlineVisible && ctl.outlineAvailable) : false
        enabled: ctl ? ctl.outlineAvailable : false
        onTriggered: ctl && ctl.toggleOutline()
    }

    MenuAction {
        objectName: "menuSettings"
        text: "设置"
        hint: "S"
        marked: ctl ? ctl.settingsVisible : false
        enabled: root.cHasBook
        onTriggered: ctl && ctl.toggleSettings()
    }

    MenuDivider { }

    // ------------------------------------------------------------------ the page

    // The two gestures on the page offer no row of their own - a drag is not something
    // a menu can describe - so the one operation they carry appears here, and the row
    // greys out until there is something to copy (FR-070 / FR-071).  It is the reader's
    // second way to the clipboard, for the reader who has just marked a passage and
    // would rather not lose it by reaching for the pointer again.
    MenuAction {
        objectName: "menuCopy"
        text: "复制"
        hint: "Ctrl+C"
        enabled: ctl ? ctl.hasSelection : false
        onTriggered: ctl && ctl.copySelection()
    }

    MenuDivider { }

    // ---------------------------------------------------------------- navigation

    MenuAction {
        objectName: "menuScrollUp"
        text: "向上滚动"
        hint: "\u2191"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollUp()
    }

    MenuAction {
        objectName: "menuScrollDown"
        text: "向下滚动"
        hint: "\u2193"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollDown()
    }

    MenuAction {
        objectName: "menuScrollPageUp"
        text: "上滚一屏"
        hint: "PgUp"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollPageUp()
    }

    MenuAction {
        objectName: "menuScrollPageDown"
        text: "下滚一屏"
        hint: "PgDn"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollPageDown()
    }

    MenuAction {
        objectName: "menuSectionTop"
        text: "本卷开头"
        hint: "Home"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollToTop()
    }

    MenuAction {
        objectName: "menuSectionEnd"
        text: "本卷结尾"
        hint: "End"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.scrollToBottom()
    }

    MenuAction {
        objectName: "menuPreviousSection"
        text: "上一章"
        hint: "["
        enabled: root.cHasBook
        onTriggered: ctl && ctl.previousSection()
    }

    MenuAction {
        objectName: "menuNextSection"
        text: "下一章"
        hint: "]"
        enabled: root.cHasBook
        onTriggered: ctl && ctl.nextSection()
    }

    MenuDivider { }


    // -------------------------------------------------------------- typesetting

    ValueMenu {
        objectName: "menuFontSize"
        title: "字号 " + (ctl ? ctl.fontSize.toFixed(0) : "")
        MenuAction {
            objectName: "menuFontSizeUp"
            width: parent.width
            text: "增大字号"
            hint: "Ctrl+="
            enabled: root.cHasBook
            onTriggered: ctl && ctl.increaseFont()
        }
        MenuAction {
            objectName: "menuFontSizeDown"
            width: parent.width
            text: "减小字号"
            hint: "Ctrl+-"
            enabled: root.cHasBook
            onTriggered: ctl && ctl.decreaseFont()
        }
    }

    ValueMenu {
        objectName: "menuLineHeight"
        title: "行距 " + (ctl ? ctl.lineHeight.toFixed(2) : "")
        MenuAction {
            objectName: "menuLineHeightUp"
            width: parent.width
            text: "放宽行距"
            enabled: root.cHasBook
            onTriggered: ctl && ctl.increaseLineHeight()
        }
        MenuAction {
            objectName: "menuLineHeightDown"
            width: parent.width
            text: "收紧行距"
            enabled: root.cHasBook
            onTriggered: ctl && ctl.decreaseLineHeight()
        }
    }

    ValueMenu {
        objectName: "menuMargin"
        title: "边距 " + (ctl ? ctl.margin : "")
        MenuAction {
            objectName: "menuMarginWide"
            width: parent.width
            text: "放宽边距"
            enabled: root.cHasBook
            onTriggered: ctl && ctl.increaseMargin()
        }
        MenuAction {
            objectName: "menuMarginNarrow"
            width: parent.width
            text: "收窄边距"
            enabled: root.cHasBook
            onTriggered: ctl && ctl.decreaseMargin()
        }
    }

    ValueMenu {
        objectName: "menuFont"
        title: "字体 " + (ctl ? ctl.fontChoiceLabel : "")
        MenuAction {
            objectName: "menuFontSerif"
            width: parent.width
            text: "宋体"
            marked: ctl ? ctl.fontChoice === "serif" : false
            enabled: root.cHasBook
            onTriggered: ctl && ctl.setFontChoice("serif")
        }
        MenuAction {
            objectName: "menuFontSans"
            width: parent.width
            text: "黑体"
            marked: ctl ? ctl.fontChoice === "sans" : false
            enabled: root.cHasBook
            onTriggered: ctl && ctl.setFontChoice("sans")
        }
        MenuAction {
            objectName: "menuFontKai"
            width: parent.width
            text: "楷体"
            marked: ctl ? ctl.fontChoice === "kai" : false
            enabled: root.cHasBook
            onTriggered: ctl && ctl.setFontChoice("kai")
        }
    }

    ValueMenu {
        objectName: "menuJustify"
        title: "对齐 " + (ctl ? (ctl.justify ? "两端" : "左对齐") : "")
        MenuAction {
            objectName: "menuJustifyOn"
            width: parent.width
            text: "两端对齐"
            marked: ctl ? ctl.justify : false
            enabled: root.cHasBook
            onTriggered: if (ctl && !ctl.justify) { ctl.toggleJustify() }
        }
        MenuAction {
            objectName: "menuJustifyOff"
            width: parent.width
            text: "左对齐"
            marked: ctl ? !ctl.justify : false
            enabled: root.cHasBook
            onTriggered: if (ctl && ctl.justify) { ctl.toggleJustify() }
        }
    }

    ValueMenu {
        objectName: "menuTheme"
        title: "主题 " + (ctl ? ctl.themeLabel : "")
        MenuAction {
            objectName: "menuThemeLight"
            width: parent.width
            text: "日间"
            marked: ctl ? ctl.themeName === "light" : false
            onTriggered: ctl && ctl.setTheme("light")
        }
        MenuAction {
            objectName: "menuThemeSepia"
            width: parent.width
            text: "米色"
            marked: ctl ? ctl.themeName === "sepia" : false
            onTriggered: ctl && ctl.setTheme("sepia")
        }
        MenuAction {
            objectName: "menuThemeDark"
            width: parent.width
            text: "夜间"
            marked: ctl ? ctl.themeName === "dark" : false
            onTriggered: ctl && ctl.setTheme("dark")
        }
    }

    MenuDivider { }

    // ------------------------------------------------------------------- the app

    MenuAction {
        objectName: "menuClosePanels"
        text: "关闭所有面板"
        hint: "Esc"
        enabled: root.cHasPanels
        onTriggered: ctl && ctl.closePanels()
    }

    MenuAction {
        objectName: "menuFullScreen"
        text: root.fullScreen ? "退出全屏" : "全屏"
        hint: "F11"
        marked: root.fullScreen
        onTriggered: root.toggleFullScreenRequested()
    }

    MenuAction {
        objectName: "menuQuit"
        text: "退出"
        hint: "Ctrl+Q"
        onTriggered: Qt.quit()
    }
}
