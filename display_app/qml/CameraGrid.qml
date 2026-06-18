import QtQuick
import QtQuick.Layouts
import "components"
import styles

Item {
    id: grid

    property var cameraModel: []
    property string gridLayout: "2x2"
    property string displayMode: "auto"
    signal openPreview(string cameraId)

    GridLayout {
        anchors.fill: parent
        visible: grid.cameraModel.length > 0
        columns: {
            var parts = grid.gridLayout.split("x");
            return parts.length === 2 ? parseInt(parts[0]) : 2;
        }
        rows: {
            var parts = grid.gridLayout.split("x");
            return parts.length === 2 ? parseInt(parts[1]) : 2;
        }
        rowSpacing: 4
        columnSpacing: 4

        Repeater {
            model: grid.cameraModel

            CameraTile {
                Layout.fillWidth: true
                Layout.fillHeight: true
                cameraId: modelData.cameraId || ""
                cameraStatus: modelData.status || "ok"
                defectLabel: modelData.defectLabel || ""
                live: modelData.live || false
                frameVersion: modelData.frameVersion || 0
                displayMode: grid.displayMode
                onOpenPreview: function(cameraId) {
                    grid.openPreview(cameraId)
                }
            }
        }
    }

    EmptyState {
        anchors.fill: parent
        visible: grid.cameraModel.length === 0
        title: qsTr("等待检测结果")
        message: qsTr("当前还没有可显示的检测图像，前端会自动读取 trace/display_latest.json。")
        badgeText: qsTr("AOI")
        accentColor: Theme.statusWarning
    }
}
