pragma Singleton
import QtQuick

QtObject {
    // ── Background ──
    readonly property color bgPrimary: "#0d1117"
    readonly property color bgSecondary: "#161b22"
    readonly property color bgTertiary: "#21262d"
    readonly property color bgCard: "#161b22"
    readonly property color bgOverlay: Qt.rgba(0, 0, 0, 0.92)

    // ── Accent ──
    readonly property color accent: "#58a6ff"
    readonly property color accentDim: Qt.rgba(0.345, 0.651, 1, 0.15)

    // ── Status ──
    readonly property color statusOK: "#3fb950"
    readonly property color statusOKDim: Qt.rgba(0.247, 0.725, 0.314, 0.18)
    readonly property color statusNG: "#f85149"
    readonly property color statusNGDim: Qt.rgba(0.973, 0.318, 0.286, 0.2)
    readonly property color statusWarning: "#d2991d"
    readonly property color statusWarningDim: Qt.rgba(0.824, 0.6, 0.114, 0.18)
    readonly property color statusReject: "#db6d28"
    readonly property color statusRejectDim: Qt.rgba(0.859, 0.427, 0.157, 0.18)

    // ── Text ──
    readonly property color textPrimary: "#e6edf3"
    readonly property color textSecondary: "#8b949e"
    readonly property color textMuted: "#484f58"

    // ── Border ──
    readonly property color borderDefault: Qt.rgba(0.945, 0.949, 0.953, 0.1)
    readonly property color borderStrong: Qt.rgba(0.945, 0.949, 0.953, 0.2)

    // ── Font sizes (industrial — readable at 1m+ distance) ──
    readonly property int fontSizeXS: 11
    readonly property int fontSizeSM: 13
    readonly property int fontSizeMD: 16
    readonly property int fontSizeLG: 22
    readonly property int fontSizeXL: 32
    readonly property int fontSizeXXL: 48

    // ── Spacing ──
    readonly property int spacingXS: 4
    readonly property int spacingSM: 8
    readonly property int spacingMD: 12
    readonly property int spacingLG: 20
    readonly property int spacingXL: 32

    // ── Radius ──
    readonly property int radiusSM: 4
    readonly property int radiusMD: 8
    readonly property int radiusLG: 12

    // ── Touch ──
    readonly property int touchMin: 48
    readonly property int touchComfort: 56

    // ── Accent variants ──
    readonly property color accentGreen: "#00ff88"
    readonly property color accentGreenDim: Qt.rgba(0, 1, 0.533, 0.15)
    readonly property color accentGreenGradient: "#00cc6a"

    // ── Animation ──
    readonly property int animFast: 150
    readonly property int animNormal: 200
    readonly property int animSlow: 300
    readonly property int animToast: 3000

    // ── Elevation ──
    readonly property real elevationLow: 0.08
    readonly property real elevationMid: 0.15
    readonly property real elevationHigh: 0.25

    // ── Card ──
    readonly property color cardGlass: Qt.rgba(1, 1, 1, 0.04)
    readonly property color cardGlassBorder: Qt.rgba(1, 1, 1, 0.08)
}
