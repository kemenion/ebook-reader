import QtQuick
import EbookReader 1.0
import QtQuick.Controls

// What is inside the current section (FR-018 / FR-019).
//
// One shape: the section's own headings, down to level 3.  The column used to offer a
// page-per-row map when a section had too few headings to have a structure, and that
// fallback died with paging itself (ADR-016) - over a continuous column those rows
// would move every time the window was resized, and a map whose entries wander is not
// a map.  A section with no headings has no rows, and the controller keeps the column
// shut rather than opening it empty (FR-019).
//
// Same guarded accessors as TocSidebar: this component is constructed before
// Python injects the controller, so every read of `ctl` is mirrored through a
// property with a fallback.
Rectangle {
    id: root

    property var ctl: null

    readonly property color cPanel: ctl ? ctl.panelColor : "#f4f4f4"
    readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#8a8a8a"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property string cSectionTitle: ctl ? ctl.sectionTitle : ""
    readonly property var cItems: ctl ? ctl.outlineItems : []
    readonly property int cCurrentRow: ctl ? ctl.currentOutlineRow : -1
    readonly property bool cEmpty: cItems.length === 0

    color: cPanel

    // Scrolls the highlighted row into view; called when the column opens, so the
    // panel never starts at the top of a chapter the reader is halfway through.
    function revealCurrent() {
        if (cCurrentRow >= 0 && cCurrentRow < cItems.length) {
            list.positionViewAtIndex(cCurrentRow, ListView.Center)
        }
    }

    ListView {
        id: list
        // Named for the tests, which click a row through the real event path.
        objectName: "outlineList"
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
            highlighted: modelData.row === root.cCurrentRow

            contentItem: Text {
                id: label
                text: modelData.title
                color: root.cPanelText
                font.pixelSize: 15
                font.bold: modelData.row === root.cCurrentRow
                wrapMode: Text.Wrap
                // Headings start at h1, so the first level sits at the indent and
                // every deeper level steps in by one notch (FR-019: 1 to 3).
                leftPadding: 16 + Math.max(0, (modelData.level || 1) - 1) * 16
                rightPadding: 16
                verticalAlignment: Text.AlignVCenter
            }

            background: Rectangle {
                color: parent.highlighted ? root.cAccent
                       : (parent.hovered ? root.cMuted : "transparent")
                opacity: parent.highlighted ? 0.55 : (parent.hovered ? 0.14 : 1.0)
            }

            onClicked: if (root.ctl) { root.ctl.goToOutlineRow(modelData.row) }
        }
    }

    // Defensive: the column is never shown without rows, but an empty list is a
    // blank strip, which reads as a rendering fault rather than as "nothing here".
    Text {
        anchors.centerIn: parent
        width: parent.width - 36
        visible: root.cEmpty
        text: "本节没有可显示的内容"
        color: root.cMuted
        font.pixelSize: 14
        wrapMode: Text.Wrap
        horizontalAlignment: Text.AlignHCenter
    }

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
                text: "本节大纲"
                color: root.cPanelText
                font.pixelSize: 19
                font.bold: true
            }

            Text {
                width: parent.width
                text: root.cSectionTitle.length > 0 ? root.cSectionTitle : "未打开书籍"
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

    // Edge separator: the panel takes real space beside the page, so it needs to
    // be told apart from the page's own margin.
    Rectangle {
        anchors.left: parent.left
        width: 1
        height: parent.height
        color: root.cMuted
        opacity: 0.35
    }
}
