from __future__ import annotations

import re
import socket
import threading
import time
from dataclasses import dataclass


_SN_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class ManualTriggerError(RuntimeError):
    """Raised when the display-side manual trigger cannot be submitted."""


@dataclass(frozen=True, slots=True)
class ManualTriggerConfig:
    host: str = "127.0.0.1"
    port: int = 9000
    timeout_ms: int = 1000
    terminator: str = "\n"
    start_command: str = "start"
    sn_prefix: str = "sn"
    start_ack: str = "start_ack\n"
    sn_ack: str = "sn_ack\n"
    max_sn_length: int = 48


@dataclass(frozen=True, slots=True)
class ManualTriggerResult:
    sn: str
    host: str
    port: int
    elapsed_ms: float


class ManualTriggerClient:
    """Submit a UI-initiated trigger through the C++ tcp_signal start_sn protocol."""

    def __init__(self, config: ManualTriggerConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._sock: socket.socket | None = None
        self._rx_buffer = b""

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def trigger(self, sn: str) -> ManualTriggerResult:
        normalized_sn = _validate_sn(sn, self._config.max_sn_length)
        start = _command_bytes(self._config.start_command, self._config.terminator)
        sn_line = _command_bytes(f"{self._config.sn_prefix} {normalized_sn}", self._config.terminator)
        started_at = time.monotonic()
        timeout_s = max(0.1, self._config.timeout_ms / 1000.0)

        with self._lock:
            try:
                sock = self._ensure_socket_locked(timeout_s)
                self._drain_socket_locked(sock)
                sock.sendall(start)
                self._read_expected_locked(sock, self._config.start_ack, timeout_s, "到位确认")
                sock.sendall(sn_line)
                self._read_expected_locked(sock, self._config.sn_ack, timeout_s, "SN 确认")
            except OSError as exc:
                self._close_locked()
                raise ManualTriggerError(f"手动触发 TCP 通信失败: {exc}") from exc
            except ManualTriggerError:
                self._close_locked()
                raise

        return ManualTriggerResult(
            sn=normalized_sn,
            host=self._config.host,
            port=self._config.port,
            elapsed_ms=(time.monotonic() - started_at) * 1000.0,
        )

    def _ensure_socket_locked(self, timeout_s: float) -> socket.socket:
        if self._sock is not None:
            return self._sock
        try:
            sock = socket.create_connection((self._config.host, self._config.port), timeout=timeout_s)
        except OSError as exc:
            raise ManualTriggerError(
                f"无法连接 C++ 手动触发端口 {self._config.host}:{self._config.port}: {exc}"
            ) from exc
        sock.settimeout(timeout_s)
        self._sock = sock
        self._rx_buffer = b""
        return sock

    def _drain_socket_locked(self, sock: socket.socket) -> None:
        previous_timeout = sock.gettimeout()
        sock.setblocking(False)
        try:
            while True:
                try:
                    chunk = sock.recv(4096)
                except BlockingIOError:
                    break
                if not chunk:
                    self._close_locked()
                    break
        finally:
            if self._sock is sock:
                sock.settimeout(previous_timeout)
            self._rx_buffer = b""

    def _read_expected_locked(
        self,
        sock: socket.socket,
        expected_text: str,
        timeout_s: float,
        stage_name: str,
    ) -> None:
        expected_variants = _expected_variants(expected_text)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if any(expected in self._rx_buffer for expected in expected_variants):
                self._rx_buffer = b""
                return
            remaining = max(0.01, deadline - time.monotonic())
            sock.settimeout(remaining)
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                raise ManualTriggerError(f"C++ 手动触发连接在等待{stage_name}时断开")
            self._rx_buffer += chunk
        preview = self._rx_buffer.decode("utf-8", errors="replace")[:120]
        raise ManualTriggerError(f"等待 C++ {stage_name}超时，收到: {preview or '无响应'}")

    def _close_locked(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None
                self._rx_buffer = b""


def decode_control_text(value: str) -> str:
    return (
        value.replace("\\r", "\r")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\0", "\0")
    )


def _command_bytes(command: str, terminator: str) -> bytes:
    return f"{command}{terminator}".encode("utf-8")


def _expected_variants(expected_text: str) -> tuple[bytes, ...]:
    encoded = expected_text.encode("utf-8")
    literal_newline = expected_text.replace("\r", "\\r").replace("\n", "\\n").encode("utf-8")
    if literal_newline == encoded:
        return (encoded,)
    return (encoded, literal_newline)


def _validate_sn(sn: str, max_length: int) -> str:
    normalized = str(sn or "").strip()
    if not normalized:
        raise ManualTriggerError("SN 不能为空")
    if len(normalized) > max_length:
        raise ManualTriggerError(f"SN 长度不能超过 {max_length} 个字符")
    if "\r" in normalized or "\n" in normalized:
        raise ManualTriggerError("SN 不能包含换行符")
    if not _SN_PATTERN.fullmatch(normalized):
        raise ManualTriggerError("SN 只能包含字母、数字、横线、下划线或点")
    return normalized
