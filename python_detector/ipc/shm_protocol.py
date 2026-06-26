from __future__ import annotations

import binascii
import ctypes
import enum
import struct
from dataclasses import dataclass
from typing import ClassVar

SHM_PROTOCOL_MAGIC = 0x53414F49
SHM_PROTOCOL_VERSION = 2
DEFAULT_SLOT_COUNT = 4
DEFAULT_FRAME_SLOT_SIZE = 16 * 1024 * 1024
DEFAULT_RESULT_SLOT_SIZE = 64 * 1024
FRAME_SHM_NAME = "/seat_aoi_cpp_to_py_frames_v1"
RESULT_SHM_NAME = "/seat_aoi_py_to_cpp_results_v1"

STRING_ID_SIZE = 64
MAX_FRAMES_PER_JOB = 64
MAX_DEFECTS_PER_RESULT = 32
MAX_EVIDENCE_LIGHTS = 8


class SlotState(enum.IntEnum):
    EMPTY = 0
    WRITING = 1
    READY = 2
    READING = 3
    CORRUPTED = 4
    TIMEOUT = 5


class PixelFormat(enum.IntEnum):
    MONO8 = 1
    MONO10 = 2
    MONO12 = 3
    MONO16 = 4
    BAYER_RG8 = 10
    BAYER_RG12 = 11
    BGR8 = 20
    RGB8 = 21


class ColorOrder(enum.IntEnum):
    MONO = 1
    BGR = 2
    RGB = 3
    BAYER_RG = 4
    BAYER_GB = 5
    BAYER_GR = 6
    BAYER_BG = 7


class DTypeCode(enum.IntEnum):
    UINT8 = 1
    UINT16 = 2
    FLOAT32 = 3


class InspectionDecision(enum.IntEnum):
    OK = 1
    NG = 2
    RECHECK = 3
    ERROR = 4


class ErrorCode(enum.IntEnum):
    NONE = 0
    PROTOCOL_MISMATCH = 1
    INVALID_PAYLOAD = 2
    CRC_MISMATCH = 3
    SLOT_UNAVAILABLE = 4
    DETECTOR_TIMEOUT = 5
    MISSING_FRAME = 6
    QUALITY_FAILED = 7
    DEVICE_FAULT = 8
    INTERNAL_ERROR = 9
    LIGHT_FAULT = 10
    CAMERA_FAULT = 11
    TRIGGER_SYNC_FAULT = 12
    CONFIGURATION_ERROR = 13
    ROBOT_FAULT = 14


def crc32(data: bytes | bytearray | memoryview) -> int:
    return binascii.crc32(data) & 0xFFFFFFFF


def decode_cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace")


def encode_cstr(value: str, size: int = STRING_ID_SIZE) -> bytes:
    encoded = value.encode("utf-8")[: size - 1]
    return encoded + b"\0" * (size - len(encoded))


@dataclass(frozen=True)
class StructSpec:
    fmt: str
    size: int
    compiled: struct.Struct

    @classmethod
    def from_format(cls, fmt: str) -> "StructSpec":
        compiled = struct.Struct(fmt)
        return cls(fmt=fmt, size=compiled.size, compiled=compiled)

    def unpack_from(self, buffer, offset: int = 0):
        return self.compiled.unpack_from(buffer, offset)

    def pack(self, *values):
        return self.compiled.pack(*values)

    def pack_into(self, buffer, offset: int, *values) -> None:
        self.compiled.pack_into(buffer, offset, *values)


SHM_HEADER = StructSpec.from_format("<IIIIQQQ")
LIGHT_FRAME_META = StructSpec.from_format("<IIIIIIIIIIIIIQQQIf3f3f64s64s64sQQII")
SEAT_JOB_META = StructSpec.from_format("<QQ64s64s64sIIIIQ")
DEFECT_RESULT_META = StructSpec.from_format("<64s64s64sI64s64s64s4ifII8iqII")
INSPECTION_RESULT_META = StructSpec.from_format("<QQ64sIIIIfI")
FRAME_SLOT_HEADER_PREFIX = StructSpec.from_format("<IQQIIII")
RESULT_SLOT_HEADER_PREFIX = StructSpec.from_format("<IQQIIII")

FRAME_SLOT_HEADER_SIZE = FRAME_SLOT_HEADER_PREFIX.size + SEAT_JOB_META.size
RESULT_SLOT_HEADER_SIZE = RESULT_SLOT_HEADER_PREFIX.size + INSPECTION_RESULT_META.size

EXPECTED_SIZES = {
    "ShmHeader": 40,
    "FrameSlotHeader": 268,
    "ResultSlotHeader": 140,
    "LightFrameMeta": 324,
    "SeatJobMeta": 232,
    "InspectionResultMeta": 104,
    "DefectResultMeta": 464,
}


def assert_protocol_layout() -> None:
    actual = protocol_sizes()
    mismatches = {
        name: (actual[name], expected)
        for name, expected in EXPECTED_SIZES.items()
        if actual[name] != expected
    }
    if mismatches:
        raise AssertionError(f"protocol layout mismatch: {mismatches}")


def protocol_sizes() -> dict[str, int]:
    return {
        "ShmHeader": SHM_HEADER.size,
        "FrameSlotHeader": FRAME_SLOT_HEADER_SIZE,
        "ResultSlotHeader": RESULT_SLOT_HEADER_SIZE,
        "LightFrameMeta": LIGHT_FRAME_META.size,
        "SeatJobMeta": SEAT_JOB_META.size,
        "InspectionResultMeta": INSPECTION_RESULT_META.size,
        "DefectResultMeta": DEFECT_RESULT_META.size,
    }


def frame_slot_meta_offset() -> int:
    return FRAME_SLOT_HEADER_SIZE


def frame_slot_image_offset(frame_meta_count: int) -> int:
    return FRAME_SLOT_HEADER_SIZE + LIGHT_FRAME_META.size * frame_meta_count


def result_slot_defects_offset() -> int:
    return RESULT_SLOT_HEADER_SIZE


class AtomicU32:
    """用于在 mmap 共享内存中读写 slot 状态的小型辅助类。

    跨平台内存排序警告：当前 store 使用 struct.pack_into 实现，不提供
    CPU 级写屏障（memory_order_release）。在 x86/x86_64 TSO 架构上，硬件保证
    store-store 不重排，因此 C++ 侧的 memory_order_acquire 读取能正确观察到
    先写入的数据。如果将来部署到 ARM 等弱序架构，必须替换为 C 扩展或调用
    ``ctypes.CDLL('libc.so.6').sync_synchronize()`` 等系统屏障原语。
    """

    _ctype: ClassVar[type[ctypes.c_uint32]] = ctypes.c_uint32

    @staticmethod
    def load(buf: memoryview, offset: int) -> int:
        return struct.unpack_from("<I", buf, offset)[0]

    @staticmethod
    def store(buf: memoryview, offset: int, value: int) -> None:
        struct.pack_into("<I", buf, offset, int(value))

    @staticmethod
    def fence() -> None:
        """写屏障桩：在 x86/x86_64 上为 no-op（硬件 TSO 保证顺序）。
        ARM/弱序架构部署前需替换为真实 CPU 屏障指令。"""
        pass
