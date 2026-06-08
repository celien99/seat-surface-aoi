from __future__ import annotations

from dataclasses import dataclass

from python_detector.models.inference_engine import DefectCandidate


@dataclass
class FusedResult:
    candidates: list[DefectCandidate]


class FusionEngine:
    def fuse(self, candidates: list[DefectCandidate]) -> FusedResult:
        return FusedResult(candidates=candidates)

