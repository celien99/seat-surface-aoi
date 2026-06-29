from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np

from python_detector.ipc.data_types import LightFrame


@dataclass(frozen=True)
class EccAlignmentResult:
    light_id: str
    matrix_3x3: tuple[float, ...]
    shift_xy: tuple[int, int]
    correlation: float
    iterations: int
    converged: bool
    mean_error_px: float
    message: str


class EccRegistration:
    def align_translation(
        self,
        base: LightFrame,
        moving: LightFrame,
        search_radius_px: int,
        max_iterations: int,
        convergence_epsilon: float,
        min_correlation: float,
    ) -> EccAlignmentResult:
        if base.width != moving.width or base.height != moving.height:
            return EccAlignmentResult(
                light_id=moving.light_id,
                matrix_3x3=self._translation_matrix(0, 0),
                shift_xy=(0, 0),
                correlation=-1.0,
                iterations=0,
                converged=False,
                mean_error_px=999.0,
                message="ECC 输入 ROI 尺寸不一致",
            )
        base_array = self._active_array(base).astype(np.float32, copy=False)
        moving_array = self._active_array(moving).astype(np.float32, copy=False)

        # 优先尝试 OpenCV 梯度下降 ECC，显著快于暴力搜索
        result = self._ecc_opencv(
            base_array, moving_array, moving.light_id,
            max_iterations, convergence_epsilon, min_correlation,
        )
        if result is not None:
            return result

        # OpenCV 不可用或不收敛时回退到暴力搜索
        return self._ecc_exhaustive(
            base_array, moving_array, moving.light_id,
            search_radius_px, max_iterations, convergence_epsilon, min_correlation,
        )

    def _ecc_opencv(
        self,
        base_array: np.ndarray,
        moving_array: np.ndarray,
        light_id: str,
        max_iterations: int,
        convergence_epsilon: float,
        min_correlation: float,
    ) -> EccAlignmentResult | None:
        """OpenCV findTransformECC 梯度下降配准，平移模型仅 2 参数。"""
        try:
            import cv2  # type: ignore
        except ImportError:
            return None
        try:
            warp_matrix = np.eye(2, 3, dtype=np.float32)
            criteria = (
                cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                max_iterations,
                float(convergence_epsilon),
            )
            correlation, updated_warp = cv2.findTransformECC(
                base_array, moving_array, warp_matrix,
                cv2.MOTION_TRANSLATION, criteria,
            )
            corr = float(correlation)
            if not math.isfinite(corr):
                return None
            warp_matrix = np.asarray(updated_warp, dtype=np.float32)
            dx = int(round(float(warp_matrix[0, 2])))
            dy = int(round(float(warp_matrix[1, 2])))
            converged = corr >= float(min_correlation) and corr >= 0.0
            mean_error = math.sqrt(float(dx * dx + dy * dy))
            return EccAlignmentResult(
                light_id=light_id,
                matrix_3x3=self._translation_matrix(dx, dy),
                shift_xy=(dx, dy),
                correlation=corr,
                iterations=max_iterations,
                converged=converged,
                mean_error_px=mean_error,
                message="ECC (OpenCV) pass" if converged else "ECC (OpenCV) correlation below threshold",
            )
        except (cv2.error, ValueError, TypeError, IndexError):
            return None

    def _ecc_exhaustive(
        self,
        base_array: np.ndarray,
        moving_array: np.ndarray,
        light_id: str,
        search_radius_px: int,
        max_iterations: int,
        convergence_epsilon: float,
        min_correlation: float,
    ) -> EccAlignmentResult:
        """暴力搜索平移配准（OpenCV 不可用或不收敛时的回退方案）。

        安全约束：
        - search_radius_px 硬上限为 10，防止 O(R²) 爆炸
        - max_iterations 作为总迭代次数的硬上限
        """
        # 硬上限防止暴力搜索在异常大半径下 O(R²) 爆炸
        MAX_EXHAUSTIVE_RADIUS = 10
        effective_radius = min(search_radius_px, MAX_EXHAUSTIVE_RADIUS)
        if search_radius_px > MAX_EXHAUSTIVE_RADIUS:
            message = (
                f"ECC 暴力搜索半径 {search_radius_px} 超出硬上限 {MAX_EXHAUSTIVE_RADIUS}，"
                f"已截断为 {MAX_EXHAUSTIVE_RADIUS}；建议检查 OpenCV 是否可用或减小 search_radius_px"
            )
        else:
            message = ""

        best_shift = (0, 0)
        best_correlation = -1.0
        iterations = 0
        previous_best = -1.0
        base_f64 = base_array.astype(np.float64, copy=False)
        moving_f64 = moving_array.astype(np.float64, copy=False)

        for radius in range(effective_radius + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    # 硬上限：总迭代次数不得超过 max_iterations
                    if iterations >= max_iterations:
                        break
                    correlation = self._normalized_correlation(base_f64, moving_f64, dx, dy)
                    iterations += 1
                    if correlation > best_correlation:
                        best_correlation = correlation
                        best_shift = (dx, dy)
                if iterations >= max_iterations:
                    break
            if iterations >= max_iterations:
                break
            if radius > 0 and abs(best_correlation - previous_best) < convergence_epsilon:
                break
            previous_best = best_correlation

        converged = best_correlation >= min_correlation and best_correlation >= 0.0
        mean_error = math.sqrt(float(best_shift[0] * best_shift[0] + best_shift[1] * best_shift[1]))
        result_msg = message or (
            "ECC (exhaustive) pass" if converged else "ECC (exhaustive) correlation below threshold"
        )
        return EccAlignmentResult(
            light_id=light_id,
            matrix_3x3=self._translation_matrix(best_shift[0], best_shift[1]),
            shift_xy=best_shift,
            correlation=best_correlation,
            iterations=iterations,
            converged=converged,
            mean_error_px=mean_error,
            message=result_msg,
        )

    def apply_translation(self, moving: LightFrame, shift_xy: tuple[int, int]) -> LightFrame:
        dx, dy = shift_xy
        if dx == 0 and dy == 0:
            return moving
        source = self._active_array(moving)
        row_indices = np.clip(np.arange(moving.height) + dy, 0, moving.height - 1)
        col_indices = np.clip(np.arange(moving.width) + dx, 0, moving.width - 1)
        aligned = source[row_indices[:, None], col_indices[None, :]]
        return LightFrame(
            camera_id=moving.camera_id,
            light_id=moving.light_id,
            frame_index=moving.frame_index,
            light_seq_index=moving.light_seq_index,
            width=moving.width,
            height=moving.height,
            channels=moving.channels,
            stride_bytes=moving.width,
            pixel_format=moving.pixel_format,
            bit_depth=moving.bit_depth,
            color_order=moving.color_order,
            dtype=moving.dtype,
            timestamp_us=moving.timestamp_us,
            exposure_us=moving.exposure_us,
            gain=moving.gain,
            calibration_id=moving.calibration_id,
            image_crc32=moving.image_crc32,
            image=memoryview(bytearray(np.ascontiguousarray(aligned).tobytes())),
            origin_xy=moving.origin_xy,
            source_width=moving.source_width,
            source_height=moving.source_height,
            roi_to_source_matrix=moving.roi_to_source_matrix,
            source_to_roi_matrix=moving.source_to_roi_matrix,
        )

    def _normalized_correlation(self, base_array: np.ndarray, moving_array: np.ndarray, dx: int, dy: int) -> float:
        base_height, base_width = base_array.shape
        moving_height, moving_width = moving_array.shape
        if dx >= 0:
            base_x = slice(0, base_width - dx)
            moving_x = slice(dx, moving_width)
        else:
            base_x = slice(-dx, base_width)
            moving_x = slice(0, moving_width + dx)
        if dy >= 0:
            base_y = slice(0, base_height - dy)
            moving_y = slice(dy, moving_height)
        else:
            base_y = slice(-dy, base_height)
            moving_y = slice(0, moving_height + dy)
        a = base_array[base_y, base_x]
        b = moving_array[moving_y, moving_x]
        count = a.size
        if count < 4:
            return -1.0
        sum_a = float(a.sum())
        sum_b = float(b.sum())
        sum_aa = float(np.square(a).sum())
        sum_bb = float(np.square(b).sum())
        sum_ab = float((a * b).sum())
        numerator = sum_ab - (sum_a * sum_b / count)
        denom_a = sum_aa - (sum_a * sum_a / count)
        denom_b = sum_bb - (sum_b * sum_b / count)
        denom = math.sqrt(denom_a * denom_b)
        if denom <= 1e-9:
            return -1.0
        return numerator / denom

    def _translation_matrix(self, dx: int, dy: int) -> tuple[float, ...]:
        return (1.0, 0.0, float(dx), 0.0, 1.0, float(dy), 0.0, 0.0, 1.0)

    def _active_array(self, frame: LightFrame) -> np.ndarray:
        raw = np.frombuffer(frame.image, dtype=np.uint8, count=frame.stride_bytes * frame.height)
        return raw.reshape(frame.height, frame.stride_bytes)[:, : frame.width]
