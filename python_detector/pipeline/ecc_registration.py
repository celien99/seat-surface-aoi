from __future__ import annotations

from dataclasses import dataclass
import math

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
        best_shift = (0, 0)
        best_correlation = -1.0
        iterations = 0
        previous_best = -1.0
        for radius in range(search_radius_px + 1):
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) != radius:
                        continue
                    correlation = self._normalized_correlation(base, moving, dx, dy)
                    iterations += 1
                    if correlation > best_correlation:
                        best_correlation = correlation
                        best_shift = (dx, dy)
            if iterations >= max_iterations:
                break
            if radius > 0 and abs(best_correlation - previous_best) < convergence_epsilon:
                break
            previous_best = best_correlation

        converged = best_correlation >= min_correlation and best_correlation >= 0.0
        mean_error = math.sqrt(float(best_shift[0] * best_shift[0] + best_shift[1] * best_shift[1]))
        return EccAlignmentResult(
            light_id=moving.light_id,
            matrix_3x3=self._translation_matrix(best_shift[0], best_shift[1]),
            shift_xy=best_shift,
            correlation=best_correlation,
            iterations=iterations,
            converged=converged,
            mean_error_px=mean_error,
            message="ECC translation pass" if converged else "ECC correlation below threshold",
        )

    def apply_translation(self, moving: LightFrame, shift_xy: tuple[int, int]) -> LightFrame:
        dx, dy = shift_xy
        if dx == 0 and dy == 0:
            return moving
        aligned = bytearray(moving.width * moving.height)
        for y in range(moving.height):
            source_y = max(0, min(moving.height - 1, y + dy))
            for x in range(moving.width):
                source_x = max(0, min(moving.width - 1, x + dx))
                aligned[y * moving.width + x] = moving.image[source_y * moving.stride_bytes + source_x]
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
            image=memoryview(aligned),
            origin_xy=moving.origin_xy,
            source_width=moving.source_width,
            source_height=moving.source_height,
            roi_to_source_matrix=moving.roi_to_source_matrix,
            source_to_roi_matrix=moving.source_to_roi_matrix,
        )

    def _normalized_correlation(self, base: LightFrame, moving: LightFrame, dx: int, dy: int) -> float:
        count = 0
        sum_a = 0.0
        sum_b = 0.0
        sum_aa = 0.0
        sum_bb = 0.0
        sum_ab = 0.0
        for y in range(base.height):
            moving_y = y + dy
            if moving_y < 0 or moving_y >= moving.height:
                continue
            base_row = y * base.stride_bytes
            moving_row = moving_y * moving.stride_bytes
            for x in range(base.width):
                moving_x = x + dx
                if moving_x < 0 or moving_x >= moving.width:
                    continue
                a = float(base.image[base_row + x])
                b = float(moving.image[moving_row + moving_x])
                count += 1
                sum_a += a
                sum_b += b
                sum_aa += a * a
                sum_bb += b * b
                sum_ab += a * b
        if count < 4:
            return -1.0
        numerator = sum_ab - (sum_a * sum_b / count)
        denom_a = sum_aa - (sum_a * sum_a / count)
        denom_b = sum_bb - (sum_b * sum_b / count)
        denom = math.sqrt(denom_a * denom_b)
        if denom <= 1e-9:
            return -1.0
        return numerator / denom

    def _translation_matrix(self, dx: int, dy: int) -> tuple[float, ...]:
        return (1.0, 0.0, float(dx), 0.0, 1.0, float(dy), 0.0, 0.0, 1.0)
