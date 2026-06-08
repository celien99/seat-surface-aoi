from __future__ import annotations

from dataclasses import dataclass, field
import math


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
    origin_xy: tuple[int, int] = (0, 0)
    source_width: int | None = None
    source_height: int | None = None
    roi_to_source_matrix: tuple[float, ...] | None = None
    source_to_roi_matrix: tuple[float, ...] | None = None

    @property
    def bbox_xyxy_pixel(self) -> tuple[int, int, int, int]:
        if self.roi_to_source_matrix is not None:
            corners = (
                (0.0, 0.0),
                (float(self.width - 1), 0.0),
                (float(self.width - 1), float(self.height - 1)),
                (0.0, float(self.height - 1)),
            )
            mapped = [_apply_homography(self.roi_to_source_matrix, x, y) for x, y in corners]
            if any(point is None for point in mapped):
                raise ValueError(f"{self.camera_id}/{self.light_id}: ROI 坐标矩阵无效")
            xs = [point[0] for point in mapped if point is not None]
            ys = [point[1] for point in mapped if point is not None]
            x0 = math.floor(min(xs))
            y0 = math.floor(min(ys))
            x1 = math.ceil(max(xs))
            y1 = math.ceil(max(ys))
            if self.source_width is not None:
                x0 = max(0, min(self.source_width - 1, x0))
                x1 = max(0, min(self.source_width - 1, x1))
            if self.source_height is not None:
                y0 = max(0, min(self.source_height - 1, y0))
                y1 = max(0, min(self.source_height - 1, y1))
            return (x0, y0, x1, y1)
        x0, y0 = self.origin_xy
        return (x0, y0, x0 + self.width - 1, y0 + self.height - 1)


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


def _apply_homography(matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float] | None:
    denom = matrix[6] * x + matrix[7] * y + matrix[8]
    if abs(denom) < 1e-9:
        return None
    mapped_x = (matrix[0] * x + matrix[1] * y + matrix[2]) / denom
    mapped_y = (matrix[3] * x + matrix[4] * y + matrix[5]) / denom
    return mapped_x, mapped_y
