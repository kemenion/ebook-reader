import QtQuick
import QtQuick.Controls
import EbookReader 1.0

// Page counter, progress bar and section title (FR-067).
Rectangle {
    id: root

    // Injected by Main.qml; guarded while null.
    property var ctl: null

    readonly property color cPanel: ctl ? ctl.panelColor : "#f4f4f4"
    readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#8a8a8a"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property string cSectionTitle: ctl ? ctl.sectionTitle : ""
    readonly property string cProgressText: ctl ? ctl.progressText : ""
    readonly property string cDiagnostics: ctl ? ctl.statusText : ""
    readonly property string cError: ctl ? ctl.errorMessage : ""
    readonly property real cProgress: ctl ? ctl.progress : 0

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

    Text {
        id: sectionLabel
        anchors.left: parent.left
        anchors.leftMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(parent.width * 0.42, implicitWidth)
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
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(parent.width * 0.4, implicitWidth)
        text: cError.length > 0 ? cError : cDiagnostics
        color: cError.length > 0 ? "#c0392b" : cMuted
        font.pixelSize: 12
        elide: Text.ElideRight
        horizontalAlignment: Text.AlignRight
    }
}
