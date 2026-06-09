from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.recipe_schema import Recipe, ThresholdConfig
from python_detector.models.inference_engine import DefectCandidate


@dataclass(frozen=True)
class FilteredCandidate:
    candidate: DefectCandidate
    decision: str
    severity: str


class DefectFilter:
    def filter(self, candidates: list[DefectCandidate], recipe: Recipe) -> list[FilteredCandidate]:
        filtered: list[FilteredCandidate] = []
        for candidate in candidates:
            threshold = recipe.thresholds.get(candidate.class_name, ThresholdConfig())
            if candidate.score >= threshold.ng_score and candidate.area_px >= threshold.min_area_px:
                filtered.append(FilteredCandidate(candidate=candidate, decision="NG", severity="critical"))
            elif candidate.score >= threshold.recheck_score:
                filtered.append(FilteredCandidate(candidate=candidate, decision="RECHECK", severity="suspect"))
        return filtered
