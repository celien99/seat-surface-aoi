import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: statsScreen
    color: Theme.bgPrimary
    property var viewModel: null

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLG
        spacing: Theme.spacingLG

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: qsTr("生产统计")
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
                onClicked: { if (statsScreen.viewModel) statsScreen.viewModel.refresh() }
            }
        }

        RowLayout {
            spacing: Theme.spacingMD
            InfoCard {
                accentColor: Theme.statusOK
                cardLabel: qsTr("今日 OK")
                cardValue: String(statsScreen.viewModel ? statsScreen.viewModel.ok : 0)
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.statusNG
                cardLabel: qsTr("今日 NG")
                cardValue: String(statsScreen.viewModel ? statsScreen.viewModel.ng : 0)
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.accent
                cardLabel: qsTr("合格率")
                cardValue: (statsScreen.viewModel ? statsScreen.viewModel.okRate.toFixed(1) : "0.0") + "%"
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.statusWarning
                cardLabel: qsTr("总计")
                cardValue: String(statsScreen.viewModel ? statsScreen.viewModel.total : 0)
                Layout.fillWidth: true
            }
        }

        Text {
            text: qsTr("缺陷分布")
            color: Theme.textPrimary
            font.pixelSize: Theme.fontSizeMD
            font.bold: true
        }

        ListView {
            id: defectList
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: {
                var dist = statsScreen.viewModel ? statsScreen.viewModel.defectDistribution : ({})
                return Object.keys(dist).map(function(key) { return { type: key, count: dist[key] } })
            }
            visible: count > 0

            delegate: RowLayout {
                width: ListView.view.width
                height: 32
                spacing: Theme.spacingMD

                Rectangle {
                    Layout.preferredWidth: 12
                    Layout.preferredHeight: 12
                    radius: 6
                    color: Theme.statusNG
                }
                Text {
                    text: modelData.type
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontSizeSM
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                Text {
                    text: String(modelData.count)
                    color: Theme.textSecondary
                    font.pixelSize: Theme.fontSizeSM
                    font.bold: true
                    Layout.preferredWidth: 80
                    horizontalAlignment: Text.AlignRight
                }
            }
        }

        EmptyState {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: defectList.count === 0
            title: qsTr("暂无缺陷记录")
            message: qsTr("NG 记录产生后会自动更新缺陷分布。")
            badgeText: qsTr("OK")
            accentColor: Theme.statusOK
        }
    }
}
