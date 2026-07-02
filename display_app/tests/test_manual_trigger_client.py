from __future__ import annotations

import re
import socket
import threading
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from display_app.infrastructure.image_provider import CameraImageProvider
from display_app.services.display_bridge import DisplayBridge
from display_app.services.manual_trigger_client import ManualTriggerClient, ManualTriggerConfig, ManualTriggerError
from display_app.services.operator_journal import OperatorJournal
from display_app.viewmodels.main_viewmodel import MainViewModel


def test_manual_trigger_client_sends_start_sn_handshake() -> None:
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )

    result = client.trigger("SN-001")

    assert result.sn == "SN-001"
    assert server.lines == ["start\n", "sn SN-001\n"]


def test_manual_trigger_client_rejects_invalid_sn() -> None:
    client = ManualTriggerClient(ManualTriggerConfig())

    try:
        client.trigger("BAD SN")
    except ManualTriggerError as exc:
        assert "SN 只能包含" in str(exc)
    else:
        raise AssertionError("invalid SN should fail")


def test_manual_trigger_client_accepts_ack_with_newline() -> None:
    """验证 display_app 兼容带 \\n 终止符的响应（向后兼容旧版 cpp 配置）。"""
    server = _StartSnServer(newline_ack=True)
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )

    result = client.trigger("SN-NL")

    assert result.sn == "SN-NL"
    assert server.lines == ["start\n", "sn SN-NL\n"]


def test_main_view_model_manual_trigger_defaults_to_read_only(tmp_path: Path) -> None:
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()))

    view_model.manualTrigger()

    assert view_model.triggerEnabled is False
    assert "未启用" in view_model.triggerError


def test_main_view_model_generates_timestamp_sn_and_persists_action(tmp_path: Path) -> None:
    _ensure_qt_app()
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )
    journal = OperatorJournal(tmp_path)
    view_model = MainViewModel(
        DisplayBridge(tmp_path, CameraImageProvider()),
        journal=journal,
        manual_trigger_client=client,
    )

    view_model.manualTrigger()
    assert server.done.wait(2.0)
    _wait_until(lambda: view_model.manualTriggerStage == "waiting_result")
    generated_sn = _server_sn(server)

    assert view_model.triggerEnabled is False
    assert view_model.manualTriggerPending is True
    assert view_model.manualTriggerStage == "waiting_result"
    assert re.fullmatch(r"MANUAL_\d{20}", generated_sn)
    assert view_model.manualSn == generated_sn
    assert view_model.triggerError == ""
    assert server.lines == ["start\n", f"sn {generated_sn}\n"]
    journal_text = (tmp_path / "display_operator_events.jsonl").read_text(encoding="utf-8")
    assert "manual_trigger" in journal_text
    assert generated_sn in journal_text


def test_main_view_model_manual_trigger_unlocks_after_matching_display_result(tmp_path: Path) -> None:
    _ensure_qt_app()
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )
    view_model = MainViewModel(
        DisplayBridge(tmp_path, CameraImageProvider()),
        manual_trigger_client=client,
    )

    view_model.manualTrigger()
    assert server.done.wait(2.0)
    _wait_until(lambda: view_model.manualTriggerStage == "waiting_result")
    generated_sn = _server_sn(server)

    _write_latest(tmp_path, seat_id=generated_sn, decision="OK")
    view_model.pollLatest()

    assert view_model.manualTriggerPending is False
    assert view_model.manualTriggerStage == "idle"
    assert view_model.triggerEnabled is True
    assert view_model.manualSn == ""
    assert view_model.lastTriggerResult == "OK"


def test_main_view_model_manual_trigger_unlocks_after_prefixed_display_result(tmp_path: Path) -> None:
    _ensure_qt_app()
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )
    view_model = MainViewModel(
        DisplayBridge(tmp_path, CameraImageProvider()),
        manual_trigger_client=client,
    )

    view_model.manualTrigger()
    assert server.done.wait(2.0)
    _wait_until(lambda: view_model.manualTriggerStage == "waiting_result")
    generated_sn = _server_sn(server)

    _write_latest(tmp_path, seat_id=f"LINE1_AOI_01_{generated_sn}", decision="OK")
    view_model.pollLatest()

    assert view_model.manualTriggerPending is False
    assert view_model.manualTriggerStage == "idle"
    assert view_model.triggerEnabled is True
    assert view_model.manualSn == ""
    assert view_model.lastTriggerResult == "OK"


def test_main_view_model_manual_trigger_unlocks_after_compact_trace_result(tmp_path: Path) -> None:
    _ensure_qt_app()
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )
    view_model = MainViewModel(
        DisplayBridge(tmp_path, CameraImageProvider()),
        manual_trigger_client=client,
    )

    view_model.manualTrigger()
    assert server.done.wait(2.0)
    _wait_until(lambda: view_model.manualTriggerStage == "waiting_result")
    generated_sn = _server_sn(server)

    _write_latest(tmp_path, seat_id=f"{generated_sn}_153012248123", decision="OK")
    view_model.pollLatest()

    assert view_model.manualTriggerPending is False
    assert view_model.manualTriggerStage == "idle"
    assert view_model.triggerEnabled is True
    assert view_model.manualSn == ""
    assert view_model.lastTriggerResult == "OK"


def test_main_view_model_manual_trigger_timeout_reenables_button(tmp_path: Path) -> None:
    _ensure_qt_app()
    server = _StartSnServer()
    server.start()
    client = ManualTriggerClient(
        ManualTriggerConfig(host="127.0.0.1", port=server.port, timeout_ms=1000)
    )
    view_model = MainViewModel(
        DisplayBridge(tmp_path, CameraImageProvider()),
        manual_trigger_client=client,
        manual_trigger_result_timeout_ms=1000,
    )

    view_model.manualTrigger()
    assert server.done.wait(2.0)
    _wait_until(lambda: view_model.manualTriggerStage == "waiting_result")
    _wait_until(lambda: _refresh_and_check(view_model, lambda: not view_model.manualTriggerPending), timeout_s=2.0)

    assert view_model.triggerEnabled is True
    assert view_model.manualSn == ""
    assert "等待检测结果超时" in view_model.triggerError


class _StartSnServer:
    def __init__(self, newline_ack: bool = False) -> None:
        self._ready = threading.Event()
        self.done = threading.Event()
        self.lines: list[str] = []
        self.port = 0
        self._newline_ack = newline_ack
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(2.0)

    def _run(self) -> None:
        nl = b"\n" if self._newline_ack else b""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.port = int(server.getsockname()[1])
            self._ready.set()
            conn, _addr = server.accept()
            with conn:
                self.lines.append(_read_line(conn))
                conn.sendall(b"start_ack" + nl)
                self.lines.append(_read_line(conn))
                conn.sendall(b"sn_ack" + nl)
        self.done.set()


def _read_line(conn: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(1)
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8")


def _server_sn(server: _StartSnServer) -> str:
    assert len(server.lines) >= 2
    prefix = "sn "
    line = server.lines[1]
    assert line.startswith(prefix)
    return line.removeprefix(prefix).strip()


def _ensure_qt_app() -> None:
    if QCoreApplication.instance() is None:
        QCoreApplication([])


def _write_latest(tmp_path: Path, *, seat_id: str, decision: str) -> None:
    import json
    import time

    payload = {
        "schema": "seat_surface_aoi.display_event.v1",
        "timestamp_ms": int(time.time() * 1000),
        "source": "python_detector",
        "sequence_id": 10,
        "trigger_id": 20,
        "seat_id": seat_id,
        "sku": "seat_a_black_leather",
        "recipe_id": "seat_a_black_leather_v1",
        "decision": decision,
        "quality_pass": True,
        "error_code": 0,
        "elapsed_ms": 50.0,
        "defect_count": 0,
        "defects": [],
        "message": "",
        "images": [],
        "overlays": [],
    }
    (tmp_path / "display_latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _refresh_and_check(view_model: MainViewModel, predicate: object) -> bool:
    view_model.refreshTriggerState()
    return bool(predicate())


def _wait_until(predicate: object, timeout_s: float = 2.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not met before timeout")
