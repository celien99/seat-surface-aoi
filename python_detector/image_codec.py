from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class ImageCodecError(ValueError):
    """图像编解码失败。"""


@dataclass(frozen=True)
class RasterImage:
    width: int
    height: int
    channels: int
    pixels: bytes


def load_raster_image(path: str | Path) -> RasterImage:
    image_path = Path(path)
    data = image_path.read_bytes()
    if data[:8] == PNG_SIGNATURE:
        return _load_png(data, image_path)
    return _load_netpbm(data, image_path)


def load_gray_image(path: str | Path) -> RasterImage:
    image = load_raster_image(path)
    if image.channels != 1:
        raise ImageCodecError(f"仅支持灰度图像: {path}")
    return image


def write_gray_png(path: str | Path, width: int, height: int, pixels: bytes) -> None:
    _write_png(Path(path), width, height, 1, pixels)


def write_rgb_png(path: str | Path, width: int, height: int, pixels: bytes) -> None:
    _write_png(Path(path), width, height, 3, pixels)


def _load_png(data: bytes, path: Path) -> RasterImage:
    offset = len(PNG_SIGNATURE)
    width = 0
    height = 0
    bit_depth = -1
    color_type = -1
    interlace = -1
    compressed = bytearray()
    while offset < len(data):
        if offset + 8 > len(data):
            raise ImageCodecError(f"PNG chunk 截断: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + length
        if chunk_data_end + 4 > len(data):
            raise ImageCodecError(f"PNG chunk 数据截断: {path}")
        chunk_data = data[chunk_data_start:chunk_data_end]
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
                ">IIBBBBB",
                chunk_data,
            )
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
        offset = chunk_data_end + 4

    if width <= 0 or height <= 0:
        raise ImageCodecError(f"PNG 缺少有效 IHDR: {path}")
    if bit_depth != 8 or interlace != 0:
        raise ImageCodecError(f"仅支持 8bit 非隔行 PNG: {path}")
    channels = {0: 1, 2: 3}.get(color_type)
    if channels is None:
        raise ImageCodecError(f"仅支持灰度或 RGB PNG: {path}")
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise ImageCodecError(f"PNG 解压失败: {path}: {exc}") from exc
    return RasterImage(
        width=width,
        height=height,
        channels=channels,
        pixels=_unfilter_png(raw, width, height, channels, path),
    )


def _load_netpbm(data: bytes, path: Path) -> RasterImage:
    tokens, data_offset = _read_netpbm_header(data, path)
    if len(tokens) != 4:
        raise ImageCodecError(f"Netpbm header 无效: {path}")
    magic, width_raw, height_raw, max_value_raw = tokens
    if magic not in {b"P5", b"P6"}:
        raise ImageCodecError(f"仅支持二进制 PGM/PPM: {path}")
    try:
        width = int(width_raw)
        height = int(height_raw)
        max_value = int(max_value_raw)
    except ValueError as exc:
        raise ImageCodecError(f"Netpbm header 数字无效: {path}") from exc
    if width <= 0 or height <= 0:
        raise ImageCodecError(f"Netpbm 尺寸无效: {path}")
    if max_value != 255:
        raise ImageCodecError(f"仅支持 8bit Netpbm maxval=255: {path}")
    channels = 1 if magic == b"P5" else 3
    expected = width * height * channels
    pixels = data[data_offset:]
    if len(pixels) != expected:
        raise ImageCodecError(f"Netpbm 图像长度不匹配: {path}: {len(pixels)} != {expected}")
    return RasterImage(width=width, height=height, channels=channels, pixels=pixels)


def _unfilter_png(raw: bytes, width: int, height: int, channels: int, path: Path) -> bytes:
    stride = width * channels
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise ImageCodecError(f"PNG 解压长度不匹配: {path}: {len(raw)} != {expected}")
    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(stride)
    for _row_index in range(height):
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                current[index] = (current[index] + left) & 0xFF
        elif filter_type == 2:
            for index in range(stride):
                current[index] = (current[index] + previous[index]) & 0xFF
        elif filter_type == 3:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                up = previous[index]
                current[index] = (current[index] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for index in range(stride):
                left = current[index - channels] if index >= channels else 0
                up = previous[index]
                upper_left = previous[index - channels] if index >= channels else 0
                current[index] = (current[index] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise ImageCodecError(f"PNG filter 不支持: {path}: {filter_type}")
        rows.append(current)
        previous = current
    return b"".join(rows)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    pa = abs(estimate - left)
    pb = abs(estimate - up)
    pc = abs(estimate - upper_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return upper_left


def _write_png(path: Path, width: int, height: int, channels: int, pixels: bytes) -> None:
    if width <= 0 or height <= 0:
        raise ImageCodecError(f"PNG 尺寸无效: {path}")
    if channels not in {1, 3}:
        raise ImageCodecError(f"PNG 通道数无效: {path}: {channels}")
    expected = width * height * channels
    if len(pixels) != expected:
        raise ImageCodecError(f"PNG 像素长度不匹配: {path}: {len(pixels)} != {expected}")
    stride = width * channels
    raw_rows = bytearray()
    for row in range(height):
        start = row * stride
        raw_rows.append(0)
        raw_rows.extend(pixels[start:start + stride])
    color_type = 0 if channels == 1 else 2
    payload = zlib.compress(bytes(raw_rows))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + _png_chunk(b"IDAT", payload)
        + _png_chunk(b"IEND", b"")
    )


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def _read_netpbm_header(data: bytes, path: Path) -> tuple[list[bytes], int]:
    tokens: list[bytes] = []
    index = 0
    length = len(data)
    while len(tokens) < 4:
        while index < length and data[index] in b" \t\r\n":
            index += 1
        if index >= length:
            raise ImageCodecError(f"Netpbm header 截断: {path}")
        if data[index] == ord("#"):
            while index < length and data[index] not in b"\r\n":
                index += 1
            continue
        start = index
        while index < length and data[index] not in b" \t\r\n":
            index += 1
        tokens.append(data[start:index])
    if index >= length or data[index] not in b" \t\r\n":
        raise ImageCodecError(f"Netpbm header 缺少像素分隔符: {path}")
    while index < length and data[index] in b" \t\r\n":
        index += 1
        break
    return tokens, index
