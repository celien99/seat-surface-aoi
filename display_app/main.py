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
from display_app.services.manual_trigger_client import (
    ManualTriggerClient,
    ManualTriggerConfig,
    decode_control_text,
)
from display_app.services.operator_journal import OperatorJournal
from display_app.viewmodels.main_viewmodel import MainViewModel


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动 Seat Surface AOI PySide6/QML 展示前端。")
    parser.add_argument("--trace-root", default="trace", help="detector display_latest.json 所在目录。")
    parser.add_argument("--line-id", default="AOI-1", help="前端状态栏显示的产线编号。")
    parser.add_argument("--grid-layout", default="2x2", help="相机网格布局，例如 2x2、3x2。")
    parser.add_argument("--poll-ms", type=int, default=300, help="轮询 display_latest.json 的周期。")
    parser.add_argument("--ng-popup-seconds", type=int, default=30, help="NG 弹窗自动确认倒计时。")
    parser.add_argument("--enable-manual-trigger", action="store_true", help="启用首页手动触发按钮。")
    parser.add_argument("--manual-trigger-host", default="127.0.0.1", help="C++ tcp_signal 监听地址。")
    parser.add_argument("--manual-trigger-port", type=int, default=9002, help="C++ display_manual_trigger 独立监听端口。")
    parser.add_argument("--manual-trigger-timeout-ms", type=int, default=1000, help="手动触发 TCP 超时。")
    parser.add_argument("--manual-trigger-terminator", default="\\n", help="手动触发命令结尾，支持 \\\\n。")
    parser.add_argument("--manual-trigger-start-command", default="start", help="两步协议到位信号命令。")
    parser.add_argument("--manual-trigger-sn-prefix", default="sn", help="两步协议 SN 前缀。")
    parser.add_argument("--manual-trigger-start-ack", default="start_ack", help="到位信号确认文本（对齐生产配置不带 \\n）。")
    parser.add_argument("--manual-trigger-sn-ack", default="sn_ack", help="SN 确认文本（对齐生产配置不带 \\n）。")
    parser.add_argument("--manual-trigger-result-timeout-ms", type=int, default=30000, help="手动触发后等待展示结果的最长时间。")
    args, qt_args = parser.parse_known_args(argv)
    args.qt_args = qt_args
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

    app = QGuiApplication([sys.argv[0], *args.qt_args])
    image_provider = CameraImageProvider()
    bridge = DisplayBridge(args.trace_root, image_provider)
    bridge.skip_existing_events()
    journal = OperatorJournal(args.trace_root)
    manual_trigger_client = (
        ManualTriggerClient(
            ManualTriggerConfig(
                host=args.manual_trigger_host,
                port=args.manual_trigger_port,
                timeout_ms=args.manual_trigger_timeout_ms,
                terminator=decode_control_text(args.manual_trigger_terminator),
                start_command=args.manual_trigger_start_command,
                sn_prefix=args.manual_trigger_sn_prefix,
                start_ack=decode_control_text(args.manual_trigger_start_ack),
                sn_ack=decode_control_text(args.manual_trigger_sn_ack),
            )
        )
        if args.enable_manual_trigger
        else None
    )
    view_model = MainViewModel(
        bridge,
        line_id=args.line_id,
        grid_layout=args.grid_layout,
        ng_popup_seconds=args.ng_popup_seconds,
        manual_trigger_result_timeout_ms=args.manual_trigger_result_timeout_ms,
        journal=journal,
        manual_trigger_client=manual_trigger_client,
    )

    engine = QQmlApplicationEngine()
    engine.addImageProvider("camera", image_provider)
    if getattr(sys, "frozen", False):
        _base = Path(sys._MEIPASS) / "display_app"
    else:
        _base = Path(__file__).resolve().parent
    qml_root = _base / "qml"
    style_root = _base / "resources" / "styles"
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
