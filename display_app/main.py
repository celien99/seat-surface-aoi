from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from display_app.infrastructure.image_provider import CameraImageProvider
from display_app.services.display_bridge import DisplayBridge
from display_app.viewmodels.main_viewmodel import MainViewModel


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Seat Surface AOI PySide6/QML 展示前端。")
    parser.add_argument("--trace-root", default="trace", help="detector display_latest.json 所在目录。")
    parser.add_argument("--line-id", default="AOI-1", help="前端状态栏显示的产线编号。")
    parser.add_argument("--grid-layout", default="2x2", help="相机网格布局，例如 2x2、3x2。")
    parser.add_argument("--poll-ms", type=int, default=300, help="轮询 display_latest.json 的周期。")
    parser.add_argument("--ng-popup-seconds", type=int, default=30, help="NG 弹窗自动确认倒计时。")
    args, qt_args = parser.parse_known_args(argv)
    args.qt_args = qt_args
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

    app = QGuiApplication([sys.argv[0], *args.qt_args])
    image_provider = CameraImageProvider()
    bridge = DisplayBridge(args.trace_root, image_provider)
    view_model = MainViewModel(
        bridge,
        line_id=args.line_id,
        grid_layout=args.grid_layout,
        ng_popup_seconds=args.ng_popup_seconds,
    )

    engine = QQmlApplicationEngine()
    engine.addImageProvider("camera", image_provider)
    qml_root = Path(__file__).resolve().parent / "qml"
    style_root = Path(__file__).resolve().parent / "resources" / "styles"
    engine.addImportPath(str(qml_root))
    engine.addImportPath(str(style_root.parent))
    engine.rootContext().setContextProperty("mainViewModel", view_model)
    engine.rootContext().setContextProperty("statsViewModel", view_model)
    engine.rootContext().setContextProperty("logViewModel", view_model)
    engine.rootContext().setContextProperty("reviewViewModel", view_model)

    timer = QTimer()
    timer.setInterval(max(100, int(args.poll_ms)))
    timer.timeout.connect(view_model.refreshTriggerState)
    timer.start()
    view_model.pollLatest()

    engine.load(QUrl.fromLocalFile(str(qml_root / "main.qml")))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
