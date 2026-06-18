import QtQuick
import QtQuick.Layouts
import styles

Rectangle {
    id: root

    property string cardLabel: ""
    property string cardValue: ""
    property string cardSubtext: ""
    property color accentColor: Theme.accent

    color: Theme.bgCard
    radius: Theme.radiusMD
    border {
        width: 1
        color: Theme.borderDefault
    }

    implicitWidth: 160
    implicitHeight: 80
    clip: true

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingMD
        spacing: 2

        Text {
            Layout.fillWidth: true
            text: cardLabel
            font.pixelSize: Theme.fontSizeXS
            color: Theme.textSecondary
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            text: cardValue
            font.pixelSize: Theme.fontSizeXL
            font.bold: true
            color: accentColor
            elide: Text.ElideRight
            minimumPixelSize: Theme.fontSizeSM
            fontSizeMode: Text.Fit
        }
        Text {
            Layout.fillWidth: true
            text: cardSubtext
            font.pixelSize: Theme.fontSizeXS
            color: Theme.textMuted
            visible: cardSubtext !== ""
            elide: Text.ElideRight
        }
    }
}
