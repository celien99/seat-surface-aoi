"""共享内存 IPC 客户端与协议辅助模块。"""

from python_detector.ipc.data_types import CameraBundle, DefectResult, InspectionResult, LightFrame, SeatInspectionJob
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    ColorOrder,
    DTypeCode,
    ErrorCode,
    InspectionDecision,
    PixelFormat,
    SlotState,
    assert_protocol_layout,
    crc32,
    protocol_sizes,
)

__all__ = [
    "CameraBundle",
    "ColorOrder",
    "DTypeCode",
    "DefectResult",
    "ErrorCode",
    "InspectionDecision",
    "InspectionResult",
    "LightFrame",
    "PixelFormat",
    "SeatInspectionJob",
    "ShmClient",
    "SlotState",
    "assert_protocol_layout",
    "crc32",
    "protocol_sizes",
]
