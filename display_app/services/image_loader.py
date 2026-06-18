from __future__ import annotations

from pathlib import Path

import numpy as np


class NetpbmImageError(ValueError):
    """Raised when a PGM/PPM trace image cannot be decoded."""


def load_netpbm_bgr(path: str | Path) -> np.ndarray:
    """Load binary P5 PGM or P6 PPM files written by TraceWriter as BGR."""

    image_path = Path(path)
    data = image_path.read_bytes()
    magic, width, height, max_value, offset = _parse_header(data, image_path)
    if max_value <= 0 or max_value > 255:
        raise NetpbmImageError(f"不支持的 Netpbm max value: {image_path} max={max_value}")

    channels = 1 if magic == b"P5" else 3
    expected = width * height * channels
    payload = data[offset : offset + expected]
    if len(payload) != expected:
        raise NetpbmImageError(f"Netpbm 图像数据长度不足: {image_path}")

    array = np.frombuffer(payload, dtype=np.uint8)
    if channels == 1:
        gray = array.reshape((height, width))
        return np.repeat(gray[:, :, None], 3, axis=2).copy()

    rgb = array.reshape((height, width, 3))
    return rgb[:, :, ::-1].copy()


def _parse_header(data: bytes, path: Path) -> tuple[bytes, int, int, int, int]:
    tokens: list[bytes] = []
    index = 0
    length = len(data)
    while len(tokens) < 4:
        while index < length and data[index] in b" \t\r\n":
            index += 1
        if index >= length:
            raise NetpbmImageError(f"Netpbm header 不完整: {path}")
        if data[index] == ord("#"):
            while index < length and data[index] not in b"\r\n":
                index += 1
            continue
        start = index
        while index < length and data[index] not in b" \t\r\n":
            index += 1
        tokens.append(data[start:index])

    if index >= length or data[index] not in b" \t\r\n":
        raise NetpbmImageError(f"Netpbm header 后缺少数据分隔符: {path}")
    while index < length and data[index] in b" \t\r\n":
        index += 1
        break

    magic = tokens[0]
    if magic not in {b"P5", b"P6"}:
        raise NetpbmImageError(f"不支持的 Netpbm 格式: {path} magic={magic!r}")
    try:
        width = int(tokens[1])
        height = int(tokens[2])
        max_value = int(tokens[3])
    except ValueError as exc:
        raise NetpbmImageError(f"Netpbm header 数值非法: {path}") from exc
    if width <= 0 or height <= 0:
        raise NetpbmImageError(f"Netpbm 尺寸非法: {path} {width}x{height}")
    return magic, width, height, max_value, index
