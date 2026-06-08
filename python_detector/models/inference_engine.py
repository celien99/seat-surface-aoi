from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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
        suspicious = max(feature_group.features.get("ch4_high_max_min", [0]))
        if suspicious > 240:
            return [self._candidate(feature_group, 0.22)]
        return []

    def _candidate(self, feature_group: FeatureGroup, score: float) -> DefectCandidate:
        x0, y0, x1, y1 = feature_group.roi_bbox_xyxy_pixel
        if x1 <= x0 or y1 <= y0:
            bbox = (1, 1, 8, 8)
        else:
            width = x1 - x0 + 1
            height = y1 - y0 + 1
            box_width = min(8, max(width, 1))
            box_height = min(8, max(height, 1))
            bbox = (x0, y0, x0 + box_width - 1, y0 + box_height - 1)
        return DefectCandidate(
            camera_id=feature_group.camera_id,
            roi_name=feature_group.roi_name,
            class_name="scratch",
            score=score,
            bbox_xyxy_pixel=bbox,
            area_px=(bbox[2] - bbox[0] + 1) * (bbox[3] - bbox[1] + 1),
            evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
        )


class OnnxModel:
    def __init__(self, config: ModelConfig) -> None:
        if not config.model_path:
            raise RuntimeError("ONNX 模型路径不能为空")
        path = Path(config.model_path)
        if not path.exists():
            raise RuntimeError(f"ONNX 模型文件不存在: {config.model_path}")
        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError("onnxruntime 未安装，无法启用 ONNX 后端") from exc
        self.session = ort.InferenceSession(str(path))
        self.config = config

    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        if self.config.output_decode == "none":
            raise RuntimeError("ONNX 输出解码未配置，不能默认输出 OK")
        if feature_group.tensor_nchw is None:
            raise RuntimeError("ONNX 输入 tensor 缺失")
        try:
            import numpy as np  # type: ignore
        except Exception as exc:
            raise RuntimeError("numpy 未安装，无法构建 ONNX 输入") from exc

        input_info = self.session.get_inputs()
        if not input_info:
            raise RuntimeError("ONNX 模型没有输入节点")
        input_name = input_info[0].name
        tensor = np.asarray(feature_group.tensor_nchw, dtype=np.float32)
        outputs = self.session.run(None, {input_name: tensor})
        if self.config.output_decode == "detection_rows":
            return self._decode_detection_rows(outputs, feature_group)
        raise RuntimeError(f"不支持的 ONNX 输出解码方式: {self.config.output_decode}")

    def _decode_detection_rows(self, outputs: list[Any], feature_group: FeatureGroup) -> list[DefectCandidate]:
        try:
            import numpy as np  # type: ignore
        except Exception as exc:
            raise RuntimeError("numpy 未安装，无法解析 ONNX 输出") from exc
        if not outputs:
            raise RuntimeError("ONNX 输出为空")
        rows = np.asarray(outputs[0], dtype=np.float32)
        if rows.ndim == 3 and rows.shape[0] == 1:
            rows = rows[0]
        if rows.ndim != 2 or rows.shape[1] < 6:
            raise RuntimeError(f"ONNX detection_rows 输出形状无效: {tuple(rows.shape)}")

        candidates: list[DefectCandidate] = []
        for row in rows:
            score = float(row[4])
            if score < self.config.score_threshold:
                continue
            class_id = int(row[5])
            if class_id < 0 or class_id >= len(self.config.class_names):
                raise RuntimeError(f"ONNX 输出 class_id 越界: {class_id}")
            bbox = self._map_bbox_xyxy(row[:4], feature_group)
            area_px = max(bbox[2] - bbox[0] + 1, 0) * max(bbox[3] - bbox[1] + 1, 0)
            candidates.append(
                DefectCandidate(
                    camera_id=feature_group.camera_id,
                    roi_name=feature_group.roi_name,
                    class_name=self.config.class_names[class_id],
                    score=score,
                    bbox_xyxy_pixel=bbox,
                    area_px=area_px,
                    evidence_lights=self._evidence_lights(feature_group),
                )
            )
        return candidates

    def _evidence_lights(self, feature_group: FeatureGroup) -> list[str]:
        evidence: list[str] = []
        for channel_name in feature_group.tensor_channel_names:
            evidence.extend(feature_group.evidence_lights_by_channel.get(channel_name, ()))
        return list(dict.fromkeys(evidence))

    def _map_bbox_xyxy(self, raw_bbox: Any, feature_group: FeatureGroup) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = (float(value) for value in raw_bbox)
        roi_x0, roi_y0, roi_x1, roi_y1 = feature_group.roi_bbox_xyxy_pixel
        width = max(roi_x1 - roi_x0 + 1, 1)
        height = max(roi_y1 - roi_y0 + 1, 1)
        if self.config.bbox_format == "xyxy_normalized":
            x0 = roi_x0 + x0 * width
            x1 = roi_x0 + x1 * width
            y0 = roi_y0 + y0 * height
            y1 = roi_y0 + y1 * height
        elif self.config.bbox_format == "xyxy_pixel":
            x0 += roi_x0
            x1 += roi_x0
            y0 += roi_y0
            y1 += roi_y0
        else:
            raise RuntimeError(f"不支持的 bbox_format: {self.config.bbox_format}")

        mapped = (
            int(round(max(min(x0, roi_x1), roi_x0))),
            int(round(max(min(y0, roi_y1), roi_y0))),
            int(round(max(min(x1, roi_x1), roi_x0))),
            int(round(max(min(y1, roi_y1), roi_y0))),
        )
        if mapped[2] < mapped[0] or mapped[3] < mapped[1]:
            raise RuntimeError(f"ONNX 输出 bbox 无效: {mapped}")
        return mapped


class ModelRegistry:
    def __init__(self) -> None:
        self._cache: dict[str, ModelBackend] = {}

    def get_model(self, model_key: str, recipe: Recipe) -> ModelBackend:
        config = recipe.models.get(model_key)
        if config is None:
            raise RuntimeError(f"配方引用了不存在的模型: {model_key}")
        cache_key = (
            f"{model_key}:{config.backend}:{config.model_path or ''}:"
            f"{config.output_decode}:{config.bbox_format}:{','.join(config.input_channels)}"
        )
        if cache_key not in self._cache:
            self._cache[cache_key] = self._create_model(config)
        return self._cache[cache_key]

    def _create_model(self, config: ModelConfig) -> ModelBackend:
        if config.backend == "fake":
            return FakeModel(config.fake_mode)
        if config.backend == "onnx":
            return OnnxModel(config)
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
