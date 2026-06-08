from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from python_detector.config.recipe_schema import ModelConfig, Recipe
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


class ModelBackend(Protocol):
    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        ...


class FakeModel:
    def __init__(self, mode: str = "auto") -> None:
        self.mode = mode

    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        if self.mode == "ok":
            return []
        if self.mode == "ng":
            return [self._candidate(feature_group, 0.88)]
        if self.mode == "recheck":
            return [self._candidate(feature_group, 0.22)]
        suspicious = max(feature_group.features.get("high_lr_diff", [0]))
        if suspicious > 240:
            return [self._candidate(feature_group, 0.22)]
        return []

    def _candidate(self, feature_group: FeatureGroup, score: float) -> DefectCandidate:
        return DefectCandidate(
            camera_id=feature_group.camera_id,
            roi_name=feature_group.roi_name,
            class_name="scratch",
            score=score,
            bbox_xyxy_pixel=(1, 1, 8, 8),
            area_px=49,
            evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
        )


class OnnxModel:
    def __init__(self, model_path: str | None) -> None:
        if not model_path:
            raise RuntimeError("ONNX 模型路径不能为空")
        path = Path(model_path)
        if not path.exists():
            raise RuntimeError(f"ONNX 模型文件不存在: {model_path}")
        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError("onnxruntime 未安装，无法启用 ONNX 后端") from exc
        self.session = ort.InferenceSession(str(path))

    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        raise RuntimeError("ONNX 输出解码尚未配置，不能默认输出 OK")


class ModelRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, ModelBackend] = {}

    def get_model(self, model_key: str, recipe: Recipe) -> ModelBackend:
        config = recipe.models.get(model_key) or recipe.models.get("default") or ModelConfig()
        cache_key = f"{model_key}:{config.backend}:{config.model_path or ''}"
        if cache_key not in self._cache:
            self._cache[cache_key] = self._create_model(config)
        return self._cache[cache_key]

    def _create_model(self, config: ModelConfig) -> ModelBackend:
        if config.backend == "fake":
            return FakeModel(config.fake_mode)
        if config.backend == "onnx":
            return OnnxModel(config.model_path)
        raise RuntimeError(f"不支持的模型后端: {config.backend}")


class InferenceEngine:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self.model_registry = model_registry

    def infer(self, feature_groups: list[FeatureGroup], recipe: Recipe) -> list[DefectCandidate]:
        candidates: list[DefectCandidate] = []
        for group in feature_groups:
            model = self.model_registry.get_model(group.model_key, recipe)
            candidates.extend(model.run(group))
        return candidates
