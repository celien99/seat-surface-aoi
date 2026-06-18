import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import styles

Rectangle {
    id: root

    width: 440
    height: Math.max(160, innerLayout.implicitHeight + 3)
    anchors.centerIn: parent
    visible: false
    z: 998
    focus: visible
    radius: 14
    color: "#1a1e26"
    border { width: 1; color: Qt.rgba(1, 1, 1, 0.1) }

    property string title: ""
    property string acceptText: qsTr("确认")
    property string cancelText: qsTr("取消")
    property bool showCancel: true
    default property alias dialogContent: contentCol.data

    signal accepted()
    signal rejected()

    function open() {
        root.visible = true;
        root.opacity = 0;
        root.forceActiveFocus();
        fadeIn.start();
    }
    function close() {
        fadeOut.start();
    }

    Keys.onEscapePressed: {
        root.rejected();
        root.close();
    }
    OpacityAnimator {
        id: fadeIn
        target: root
        from: 0; to: 1
        duration: Theme.animFast
        easing.type: Easing.OutCubic
    }
    OpacityAnimator {
        id: fadeOut
        target: root
        from: 1; to: 0
        duration: Theme.animFast
        onStopped: { root.visible = false; root.opacity = 1; }
    }

    gradient: Gradient {
        GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.04) }
        GradientStop { position: 0.6; color: "transparent" }
    }

    // Top accent bar
    Rectangle {
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        height: 3; radius: 14
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.accent }
            GradientStop { position: 1.0; color: Qt.rgba(0.345, 0.651, 1, 0.5) }
        }
    }

    ColumnLayout {
        id: innerLayout
        anchors.fill: parent
        anchors.topMargin: 3
        spacing: 0

        // ── Header row ──
        Item {
            Layout.fillWidth: true
            Layout.preferredHeight: 48
            Layout.leftMargin: 20; Layout.rightMargin: 20
            Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: root.title
                color: Theme.textPrimary
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
        }

        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 1
            Layout.leftMargin: 20; Layout.rightMargin: 20
            color: Qt.rgba(1, 1, 1, 0.06)
        }

        // ── Content area ──
        ColumnLayout {
            id: contentCol
            Layout.fillWidth: true
            Layout.leftMargin: 20; Layout.rightMargin: 20
            Layout.topMargin: 14; Layout.bottomMargin: 14
            spacing: 12
        }

        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 1
            Layout.leftMargin: 20; Layout.rightMargin: 20
            color: Qt.rgba(1, 1, 1, 0.06)
        }

        // ── Footer ──
        Item {
            Layout.fillWidth: true; Layout.preferredHeight: 52
            Layout.leftMargin: 20; Layout.rightMargin: 14

            RowLayout {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 10

                ActionButton {
                    visible: root.showCancel
                    buttonText: root.cancelText
                    bgColor: Qt.rgba(1, 1, 1, 0.035)
                    textColor: Theme.textSecondary
                    borderColor: Qt.rgba(1, 1, 1, 0.15)
                    implicitWidth: 88
                    implicitHeight: 34
                    compact: true
                    onClicked: { root.rejected(); root.close(); }
                }

                ActionButton {
                    buttonText: root.acceptText
                    bgColor: Theme.accent
                    textColor: "#ffffff"
                    implicitWidth: 88
                    implicitHeight: 34
                    compact: true
                    onClicked: { root.accepted(); root.close(); }
                }
            }
        }
    }
}
