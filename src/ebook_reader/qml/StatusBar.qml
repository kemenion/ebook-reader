import QtQuick
import QtQuick.Controls
import EbookReader 1.0

// Progress text and section title (FR-067), plus the one control that is always on
// screen: the table-of-contents toggle (FR-013).
//
// The counter is book-wide and position-based rather than a page number: with the text
// running as one column there are no pages to number, so the middle label reports how
// far into the chapter and into the book the reader is (FR-075).  The thin bar along
// the bottom edge is the same number, drawn.
//
// The button lives here rather than in a toolbar of its own, because the status bar
// is outside the page box: a control placed here costs the text nothing, and it is
// in the same place whatever the reader is looking at.  It is also what makes the
// panel findable at all - the `T` shortcut is only documented inside the settings
// drawer, which itself is opened with a shortcut.
Rectangle {
    id: root

    // Injected by Main.qml; guarded while null.
    property var ctl: null

    readonly property color cPanel: ctl ? ctl.panelColor : "#eaeaea"
    readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#808080"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property string cSectionTitle: ctl ? ctl.sectionTitle : ""
    readonly property string cProgressText: ctl ? ctl.progressText : ""
    readonly property string cDiagnostics: ctl ? ctl.statusText : ""
    readonly property string cError: ctl ? ctl.errorMessage : ""
    readonly property real cProgress: ctl ? ctl.progress : 0

    // A passing word from the controller - 已复制 12 字 once a marked passage reaches
    // the clipboard (FR-070) - which takes the diagnostics' place for a few seconds.
    // The copy happens by itself, out of sight, so the answer to "did that work?" has
    // to appear somewhere the reader is already looking; the diagnostics are the
    // control they can do without while it is shown.
    property string cMessage: ""

    Connections {
        target: root.ctl
        function onStatusMessage(text) {
            root.cMessage = text
            messageTimer.restart()
        }
    }

    Timer {
        id: messageTimer
        interval: 2400
        onTriggered: root.cMessage = ""
    }

    height: 34
    color: cPanel

    Rectangle {
        anchors.top: parent.top
        width: parent.width
        height: 1
        color: cMuted
        opacity: 0.25
    }

    Rectangle {
        // Thin book-wide progress indicator along the bottom edge.
        anchors.bottom: parent.bottom
        width: parent.width * Math.max(0, Math.min(1, cProgress))
        height: 2
        color: cAccent
        Behavior on width { NumberAnimation { duration: 120 } }
    }

    // ------------------------------------------------------------------ contents

    Rectangle {
        id: tocToggle
        objectName: "tocToggle"

        // The state the colours below are drawn from, and what the tests read.  Not
        // a `checkable` QQC2 button: such a button flips its own `checked` before
        // emitting `clicked`, which would overwrite the binding onto the controller
        // and leave the button and the panel disagreeing (the same reason the menu
        // rows use `marked`).
        property string text: "目录"
        property string hint: "T"
        property bool checked: ctl ? ctl.tocVisible : false
        // Nothing to show - no book, or a book that brought no table of contents -
        // means nothing to offer: the button greys out instead of doing nothing
        // silently, exactly as the menu's 目录 row does.
        readonly property bool cUsable: ctl ? (ctl.hasBook && ctl.tocAvailable) : false

        anchors.left: parent.left
        anchors.leftMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        width: 78
        height: 22
        radius: 4
        enabled: cUsable
        opacity: cUsable ? 1.0 : 0.4
        // Filled while the column is open, outlined while it is shut: the state has
        // to be visible without hovering, or the control only answers questions the
        // reader has already asked.
        color: checked ? cAccent : "transparent"
        border.width: 1
        border.color: checked ? "transparent" : cMuted
        Behavior on color { ColorAnimation { duration: 90 } }

        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: cAccent
            opacity: tocToggle.checked ? 0 : (hover.containsMouse ? 0.3 : 0)
            Behavior on opacity { NumberAnimation { duration: 90 } }
        }

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 10
            anchors.verticalCenter: parent.verticalCenter
            text: tocToggle.text
            color: cPanelText
            font.pixelSize: 13
        }

        Text {
            // The shortcut, in the same place and the same size as the menu's hint
            // column, so the two surfaces teach the same thing.
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            text: tocToggle.hint
            color: cMuted
            font.pixelSize: 11
        }

        MouseArea {
            id: hover
            anchors.fill: parent
            // Belt and braces with the `enabled` above: whichever layer Qt stops at,
            // a greyed button takes no clicks, and the click does not fall through to
            // anything that turns a page.
            enabled: tocToggle.cUsable
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: if (root.ctl) { root.ctl.toggleToc() }
        }
    }

    Text {
        id: sectionLabel
        anchors.left: tocToggle.right
        anchors.leftMargin: 14
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(parent.width * 0.36, implicitWidth)
        text: cSectionTitle
        color: cPanelText
        font.pixelSize: 13
        elide: Text.ElideRight
    }

    Text {
        anchors.centerIn: parent
        text: cProgressText
        color: cMuted
        font.pixelSize: 13
    }

    Text {
        objectName: "messageLabel"
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(parent.width * 0.4, implicitWidth)
        text: cError.length > 0 ? cError
              : cMessage.length > 0 ? cMessage
              : cDiagnostics
        color: cError.length > 0 ? "#c0392b" : cMuted
        font.pixelSize: 12
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignRight
    }
}
