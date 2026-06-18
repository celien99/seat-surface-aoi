import QtQuick
import QtQuick.Layouts
import styles

Rectangle {
    id: root

    property string title: ""
    property string message: ""
    property string badgeText: ""
    property color accentColor: Theme.textSecondary

    color: Theme.bgCard
    radius: Theme.radiusMD
    border { width: 1; color: Theme.borderDefault }

    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.spacingXL * 2, 520)
        spacing: Theme.spacingSM

        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            visible: root.badgeText !== ""
            implicitWidth: badgeLabel.implicitWidth + 22
            implicitHeight: 28
            radius: Theme.radiusSM
            color: Theme.bgTertiary
            border { width: 1; color: root.accentColor }

            Text {
                id: badgeLabel
                anchors.centerIn: parent
                text: root.badgeText
                color: root.accentColor
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
            }
        }

        Text {
            Layout.fillWidth: true
            text: root.title
            color: Theme.textSecondary
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            visible: text !== ""
        }

        Text {
            Layout.fillWidth: true
            text: root.message
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeSM
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.Wrap
            lineHeight: 1.35
            visible: text !== ""
        }
    }
}
