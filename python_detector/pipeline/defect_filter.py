from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.recipe_schema import DecisionThresholdConfig, Recipe
from python_detector.models.inference_engine import DefectCandidate


@dataclass(frozen=True)
class FilteredCandidate:
    candidate: DefectCandidate
    decision: str
    severity: str


class DefectFilter:
    def filter(self, candidates: list[DefectCandidate], recipe: Recipe) -> list[FilteredCandidate]:
        filtered: list[FilteredCandidate] = []
        threshold = recipe.decision_threshold
        for candidate in candidates:
            recheck_score = threshold.recheck_score if candidate.recheck_score is None else candidate.recheck_score
            ng_score = threshold.ng_score if candidate.ng_score is None else candidate.ng_score
            if candidate.score >= ng_score and candidate.area_px >= threshold.min_area_px:
                if self._aspect_ratio_ok(candidate.bbox_xyxy_pixel, threshold):
                    filtered.append(FilteredCandidate(candidate=candidate, decision="NG", severity="critical"))
                    continue
                # 长宽比不通过则降级为 RECHECK
                if candidate.score >= recheck_score:
                    filtered.append(FilteredCandidate(candidate=candidate, decision="RECHECK", severity="suspect"))
            elif candidate.score >= recheck_score:
                filtered.append(FilteredCandidate(candidate=candidate, decision="RECHECK", severity="suspect"))
        return filtered

    @staticmethod
    def _aspect_ratio_ok(bbox: tuple[int, int, int, int], threshold: DecisionThresholdConfig) -> bool:
        """检查 bbox 长宽比是否在配置范围内。0 值表示不限制。"""
        if threshold.min_aspect_ratio <= 0.0 and threshold.max_aspect_ratio <= 0.0:
            return True
        width = bbox[2] - bbox[0] + 1
        height = bbox[3] - bbox[1] + 1
        if height <= 0:
            return False
        ar = width / height
        if threshold.min_aspect_ratio > 0.0 and ar < threshold.min_aspect_ratio:
            return False
        if threshold.max_aspect_ratio > 0.0 and ar > threshold.max_aspect_ratio:
            return False
        return True
