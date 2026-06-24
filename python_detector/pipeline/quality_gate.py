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
    dark_ratio: float
    sharpness: float
    motion_gradient: float
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
        if job.sku != recipe.sku:
            messages.append(f"sku mismatch: job={job.sku} recipe={recipe.sku}")

        required_views = recipe.required_view_keys()
        seen_views: set[tuple[str, str]] = set()
        for bundle in job.camera_bundles:
            pose_id = bundle.pose_id or bundle.camera_id
            view_key = (bundle.camera_id, pose_id)
            if view_key in seen_views:
                messages.append(f"{bundle.camera_id}/{pose_id}: duplicate camera pose bundle")
            seen_views.add(view_key)
            if not recipe.accepts_camera_pose(bundle.camera_id, pose_id):
                messages.append(recipe.view_not_enabled_message(bundle.camera_id, pose_id))
            reports.extend(self._check_camera_bundle(bundle, recipe, messages))
        for camera_id, pose_id in sorted(required_views):
            if (camera_id, pose_id) in seen_views:
                continue
            if pose_id == camera_id and any(seen_camera_id == camera_id for seen_camera_id, _ in seen_views):
                continue
            messages.append(recipe.missing_view_message(camera_id, pose_id))
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
        self._check_capture_consistency(bundle, recipe, messages)
        for light_id, frame in bundle.light_frames.items():
            if frame.camera_id != bundle.camera_id:
                messages.append(f"{bundle.camera_id}/{light_id}: frame camera_id mismatch {frame.camera_id}")
            if frame.light_id != light_id:
                messages.append(f"{bundle.camera_id}/{light_id}: frame light_id mismatch {frame.light_id}")
            reports.append(self._check_frame(frame, recipe))
        self._check_light_stability(bundle, recipe, reports, messages)
        return reports

    def _check_capture_consistency(self, bundle: CameraBundle, recipe: Recipe, messages: list[str]) -> None:
        self._check_light_sequence(bundle, recipe, messages)
        frames = [
            bundle.light_frames[light_id]
            for light_id in recipe.quality.required_lights
            if light_id in bundle.light_frames
        ]
        if len(frames) < 2:
            return

        timestamps = [frame.timestamp_us for frame in frames]
        if any(timestamp <= 0 for timestamp in timestamps):
            messages.append(f"{bundle.camera_id}: invalid frame timestamp")
        capture_span_us = max(timestamps) - min(timestamps)
        if capture_span_us > recipe.quality.max_capture_span_us:
            messages.append(
                f"{bundle.camera_id}: capture timestamp span {capture_span_us}us exceeds {recipe.quality.max_capture_span_us}us"
            )
        if recipe.quality.require_monotonic_timestamps:
            for earlier, later in zip(timestamps, timestamps[1:]):
                if later < earlier:
                    messages.append(f"{bundle.camera_id}: timestamps are not monotonic by required light order")
                    break

        frame_indices = [frame.frame_index for frame in frames]
        if recipe.quality.require_unique_frame_indices and len(set(frame_indices)) != len(frame_indices):
            messages.append(f"{bundle.camera_id}: duplicate frame_index in required lights")

        light_seq_indices = [frame.light_seq_index for frame in frames]
        if len(set(light_seq_indices)) != len(light_seq_indices):
            messages.append(f"{bundle.camera_id}: duplicate light_seq_index in required lights")
        self._check_robot_pose_consistency(bundle, frames, messages)
        self._check_required_light_capture_params(bundle, frames, recipe, messages)

    def _check_light_sequence(self, bundle: CameraBundle, recipe: Recipe, messages: list[str]) -> None:
        light_order = recipe.light_order
        light_seq_by_id = {light_id: index for index, light_id in enumerate(light_order)}
        seen_indices: set[int] = set()
        for frame in bundle.light_frames.values():
            expected_seq_index = light_seq_by_id.get(frame.light_id)
            if expected_seq_index is None:
                messages.append(f"{bundle.camera_id}/{frame.light_id}: light not in configured light_order")
                continue
            if frame.light_seq_index != expected_seq_index:
                messages.append(
                    f"{bundle.camera_id}/{frame.light_id}: light_seq_index {frame.light_seq_index} "
                    f"does not match configured order {expected_seq_index}"
                )
            if frame.light_seq_index in seen_indices:
                messages.append(f"{bundle.camera_id}: duplicate light_seq_index in captured lights")
            seen_indices.add(frame.light_seq_index)

    def _check_robot_pose_consistency(
        self,
        bundle: CameraBundle,
        frames: list[LightFrame],
        messages: list[str],
    ) -> None:
        label = f"{bundle.camera_id}/{bundle.pose_id}" if bundle.pose_id else bundle.camera_id
        shot_ids = [frame.shot_id for frame in frames]
        non_zero_shots = [shot_id for shot_id in shot_ids if shot_id != 0]
        if non_zero_shots:
            if len(non_zero_shots) != len(shot_ids):
                messages.append(f"{label}: mixed empty and non-empty shot_id in required lights")
            elif len(set(shot_ids)) != 1:
                messages.append(f"{label}: inconsistent shot_id in required lights")

        robot_timestamps = [frame.robot_timestamp_us for frame in frames]
        non_zero_robot_timestamps = [timestamp for timestamp in robot_timestamps if timestamp != 0]
        has_robot_pose = any(
            any(abs(value) > 1e-6 for value in frame.robot_tcp_xyz_mm + frame.robot_rpy_deg)
            for frame in frames
        )
        if non_zero_robot_timestamps or has_robot_pose:
            if len(non_zero_robot_timestamps) != len(robot_timestamps):
                messages.append(f"{label}: mixed empty and non-empty robot_timestamp_us in required lights")
            elif len(set(robot_timestamps)) != 1:
                messages.append(f"{label}: inconsistent robot_timestamp_us in required lights")
            reference_xyz = frames[0].robot_tcp_xyz_mm
            reference_rpy = frames[0].robot_rpy_deg
            for frame in frames[1:]:
                if not self._float_tuple_close(reference_xyz, frame.robot_tcp_xyz_mm):
                    messages.append(f"{label}: inconsistent robot_tcp_xyz_mm in required lights")
                    break
            for frame in frames[1:]:
                if not self._float_tuple_close(reference_rpy, frame.robot_rpy_deg):
                    messages.append(f"{label}: inconsistent robot_rpy_deg in required lights")
                    break

    def _check_required_light_capture_params(
        self,
        bundle: CameraBundle,
        frames: list[LightFrame],
        recipe: Recipe,
        messages: list[str],
    ) -> None:
        exposures = [frame.exposure_us for frame in frames]
        if any(exposure <= 0 for exposure in exposures):
            messages.append(f"{bundle.camera_id}: invalid exposure_us")
        exposure_delta_us = max(exposures) - min(exposures)
        if exposure_delta_us > recipe.quality.max_exposure_delta_us:
            messages.append(
                f"{bundle.camera_id}: exposure delta {exposure_delta_us}us exceeds {recipe.quality.max_exposure_delta_us}us"
            )

        gains = [frame.gain for frame in frames]
        if any(gain <= 0 for gain in gains):
            messages.append(f"{bundle.camera_id}: invalid gain")
        gain_delta = max(gains) - min(gains)
        if gain_delta > recipe.quality.max_gain_delta:
            messages.append(f"{bundle.camera_id}: gain delta {gain_delta:.3f} exceeds {recipe.quality.max_gain_delta:.3f}")

    def _float_tuple_close(self, left: tuple[float, ...], right: tuple[float, ...]) -> bool:
        return len(left) == len(right) and all(abs(a - b) <= 1e-4 for a, b in zip(left, right))

    def _check_frame(self, frame: LightFrame, recipe: Recipe) -> FrameQuality:
        values = frame.image
        meta_messages = self._frame_meta_messages(frame)
        if meta_messages:
            return FrameQuality(
                frame.camera_id,
                frame.light_id,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                False,
                meta_messages,
            )
        expected_min = frame.stride_bytes * frame.height
        if len(values) < expected_min:
            return FrameQuality(frame.camera_id, frame.light_id, 0.0, 0.0, 0.0, 0.0, 0.0, False, ["image shorter than stride"])

        sample = self._active_pixel_bytes(frame)
        mean_gray = sum(sample) / len(sample)
        saturation_ratio = sum(1 for value in sample if value >= 250) / len(sample)
        dark_ratio = sum(1 for value in sample if value <= 5) / len(sample)
        sharpness = self._sharpness(sample, frame.width, frame.height)
        motion_gradient = self._motion_gradient(sample, frame.width, frame.height)
        messages: list[str] = []
        if saturation_ratio > recipe.quality.max_saturation_ratio:
            messages.append("overexposure saturation ratio exceeded")
        if dark_ratio > recipe.quality.max_dark_ratio:
            messages.append("underexposure dark ratio exceeded")
        if mean_gray < recipe.quality.min_mean_gray:
            messages.append("underexposure mean gray below threshold")
        if mean_gray > recipe.quality.max_mean_gray:
            messages.append("overexposure mean gray above threshold")
        if sharpness < recipe.quality.min_sharpness:
            messages.append("sharpness below threshold")
        if motion_gradient < recipe.quality.min_motion_gradient:
            messages.append("motion blur gradient below threshold")
        return FrameQuality(
            camera_id=frame.camera_id,
            light_id=frame.light_id,
            mean_gray=mean_gray,
            saturation_ratio=saturation_ratio,
            dark_ratio=dark_ratio,
            sharpness=sharpness,
            motion_gradient=motion_gradient,
            is_pass=not messages,
            messages=messages,
        )

    def _check_light_stability(
        self,
        bundle: CameraBundle,
        recipe: Recipe,
        reports: list[FrameQuality],
        messages: list[str],
    ) -> None:
        report_by_light = {report.light_id: report for report in reports if report.camera_id == bundle.camera_id}
        required_reports = [
            report_by_light[light_id]
            for light_id in recipe.quality.required_lights
            if light_id in report_by_light and report_by_light[light_id].is_pass
        ]
        if len(required_reports) < 2:
            return
        means = [report.mean_gray for report in required_reports]
        mean_delta = max(means) - min(means)
        if mean_delta > recipe.quality.max_light_mean_delta:
            messages.append(
                f"{bundle.camera_id}: required light mean delta {mean_delta:.2f} exceeds {recipe.quality.max_light_mean_delta:.2f}"
            )

    def _frame_meta_messages(self, frame: LightFrame) -> list[str]:
        messages: list[str] = []
        if frame.pixel_format != "MONO8":
            messages.append(f"unsupported pixel_format: {frame.pixel_format}")
        if frame.color_order != "MONO":
            messages.append(f"unsupported color_order: {frame.color_order}")
        if frame.dtype != "UINT8" or frame.bit_depth != 8:
            messages.append(f"unsupported dtype/bit depth: {frame.dtype}/{frame.bit_depth}")
        if frame.width <= 0 or frame.height <= 0 or frame.channels <= 0:
            messages.append("invalid image shape")
            return messages
        if frame.channels != 1:
            messages.append(f"expected mono channel count 1, got {frame.channels}")
        row_width = frame.width * frame.channels
        if frame.stride_bytes < row_width:
            messages.append(f"stride smaller than active row width: {frame.stride_bytes} < {row_width}")
        return messages

    def _active_pixel_bytes(self, frame: LightFrame) -> bytes:
        if frame.stride_bytes == frame.width * frame.channels:
            return bytes(frame.image[: frame.width * frame.height * frame.channels])
        rows = bytearray()
        row_width = frame.width * frame.channels
        for row in range(frame.height):
            start = row * frame.stride_bytes
            rows.extend(frame.image[start : start + row_width])
        return bytes(rows)

    def _sharpness(self, data: bytes, width: int, height: int) -> float:
        if width < 3 or height < 3:
            return 0.0
        total = 0
        count = 0
        for y in range(1, height - 1):
            row = y * width
            for x in range(1, width - 1):
                center = data[row + x]
                lap = (
                    int(data[row - width + x])
                    + int(data[row + width + x])
                    + int(data[row + x - 1])
                    + int(data[row + x + 1])
                    - 4 * int(center)
                )
                total += abs(lap)
                count += 1
        return total / max(count, 1)

    def _motion_gradient(self, data: bytes, width: int, height: int) -> float:
        if width < 2 or height < 2:
            return 0.0
        horizontal_total = 0
        horizontal_count = 0
        for y in range(height):
            row = y * width
            for x in range(width - 1):
                horizontal_total += abs(int(data[row + x + 1]) - int(data[row + x]))
                horizontal_count += 1

        vertical_total = 0
        vertical_count = 0
        for y in range(height - 1):
            row = y * width
            next_row = row + width
            for x in range(width):
                vertical_total += abs(int(data[next_row + x]) - int(data[row + x]))
                vertical_count += 1

        horizontal_mean = horizontal_total / max(horizontal_count, 1)
        vertical_mean = vertical_total / max(vertical_count, 1)
        return min(horizontal_mean, vertical_mean)
