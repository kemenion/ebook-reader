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
    readonly property color cMuted: ctl ? ctl.mutedColor : "#8a8a8a"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property bool cTocOpen: ctl ? ctl.tocVisible : false
    readonly property bool cSettingsOpen: ctl ? ctl.settingsVisible : false

    visible: true
    width: 1000
    height: 1380
    minimumWidth: 520
    minimumHeight: 560
    title: cTitle
    color: cBg

    // Pagination follows the reading area, not the window: the status bar is not
    // part of the page.  The side panels float *above* the page rather than
    // shrinking it, so opening the table of contents does not change the page box
    // and therefore triggers no re-layout at all.
   
    function reportSize() {
        // Called from Component.onCompleted, i.e. before Python injects the
        // controller, so it has to tolerate being called that early.
        if (!ctl) { return }
        ctl.setViewSize(Math.round(width), Math.round(height - statusBar.height),
                        window.screen ? window.screen.devicePixelRatio : 1.0)
    }

    onWidthChanged: resizeTimer.restart()
    onHeightChanged: resizeTimer.restart()
    onClosing: ctl.saveWindow(width, height)

    Timer {
        id: resizeTimer
        interval: 140
        onTriggered: window.reportSize()
    }

    Component.onCompleted: window.reportSize()

    // ---------------------------------------------------------------- reading

    Item {
        id: readingArea
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: statusBar.top
        clip: true

        Rectangle {
            anchors.fill: parent
            color: cBg
        }

        PageView {
            id: pageView
            anchors.fill: parent
            ctl: window.ctl
        }

        // Click zones: forward on the right half, back on the left (FR-062).
        MouseArea {
            anchors.left: parent.left
            anchors.right: parent.horizontalCenter
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            acceptedButtons: Qt.LeftButton
            onClicked: ctl && ctl.previousPage()
        }

        MouseArea {
            anchors.left: parent.horizontalCenter
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            acceptedButtons: Qt.LeftButton
            onClicked: ctl && ctl.nextPage()
        }

        WheelHandler {
            target: null
            onWheel: function (event) {
                if (Math.abs(event.angleDelta.y) < 40) { return }
                if (event.angleDelta.y < 0) { ctl && ctl.nextPage() }
                else { ctl && ctl.previousPage() }
            }
        }

        // Clicking beside an open panel closes it, like a drawer.
        Rectangle {
            anchors.fill: parent
            visible: cTocOpen || cSettingsOpen
            color: "transparent"
            MouseArea {
                anchors.fill: parent
                onClicked: ctl && ctl.closePanels()
            }
        }

        TocSidebar {
            id: tocPanel
            // Only the vertical edges are anchored: setting an anchor *and* an
            // explicit x on the same axis silently discards the x, which made the
            // panel permanently visible and its slide animation a no-op.
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            ctl: window.ctl
            x: cTocOpen ? 0 : -width
            Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            onXChanged: if (cTocOpen && Math.abs(x) < 1) revealCurrent()
        }

        SettingsPanel {
            id: settingsPanel
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

    Shortcut { sequences: ["Right", "PageDown", "Space", "Down"]
               onActivated: ctl && ctl.nextPage() }
    Shortcut { sequences: ["Left", "PageUp", "Backspace", "Up"]
               onActivated: ctl && ctl.previousPage() }
    Shortcut { sequence: "Home"; onActivated: ctl && ctl.firstPage() }
    Shortcut { sequence: "End"; onActivated: ctl && ctl.lastPage() }
    Shortcut { sequence: "BracketRight"; onActivated: ctl && ctl.nextSection() }
    Shortcut { sequence: "BracketLeft"; onActivated: ctl && ctl.previousSection() }
    Shortcut { sequence: "T"; onActivated: ctl && ctl.toggleToc() }
    Shortcut { sequence: "S"; onActivated: ctl && ctl.toggleSettings() }
    Shortcut { sequence: "Escape"; onActivated: ctl && ctl.closePanels() }
    Shortcut { sequence: "StandardKey.Open"; onActivated: fileDialog.open() }
    Shortcut { sequence: "StandardKey.Quit"; onActivated: Qt.quit() }
    Shortcut { sequence: "Ctrl++"; onActivated: ctl && ctl.increaseFont() }
    Shortcut { sequence: "Ctrl+="; onActivated: ctl && ctl.increaseFont() }
    Shortcut { sequence: "Ctrl+-"; onActivated: ctl && ctl.decreaseFont() }
    Shortcut {
        sequence: "F11"
        onActivated: window.visibility === Window.FullScreen
                     ? window.showNormal() : window.showFullScreen()
    }

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

