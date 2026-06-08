from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LightFrame:
    camera_id: str
    light_id: str
    frame_index: int
    light_seq_index: int
    width: int
    height: int
    channels: int
    stride_bytes: int
    pixel_format: str
    bit_depth: int
    color_order: str
    dtype: str
    timestamp_us: int
    exposure_us: int
    gain: float
    calibration_id: str
    image_crc32: int
    image: memoryview


@dataclass
class CameraBundle:
    camera_id: str
    pose_id: str
    light_frames: dict[str, LightFrame]


@dataclass
class SeatInspectionJob:
    sequence_id: int
    trigger_id: int
    seat_id: str
    recipe_id: str
    sku: str
    camera_bundles: list[CameraBundle]


@dataclass
class DefectResult:
    defect_id: str
    class_name: str
    severity: str
    camera_id: str
    roi_name: str
    bbox_xyxy_pixel: tuple[int, int, int, int]
    score: float
    area_px: int
    evidence_lights: list[str]
    mask_offset: int | None
    decision: str


@dataclass
class InspectionResult:
    sequence_id: int
    trigger_id: int
    seat_id: str
    decision: str
    defects: list[DefectResult] = field(default_factory=list)
    quality_pass: bool = False
    error_code: int = 0
    elapsed_ms: float = 0.0

