import QtQuick
import EbookReader 1.0
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

// Application shell: reading area, side panels, status bar and shortcuts.
ApplicationWindow {
    id: window

    // The controller is injected from Python right after the window is loaded via
    // `root.setProperty("ctl", controller)`, which means every component below is
    // constructed *before* it exists.  Values are therefore mirrored through
    // guarded read-only properties, so a binding can never read a property of
    // null; when `ctl` arrives, all of them re-evaluate.
    property var ctl: null

    readonly property bool cHasBook: ctl ? ctl.hasBook : false
    readonly property string cTitle: ctl && ctl.bookTitle.length > 0
                                     ? ctl.bookTitle + " — 电子书阅读器"
                                     : "电子书阅读器"
    readonly property color cBg: ctl ? ctl.backgroundColor : "#ffffff"
    readonly property color cFg: ctl ? ctl.textColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#808080"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property bool cTocOpen: ctl ? ctl.tocVisible : false
    readonly property bool cSettingsOpen: ctl ? ctl.settingsVisible : false
    readonly property bool cOutlineOpen: ctl ? ctl.outlineVisible : false
    // The controller keeps the outline toggled on across a book change, but an
    // empty column is worse than no column, so it only counts as open while the
    // current section has something to put in it (FR-019).
    readonly property bool cOutlineShown: cOutlineOpen && (ctl ? ctl.outlineAvailable : false)

    // The table of contents and the outline take real space beside the page;
    // settings is still a drawer that floats over it.
    readonly property int openColumns: (cTocOpen ? 1 : 0) + (cOutlineShown ? 1 : 0)

    // The side columns' width, in px.  Two numbers, and the reader owns one of them.
    //
    // `panelWidth` is the reader's own width: 0 until they drag the divider, then
    // whatever they last dragged it to - which is what a saved width is read back
    // into when the controller arrives (FR-078 / ADR-022).  `autoPanelWidth` is the
    // width the window picks for itself (ADR-014 ②), and it is what a reader who has
    // never dragged still gets, so nothing about the layout changes for them.
    property real panelWidth: 0
    readonly property real autoPanelWidth:
        Math.max(200, Math.min(320, (width - 440) / Math.max(1, openColumns)))
    // What a drag may ask for.  The floor is 160 px - a title still wraps legibly,
    // and `settings_store._PANEL_WIDTH_*` holds the same number for the file - and the
    // ceiling is 480 px, but the window has the last word: the text column never goes
    // below the 320 px it already has at the smallest allowed window with both columns
    // open (720 - 2 × 200), so no drag can squeeze the page out of the window or make
    // two columns overlap (ADR-014 ②).
    readonly property real minPanelWidth: 160
    readonly property real maxPanelWidth: 480
    readonly property real allowedPanelWidth:
        Math.max(minPanelWidth,
                 Math.min(maxPanelWidth, (width - 320) / Math.max(1, openColumns)))
    // The width the two columns are drawn at: the reader's number when there is one
    // (clamped to what this window can afford), the automatic one otherwise.  Both
    // columns read this, so they stay the same width (ADR-014 ②).
    readonly property real sidePanelWidth:
        panelWidth > 0
        ? Math.max(minPanelWidth, Math.min(allowedPanelWidth, panelWidth))
        : autoPanelWidth
    // One definition of the text column's width, used both by the item that shows
    // the page and by the page box handed to Python, so the two cannot drift apart.
    readonly property real pageColumnWidth:
        Math.max(120, width - (cTocOpen ? sidePanelWidth : 0)
                        - (cOutlineShown ? sidePanelWidth : 0))

    visible: true
    width: 1400
    height: 1380
    minimumWidth: 720
    minimumHeight: 560
    title: cTitle
    color: cBg

    // Pagination follows the text column, not the window: the status bar is not
    // part of the page and neither are the side panels.  A panel therefore does
    // change the page box, and the section is laid out again - that is the price of
    // the text keeping its full width when a panel is beside it rather than under
    // it, and the reader's place survives it because it is kept as a block index
    // (ADR-011).

    function reportSize() {
        // Called from Component.onCompleted, i.e. before Python injects the
        // controller, so it has to tolerate being called that early.
        if (!ctl) { return }
        ctl.setViewSize(Math.round(pageColumnWidth),
                        Math.round(height - statusBar.height),
                        window.screen ? window.screen.devicePixelRatio : 1.0)
    }

    onWidthChanged: resizeTimer.restart()
    onHeightChanged: resizeTimer.restart()

    // A panel opening or closing is a single discrete event, so it does not need
    // the debounce the timer exists to provide for the stream a resize drag makes.
    // It does need deferring to the next event loop turn, though: this handler runs
    // in the middle of the change cascade, before the derived column widths have
    // been recomputed, so reading them here would size the page for the layout that
    // is on its way out.
    onCTocOpenChanged: Qt.callLater(function () { window.reportSize() })
    onCOutlineShownChanged: Qt.callLater(function () { window.reportSize() })
    // A drag is a stream of changes like a window resize, so the page box follows it
    // the same way: one re-layout, after the handle has been let go (FR-051 / FR-078).
    onPanelWidthChanged: resizeTimer.restart()

    // The width the reader dragged to, read once as the controller arrives.  The shell
    // is built before Python injects it and is resized by nothing else, so a plain
    // assignment is enough here - and it is also what leaves `panelWidth` free for the
    // drag to write to, which a binding would block.
    onCtlChanged: {
        if (ctl && ctl.panelWidth > 0) { window.panelWidth = ctl.panelWidth }
    }

    // Where a drag asks for a width: clamped to what this window can afford, so that
    // the divider stops at the edge of the page instead of over it (FR-078).
    function dragPanelTo(pointerX) {
        window.panelWidth = Math.max(minPanelWidth, Math.min(allowedPanelWidth, pointerX))
    }

    // A double click on the divider hands the width back to the window and forgets the
    // preference entirely - not "pins the width the window happens to have now", which
    // would freeze today's size into every future session (FR-078).
    function forgetPanelWidth() {
        window.panelWidth = 0
        if (ctl) { ctl.savePanelWidth(0) }
    }

    onClosing: ctl.saveWindow(width, height)

    // Opening a book takes the window to full size (FR-077).  A reader who has asked
    // for a book has asked for a page, and a page wants the screen; the empty shell
    // keeps the size it was built with, so that opening a book from the file dialog
    // stays possible on a display smaller than that size.  It fires on every book, not
    // only the first one - picking another book from the menu is the same request - and
    // it leaves full screen alone: `F11` is the reader's own choice of how much chrome
    // to keep, and opening a book is not the moment to overrule it.
    onCHasBookChanged: {
        if (cHasBook && window.visibility !== Window.FullScreen) { window.showMaximized() }
    }

    function toggleFullScreen() {
        if (window.visibility === Window.FullScreen) { window.showNormal() }
        else { window.showFullScreen() }
    }

    Timer {
        id: resizeTimer
        interval: 140
        onTriggered: window.reportSize()
    }

    Component.onCompleted: window.reportSize()

    // ------------------------------------------------------------- right-click

    // One right-click surface for the whole window (FR-071).  It is declared *first*,
    // which puts it underneath the page and the panels, and that is the point: a
    // MouseArea only takes the buttons it lists, so a right press that nothing above
    // wants falls through the whole stack and lands here - over the text, over a
    // panel, over the status bar - while the left button is accepted higher up, by
    // the page's click zones (FR-062) and the panel rows, and never reaches this
    // layer.  Bottom rather than top, so that nothing loses a right-click it may
    // want: this layer only ever gets what is left over.  Scroll gestures are
    // explicitly left alone, or two-finger scrolling would be taken from the page
    // (FR-063).
    MouseArea {
        id: contextArea
        objectName: "contextArea"
        anchors.fill: parent
        acceptedButtons: Qt.RightButton
        scrollGestureEnabled: false
        onPressed: function (mouse) { contextMenu.popup(mouse.x, mouse.y) }
    }

    ReaderMenu {
        id: contextMenu
        ctl: window.ctl
        fullScreen: window.visibility === Window.FullScreen
        onOpenFileRequested: fileDialog.open()
        onToggleFullScreenRequested: window.toggleFullScreen()
    }

    // ---------------------------------------------------------------- reading

    Item {
        id: readingArea
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: statusBar.top
        clip: true

        // The text column.  Panels are siblings of this item, not overlays on it,
        // so this is the rectangle the text is set for and the rectangle the wheel
        // covers: a wheel over a panel never scrolls the text, and scrolling never
        // depends on where a panel happens to be drawn.
        Rectangle {
            id: pageArea
            // Named for the layout test, which checks that the three columns tile
            // the window exactly.
            objectName: "pageArea"
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            x: cTocOpen ? sidePanelWidth : 0
            width: window.pageColumnWidth
            color: cBg
            // While the debounce runs the image is still the old, wider one; the
            // PageItem letterboxes it, but clipping keeps the panel edge clean.
            clip: true

            ReadingView {
                id: readingView
                anchors.fill: parent
                ctl: window.ctl
                // The position bar and the 下一章 line stop short of the scroll bar,
                // which is drawn over this same edge (FR-076 / FR-074).
                rightInset: scrollBar.width
            }

            // The wheel scrolls the text, and that is all it does - the same gesture
            // a long web page answers to.  There is no click-to-turn-page any more:
            // with a continuous column, half-screens and page-sized jumps are
            // guesses at a unit that no longer exists (FR-073).
            //
            // Both deltas are forwarded, not just the angle.  A mouse notch arrives as
            // 120 units of angle, but a touchpad and a high-resolution or free-spinning
            // wheel arrive as screen pixels with no angle at all - and reading only the
            // angle left those gestures scrolling by exactly nothing.  Which delta
            // counts, and how far it moves, is the controller's decision, so that the
            // wheel, the keys and the menu move by the same units (FR-063 / FR-073).
            //
            // `acceptedDevices` has to name the TouchPad, or the wheel is dead on
            // Wayland: there the compositor's `wl_pointer.axis` events are attributed
            // to the seat's pointer device, which Qt registers as a TouchPad, while a
            // handler's default is Mouse alone - and the handler then declines every
            // wheel event before `onWheel` is reached (defect 24).
            WheelHandler {
                target: null
                acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
                onWheel: function (event) {
                    if (!ctl) { return }
                    ctl.wheelScroll(event.angleDelta.y, event.pixelDelta.y)
                    event.accepted = true
                }
            }

            // The scroll bar (FR-076).  Drawn over the right margin rather than
            // beside the text, so its appearance never changes the page box - a
            // relayout every time the bar shows up would be absurd.
            Item {
                id: scrollBar
                objectName: "scrollBar"
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                anchors.right: parent.right
                width: 14
                visible: ctl && ctl.hasBook && ctl.scrollMax > 1

                // One handle height per screenful of text, so the bar says how long
                // the chapter is as well as where the reader is in it.
                readonly property real handleHeight:
                    Math.max(30, height * (ctl && ctl.contentHeight > 0
                                           ? Math.min(1.0, ctl.viewportHeight / ctl.contentHeight)
                                           : 1.0))
                readonly property real usable: Math.max(1, height - handleHeight)

                function fractionFor(y) {
                    return (y - handleHeight / 2) / usable
                }

                Rectangle {
                    id: scrollHandle
                    objectName: "scrollHandle"
                    x: 4
                    width: parent.width - 8
                    radius: width / 2
                    color: ctl ? ctl.mutedColor : "#808080"
                    opacity: dragArea.pressed ? 0.9 : 0.45
                    height: parent.handleHeight
                    y: ctl && ctl.scrollMax > 0
                       ? (ctl.scrollOffset / ctl.scrollMax) * parent.usable
                       : 0
                }

                MouseArea {
                    id: dragArea
                    anchors.fill: parent
                    onPressed: function (mouse) { if (ctl) { ctl.scrollToFraction(scrollBar.fractionFor(mouse.y)) } }
                    onPositionChanged: function (mouse) {
                        if (pressed && ctl) { ctl.scrollToFraction(scrollBar.fractionFor(mouse.y)) }
                    }
                }
            }

            // Settings is the one panel that still floats over the text, so
            // clicking beside it closes it like a drawer - and only it: the
            // columns beside the text stay where they are, because a click beside
            // the drawer is not a request to put the map away.
            Rectangle {
                objectName: "settingsShield"
                anchors.fill: parent
                visible: cSettingsOpen
                color: "transparent"
                MouseArea {
                    anchors.fill: parent
                    onClicked: ctl && ctl.closeSettings()
                }
            }
        }

        TocSidebar {
            id: tocPanel
            objectName: "tocPanel"
            // Only the vertical edges are anchored: setting an anchor *and* an
            // explicit x on the same axis silently discards the x, which made the
            // panel permanently visible.  The width comes from here rather than from
            // the component, because it depends on how many columns are open.
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: window.sidePanelWidth
            ctl: window.ctl
            x: 0
            visible: cTocOpen
            onVisibleChanged: if (visible) revealCurrent()
        }

        // The contents column's edge, made draggable (FR-078 / ADR-022).  A sibling of
        // the panels rather than a child of one, because it has to straddle the seam: a
        // press on the panel side would be a row click to the list, and a press on the
        // page side a click to the text (FR-062) - the divider is the only thing on
        // screen whose job is the edge itself.
        Rectangle {
            id: tocDivider
            objectName: "tocDivider"
            visible: cTocOpen
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            // Wide enough to hit without aiming, and centred on the edge so that half
            // of it hangs over the text: a reader aiming at the boundary gets it.
            width: 8
            x: window.sidePanelWidth - width / 2
            color: dividerArea.containsMouse || dividerArea.pressed
                   ? Qt.alpha(cMuted, 0.45)
                   : "transparent"

            MouseArea {
                id: dividerArea
                objectName: "tocDividerArea"
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.SplitHCursor
                // Where the pointer was, and how wide the column was, when the button
                // went down.  Both are read through `readingArea`, which does not move:
                // this item travels with the column it is resizing, so a delta measured
                // in its own coordinates would feed the column's movement back into the
                // drag and the edge would shake.
                property real pressX: 0
                property real pressWidth: 0
                onPressed: function (mouse) {
                    pressX = dividerArea.mapToItem(readingArea, mouse.x, mouse.y).x
                    pressWidth = window.sidePanelWidth
                }
                onPositionChanged: function (mouse) {
                    if (!pressed) { return }
                    var moved = dividerArea.mapToItem(readingArea, mouse.x, mouse.y).x - pressX
                    window.dragPanelTo(pressWidth + moved)
                }
                // Once per drag, on release: the width is a preference, and the file is
                // not to be written on every mouse move (ADR-022).
                onReleased: if (ctl) { ctl.savePanelWidth(Math.round(window.panelWidth)) }
                onDoubleClicked: window.forgetPanelWidth()
            }
        }

        OutlinePanel {
            id: outlinePanel
            objectName: "outlinePanel"
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: window.sidePanelWidth
            ctl: window.ctl
            x: parent.width - width
            visible: cOutlineShown
            onVisibleChanged: if (visible) revealCurrent()
        }

        SettingsPanel {
            id: settingsPanel
            objectName: "settingsPanel"
            // Still a drawer: it keeps its own fixed width and slides over the text
            // column instead of taking a column of its own, which is also why it is
            // the only panel that keeps an animation - nothing has to move in step
            // with it.
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            ctl: window.ctl
            x: cSettingsOpen ? parent.width - width : parent.width
            Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
        }


        // Shown until a book is opened.
        ColumnLayout {
            anchors.centerIn: parent
            visible: !cHasBook
            spacing: 16

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "电子书阅读器"
                color: cFg
                font.pixelSize: 30
                font.bold: true
            }
            Text {
                Layout.alignment: Qt.AlignHCenter
                text: "命令行：ebook-reader 你的书.epub"
                color: cMuted
                font.pixelSize: 14
            }
            Rectangle {
                Layout.alignment: Qt.AlignHCenter
                implicitWidth: 168
                implicitHeight: 40
                radius: 6
                color: cAccent
                Text {
                    anchors.centerIn: parent
                    text: "打开文件…"
                    color: cFg
                    font.pixelSize: 15
                }
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: fileDialog.open()
                }
            }
        }
    }

    StatusBar {
        id: statusBar
        ctl: window.ctl
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
    }

    // --------------------------------------------------------------- shortcuts

    // Scrolling, browser-style: one line per arrow key, one screen per page key,
    // the ends of the chapter on Home and End (FR-073).
    //
    // The sequences are spelled the way ``QKeySequence`` parses them, which is also
    // the way the menu's hint column writes them: "PgDown", not "PageDown", and "]"
    // rather than the C++ enumeration name "BracketRight".  A sequence Qt cannot
    // parse is silently never registered - the key simply does nothing - so these
    // spellings are load-bearing, and `test_scroll_view.py` presses each one.
    Shortcut { sequence: "Down"; onActivated: ctl && ctl.scrollDown() }
    Shortcut { sequence: "Up"; onActivated: ctl && ctl.scrollUp() }
    Shortcut { sequences: ["PgDown", "Space", "Right"]
               onActivated: ctl && ctl.scrollPageDown() }
    Shortcut { sequences: ["PgUp", "Backspace", "Left"]
               onActivated: ctl && ctl.scrollPageUp() }
    Shortcut { sequence: "Home"; onActivated: ctl && ctl.scrollToTop() }
    Shortcut { sequence: "End"; onActivated: ctl && ctl.scrollToBottom() }
    Shortcut { sequence: "]"; onActivated: ctl && ctl.nextSection() }
    Shortcut { sequence: "["; onActivated: ctl && ctl.previousSection() }
    Shortcut { sequence: "T"; onActivated: ctl && ctl.toggleToc() }
    Shortcut { sequence: "O"; onActivated: ctl && ctl.toggleOutline() }
    Shortcut { sequence: "S"; onActivated: ctl && ctl.toggleSettings() }
    Shortcut { sequence: "Escape"; onActivated: ctl && ctl.closePanels() }
    // The passage is copied the moment the gesture that marked it ends; this is the way
    // to put it back on the clipboard after something else has taken it over - the
    // passage stays marked until the reader puts it down with a click (FR-070).
    Shortcut { sequence: "Ctrl+C"; onActivated: ctl && ctl.copySelection() }
    Shortcut { sequence: "Ctrl+O"; onActivated: fileDialog.open() }
    Shortcut { sequence: "Ctrl+Q"; onActivated: Qt.quit() }
    Shortcut { sequence: "Ctrl++"; onActivated: ctl && ctl.increaseFont() }
    Shortcut { sequence: "Ctrl+="; onActivated: ctl && ctl.increaseFont() }
    Shortcut { sequence: "Ctrl+-"; onActivated: ctl && ctl.decreaseFont() }
    Shortcut { sequence: "F11"; onActivated: window.toggleFullScreen() }

    FileDialog {
        id: fileDialog
        title: "选择 EPUB 文件"
        nameFilters: ["EPUB 电子书 (*.epub)", "所有文件 (*)"]
        onAccepted: ctl.openBook(window.localPath(selectedFile))
    }

    // Turns a file:// url into a plain filesystem path.
    function localPath(url) {
        var text = "" + url
        if (text.indexOf("file://") === 0) {
            text = text.substring(7)
            try { text = decodeURIComponent(text) } catch (error) { }
        }
        return text
    }
}

