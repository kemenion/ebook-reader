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
    // Read from the controller rather than out of the rows: a row that carried its own
    // "current" flag made every jump republish the list, and a view handed a new model
    // starts over at its first row (缺陷 25).  Same shape as OutlinePanel.
    readonly property int cCurrentRow: ctl ? ctl.currentTocRow : -1

    // Width comes from Main.qml, which shares the window between the open columns.
    color: cPanel

    function revealCurrent() {
        if (cCurrentRow >= 0 && cCurrentRow < cItems.length) {
            list.positionViewAtIndex(cCurrentRow, ListView.Center)
        }
    }

    ListView {
        id: list
        // Named for the tests, which click a row through the real event path.
        objectName: "tocList"
        anchors.fill: parent
        anchors.topMargin: header.height + 8
        anchors.bottomMargin: 12
        clip: true
        model: root.cItems
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        delegate: ItemDelegate {
            id: row
            width: list.width
            height: Math.max(38, label.implicitHeight + 16)
            // Contents levels start at 0 for the top level (the nav and NCX walkers both
            // start there), unlike the outline's headings, which start at 1.
            readonly property int rowLevel: Math.max(0, modelData.level || 0)
            highlighted: modelData.row === root.cCurrentRow

            contentItem: Text {
                id: label
                text: modelData.title
                color: root.cPanelText
                // A top-level title keeps the panel's own size, a nested one steps down
                // a notch: depth stays legible even where the indent is easy to miss.
                font.pixelSize: row.rowLevel > 0 ? 14 : 15
                font.bold: row.highlighted
                wrapMode: Text.Wrap
                leftPadding: 16 + row.rowLevel * 16
                rightPadding: 16
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                id: rowBackground
                // Colour with alpha rather than a translucent item: item opacity would
                // fade the guides and the bar along with the fill.
                color: row.highlighted ? Qt.alpha(root.cAccent, 0.55)
                       : (row.hovered ? Qt.alpha(root.cMuted, 0.14) : "transparent")

                // One hairline per level above this row, drawn at the indent that level's
                // own text starts at, so a nested row reads as sitting *under* its parent
                // instead of floating at an unexplained distance to its right.
                Repeater {
                    model: row.rowLevel
                    Rectangle {
                        x: 16 + index * 16 - 6
                        width: 1
                        height: rowBackground.height
                        color: root.cMuted
                        opacity: 0.3
                    }
                }

                // The reader's place: the fill says which row, the bar says "and you are
                // in it" - the part that stays visible when the fill is pale.
                Rectangle {
                    anchors.left: parent.left
                    width: 3
                    height: parent.height
                    visible: row.highlighted
                    color: root.cAccent
                }
            }

            onClicked: if (root.ctl) { root.ctl.goToTocRow(modelData.row) }
        }
    }

    // Same shape as the outline column's header - heading over the book's title - so the
    // two maps that face the page read as one design instead of two.
    Rectangle {
        id: header
        width: parent.width
        height: labels.implicitHeight + 26
        color: "transparent"

        Column {
            id: labels
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.leftMargin: 18
            anchors.rightMargin: 18
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3

            Text {
                text: "目录"
                color: root.cPanelText
                font.pixelSize: 19
                font.bold: true
            }

            Text {
                width: parent.width
                text: root.cBookTitle.length > 0 ? root.cBookTitle : "未打开书籍"
                color: root.cMuted
                font.pixelSize: 12
                elide: Text.ElideRight
            }
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
