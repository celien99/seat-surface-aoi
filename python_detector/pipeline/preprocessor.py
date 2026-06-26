from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from python_detector.config.calibration_manager import Calibration, CalibrationManager, RoiMask, RoiTemplate
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
        # 像素坐标范围 [0, width-1]/[0, height-1]；x1 >= width 意味着多边形最大 x
        # 超出图像右边界（而非在最后一个像素上，后者的 x1 = width-1 应通过检查）。
        # 闭区间 bbox = [x0, x1] 与 numpy 左闭右开切片 [x0 : x1+1) 一致。
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
        if roi.mask is not None:
            cropped = self._apply_roi_mask(cropped, output_width, output_height, roi.mask, frame, roi.roi_name)
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
        expected = frame.stride_bytes * frame.height
        raw = np.frombuffer(frame.image, dtype=np.uint8, count=expected)
        image = raw.reshape(frame.height, frame.stride_bytes)
        return bytearray(np.ascontiguousarray(image[y0 : y0 + height, x0 : x0 + width]).tobytes())

    def _apply_roi_mask(
        self,
        pixels: bytearray,
        width: int,
        height: int,
        mask: RoiMask,
        frame: LightFrame,
        roi_name: str,
    ) -> bytearray:
        if mask.width != width or mask.height != height:
            raise ValueError(
                f"{frame.camera_id}/{frame.light_id}/{roi_name}: ROI mask 尺寸不一致 "
                f"{mask.width}x{mask.height} != {width}x{height}"
            )
        if len(mask.pixels) != width * height:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}/{roi_name}: ROI mask 像素长度不匹配")
        image = np.frombuffer(pixels, dtype=np.uint8).reshape(height, width).copy()
        mask_array = np.frombuffer(mask.pixels, dtype=np.uint8).reshape(height, width)
        image[mask_array == 0] = 0
        return bytearray(image.tobytes())

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
        warped = self._warp_image(frame, transform, output_width, output_height, roi.roi_name)
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

    def _warp_image(
        self,
        frame: LightFrame,
        transform: tuple[float, ...],
        output_width: int,
        output_height: int,
        roi_name: str,
    ) -> bytearray:
        grid_y, grid_x = np.indices((output_height, output_width), dtype=np.float64)
        denom = transform[6] * grid_x + transform[7] * grid_y + transform[8]
        if np.any(np.abs(denom) < 1e-9):
            raise ValueError(f"{frame.camera_id}/{frame.light_id}/{roi_name}: ROI 透视变换无效")
        source_x = (transform[0] * grid_x + transform[1] * grid_y + transform[2]) / denom
        source_y = (transform[3] * grid_x + transform[4] * grid_y + transform[5]) / denom
        warped = self._sample_bilinear_grid(frame, source_x, source_y)
        return bytearray(warped.tobytes())

    def _sample_bilinear_grid(self, frame: LightFrame, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        source = np.frombuffer(frame.image, dtype=np.uint8, count=frame.stride_bytes * frame.height)
        source = source.reshape(frame.height, frame.stride_bytes)[:, : frame.width].astype(np.float64, copy=False)
        clipped_x = np.clip(x, 0.0, float(frame.width - 1))
        clipped_y = np.clip(y, 0.0, float(frame.height - 1))
        x0 = clipped_x.astype(np.int64)
        y0 = clipped_y.astype(np.int64)
        x1 = np.minimum(x0 + 1, frame.width - 1)
        y1 = np.minimum(y0 + 1, frame.height - 1)
        dx = clipped_x - x0
        dy = clipped_y - y0
        top = source[y0, x0] * (1.0 - dx) + source[y0, x1] * dx
        bottom = source[y1, x0] * (1.0 - dx) + source[y1, x1] * dx
        return np.rint(top * (1.0 - dy) + bottom * dy).astype(np.uint8)
