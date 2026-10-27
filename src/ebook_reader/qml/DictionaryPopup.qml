import QtQuick

// The word card behind 悬停查词 (FR-095): the pronunciation of the word under the
// pointer and a line or two of its meaning, without leaving the page.
//
// It is a *reading aid*, not a panel.  It takes no focus, changes nothing about the page,
// and is gone the moment the pointer moves off the word or the page moves under it - so
// it may be laid *over* the text in a way a column never could be.  Its colours are the
// page's own: the band for the paper, the muted ink for everything the reader is not
// being told (FR-090).
//
// The card is deliberately not clickable.  A click on it would have to mean something,
// and the reader's next gesture is always the same one - carrying on reading.
Rectangle {
    id: card

    // Named so the integration tests can read the card back out of the item tree.
    objectName: "dictionaryPopup"

    // Injected by ReadingView.qml; guarded while null.
    property var ctl: null

    readonly property int padding: 10

    // Wide enough for a gloss line and a phonetic, narrow enough that the paragraph
    // behind it stays readable around the edges (FR-095).
    readonly property int preferredWidth: 320

    // The margin the card keeps from the edges of the page area, so that the line it
    // belongs to can still be seen beside it.
    readonly property int edgeMargin: 8

    // How far below the pointer the card sits: clear of the pointer itself, so the word
    // it explains is not covered by the thing explaining it.
    readonly property int pointerGap: 16

    // The height the content asks for, before the page sets a limit.
    readonly property real naturalHeight: content.implicitHeight + 2 * padding

    width: Math.max(
               140,
               Math.min(preferredWidth,
                        (parent ? parent.width : preferredWidth) - 2 * edgeMargin))
    // A gloss that will not fit is cut rather than allowed to grow over the page: the
    // card is a glance, and three lines is what it is for (`glosses.MAX_SENSES`).
    height: Math.max(
                24,
                Math.min(naturalHeight,
                         (parent ? parent.height : naturalHeight) - 2 * edgeMargin))

    visible: ctl ? ctl.lookupVisible : false
    radius: 6
    // The band is the paper stepped down - the same surface the two strips at the ends of
    // the column use - and the hairline is the muted ink, so the card reads as part of
    // the page rather than as a fourth column (`settings.py` is the only palette).
    color: ctl ? ctl.bandColor : "#f6f6f6"
    border.width: 1
    border.color: ctl ? ctl.mutedColor : "#808080"
    clip: true

    Column {
        id: content

        // Named for the tests, which read the gloss lines back out of the delegate items:
        // a Repeater's delegates are visual children of this column and are not reachable
        // through the object tree (`findChild`).
        objectName: "dictionaryBody"

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: card.padding
        spacing: 4

        // The word itself.  A word the dictionary files under another spelling - "went"
        // is under "go" - says both, because the reader pointed at one word and is being
        // told about another.  Case alone is not a difference: `form` is lower-cased by
        // the lookup, so "Paris" must not come out as "paris → Paris".
        Text {
            objectName: "dictionaryWord"
            width: parent.width
            text: {
                if (!card.ctl) {
                    return ""
                }
                const form = card.ctl.lookupForm
                const word = card.ctl.lookupWord
                return form === word.toLowerCase() ? word : form + "  →  " + word
            }
            color: card.ctl ? card.ctl.textColor : "#1b1b1b"
            font.pixelSize: 15
            font.bold: true
            wrapMode: Text.Wrap
        }

        Text {
            objectName: "dictionaryPhonetic"
            width: parent.width
            visible: text.length > 0
            text: card.ctl ? card.ctl.lookupPhonetic : ""
            color: card.ctl ? card.ctl.mutedColor : "#808080"
            font.pixelSize: 13
            wrapMode: Text.Wrap
        }

        Repeater {
            model: card.ctl ? card.ctl.lookupSenses : []

            delegate: Text {
                required property string modelData

                objectName: "dictionarySense"
                width: content.width
                text: modelData
                color: card.ctl ? card.ctl.textColor : "#1b1b1b"
                font.pixelSize: 13
                wrapMode: Text.Wrap
            }
        }
    }

    // The pointer may move onto the card to read it, and the page underneath must not
    // take that as a pointer over the text *behind* the card - which would swap the word
    // being explained.  `hoverEnabled` is what makes this item take the hover away from
    // the page; the clicks are taken as well, so a stray one changes nothing.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.AllButtons
    }
}
