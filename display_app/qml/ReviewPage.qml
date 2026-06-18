import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: reviewScreen
    color: Theme.bgPrimary
    property var viewModel: null
    property var reviewModel: viewModel ? viewModel.reviews : []

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLG
        spacing: Theme.spacingMD

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("复核队列")
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
                onClicked: { if (reviewScreen.viewModel) reviewScreen.viewModel.refresh() }
            }
        }

        ListView {
            id: reviewList
            Layout.fillWidth: true
            Layout.fillHeight: true
            model: reviewScreen.reviewModel
            clip: true
            visible: count > 0

            delegate: Rectangle {
                width: reviewList.width
                height: 72
                color: index % 2 === 0 ? Theme.bgPrimary : Theme.bgSecondary
                border { width: 1; color: Theme.borderDefault }

                RowLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.spacingMD
                    spacing: Theme.spacingMD

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        Text {
                            text: (modelData.camera_id || "--") + "  " + (modelData.defect_type || "未知")
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontSizeSM
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: qsTr("置信度: ") + (modelData.confidence ? modelData.confidence.toFixed(3) : "0.000")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSizeXS
                        }
                        Text {
                            text: qsTr("原因: ") + (modelData.reason || "--")
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSizeXS
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }
                        Text {
                            text: (modelData.source === "cpp_controller" ? qsTr("主控  ") : qsTr("检测  "))
                                  + (modelData.timestamp ? new Date(modelData.timestamp * 1000).toLocaleString(Qt.locale()) : "")
                            color: Theme.textMuted
                            font.pixelSize: Theme.fontSizeXS
                        }
                    }

                    ActionButton {
                        buttonText: qsTr("确认缺陷")
                        bgColor: Theme.statusNG
                        implicitHeight: 32
                        Layout.preferredWidth: 96
                        font.pixelSize: Theme.fontSizeXS
                        onClicked: { if (reviewScreen.viewModel) reviewScreen.viewModel.confirmAsDefect(modelData.id) }
                    }
                    ActionButton {
                        buttonText: qsTr("误报忽略")
                        bgColor: Theme.statusOK
                        implicitHeight: 32
                        Layout.preferredWidth: 96
                        font.pixelSize: Theme.fontSizeXS
                        onClicked: { if (reviewScreen.viewModel) reviewScreen.viewModel.dismissAsOK(modelData.id) }
                    }
                }
            }
        }

        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: reviewList.count === 0
            title: qsTr("暂无待复核记录")
            message: qsTr("在 NG 弹窗中标记待复核的记录会进入这里。")
            badgeText: qsTr("REVIEW")
            accentColor: Theme.statusOK
        }
    }
}
