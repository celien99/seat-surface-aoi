import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

ApplicationWindow {
    id: window
    visible: true
    title: qsTr("Seat Surface AOI 在线展示")
    width: 1600
    height: 960
    minimumWidth: 1100
    minimumHeight: 720
    color: Theme.bgPrimary

    property var mainVm: mainViewModel
    property var statsVm: statsViewModel
    property var logVm: logViewModel
    property var reviewVm: reviewViewModel

    header: Rectangle {
        height: 48
        color: Theme.bgSecondary

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.spacingLG
            anchors.rightMargin: Theme.spacingSM
            spacing: Theme.spacingMD

            Text {
                text: qsTr("Seat Surface AOI 在线展示")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontSizeMD
                font.bold: true
            }

            Rectangle {
                width: 1
                height: 24
                color: Theme.borderStrong
            }

            Text {
                text: qsTr("产线 ") + (window.mainVm ? window.mainVm.lineId : "--")
                color: Theme.accent
                font.pixelSize: Theme.fontSizeSM
            }

            Item { Layout.fillWidth: true }

            TabBar {
                id: navBar
                background: null
                Layout.preferredHeight: 48

                onCurrentIndexChanged: {
                    if (currentIndex === 1 && window.statsVm) window.statsVm.refresh()
                    if (currentIndex === 2 && window.logVm) window.logVm.refresh()
                    if (currentIndex === 3 && window.reviewVm) window.reviewVm.refresh()
                }

                NavTabButton { text: qsTr("监控") }
                NavTabButton { text: qsTr("统计") }
                NavTabButton { text: qsTr("日志") }
                NavTabButton { text: qsTr("复核") }
            }
        }
    }

    StackLayout {
        anchors.fill: parent
        currentIndex: navBar.currentIndex

        MainScreen {
            viewModel: window.mainVm
        }

        StatsPage {
            viewModel: window.statsVm
        }

        LogsPage {
            viewModel: window.logVm
        }

        ReviewPage {
            viewModel: window.reviewVm
        }
    }
}
