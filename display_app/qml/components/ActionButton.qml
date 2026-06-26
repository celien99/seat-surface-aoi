import QtQuick
import QtQuick.Controls.Basic
import styles

Button {
    id: control
    property color bgColor: Theme.accent
    property color textColor: "#ffffff"
    property color borderColor: Qt.rgba(1, 1, 1, 0.10)
    property color disabledBgColor: Qt.rgba(1, 1, 1, 0.04)
    property color disabledTextColor: Theme.textMuted
    property string buttonText: ""
    property bool compact: false
    property bool busy: false

    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    leftPadding: compact ? Theme.spacingSM : Theme.spacingMD
    rightPadding: compact ? Theme.spacingSM : Theme.spacingMD
    topPadding: 0
    bottomPadding: 0
    implicitHeight: compact ? 32 : Math.max(Theme.touchMin, implicitContentHeight + 20)

    font.pixelSize: Theme.fontSizeSM
    font.bold: true
    opacity: enabled ? 1.0 : 0.62
    scale: pressed ? 0.98 : 1.0

    contentItem: Item {
        implicitWidth: contentRow.implicitWidth
        implicitHeight: Math.max(contentRow.implicitHeight, Theme.fontSizeSM + 4)

        Row {
            id: contentRow
            anchors.centerIn: parent
            spacing: Theme.spacingXS

            Item {
                id: spinner
                width: 14
                height: 14
                anchors.verticalCenter: parent.verticalCenter
                visible: control.busy

                Rectangle {
                    width: 14
                    height: 14
                    radius: 7
                    color: "transparent"
                    border.width: 2
                    border.color: control.enabled ? textColor : disabledTextColor
                    opacity: 0.25
                }

                Rectangle {
                    width: 4
                    height: 4
                    radius: 2
                    color: control.enabled ? textColor : disabledTextColor
                    anchors.horizontalCenter: parent.horizontalCenter
                    anchors.top: parent.top
                }

                RotationAnimator on rotation {
                    running: control.busy && control.visible
                    loops: Animation.Infinite
                    from: 0
                    to: 360
                    duration: 900
                }
            }

            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: buttonText || control.text
                color: control.enabled ? textColor : disabledTextColor
                font: control.font
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                elide: Text.ElideRight
                maximumLineCount: 1
            }
        }
    }

    background: Rectangle {
        radius: Theme.radiusMD
        color: !control.enabled ? disabledBgColor
               : control.down ? Qt.darker(bgColor, 1.20)
               : control.hovered ? Qt.lighter(bgColor, 1.12)
               : bgColor
        border {
            width: control.activeFocus ? 2 : 1
            color: control.activeFocus ? Theme.accent
                   : control.hovered && control.enabled ? Qt.lighter(borderColor, 1.35)
                   : borderColor
        }

        Behavior on color { ColorAnimation { duration: Theme.animFast } }
        Behavior on border.color { ColorAnimation { duration: Theme.animFast } }
    }

    Behavior on scale { NumberAnimation { duration: Theme.animFast; easing.type: Easing.OutCubic } }
    Behavior on opacity { NumberAnimation { duration: Theme.animFast } }
}
