import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import "components"
import styles

Rectangle {
    id: mainScreen
    color: Theme.bgPrimary

    property var viewModel: null
    property string previewCameraId: ""
    property int previewFrameVersion: 0
    property string displayMode: "auto"

    function openCameraPreview(cameraId) {
        previewCameraId = cameraId
        previewFrameVersion = frameVersionForCamera(cameraId)
        previewOverlay.visible = true
        previewOverlay.forceActiveFocus()
    }

    Connections {
        target: mainScreen.viewModel
        ignoreUnknownSignals: true
        function onCameraListChanged() {
            if (previewOverlay.visible) {
                mainScreen.previewFrameVersion = mainScreen.frameVersionForCamera(mainScreen.previewCameraId)
            }
        }
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        StatusBar {
            id: statusBar
            Layout.fillWidth: true
            lineId: viewModel ? viewModel.lineId : ""
            systemStatus: viewModel ? viewModel.systemStatus : "stopped"
            okCount: viewModel ? viewModel.okCount : 0
            ngCount: viewModel ? viewModel.ngCount : 0
            tactRate: viewModel ? viewModel.tactRate : 0.0
            lineStatus: viewModel ? viewModel.lineStatus : "unknown"
            lineConnected: viewModel ? viewModel.lineConnected : false
            lineBusy: viewModel ? viewModel.lineBusy : false
            lastTriggerResult: viewModel ? viewModel.lastTriggerResult : ""
            triggerError: viewModel ? viewModel.triggerErrorDisplay : ""
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            color: Theme.bgPrimary

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: Theme.spacingMD
                anchors.rightMargin: Theme.spacingMD
                spacing: Theme.spacingSM

                Text {
                    text: qsTr("相机")
                    color: Theme.textPrimary
                    font.pixelSize: Theme.fontSizeSM
                    font.bold: true
                }

                Text {
                    text: viewModel ? qsTr("已连接 ") + connectedCameraCount(viewModel.cameraList) + "/" + viewModel.cameraList.length : qsTr("已连接 0/0")
                    color: {
                        if (!viewModel || viewModel.cameraList.length === 0) return Theme.textMuted
                        return connectedCameraCount(viewModel.cameraList) === viewModel.cameraList.length ? Theme.statusOK : Theme.statusWarning
                    }
                    font.pixelSize: Theme.fontSizeSM
                }

                Item { Layout.fillWidth: true }

                StatusBadge {
                    visible: viewModel && viewModel.triggerErrorDisplay !== ""
                    badgeText: qsTr("触发异常")
                    badgeStatus: "ng"
                    maxBadgeWidth: 100
                }

                RowLayout {
                    spacing: 4

                    ActionButton {
                        buttonText: qsTr("自动")
                        bgColor: mainScreen.displayMode === "auto" ? Theme.accent : Theme.bgTertiary
                        implicitHeight: 30
                        Layout.preferredWidth: 56
                        onClicked: mainScreen.displayMode = "auto"
                    }

                    ActionButton {
                        buttonText: qsTr("原图")
                        bgColor: mainScreen.displayMode === "original" ? Theme.accent : Theme.bgTertiary
                        implicitHeight: 30
                        Layout.preferredWidth: 56
                        onClicked: mainScreen.displayMode = "original"
                    }

                    ActionButton {
                        buttonText: qsTr("检测图")
                        bgColor: mainScreen.displayMode === "overlay" ? Theme.accent : Theme.bgTertiary
                        implicitHeight: 30
                        Layout.preferredWidth: 68
                        onClicked: mainScreen.displayMode = "overlay"
                    }

                    ActionButton {
                        buttonText: qsTr("热力图")
                        bgColor: mainScreen.displayMode === "heatmap" ? Theme.accent : Theme.bgTertiary
                        implicitHeight: 30
                        Layout.preferredWidth: 68
                        onClicked: mainScreen.displayMode = "heatmap"
                    }
                }

                ActionButton {
                    buttonText: qsTr("只读展示")
                    bgColor: viewModel && viewModel.triggerEnabled ? Theme.accent : Theme.bgTertiary
                    implicitHeight: 32
                    Layout.preferredWidth: 104
                    enabled: false
                    onClicked: viewModel.manualTrigger()
                }
            }
        }

        CameraGrid {
            Layout.fillWidth: true
            Layout.fillHeight: true
            cameraModel: viewModel ? viewModel.cameraList : []
            gridLayout: viewModel ? viewModel.gridLayout : "2x2"
            displayMode: mainScreen.displayMode
            onOpenPreview: function(cameraId) {
                mainScreen.openCameraPreview(cameraId)
            }
        }
    }

    Rectangle {
        id: previewOverlay
        anchors.fill: parent
        visible: false
        focus: visible
        color: Theme.bgOverlay
        z: 90

        Keys.onEscapePressed: previewOverlay.visible = false

        MouseArea {
            anchors.fill: parent
            onClicked: previewOverlay.visible = false
        }

        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width - Theme.spacingXL * 2, 1280)
            height: Math.min(parent.height - Theme.spacingXL * 2, 820)
            radius: Theme.radiusMD
            color: Theme.bgCard
            border { width: 1; color: Theme.borderStrong }
            clip: true

            MouseArea {
                anchors.fill: parent
                onClicked: function(mouse) { mouse.accepted = true }
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 0

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 44
                    color: Theme.bgTertiary

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingMD
                        anchors.rightMargin: Theme.spacingSM
                        spacing: Theme.spacingSM

                        Text {
                            text: mainScreen.previewCameraId
                            color: Theme.textPrimary
                            font.pixelSize: Theme.fontSizeSM
                            font.bold: true
                            Layout.fillWidth: true
                            elide: Text.ElideRight
                        }

                        StatusBadge {
                            badgeText: qsTr("实时预览")
                            badgeStatus: "ok"
                            maxBadgeWidth: 96
                        }

                        ActionButton {
                            buttonText: qsTr("关闭")
                            bgColor: Theme.bgTertiary
                            implicitHeight: 30
                            Layout.preferredWidth: 72
                            onClicked: previewOverlay.visible = false
                        }
                    }
                }

                Image {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.margins: Theme.spacingSM
                    source: "image://camera/" + mainScreen.previewCameraId + previewImageSuffix() + "?v=" + mainScreen.previewFrameVersion
                    cache: false
                    fillMode: Image.PreserveAspectFit
                }
            }
        }
    }

    NGOverlay {
        id: ngOverlay
        anchors.fill: parent
        visible: viewModel ? viewModel.ngOverlayVisible : false
        defectType: viewModel ? viewModel.ngDefectType : ""
        confidence: viewModel ? viewModel.ngConfidence : 0.0
        cameraId: viewModel ? viewModel.ngCameraId : ""
        countdown: viewModel ? viewModel.remainingSeconds : 0
        imageVersion: viewModel ? viewModel.ngImageVersion : 0

        onConfirmNG: { if (viewModel) viewModel.acknowledgeNG(); }
        onMarkReview: { if (viewModel) viewModel.markReview(); }
        onDismissFalseAlarm: { if (viewModel) viewModel.dismissFalseAlarm(); }
    }

    Timer {
        interval: 1000
        running: true
        repeat: true
        onTriggered: {
            if (viewModel) {
                viewModel.refreshTriggerState()
            }
        }
    }

    function connectedCameraCount(items) {
        var count = 0
        for (var i = 0; i < items.length; i++) {
            if (items[i].live) {
                count += 1
            }
        }
        return count
    }

    function frameVersionForCamera(cameraId) {
        if (!viewModel) return 0
        var items = viewModel.cameraList
        for (var i = 0; i < items.length; i++) {
            if (items[i].cameraId === cameraId) {
                return items[i].frameVersion || 0
            }
        }
        return 0
    }

    function previewImageSuffix() {
        if (displayMode === "overlay") return "_overlay"
        if (displayMode === "heatmap") return "_heatmap"
        if (displayMode !== "auto") return ""
        if (!viewModel) return ""
        var items = viewModel.cameraList
        for (var i = 0; i < items.length; i++) {
            if (items[i].cameraId === previewCameraId && items[i].status === "ng") {
                return "_overlay"
            }
        }
        return ""
    }
}
