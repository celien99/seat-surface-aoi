from __future__ import annotations

from dataclasses import dataclass

from python_detector.pipeline.feature_builder import FeatureGroup


@dataclass
class DefectCandidate:
    camera_id: str
    roi_name: str
    class_name: str
    score: float
    bbox_xyxy_pixel: tuple[int, int, int, int]
    area_px: int
    evidence_lights: list[str]


class FakeModel:
    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        suspicious = max(feature_group.features.get("high_lr_diff", [0]))
        if suspicious > 240:
            return [
                DefectCandidate(
                    camera_id=feature_group.camera_id,
                    roi_name=feature_group.roi_name,
                    class_name="scratch",
                    score=0.22,
                    bbox_xyxy_pixel=(1, 1, 8, 8),
                    area_px=49,
                    evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
                )
            ]
        return []


class ModelRegistry:
    def __init__(self) -> None:
        self._fake_model = FakeModel()

    def get_model(self, model_key: str) -> FakeModel:
        return self._fake_model


class InferenceEngine:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self.model_registry = model_registry

    def infer(self, feature_groups: list[FeatureGroup]) -> list[DefectCandidate]:
        candidates: list[DefectCandidate] = []
        for group in feature_groups:
            model = self.model_registry.get_model(group.model_key)
            candidates.extend(model.run(group))
        return candidates

