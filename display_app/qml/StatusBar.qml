import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: statusBar
    property string lineId: ""
    property string systemStatus: "stopped"
    property int okCount: 0
    property int ngCount: 0
    property int recheckCount: 0
    property int errorCount: 0
    property real tactRate: 0.0
    property string lineStatus: "unknown"
    property bool lineConnected: false
    property bool lineBusy: false
    property string lastTriggerResult: ""
    property string triggerError: ""
    property string operationMode: ""
    property string statusMessage: ""

    height: 52
    color: Theme.bgSecondary

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: Theme.spacingMD
        anchors.rightMargin: Theme.spacingMD
        spacing: Theme.spacingSM

        // Status indicator
        Rectangle {
            width: 14; height: 14; radius: 7
            color: {
                switch (systemStatus) {
                    case "running": return Theme.statusOK;
                    case "paused": return Theme.statusWarning;
                    default: return Theme.textMuted;
                }
            }
        }
        Text {
            text: {
                switch (systemStatus) {
                    case "running": return qsTr("运行中");
                    case "paused": return qsTr("已暂停");
                    default: return qsTr("已停止");
                }
            }
            color: Theme.textPrimary
            font.pixelSize: Theme.fontSizeSM
            font.bold: true
            Layout.rightMargin: Theme.spacingMD
        }

        Rectangle {
            Layout.preferredWidth: modeText.implicitWidth + 24
            Layout.preferredHeight: 28
            radius: Theme.radiusSM
            color: operationMode === "采样模式" ? Theme.statusWarningDim : Theme.bgTertiary
            border {
                width: 1
                color: operationMode === "采样模式" ? Theme.statusWarning : Theme.borderDefault
            }
            Text {
                id: modeText
                anchors.centerIn: parent
                text: operationMode || qsTr("等待数据")
                color: operationMode === "采样模式" ? Theme.statusWarning : Theme.textSecondary
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
            }
        }

        // Separator
        Rectangle { width: 1; height: 28; color: Theme.borderStrong }

        // Line info
        Text {
            text: qsTr("产线 ") + lineId
            color: Theme.textSecondary
            font.pixelSize: Theme.fontSizeSM
            Layout.leftMargin: Theme.spacingMD
            Layout.rightMargin: Theme.spacingMD
            font.bold: true
        }

        // Separator
        Rectangle { width: 1; height: 28; color: Theme.borderStrong }

        // Tact rate
        Text {
            text: qsTr("节拍: ") + tactRate.toFixed(1) + "/min"
            color: Theme.textSecondary
            font.pixelSize: Theme.fontSizeSM
            Layout.leftMargin: Theme.spacingMD
            Layout.rightMargin: Theme.spacingMD
        }

        Rectangle { width: 1; height: 28; color: Theme.borderStrong }

        Rectangle {
            Layout.preferredWidth: lineText.implicitWidth + 24
            Layout.preferredHeight: 28
            radius: Theme.radiusSM
            color: lineBusy ? Theme.statusWarningDim : Theme.bgTertiary
            border {
                width: 1
                color: lineConnected ? Theme.statusOK : Theme.statusNG
            }
            Text {
                id: lineText
                anchors.centerIn: parent
                text: lineConnected
                      ? (lineBusy ? qsTr("线体 检测中") : qsTr("线体 ") + lineStatus)
                      : qsTr("线体 未连接")
                color: lineConnected ? Theme.textPrimary : Theme.statusNG
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
            }
        }

        Rectangle {
            visible: lastTriggerResult !== ""
            Layout.preferredWidth: resultText.implicitWidth + 20
            Layout.preferredHeight: 28
            radius: Theme.radiusSM
            color: lastTriggerResult === "NG" ? Theme.statusNGDim
                   : lastTriggerResult === "OK" ? Theme.statusOKDim
                   : Theme.bgTertiary
            border {
                width: 1
                color: lastTriggerResult === "NG" ? Theme.statusNG
                       : lastTriggerResult === "OK" ? Theme.statusOK
                       : Theme.borderDefault
            }
            Text {
                id: resultText
                anchors.centerIn: parent
                text: qsTr("上次 ") + lastTriggerResult
                color: lastTriggerResult === "NG" ? Theme.statusNG
                       : lastTriggerResult === "OK" ? Theme.statusOK
                       : Theme.textSecondary
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
            }
        }

        Rectangle {
            visible: triggerError !== ""
            Layout.preferredWidth: Math.min(errorText.implicitWidth + 20, 260)
            Layout.preferredHeight: 28
            radius: Theme.radiusSM
            color: Theme.statusNGDim
            border { width: 1; color: Theme.statusNG }
            clip: true
            Text {
                id: errorText
                anchors.centerIn: parent
                width: parent.width - 16
                text: qsTr("异常 ") + triggerError
                color: Theme.statusNG
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
                elide: Text.ElideRight
            }
        }

        Rectangle {
            visible: statusMessage !== "" && triggerError === ""
            Layout.preferredWidth: Math.min(statusText.implicitWidth + 20, 300)
            Layout.preferredHeight: 28
            radius: Theme.radiusSM
            color: Theme.bgTertiary
            border { width: 1; color: Theme.borderDefault }
            clip: true
            Text {
                id: statusText
                anchors.centerIn: parent
                width: parent.width - 16
                text: statusMessage
                color: Theme.textSecondary
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
                elide: Text.ElideRight
            }
        }

        // OK count
        Text {
            text: qsTr("OK  ") + okCount
            color: Theme.statusOK
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
            Layout.leftMargin: Theme.spacingMD
        }

        // NG count
        Text {
            text: qsTr("NG  ") + ngCount
            color: ngCount > 0 ? Theme.statusNG : Theme.textSecondary
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
            Layout.leftMargin: Theme.spacingSM
        }

        Text {
            text: qsTr("复检  ") + recheckCount
            color: recheckCount > 0 ? Theme.statusWarning : Theme.textSecondary
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
            Layout.leftMargin: Theme.spacingSM
        }

        Text {
            text: qsTr("异常  ") + errorCount
            color: errorCount > 0 ? Theme.statusNG : Theme.textSecondary
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
            Layout.leftMargin: Theme.spacingSM
        }

        Item { Layout.fillWidth: true }
    }
}
