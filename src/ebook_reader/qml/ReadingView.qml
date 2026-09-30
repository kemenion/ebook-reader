import QtQuick
import EbookReader 1.0

// The reading view: the window of text at the current scroll offset, the bar that says
// where the reader is across the top of the column, and the line that names the chapter
// after this one in the bottom margin (FR-074).
//
// Both of those strips are drawn on the *band* - the paper's own surface, one step down
// from the page and a step above the panels beside the book - so the column never looks
// like a fourth panel (FR-090).
//
// The image comes ready-made from Python and is one rendering quantum taller than the
// page box, so moving inside a quantum is the `pan` below - no Python runs while the
// wheel turns (ADR-016 / NFR-005).  The two QML items around it are not part of the
// text and do not move with it: the window slides *under* them.
Item {
    id: root

    // Named so the integration tests can read the view back out of the item tree.
    objectName: "readingView"

    // Injected by Main.qml; guarded while null.
    property var ctl: null

    // What the right edge of the column has to keep free.  The scroll bar is drawn
    // *over* that edge (FR-076), so a label longer than the column has to stop short of
    // it - otherwise a title's last glyph would sit under the bar's handle, and the
    // bar's own click area would take the clicks meant for the label.
    property real rightInset: 0

    // The margin a label keeps at either end.  Only a title too long for the
    // column ever reaches it: the label is otherwise exactly as wide as its own words,
    // and the two margins are then simply whatever is left to the side of it.
    readonly property real labelMargin: 4 + rightInset

    readonly property var cViewImage: ctl ? ctl.viewImage : null
    readonly property color cBg: ctl ? ctl.backgroundColor : "#ffffff"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#808080"
    // The ink of the page, and the band drawn on it.  The band is the paper's own
    // surface stepped down from it - *not* the panel colour the columns beside the book
    // use: the two strips below belong to the page, and dressing them in the map's
    // colour is what made the position bar read as another column (FR-074 / FR-090).
    readonly property color cText: ctl ? ctl.textColor : "#1b1b1b"
    readonly property color cBand: ctl ? ctl.bandColor : "#f6f6f6"
    readonly property size cPageSize: ctl ? ctl.viewPageSize : Qt.size(0, 0)
    readonly property bool cHasBook: ctl ? ctl.hasBook : false
    // The levels above the reader's place, outermost first, as one line of words.
    readonly property string cPathText:
        (ctl ? ctl.positionPath : []).map(function (item) { return item.title }).join(" › ")
    readonly property bool cHasPath: cPathText.length > 0
    // Both ends of the column keep their margin: the position bar is drawn in the top
    // one, the next chapter line in the bottom one.
    readonly property real cTopMargin: ctl ? ctl.topMargin : 0
    readonly property real cBottomMargin: ctl ? ctl.bottomMargin : 0
    // The chapter after this one is named once the window is as far down as the section
    // goes - and not before: a line announcing a chapter the reader has not finished the
    // current one for would be noise.  Nothing is announced past the end of the book,
    // and nothing is announced for a chapter no contents entry - nor its own first line,
    // FR-012 - names (FR-074).
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

    // 位置栏 (FR-074).  Where the reader is, in the book's own tree: the levels the
    // place sits under, outermost first, then its own name - 「第一部分 涛动周期论 ›
    // 01 人生财富靠康波——康波中的价格波动」.  Both halves come from the row the
    // contents column highlights, so the bar and the map cannot say different things,
    // and a document carrying several rows is described by the row the reader is *in*
    // rather than by the file's name.
    //
    // It spans the column at the very top and does not move with the text: the window
    // image slides under it (ADR-016), which is what makes it a place to *look* rather
    // than a line to read past.  Its height is the top margin, the one band of the page
    // it can have: at the top of a section the window begins with that blank margin, and
    // further down the bar covers no more than the margin the reader chose - a line that
    // slides under it comes back the moment the reader scrolls up again.
    //
    // Its fill is the paper's band, not the panels' colour: the words describe the
    // reader's place in the *book*, so the strip is part of the page - just far enough
    // below it to be seen as a fixture of the column rather than as blank margin.  The
    // ink is the page's own text colour, the outer levels the page's muted one.
    //
    // It is deliberately *not* a button.  It names where the reader is, so a click would
    // have to mean something the words do not say; the ways back are `[`, the menu and
    // the contents column.  A wheel over it still scrolls the text, because nothing here
    // takes the gesture.
    Rectangle {
        id: positionBar
        objectName: "positionBar"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Math.max(22, root.cTopMargin)
        color: root.cBand
        visible: root.cHasBook && ctl.positionTitle.length > 0

        Rectangle {
            anchors.bottom: parent.bottom
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: root.cMuted
            opacity: 0.35
        }

        // Centred in the column, and only as wide as the words it holds: a full-width
        // box would leave a short path looking off-centre inside it.  The current name
        // takes its room first - it is the half a reader is looking for - and the levels
        // above it are what gives way when the path is longer than the column.  Elided
        // from the *left* for the same reason: the level nearest the reader survives.
        Row {
            id: crumbs
            objectName: "positionCrumbs"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            spacing: 7

            // What the whole line may take: the column, less the margin a centred label
            // keeps at either end - the scroll bar is drawn over that same edge (FR-076).
            // The separator and the spacing between the parts are part of the line, so
            // they come off the budget before the two labels share what is left.
            readonly property real budget:
                Math.max(0, root.width - 2 * root.labelMargin
                            - (root.cHasPath ? sepLabel.width + 2 * spacing : 0))

            Text {
                id: pathLabel
                objectName: "positionPathLabel"
                visible: width > 0
                width: Math.min(implicitWidth,
                                Math.max(0, crumbs.budget - titleLabel.width))
                text: root.cPathText
                color: root.cMuted
                font.pixelSize: 13
                elide: Text.ElideLeft
                verticalAlignment: Text.AlignVCenter
            }

            Text {
                id: sepLabel
                objectName: "positionSeparator"
                visible: pathLabel.visible
                text: "›"
                color: root.cMuted
                font.pixelSize: 13
                verticalAlignment: Text.AlignVCenter
            }

            // The reader's place itself: the same weight the contents column gives the
            // row it highlights, in the page's own text colour rather than the muted
            // one - the half of the line that is the answer.
            Text {
                id: titleLabel
                objectName: "positionTitleLabel"
                width: Math.min(implicitWidth, crumbs.budget)
                text: ctl ? ctl.positionTitle : ""
                color: root.cText
                font.pixelSize: 13
                font.bold: true
                elide: Text.ElideRight
                verticalAlignment: Text.AlignVCenter
            }
        }
    }

    // 下一章.  Drawn inside the bottom margin, which is exactly where the text ends
    // when it has been scrolled all the way down, so it never covers a line of text.
    // The strip spans the column; the label inside it sits in the middle of it (FR-074).
    //
    // It wears the very same band as the position bar at the other end of the column: the
    // two are one idea - a step off the page, still the page - and neither is a panel.  A
    // second grey of its own would say the two strips were two different things.  The
    // hairline on its top edge is what separates it from the last line, since this end of
    // the column does not have blank margin under it the way the top one does.
    Rectangle {
        id: nextStrip
        objectName: "nextChapterStrip"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Math.max(22, root.cBottomMargin)
        color: root.cBand
        visible: root.cNextVisible

        Rectangle {
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            height: 1
            color: root.cMuted
            opacity: 0.35
        }

        // Centred in the column and only as wide as its own words: a full-width box
        // would leave a short title looking off-centre inside it.  A title too long for
        // the column is elided, and `labelMargin` keeps it clear of the scroll bar.  The
        // click area below is still the whole strip, so the way on is easy to hit from
        // anywhere along that line.
        Text {
            id: nextLabel
            objectName: "nextChapterLabel"
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            width: Math.min(implicitWidth, parent.width - 2 * root.labelMargin)
            text: ctl ? "下一章 · " + ctl.nextSectionTitle : ""
            color: root.cMuted
            font.pixelSize: 13
            elide: Text.ElideRight
            horizontalAlignment: Text.AlignHCenter
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
