import QtQuick
import QtQuick.Layouts
import "components"
import styles

Rectangle {
    id: tile
    property string cameraId: ""
    property string cameraStatus: "ok"
    property string defectLabel: ""
    property bool live: false
    property int frameVersion: 0
    property string displayMode: "auto"

    signal openPreview(string cameraId)

    color: Theme.bgPrimary
    radius: Theme.radiusSM
    border {
        width: cameraStatus === "ng" ? 3 : (hoverArea.containsMouse ? 2 : 1)
        color: statusColor(cameraStatus, false)
    }
    clip: true

    // Camera feed
    Image {
        id: cameraImage
        anchors.fill: parent
        anchors.margins: 4
        source: "image://camera/" + cameraId + imageSuffix() + "?v=" + frameVersion
        cache: false
        fillMode: Image.PreserveAspectFit
    }

    Rectangle {
        anchors.fill: cameraImage
        anchors.margins: 4
        visible: cameraImage.status === Image.Error && live
        color: Qt.rgba(0, 0, 0, 0.62)
        radius: Theme.radiusSM

        Text {
            anchors.centerIn: parent
            width: parent.width - Theme.spacingXL
            text: qsTr("图像加载失败")
            color: Theme.statusWarning
            font.pixelSize: Theme.fontSizeSM
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }

    // No signal placeholder
    Rectangle {
        anchors.centerIn: parent
        width: 120; height: 36; radius: Theme.radiusSM
        color: Qt.rgba(0, 0, 0, 0.5)
        visible: !live
        Text {
            anchors.centerIn: parent
            text: qsTr("无信号")
            color: Theme.textMuted
            font.pixelSize: Theme.fontSizeSM
        }
    }

    // Bottom info bar
    Rectangle {
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: 4
        height: 30
        radius: Theme.radiusSM
        color: Qt.rgba(0, 0, 0, 0.6)

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: Theme.spacingSM
            anchors.rightMargin: Theme.spacingSM
            spacing: Theme.spacingSM

            // Live dot
            Rectangle {
                width: 8; height: 8; radius: 4
                Layout.alignment: Qt.AlignVCenter
                color: live ? Theme.statusOK : Theme.textMuted
            }

            Text {
                text: cameraId || qsTr("未命名相机")
                color: Theme.textPrimary
                font.pixelSize: Theme.fontSizeXS
                font.bold: true
                Layout.alignment: Qt.AlignVCenter
                Layout.fillWidth: true
                elide: Text.ElideRight
            }

            // NG badge in bottom bar
            StatusBadge {
                visible: cameraStatus !== "ok"
                Layout.alignment: Qt.AlignVCenter
                height: 20
                badgeText: cameraStatus === "ng" ? qsTr("NG")
                           : cameraStatus === "error" ? qsTr("异常")
                           : qsTr("复检")
                badgeStatus: cameraStatus === "ng" || cameraStatus === "error" ? "ng" : "warning"
                maxBadgeWidth: 82
            }

            StatusBadge {
                visible: imageSuffix() !== ""
                Layout.alignment: Qt.AlignVCenter
                height: 20
                badgeText: imageSuffix() === "_heatmap" ? qsTr("热力") : qsTr("检测")
                badgeStatus: imageSuffix() === "_heatmap" ? "warning" : "ok"
                maxBadgeWidth: 64
            }
        }
    }

    // Defect type floating label
    Rectangle {
        visible: cameraStatus === "ng" && defectLabel !== ""
        anchors.centerIn: parent
        anchors.verticalCenterOffset: -24
        width: defectText.implicitWidth + 24
        height: 28
        radius: Theme.radiusSM
        color: Qt.rgba(0.973, 0.318, 0.286, 0.85)
        Text {
            id: defectText
            anchors.centerIn: parent
            width: parent.width - Theme.spacingSM
            text: defectLabel
            color: "#ffffff"
            font.pixelSize: Theme.fontSizeSM
            font.bold: true
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: 4
        radius: Theme.radiusSM
        color: hoverArea.containsMouse ? Qt.rgba(1, 1, 1, 0.035) : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.animFast } }
    }

    MouseArea {
        id: hoverArea
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: live ? Qt.PointingHandCursor : Qt.ArrowCursor
        acceptedButtons: Qt.LeftButton
        onDoubleClicked: {
            if (live && cameraId !== "") {
                tile.openPreview(cameraId)
            }
        }
    }

    // NG border pulse animation
    SequentialAnimation on border.color {
        running: cameraStatus === "ng"
        loops: Animation.Infinite
        ColorAnimation { from: Theme.statusNG; to: Qt.rgba(0.973, 0.318, 0.286, 0.35); duration: 600 }
        ColorAnimation { from: Qt.rgba(0.973, 0.318, 0.286, 0.35); to: Theme.statusNG; duration: 600 }
    }

    function statusColor(status, dim) {
        if (status === "ng") return dim ? Theme.statusNGDim : Theme.statusNG
        if (status === "error") return dim ? Theme.statusNGDim : Theme.statusNG
        if (status === "warn") return dim ? Theme.statusWarningDim : Theme.statusWarning
        return hoverArea.containsMouse ? Theme.borderStrong : Theme.borderDefault
    }

    function imageSuffix() {
        if (displayMode === "overlay") return "_overlay"
        if (displayMode === "heatmap") return "_heatmap"
        if (displayMode === "auto" && cameraStatus === "ng") return "_overlay"
        return ""
    }
}
