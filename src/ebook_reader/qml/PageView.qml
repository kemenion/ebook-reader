import QtQuick
import EbookReader 1.0

// One rendered page.  The image arrives ready-made from Python, so this item only
// blits it and QML is free to animate the whole item on the GPU; no Python runs
// during an animation (ADR-008 / NFR-005).
Item {
    id: root

    // Injected by Main.qml; guarded while null.
    property var ctl: null

    readonly property var cPageImage: ctl ? ctl.pageImage : null
    readonly property color cBg: ctl ? ctl.backgroundColor : "#ffffff"

    property alias pageItem: page
    property int animationDuration: 150
    property bool animationsEnabled: true

    PageItem {
        id: page
        anchors.fill: parent
        background: root.cBg

        // `when` keeps the initial null out of the QImage property: assigning null
        // to it is an error in QML, and the page simply shows the background until
        // the controller is injected and the first page arrives.
        Binding on image {
            value: root.cPageImage
            when: root.cPageImage !== null
        }

        transform: Translate { id: slide; x: 0 }
    }

    // Direction is derived from the page index so the page appears to come from
    // the side the reader is moving towards, mimicking a page turn.
    property int _lastKey: -1
    property int _direction: 1

    Connections {
        target: root.ctl

        function onPageChanged() {
            if (!root.ctl) { return }
            const key = root.ctl.sectionIndex * 100000 + root.ctl.pageIndex
            if (root._lastKey >= 0) {
                root._direction = key >= root._lastKey ? 1 : -1
            }
            root._lastKey = key
            if (root.animationsEnabled) {
                enterAnimation.fromX = root._direction * 48
                enterAnimation.restart()
            }
        }
    }

    ParallelAnimation {
        id: enterAnimation
        property real fromX: 48

        NumberAnimation {
            target: slide
            property: "x"
            from: enterAnimation.fromX
            to: 0
            duration: root.animationDuration
            easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: page
            property: "opacity"
            from: 0.0
            to: 1.0
            duration: root.animationDuration
            easing.type: Easing.OutCubic
        }
    }
}

