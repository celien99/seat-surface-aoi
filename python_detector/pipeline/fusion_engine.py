from __future__ import annotations

from dataclasses import dataclass
import math

from python_detector.config.recipe_schema import FusionConfig
from python_detector.models.inference_engine import DefectCandidate


@dataclass
class FusedResult:
    candidates: list[DefectCandidate]
    suppressed_count: int = 0
    overflow_count: int = 0


class FusionEngine:
    def fuse(self, candidates: list[DefectCandidate], config: FusionConfig | None = None) -> FusedResult:
        config = config or FusionConfig()
        groups: dict[tuple[str, str, str], list[DefectCandidate]] = {}
        for candidate in candidates:
            self._validate_candidate(candidate)
            key = (
                candidate.camera_id,
                candidate.pose_id,
                candidate.roi_name,
            )
            groups.setdefault(key, []).append(candidate)

        fused: list[DefectCandidate] = []
        suppressed_count = 0
        overflow_count = 0
        for group in groups.values():
            kept, suppressed, overflow = self._nms_group(group, config)
            fused.extend(kept)
            suppressed_count += suppressed
            overflow_count += overflow
        fused.sort(key=lambda item: (item.camera_id, item.pose_id, item.roi_name, -item.score))
        return FusedResult(candidates=fused, suppressed_count=suppressed_count, overflow_count=overflow_count)

    def _nms_group(self, candidates: list[DefectCandidate], config: FusionConfig) -> tuple[list[DefectCandidate], int, int]:
        sorted_candidates = sorted(candidates, key=lambda item: item.score, reverse=True)
        kept: list[DefectCandidate] = []
        suppressed_count = 0
        overflow_count = 0
        for candidate in sorted_candidates:
            matched_index = self._first_overlapping_index(kept, candidate, config.iou_threshold)
            if matched_index is None:
                if len(kept) < config.max_candidates_per_roi:
                    kept.append(candidate)
                else:
                    overflow_count += 1
                continue
            kept[matched_index] = self._merge_candidates(kept[matched_index], candidate)
            suppressed_count += 1
        return kept, suppressed_count, overflow_count

    def _first_overlapping_index(
        self,
        kept: list[DefectCandidate],
        candidate: DefectCandidate,
        iou_threshold: float,
    ) -> int | None:
        for index, existing in enumerate(kept):
            if self._iou(existing.bbox_xyxy_pixel, candidate.bbox_xyxy_pixel) >= iou_threshold:
                return index
        return None

    def _merge_candidates(self, primary: DefectCandidate, secondary: DefectCandidate) -> DefectCandidate:
        evidence_lights = list(dict.fromkeys(primary.evidence_lights + secondary.evidence_lights))
        if secondary.score > primary.score:
            return DefectCandidate(
                camera_id=secondary.camera_id,
                pose_id=secondary.pose_id,
                roi_name=secondary.roi_name,
                score=secondary.score,
                bbox_xyxy_pixel=secondary.bbox_xyxy_pixel,
                area_px=secondary.area_px,
                evidence_lights=evidence_lights,
            )
        return DefectCandidate(
            camera_id=primary.camera_id,
            pose_id=primary.pose_id,
            roi_name=primary.roi_name,
            score=primary.score,
            bbox_xyxy_pixel=primary.bbox_xyxy_pixel,
            area_px=primary.area_px,
            evidence_lights=evidence_lights,
        )

    def _iou(self, bbox_a: tuple[int, int, int, int], bbox_b: tuple[int, int, int, int]) -> float:
        ax0, ay0, ax1, ay1 = bbox_a
        bx0, by0, bx1, by1 = bbox_b
        inter_x0 = max(ax0, bx0)
        inter_y0 = max(ay0, by0)
        inter_x1 = min(ax1, bx1)
        inter_y1 = min(ay1, by1)
        inter_width = max(inter_x1 - inter_x0 + 1, 0)
        inter_height = max(inter_y1 - inter_y0 + 1, 0)
        intersection = inter_width * inter_height
        area_a = (ax1 - ax0 + 1) * (ay1 - ay0 + 1)
        area_b = (bx1 - bx0 + 1) * (by1 - by0 + 1)
        union = area_a + area_b - intersection
        return 0.0 if union <= 0 else intersection / union

    def _validate_candidate(self, candidate: DefectCandidate) -> None:
        x0, y0, x1, y1 = candidate.bbox_xyxy_pixel
        if not math.isfinite(candidate.score) or candidate.score < 0.0 or candidate.score > 1.0:
            raise ValueError(f"invalid candidate score: {candidate.score}")
        if x1 < x0 or y1 < y0:
            raise ValueError(f"invalid candidate bbox_xyxy_pixel: {candidate.bbox_xyxy_pixel}")
        if candidate.area_px <= 0:
            raise ValueError(f"invalid candidate area_px: {candidate.area_px}")
        if not candidate.evidence_lights:
            raise ValueError("candidate evidence_lights is empty")
