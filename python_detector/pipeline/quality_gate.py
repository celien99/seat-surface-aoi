from __future__ import annotations

from dataclasses import dataclass, field

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob


@dataclass
class FrameQuality:
    camera_id: str
    light_id: str
    mean_gray: float
    saturation_ratio: float
    sharpness: float
    is_pass: bool
    messages: list[str] = field(default_factory=list)


@dataclass
class QualityReport:
    is_pass: bool
    frame_reports: list[FrameQuality]
    messages: list[str] = field(default_factory=list)


class ImageQualityGate:
    def check(self, job: SeatInspectionJob, recipe: Recipe) -> QualityReport:
        reports: list[FrameQuality] = []
        messages: list[str] = []
        for bundle in job.camera_bundles:
            reports.extend(self._check_camera_bundle(bundle, recipe, messages))
        is_pass = not messages and all(report.is_pass for report in reports)
        return QualityReport(is_pass=is_pass, frame_reports=reports, messages=messages)

    def _check_camera_bundle(
        self,
        bundle: CameraBundle,
        recipe: Recipe,
        messages: list[str],
    ) -> list[FrameQuality]:
        reports: list[FrameQuality] = []
        for light_id in recipe.quality.required_lights:
            if light_id not in bundle.light_frames:
                messages.append(f"{bundle.camera_id}: missing required light {light_id}")
        for frame in bundle.light_frames.values():
            reports.append(self._check_frame(frame, recipe))
        return reports

    def _check_frame(self, frame: LightFrame, recipe: Recipe) -> FrameQuality:
        values = frame.image
        if frame.dtype != "UINT8" or frame.bit_depth != 8:
            return FrameQuality(
                frame.camera_id,
                frame.light_id,
                0.0,
                0.0,
                0.0,
                False,
                [f"unsupported dtype/bit depth: {frame.dtype}/{frame.bit_depth}"],
            )
        if frame.width <= 0 or frame.height <= 0 or frame.channels <= 0:
            return FrameQuality(frame.camera_id, frame.light_id, 0.0, 0.0, 0.0, False, ["invalid image shape"])
        expected_min = frame.stride_bytes * frame.height
        if len(values) < expected_min:
            return FrameQuality(frame.camera_id, frame.light_id, 0.0, 0.0, 0.0, False, ["image shorter than stride"])

        sample = bytes(values[:expected_min])
        mean_gray = sum(sample) / len(sample)
        saturation_ratio = sum(1 for value in sample if value >= 250) / len(sample)
        sharpness = self._sharpness(sample, frame.width, frame.height, frame.stride_bytes)
        messages: list[str] = []
        if saturation_ratio > recipe.quality.max_saturation_ratio:
            messages.append("overexposure saturation ratio exceeded")
        if mean_gray < recipe.quality.min_mean_gray:
            messages.append("underexposure mean gray below threshold")
        if mean_gray > recipe.quality.max_mean_gray:
            messages.append("overexposure mean gray above threshold")
        if sharpness < recipe.quality.min_sharpness:
            messages.append("sharpness below threshold")
        return FrameQuality(
            camera_id=frame.camera_id,
            light_id=frame.light_id,
            mean_gray=mean_gray,
            saturation_ratio=saturation_ratio,
            sharpness=sharpness,
            is_pass=not messages,
            messages=messages,
        )

    def _sharpness(self, data: bytes, width: int, height: int, stride: int) -> float:
        if width < 3 or height < 3:
            return 0.0
        total = 0
        count = 0
        for y in range(1, height - 1):
            row = y * stride
            for x in range(1, width - 1):
                center = data[row + x]
                lap = (
                    int(data[row - stride + x])
                    + int(data[row + stride + x])
                    + int(data[row + x - 1])
                    + int(data[row + x + 1])
                    - 4 * int(center)
                )
                total += abs(lap)
                count += 1
        return total / max(count, 1)

