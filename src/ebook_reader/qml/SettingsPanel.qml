import QtQuick
import EbookReader 1.0
import QtQuick.Controls
import QtQuick.Layouts

// Typography controls: size, font, line height, margins, justify, theme
// (FR-034 .. FR-037, FR-090).
Rectangle {
    id: root

    // Injected by Main.qml, which in turn is given the controller by Python after
    // load.  Guarded accessors keep every binding safe while `ctl` is still null.
    property var ctl: null

    readonly property color cPanel: ctl ? ctl.panelColor : "#eaeaea"
    readonly property color cPanelText: ctl ? ctl.panelTextColor : "#1b1b1b"
    readonly property color cMuted: ctl ? ctl.mutedColor : "#808080"
    readonly property color cAccent: ctl ? ctl.accentColor : "#cfe1ff"
    readonly property real cFontSize: ctl ? ctl.fontSize : 18
    readonly property real cLineHeight: ctl ? ctl.lineHeight : 1.75
    readonly property string cFontChoice: ctl ? ctl.fontChoice : "serif"
    readonly property bool cJustify: ctl ? ctl.justify : true
    readonly property string cThemeName: ctl ? ctl.themeName : "light"
    readonly property string cMarginLabel: ctl ? ctl.marginLabel : ""
    readonly property string cStatusText: ctl ? ctl.statusText : ""

    // Fixed: Main.qml deliberately leaves this one its own width, because it is the
    // only panel that floats over the text rather than taking a column.
    width: 330
    color: cPanel

    component Heading1: Text {
        color: root.cPanelText
        font.pixelSize: 13
        font.bold: true
        opacity: 0.75
    }

    component PanelButton: Rectangle {
        property string label: ""
        property bool active: false
        signal clicked()

        implicitHeight: 34
        implicitWidth: Math.max(64, buttonLabel.implicitWidth + 24)
        radius: 6
        color: active ? root.cAccent : "transparent"
        border.color: root.cMuted
        border.width: active ? 0 : 1

        Text {
            id: buttonLabel
            anchors.centerIn: parent
            text: parent.label
            color: root.cPanelText
            font.pixelSize: 14
        }

        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: parent.clicked()
        }
    }

    Flickable {
        anchors.fill: parent
        anchors.margins: 18
        contentHeight: column.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: column
            width: parent.width
            spacing: 16

            Text {
                text: "显示设置"
                color: cPanelText
                font.pixelSize: 19
                font.bold: true
            }

            Heading1 { text: "字号  " + cFontSize.toFixed(0) }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "A−"; onClicked: ctl.decreaseFont() }
                PanelButton { label: "A+"; onClicked: ctl.increaseFont() }
                Slider {
                    Layout.fillWidth: true
                    from: 12
                    to: 36
                    stepSize: 1
                    value: cFontSize
                    onMoved: ctl.setFontSize(value)
                }
            }

            Heading1 { text: "字体" }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "宋体"; active: cFontChoice === "serif"; onClicked: ctl.setFontChoice("serif") }
                PanelButton { label: "黑体"; active: cFontChoice === "sans"; onClicked: ctl.setFontChoice("sans") }
                PanelButton { label: "楷体"; active: cFontChoice === "kai"; onClicked: ctl.setFontChoice("kai") }
            }

            Heading1 { text: "行距  " + cLineHeight.toFixed(2) }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "−"; onClicked: ctl.decreaseLineHeight() }
                PanelButton { label: "+"; onClicked: ctl.increaseLineHeight() }
                Slider {
                    Layout.fillWidth: true
                    from: 1.1
                    to: 2.6
                    stepSize: 0.05
                    value: cLineHeight
                    onMoved: ctl.setLineHeight(value)
                }
            }

            Heading1 { text: cMarginLabel }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "收窄"; onClicked: ctl.decreaseMargin() }
                PanelButton { label: "放宽"; onClicked: ctl.increaseMargin() }
            }

            Heading1 { text: "对齐" }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "两端对齐"; active: cJustify; onClicked: { if (!ctl.justify) ctl.toggleJustify() } }
                PanelButton { label: "左对齐"; active: !cJustify; onClicked: { if (ctl.justify) ctl.toggleJustify() } }
            }

            Heading1 { text: "主题" }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                PanelButton { label: "日间"; active: cThemeName === "light"; onClicked: ctl.setTheme("light") }
                PanelButton { label: "米色"; active: cThemeName === "sepia"; onClicked: ctl.setTheme("sepia") }
                PanelButton { label: "夜间"; active: cThemeName === "dark"; onClicked: ctl.setTheme("dark") }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 6
                implicitHeight: 1
                color: cMuted
                opacity: 0.25
            }

            Heading1 { text: "排版诊断" }
            Text {
                Layout.fillWidth: true
                text: cStatusText
                color: cMuted
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }

            Heading1 { text: "快捷键" }
            Text {
                Layout.fillWidth: true
                color: cMuted
                font.pixelSize: 12
                lineHeight: 1.5
                wrapMode: Text.Wrap
                text: "鼠标右键：全部操作的菜单\n" +
                      "滚轮：连续滚动（上下滚动一屏用 PgUp / PgDn / 空格）\n" +
                      "↑ ↓：上下滚动一行\n" +
                      "Home / End：本卷开头 / 结尾\n" +
                      "[ / ]：上一章 / 下一章\n" +
                      "Ctrl + / Ctrl −：字号\n" +
                      "T：目录   O：本节大纲   S：设置\n" +
                      "Esc：关闭面板   F11：全屏\n" +
                      "Ctrl+Q：退出"
            }

            Item { Layout.fillHeight: true; implicitHeight: 20 }
        }
    }

    // Edge separator: the panel floats above the page, so it needs a visible edge.
    Rectangle {
        anchors.left: parent.left
        width: 1
        height: parent.height
        color: cMuted
        opacity: 0.35
    }
}
