from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.calibration_manager import Calibration, CalibrationManager, RoiTemplate
from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob
from python_detector.pipeline.roi_locator import RoiLocationReport, RoiLocator


class PreprocessRecheckError(ValueError):
    """预处理阶段发现可复检的不确定状态。"""


@dataclass
class PreparedBundle:
    camera_id: str
    calibration: Calibration
    rois: dict[str, dict[str, LightFrame]]
    roi_templates: dict[str, RoiTemplate]
    roi_location_report: RoiLocationReport | None = None
    pose_id: str = ""


class Preprocessor:
    def __init__(
        self,
        calibration_manager: CalibrationManager | None = None,
        roi_locator: RoiLocator | None = None,
    ) -> None:
        self.calibration_manager = calibration_manager or CalibrationManager()
        self.roi_locator = roi_locator or RoiLocator()

    def run(self, job: SeatInspectionJob, recipe: Recipe) -> list[PreparedBundle]:
        prepared: list[PreparedBundle] = []
        for bundle in job.camera_bundles:
            camera_recipe = recipe.camera(bundle.camera_id, bundle.pose_id)
            if camera_recipe is None:
                raise ValueError(f"{bundle.camera_id}: 配方未启用该机位")
            calibration = self.calibration_manager.load(
                bundle.camera_id,
                camera_recipe.calibration_id,
                camera_recipe.roi_template,
            )
            decoded = self._decode_frames(bundle, recipe)
            self._assert_calibration_matches(decoded, calibration)
            roi_templates, roi_report = self.roi_locator.locate(
                bundle.camera_id,
                decoded,
                calibration.roi_templates,
                recipe,
            )
            if not roi_report.is_pass:
                raise PreprocessRecheckError(f"{bundle.camera_id}: ROI 定位失败: {roi_report.message}")
            rois = {
                roi_name: {
                    light_id: self._crop_to_roi(frame, roi)
                    for light_id, frame in decoded.items()
                }
                for roi_name, roi in roi_templates.items()
            }
            prepared.append(
                PreparedBundle(
                    camera_id=bundle.camera_id,
                    pose_id=bundle.pose_id,
                    calibration=calibration,
                    rois=rois,
                    roi_templates=roi_templates,
                    roi_location_report=roi_report,
                )
            )
        return prepared

    def _decode_frames(self, bundle: CameraBundle, recipe: Recipe) -> dict[str, LightFrame]:
        decoded: dict[str, LightFrame] = {}
        for light_id, frame in bundle.light_frames.items():
            self._assert_supported(frame)
            decoded[light_id] = frame
        return decoded

    def _assert_supported(self, frame: LightFrame) -> None:
        if frame.pixel_format != "MONO8":
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: unsupported pixel_format {frame.pixel_format}")
        if frame.dtype != "UINT8":
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: unsupported dtype {frame.dtype}")
        if frame.channels != 1:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: expected mono image")
        if frame.stride_bytes < frame.width * frame.channels:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: stride smaller than row size")
        if len(frame.image) < frame.stride_bytes * frame.height:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: image shorter than stride")

    def _assert_calibration_matches(self, frames: dict[str, LightFrame], calibration: Calibration) -> None:
        for frame in frames.values():
            if frame.calibration_id != calibration.calibration_id:
                raise ValueError(
                    f"{frame.camera_id}/{frame.light_id}: calibration_id 不一致 "
                    f"{frame.calibration_id} != {calibration.calibration_id}"
                )
            expected_width, expected_height = calibration.image_size
            if frame.width != expected_width or frame.height != expected_height:
                raise ValueError(
                    f"{frame.camera_id}/{frame.light_id}: 图像尺寸与标定不一致 "
                    f"{frame.width}x{frame.height} != {expected_width}x{expected_height}"
                )

    def _crop_to_roi(self, frame: LightFrame, roi: RoiTemplate) -> LightFrame:
        x_values = [point[0] for point in roi.polygon_xy]
        y_values = [point[1] for point in roi.polygon_xy]
        x0 = min(x_values)
        y0 = min(y_values)
        x1 = max(x_values)
        y1 = max(y_values)
        if x0 < 0 or y0 < 0 or x1 >= frame.width or y1 >= frame.height:
            raise ValueError(
                f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: ROI 坐标越界 "
                f"({x0},{y0})-({x1},{y1}) image={frame.width}x{frame.height}"
            )
        output_width, output_height = roi.output_size
        if output_width <= 0 or output_height <= 0:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: ROI output_size 无效")

        bbox_width = x1 - x0 + 1
        bbox_height = y1 - y0 + 1
        origin_x, origin_y = frame.origin_xy
        if self._is_axis_aligned_rectangle(roi) and roi.output_size == (bbox_width, bbox_height):
            cropped = self._crop_bbox(frame, x0, y0, bbox_width, bbox_height)
            roi_to_source_matrix = (
                1.0,
                0.0,
                float(origin_x + x0),
                0.0,
                1.0,
                float(origin_y + y0),
                0.0,
                0.0,
                1.0,
            )
            source_to_roi_matrix = (
                1.0,
                0.0,
                -float(origin_x + x0),
                0.0,
                1.0,
                -float(origin_y + y0),
                0.0,
                0.0,
                1.0,
            )
        elif len(roi.polygon_xy) == 4:
            cropped, roi_to_source_matrix, source_to_roi_matrix = self._warp_quad(frame, roi, output_width, output_height)
        else:
            raise ValueError(
                f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: 非矩形 ROI 必须提供 4 个点用于透视展开"
            )
        return LightFrame(
            camera_id=frame.camera_id,
            pose_id=frame.pose_id,
            light_id=frame.light_id,
            frame_index=frame.frame_index,
            light_seq_index=frame.light_seq_index,
            width=output_width,
            height=output_height,
            channels=frame.channels,
            stride_bytes=output_width,
            pixel_format=frame.pixel_format,
            bit_depth=frame.bit_depth,
            color_order=frame.color_order,
            dtype=frame.dtype,
            timestamp_us=frame.timestamp_us,
            shot_id=frame.shot_id,
            robot_timestamp_us=frame.robot_timestamp_us,
            robot_tcp_xyz_mm=frame.robot_tcp_xyz_mm,
            robot_rpy_deg=frame.robot_rpy_deg,
            exposure_us=frame.exposure_us,
            gain=frame.gain,
            calibration_id=frame.calibration_id,
            image_crc32=frame.image_crc32,
            image=memoryview(cropped),
            origin_xy=(origin_x + x0, origin_y + y0),
            source_width=frame.source_width or frame.width,
            source_height=frame.source_height or frame.height,
            roi_to_source_matrix=roi_to_source_matrix,
            source_to_roi_matrix=source_to_roi_matrix,
        )

    def _crop_bbox(self, frame: LightFrame, x0: int, y0: int, width: int, height: int) -> bytearray:
        cropped = bytearray(width * height)
        for row in range(height):
            source_start = (y0 + row) * frame.stride_bytes + x0
            source_end = source_start + width
            target_start = row * width
            cropped[target_start : target_start + width] = frame.image[source_start:source_end]
        return cropped

    def _warp_quad(
        self,
        frame: LightFrame,
        roi: RoiTemplate,
        output_width: int,
        output_height: int,
    ) -> tuple[bytearray, tuple[float, ...], tuple[float, ...]]:
        source_points = self._ordered_quad(roi.polygon_xy)
        destination_points = (
            (0.0, 0.0),
            (float(output_width - 1), 0.0),
            (float(output_width - 1), float(output_height - 1)),
            (0.0, float(output_height - 1)),
        )
        transform = self._homography(destination_points, source_points)
        origin_x, origin_y = frame.origin_xy
        source_points_global = tuple((x + origin_x, y + origin_y) for x, y in source_points)
        roi_to_source_matrix = self._translate_homography(transform, float(origin_x), float(origin_y))
        source_to_roi_matrix = self._homography(source_points_global, destination_points)
        warped = bytearray(output_width * output_height)
        for y in range(output_height):
            for x in range(output_width):
                source = self._apply_homography(transform, float(x), float(y))
                if source is None:
                    raise ValueError(f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: ROI 透视变换无效")
                sx, sy = source
                warped[y * output_width + x] = self._sample_bilinear(frame, sx, sy)
        return warped, roi_to_source_matrix, source_to_roi_matrix

    def _is_axis_aligned_rectangle(self, roi: RoiTemplate) -> bool:
        if len(roi.polygon_xy) != 4:
            return False
        ordered = self._ordered_quad(roi.polygon_xy)
        x_values = sorted({point[0] for point in ordered})
        y_values = sorted({point[1] for point in ordered})
        return len(x_values) == 2 and len(y_values) == 2

    def _ordered_quad(self, points: tuple[tuple[int, int], ...]) -> tuple[tuple[float, float], ...]:
        if len(points) != 4:
            raise ValueError("ROI 透视展开需要 4 个点")
        as_float = [(float(x), float(y)) for x, y in points]
        sums = [x + y for x, y in as_float]
        diffs = [x - y for x, y in as_float]
        top_left = as_float[sums.index(min(sums))]
        bottom_right = as_float[sums.index(max(sums))]
        top_right = as_float[diffs.index(max(diffs))]
        bottom_left = as_float[diffs.index(min(diffs))]
        ordered = (top_left, top_right, bottom_right, bottom_left)
        if len(set(ordered)) != 4:
            raise ValueError("ROI 四点不能重复或退化")
        return ordered

    def _homography(
        self,
        src_points: tuple[tuple[float, float], ...],
        dst_points: tuple[tuple[float, float], ...],
    ) -> tuple[float, ...]:
        rows: list[list[float]] = []
        values: list[float] = []
        for (x, y), (u, v) in zip(src_points, dst_points):
            rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
            values.append(u)
            rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
            values.append(v)
        solved = self._solve_linear(rows, values)
        return (solved[0], solved[1], solved[2], solved[3], solved[4], solved[5], solved[6], solved[7], 1.0)

    def _translate_homography(self, transform: tuple[float, ...], dx: float, dy: float) -> tuple[float, ...]:
        return (
            transform[0] + dx * transform[6],
            transform[1] + dx * transform[7],
            transform[2] + dx * transform[8],
            transform[3] + dy * transform[6],
            transform[4] + dy * transform[7],
            transform[5] + dy * transform[8],
            transform[6],
            transform[7],
            transform[8],
        )

    def _solve_linear(self, matrix: list[list[float]], values: list[float]) -> list[float]:
        size = len(values)
        augmented = [row[:] + [value] for row, value in zip(matrix, values)]
        for col in range(size):
            pivot = max(range(col, size), key=lambda row: abs(augmented[row][col]))
            if abs(augmented[pivot][col]) < 1e-9:
                raise ValueError("ROI 透视矩阵不可逆")
            augmented[col], augmented[pivot] = augmented[pivot], augmented[col]
            pivot_value = augmented[col][col]
            for item in range(col, size + 1):
                augmented[col][item] /= pivot_value
            for row in range(size):
                if row == col:
                    continue
                factor = augmented[row][col]
                if factor == 0.0:
                    continue
                for item in range(col, size + 1):
                    augmented[row][item] -= factor * augmented[col][item]
        return [augmented[row][size] for row in range(size)]

    def _apply_homography(self, transform: tuple[float, ...], x: float, y: float) -> tuple[float, float] | None:
        denom = transform[6] * x + transform[7] * y + transform[8]
        if abs(denom) < 1e-9:
            return None
        source_x = (transform[0] * x + transform[1] * y + transform[2]) / denom
        source_y = (transform[3] * x + transform[4] * y + transform[5]) / denom
        return source_x, source_y

    def _sample_bilinear(self, frame: LightFrame, x: float, y: float) -> int:
        x = max(0.0, min(float(frame.width - 1), x))
        y = max(0.0, min(float(frame.height - 1), y))
        x0 = int(x)
        y0 = int(y)
        x1 = min(x0 + 1, frame.width - 1)
        y1 = min(y0 + 1, frame.height - 1)
        dx = x - x0
        dy = y - y0
        top = self._pixel(frame, x0, y0) * (1.0 - dx) + self._pixel(frame, x1, y0) * dx
        bottom = self._pixel(frame, x0, y1) * (1.0 - dx) + self._pixel(frame, x1, y1) * dx
        return int(round(top * (1.0 - dy) + bottom * dy))

    def _pixel(self, frame: LightFrame, x: int, y: int) -> int:
        return int(frame.image[y * frame.stride_bytes + x])
