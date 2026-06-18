import QtQuick
import styles

Rectangle {
    id: toastRoot
    visible: false
    color: {
        if (level === "error") return Theme.statusNG;
        if (level === "warning") return Theme.statusWarning;
        return Theme.accentGreen;
    }
    radius: Theme.radiusMD
    height: 40
    width: Math.min(Math.max(toastText.implicitWidth + Theme.spacingLG * 2, 180), 520)
    opacity: 0
    y: 20
    clip: true

    property string message: ""
    property string level: "success"
    property int duration: Theme.animToast

    function show(msg, lvl) {
        hideTimer.stop();
        message = msg;
        level = lvl || "success";
        opacity = 1;
        y = 0;
        visible = true;
        hideTimer.restart();
    }

    Timer {
        id: hideTimer
        interval: toastRoot.duration
        onTriggered: {
            toastRoot.opacity = 0;
            toastRoot.y = 20;
        }
    }

    Behavior on opacity {
        NumberAnimation { duration: Theme.animNormal; easing.type: Easing.OutCubic }
    }
    Behavior on y {
        NumberAnimation { duration: Theme.animNormal; easing.type: Easing.OutCubic }
    }

    onOpacityChanged: {
        if (opacity === 0) {
            visible = false;
        }
    }

    Text {
        id: toastText
        anchors.centerIn: parent
        width: parent.width - Theme.spacingLG
        text: toastRoot.message
        color: "#000"
        font.pixelSize: Theme.fontSizeXS
        font.bold: true
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
    }
}
