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

    def _normalized_correlation(self, base: LightFrame, moving: LightFrame, dx: int, dy: int) -> float:
        pairs: list[tuple[int, int]] = []
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
                pairs.append((int(base.image[base_row + x]), int(moving.image[moving_row + moving_x])))
        if len(pairs) < 4:
            return -1.0
        mean_a = sum(a for a, _b in pairs) / len(pairs)
        mean_b = sum(b for _a, b in pairs) / len(pairs)
        numerator = 0.0
        denom_a = 0.0
        denom_b = 0.0
        for a, b in pairs:
            da = float(a) - mean_a
            db = float(b) - mean_b
            numerator += da * db
            denom_a += da * da
            denom_b += db * db
        denom = math.sqrt(denom_a * denom_b)
        if denom <= 1e-9:
            return -1.0
        return numerator / denom

    def _translation_matrix(self, dx: int, dy: int) -> tuple[float, ...]:
        return (1.0, 0.0, float(dx), 0.0, 1.0, float(dy), 0.0, 0.0, 1.0)
