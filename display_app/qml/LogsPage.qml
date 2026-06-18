import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: logScreen
    color: Theme.bgPrimary
    property var viewModel: null
    property var logModel: viewModel ? viewModel.logs : []

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLG
        spacing: Theme.spacingMD

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("检测日志")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontSizeLG
                font.bold: true
                Layout.fillWidth: true
            }
            ActionButton {
                buttonText: qsTr("刷新")
                bgColor: Theme.bgTertiary
                implicitHeight: 36
                Layout.preferredWidth: 92
                onClicked: { if (logScreen.viewModel) logScreen.viewModel.refresh() }
            }
        }

        ListView {
            id: logList
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: logScreen.logModel
            clip: true
            visible: count > 0

            header: Rectangle {
                width: logList.width
                height: 34
                color: Theme.bgTertiary
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingSM
                    anchors.rightMargin: Theme.spacingSM
                    Text { text: qsTr("时间"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.fillWidth: true }
                    Text { text: qsTr("Camera"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.fillWidth: true }
                    Text { text: qsTr("状态"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.preferredWidth: 90 }
                    Text { text: qsTr("缺陷"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.fillWidth: true }
                    Text { text: qsTr("置信度"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.preferredWidth: 90; horizontalAlignment: Text.AlignRight }
                    Text { text: qsTr("操作"); color: Theme.textSecondary; font.pixelSize: Theme.fontSizeXS; Layout.preferredWidth: 120; horizontalAlignment: Text.AlignRight }
                }
            }

            delegate: Rectangle {
                width: logList.width
                height: 38
                color: index % 2 === 0 ? Theme.bgPrimary : Theme.bgSecondary

                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: Theme.spacingSM
                    anchors.rightMargin: Theme.spacingSM
                    spacing: Theme.spacingSM

                    Text {
                        text: modelData.timestamp ? new Date(modelData.timestamp * 1000).toLocaleTimeString(Qt.locale()) : ""
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSizeXS
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    Text {
                        text: modelData.camera_id || "--"
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSizeXS
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    StatusBadge {
                        badgeText: modelData.status || "--"
                        badgeStatus: modelData.status === "NG" ? "ng" : (modelData.status === "OK" ? "ok" : "warning")
                        maxBadgeWidth: 90
                        Layout.preferredWidth: 90
                    }
                    Text {
                        text: modelData.defect_type || modelData.reason || "--"
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSizeXS
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                    Text {
                        text: modelData.confidence ? modelData.confidence.toFixed(3) : "--"
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSizeXS
                        Layout.preferredWidth: 90
                        horizontalAlignment: Text.AlignRight
                    }
                    Text {
                        text: modelData.operator_action || "--"
                        color: Theme.textSecondary
                        font.pixelSize: Theme.fontSizeXS
                        Layout.preferredWidth: 120
                        horizontalAlignment: Text.AlignRight
                        elide: Text.ElideRight
                    }
                }
            }
        }

        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: logList.count === 0
            title: qsTr("暂无检测日志")
            message: qsTr("前端收到 display_latest.json 后会自动追加日志。")
            badgeText: qsTr("LOG")
            accentColor: Theme.accent
        }
    }
}
