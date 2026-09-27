import QtQuick
import EbookReader 1.0
import QtQuick.Controls

// Navigation tree (FR-013 .. FR-017).
//
// The controller is injected from Python right after the window is loaded
// (`root.setProperty("ctl", controller)`), and this component is constructed
// before that.  Every value it reads is therefore mirrored through a guarded
// accessor: without the guard, each binding that touches `ctl` while it is still
// null logs a QML type error during construction.
Rectangle {
    id: root

    property var ctl: null

    readonly property color cPanel: ctl ? ctl.panelColor : "#f4f4f4"
    readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#8a8a8a"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property string cBookTitle: ctl ? ctl.bookTitle : ""
    readonly property var cItems: ctl ? ctl.tocItems : []

    width: 330
    color: cPanel

    function revealCurrent() {
        if (!ctl) { return }
        const row = ctl.currentTocRow
        if (row >= 0) {
            list.positionViewAtIndex(row, ListView.Center)
        }
    }

    ListView {
        id: list
        anchors.fill: parent
        anchors.topMargin: header.height + 8
        anchors.bottomMargin: 12
        clip: true
        model: root.cItems
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        delegate: ItemDelegate {
            width: list.width
            height: Math.max(38, label.implicitHeight + 16)
            highlighted: modelData.current

            contentItem: Text {
                id: label
                text: modelData.title
                color: root.cPanelText
                font.pixelSize: 15
                font.bold: modelData.current
                wrapMode: Text.Wrap
                leftPadding: 16 + modelData.level * 16
                rightPadding: 14
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                color: parent.highlighted ? root.cAccent
                       : (parent.hovered ? root.cMuted : "transparent")
                opacity: parent.highlighted ? 0.55 : (parent.hovered ? 0.14 : 1.0)
            }

            onClicked: if (root.ctl) { root.ctl.goToTocRow(modelData.row) }
        }
    }

    Rectangle {
        id: header
        width: parent.width
        height: 54
        color: "transparent"

        Text {
            anchors.left: parent.left
            anchors.leftMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            text: "目录"
            color: root.cPanelText
            font.pixelSize: 19
            font.bold: true
        }

        Text {
            anchors.right: parent.right
            anchors.rightMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            text: root.cBookTitle
            color: root.cMuted
            font.pixelSize: 12
            elide: Text.ElideMiddle
            width: Math.min(180, parent.width * 0.55)
            horizontalAlignment: Text.AlignRight
        }

        Rectangle {
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: root.cMuted
            opacity: 0.25
        }
    }

    // Edge separator: the panel floats above the page, so it needs a visible edge.
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: root.cMuted
        opacity: 0.35
    }
}
