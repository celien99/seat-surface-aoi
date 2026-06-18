import QtQuick
import QtQuick.Controls.Basic
import styles

TabButton {
    id: control

    implicitWidth: Math.max(72, label.implicitWidth + Theme.spacingLG * 2)
    implicitHeight: 48
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    scale: control.down ? 0.98 : 1.0

    contentItem: Text {
        id: label
        text: control.text
        color: control.checked ? Theme.accent : (control.hovered ? Theme.textPrimary : Theme.textSecondary)
        font.pixelSize: Theme.fontSizeSM
        font.bold: control.checked
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        maximumLineCount: 1
    }

    background: Rectangle {
        radius: Theme.radiusSM
        color: control.checked ? Qt.rgba(0.345, 0.651, 1, 0.10)
              : control.down ? Qt.rgba(1, 1, 1, 0.08)
              : control.hovered ? Qt.rgba(1, 1, 1, 0.05)
              : "transparent"
        border {
            width: control.activeFocus ? 1 : 0
            color: control.activeFocus ? Theme.accent : "transparent"
        }

        Rectangle {
            width: parent.width
            height: control.checked || control.hovered || control.activeFocus ? 3 : 2
            anchors.bottom: parent.bottom
            color: control.checked || control.activeFocus ? Theme.accent
                   : control.hovered ? Qt.rgba(0.345, 0.651, 1, 0.55)
                   : "transparent"

            Behavior on color { ColorAnimation { duration: Theme.animFast } }
            Behavior on height { NumberAnimation { duration: Theme.animFast } }
        }

        Behavior on color { ColorAnimation { duration: Theme.animFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.animFast } }
    }

    Behavior on scale { NumberAnimation { duration: Theme.animFast; easing.type: Easing.OutCubic } }
}
