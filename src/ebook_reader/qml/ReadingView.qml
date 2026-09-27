import QtQuick
import EbookReader 1.0

// The reading view: the window of text at the current scroll offset, plus the line
// that names the chapter after this one once the reader has reached the end (FR-074).
//
// The image comes ready-made from Python and is one rendering quantum taller than the
// page box, so moving inside a quantum is the `pan` below - no Python runs while the
// wheel turns (ADR-016 / NFR-005).
Item {
    id: root

    // Named so the integration tests can read the view back out of the item tree.
    objectName: "readingView"

    // Injected by Main.qml; guarded while null.
    property var ctl: null

    readonly property var cViewImage: ctl ? ctl.viewImage : null
    readonly property color cBg: ctl ? ctl.backgroundColor : "#ffffff"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#8a8a8a"
    readonly property size cPageSize: ctl ? ctl.viewPageSize : Qt.size(0, 0)
    readonly property real cBottomMargin: ctl ? ctl.bottomMargin : 0
    // The next chapter is named once the window is as far down as this section goes,
    // and not before: a line announcing a chapter the reader has not finished the
    // current one for would be noise.  Nothing is announced after the last section,
    // and nothing is announced for a section no contents entry names (FR-074).
    readonly property bool cNextVisible: ctl
                                         ? (ctl.atSectionEnd && ctl.hasNextSection
                                            && ctl.nextSectionTitle.length > 0)
                                         : false

    property alias viewItem: view

    PageItem {
        id: view
        // The tests read `pan` and the image back off this item.
        objectName: "pageView"
        anchors.fill: parent
        background: root.cBg
        pageSize: root.cPageSize
        pan: ctl ? ctl.pan : 0

        // `when` keeps the initial null out of the QImage property: assigning null
        // to it is an error in QML, and the view simply shows the background until
        // the controller is injected and the first window arrives.
        Binding on image {
            value: root.cViewImage
            when: root.cViewImage !== null
        }
    }

    // 下一章.  Drawn inside the bottom margin, which is exactly where the text ends
    // when it has been scrolled all the way down, so it never covers a line of text.
    Rectangle {
        id: nextStrip
        objectName: "nextChapterStrip"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Math.max(22, root.cBottomMargin)
        color: root.cBg
        visible: root.cNextVisible

        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: root.cMuted
            opacity: 0.35
        }

        Text {
            anchors.fill: parent
            anchors.leftMargin: 4
            anchors.rightMargin: 4
            text: ctl ? "下一章 · " + ctl.nextSectionTitle : ""
            color: root.cMuted
            font.pixelSize: 13
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }

        // A label that cannot be followed is a dead end, so the line itself is the
        // way on - and `]` does the same thing from the keyboard (FR-074).
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: if (root.ctl) { root.ctl.goToNextSection() }
        }
    }
}
