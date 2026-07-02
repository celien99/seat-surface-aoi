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
    property var cameraItems: []
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

        // ── Body: NG cameras ──
        GridLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: Math.max(1, Math.min(2, cameraItems.length))
            rowSpacing: Theme.spacingMD
            columnSpacing: Theme.spacingMD

            Repeater {
                model: cameraItems

                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: Theme.bgCard
                    radius: Theme.radiusMD
                    border { width: 1; color: Theme.borderDefault }
                    clip: true

                    ColumnLayout {
                        anchors.fill: parent
                        spacing: 0

                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 36
                            color: Theme.bgTertiary
                            radius: Theme.radiusMD
                            Rectangle { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: parent.radius; color: parent.color }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spacingSM
                                anchors.rightMargin: Theme.spacingSM
                                spacing: Theme.spacingSM

                                Text {
                                    text: modelData.cameraId || "--"
                                    color: Theme.textPrimary
                                    font.pixelSize: Theme.fontSizeXS
                                    font.bold: true
                                    Layout.fillWidth: true
                                    elide: Text.ElideRight
                                }
                                StatusBadge {
                                    badgeText: qsTr("NG ") + String(modelData.defectCount || 1)
                                    badgeStatus: "ng"
                                    maxBadgeWidth: 72
                                }
                                Text {
                                    text: Number(modelData.confidence || 0).toFixed(3)
                                    color: Theme.textSecondary
                                    font.pixelSize: Theme.fontSizeXS
                                }
                            }
                        }

                        Image {
                            id: ngCameraImage
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.margins: 4
                            visible: modelData.cameraId !== "" && status !== Image.Error
                            source: "image://camera/" + modelData.cameraId + "_overlay?v=" + imageVersion
                            fillMode: Image.PreserveAspectFit
                            cache: false
                        }

                        EmptyState {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.margins: 4
                            visible: ngCameraImage.status === Image.Error || modelData.cameraId === ""
                            title: qsTr("检测图不可用")
                            message: qsTr("该 NG 机位未收到可显示检测图。")
                            badgeText: qsTr("OVERLAY")
                            accentColor: Theme.statusWarning
                        }

                        Text {
                            Layout.fillWidth: true
                            Layout.leftMargin: Theme.spacingSM
                            Layout.rightMargin: Theme.spacingSM
                            Layout.bottomMargin: Theme.spacingXS
                            text: modelData.defectLabel || "--"
                            color: Theme.textSecondary
                            font.pixelSize: Theme.fontSizeXS
                            elide: Text.ElideRight
                        }
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
