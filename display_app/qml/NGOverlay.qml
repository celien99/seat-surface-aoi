import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: overlay
    property string defectType: ""
    property real confidence: 0.0
    property string cameraId: ""
    property string affectedCameras: ""
    property int defectCount: 0
    property int cameraCount: 0
    property int countdown: 30
    property int imageVersion: 0

    signal confirmNG()
    signal markReview()
    signal dismissFalseAlarm()

    anchors.fill: parent
    color: Theme.bgOverlay
    z: 100
    focus: visible

    onVisibleChanged: {
        if (visible) {
            forceActiveFocus()
        }
    }

    Keys.onPressed: function(event) {
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter || event.key === Qt.Key_1) {
            overlay.confirmNG()
            event.accepted = true
        } else if (event.key === Qt.Key_2) {
            overlay.markReview()
            event.accepted = true
        } else if (event.key === Qt.Key_3) {
            overlay.dismissFalseAlarm()
            event.accepted = true
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingXL
        spacing: Theme.spacingLG

        // ── Header ──
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMD

            Rectangle {
                Layout.preferredWidth: bannerText.implicitWidth + 40
                Layout.preferredHeight: 48
                radius: Theme.radiusMD
                color: Theme.statusNG
                Text {
                    id: bannerText
                    anchors.centerIn: parent
                    text: qsTr("缺陷检出")
                    color: "#ffffff"
                    font.pixelSize: Theme.fontSizeLG
                    font.bold: true
                }
            }

            Item { Layout.fillWidth: true }

            Rectangle {
                Layout.preferredHeight: 48
                Layout.preferredWidth: countdownLabel.implicitWidth + 32
                radius: Theme.radiusMD
                color: countdown <= 5 ? Theme.statusNGDim : Theme.bgTertiary
                border {
                    width: 1
                    color: countdown <= 5 ? Theme.statusNG : Theme.borderStrong
                }
                Text {
                    id: countdownLabel
                    anchors.centerIn: parent
                    text: countdown <= 0 ? qsTr("正在自动确认") : qsTr("自动确认 ") + countdown + "s"
                    color: countdown <= 5 ? Theme.statusNG : Theme.textSecondary
                    font.pixelSize: Theme.fontSizeSM
                    font.bold: true
                }
            }
        }

        // ── Body: images side by side ──
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spacingMD

            // Original image
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme.bgCard
                radius: Theme.radiusMD
                border { width: 1; color: Theme.borderDefault }

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 32
                        color: Theme.bgTertiary
                        radius: Theme.radiusMD
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.radius; color: parent.color }
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("原图  ") + cameraId
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSizeXS
                        }
                    }
                    Image {
                        id: originalImage
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 4
                        visible: cameraId !== "" && status !== Image.Error
                        source: "image://camera/" + cameraId + "_original?v=" + imageVersion
                        fillMode: Image.PreserveAspectFit
                        cache: false
                    }
                    EmptyState {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 4
                        visible: originalImage.status === Image.Error || cameraId === ""
                        title: qsTr("原图不可用")
                        message: qsTr("未收到该相机的告警原图。")
                        badgeText: qsTr("IMAGE")
                        accentColor: Theme.statusWarning
                    }
                }
            }

            // Detection overlay
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme.bgCard
                radius: Theme.radiusMD
                border { width: 1; color: Theme.borderDefault }

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 0
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 32
                        color: Theme.bgTertiary
                        radius: Theme.radiusMD
                        Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.radius; color: parent.color }
                        Text {
                            anchors.centerIn: parent
                            text: qsTr("检测图  分数: ") + confidence.toFixed(4)
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSizeXS
                        }
                    }
                    Image {
                        id: overlayImage
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 4
                        visible: cameraId !== "" && status !== Image.Error
                        source: "image://camera/" + cameraId + "_overlay?v=" + imageVersion
                        fillMode: Image.PreserveAspectFit
                        cache: false
                    }
                    EmptyState {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.margins: 4
                        visible: overlayImage.status === Image.Error || cameraId === ""
                        title: qsTr("检测图不可用")
                        message: qsTr("可先按当前缺陷信息处理，随后在日志中复核该记录。")
                        badgeText: qsTr("OVERLAY")
                        accentColor: Theme.statusWarning
                    }
                }
            }
        }

        // ── Info cards ──
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMD

            InfoCard {
                accentColor: Theme.statusNG
                cardLabel: qsTr("缺陷类型")
                cardValue: defectType || "--"
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.statusNG
                cardLabel: qsTr("NG 机位")
                cardValue: cameraCount > 0 ? cameraCount.toString() : "--"
                cardSubtext: affectedCameras
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.statusWarning
                cardLabel: qsTr("缺陷数")
                cardValue: defectCount > 0 ? defectCount.toString() : "--"
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.statusWarning
                cardLabel: qsTr("置信度")
                cardValue: confidence.toFixed(3)
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.accent
                cardLabel: qsTr("相机")
                cardValue: cameraId
                Layout.fillWidth: true
            }
            InfoCard {
                accentColor: Theme.textSecondary
                cardLabel: qsTr("自动动作")
                cardValue: "NG"
                cardSubtext: qsTr("超时后")
                Layout.fillWidth: true
            }
        }

        // ── Action buttons ──
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingLG

            ActionButton {
                buttonText: qsTr("1  确认缺陷")
                bgColor: Theme.statusNG
                Layout.fillWidth: true
                implicitHeight: Theme.touchComfort
                font { pixelSize: Theme.fontSizeMD; bold: true }
                onClicked: overlay.confirmNG()
            }
            ActionButton {
                buttonText: qsTr("2  标记待复核")
                bgColor: Theme.statusWarning
                textColor: "#000000"
                Layout.fillWidth: true
                implicitHeight: Theme.touchComfort
                font { pixelSize: Theme.fontSizeMD; bold: true }
                onClicked: overlay.markReview()
            }
            ActionButton {
                buttonText: qsTr("3  误报忽略")
                bgColor: Theme.bgTertiary
                Layout.fillWidth: true
                implicitHeight: Theme.touchComfort
                font { pixelSize: Theme.fontSizeMD; bold: true }
                onClicked: overlay.dismissFalseAlarm()
            }
        }
    }
}
