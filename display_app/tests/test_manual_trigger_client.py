from __future__ import annotations

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


def test_main_view_model_manual_trigger_defaults_to_read_only(tmp_path: Path) -> None:
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()))

    view_model.manualTrigger()

    assert view_model.triggerEnabled is False
    assert "未启用" in view_model.triggerError


def test_main_view_model_submits_manual_trigger_and_persists_action(tmp_path: Path) -> None:
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

    view_model.submitManualTrigger("SN-100")
    assert server.done.wait(2.0)
    _wait_until(lambda: not view_model.manualTriggerPending)

    assert view_model.triggerEnabled is True
    assert view_model.triggerError == ""
    assert server.lines == ["start\n", "sn SN-100\n"]
    journal_text = (tmp_path / "display_operator_events.jsonl").read_text(encoding="utf-8")
    assert "manual_trigger" in journal_text
    assert "SN-100" in journal_text


class _StartSnServer:
    def __init__(self) -> None:
        self._ready = threading.Event()
        self.done = threading.Event()
        self.lines: list[str] = []
        self.port = 0
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        assert self._ready.wait(2.0)

    def _run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            self.port = int(server.getsockname()[1])
            self._ready.set()
            conn, _addr = server.accept()
            with conn:
                self.lines.append(_read_line(conn))
                conn.sendall(b"start_ack\n")
                self.lines.append(_read_line(conn))
                conn.sendall(b"sn_ack\n")
        self.done.set()


def _read_line(conn: socket.socket) -> str:
    data = b""
    while not data.endswith(b"\n"):
        chunk = conn.recv(1)
        if not chunk:
            break
        data += chunk
    return data.decode("utf-8")


def _ensure_qt_app() -> None:
    if QCoreApplication.instance() is None:
        QCoreApplication([])


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
