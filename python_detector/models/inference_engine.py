from __future__ import annotations

from dataclasses import dataclass
import math
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
        height, width = feature_group.feature_shape_hw
        if width <= 0 or height <= 0:
            bbox = (1, 1, 8, 8)
        else:
            box_width = min(8, max(width, 1))
            box_height = min(8, max(height, 1))
            bbox = _map_roi_bbox_to_source((0.0, 0.0, float(box_width - 1), float(box_height - 1)), feature_group)
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
            if not math.isfinite(score) or score < 0.0 or score > 1.0:
                raise RuntimeError(f"ONNX 输出 score 越界或非有限: {score}")
            if score < self.config.score_threshold:
                continue
            class_value = float(row[5])
            if not math.isfinite(class_value) or not class_value.is_integer():
                raise RuntimeError(f"ONNX 输出 class_id 不是整数: {class_value}")
            class_id = int(class_value)
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
        feature_height, feature_width = feature_group.feature_shape_hw
        width = max(feature_width, 1)
        height = max(feature_height, 1)
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
            raise RuntimeError(f"ONNX 输出 bbox 包含非有限值: {(x0, y0, x1, y1)}")
        if self.config.bbox_format == "xyxy_normalized":
            if not all(0.0 <= value <= 1.0 for value in (x0, y0, x1, y1)):
                raise RuntimeError(f"ONNX 归一化 bbox 越界: {(x0, y0, x1, y1)}")
            x0 = x0 * float(width - 1)
            x1 = x1 * float(width - 1)
            y0 = y0 * float(height - 1)
            y1 = y1 * float(height - 1)
        elif self.config.bbox_format == "xyxy_pixel":
            if not (0.0 <= x0 <= float(width - 1) and 0.0 <= x1 <= float(width - 1)):
                raise RuntimeError(f"ONNX 像素 bbox x 越界: {(x0, y0, x1, y1)}")
            if not (0.0 <= y0 <= float(height - 1) and 0.0 <= y1 <= float(height - 1)):
                raise RuntimeError(f"ONNX 像素 bbox y 越界: {(x0, y0, x1, y1)}")
        else:
            raise RuntimeError(f"不支持的 bbox_format: {self.config.bbox_format}")
        if x1 < x0 or y1 < y0:
            raise RuntimeError(f"ONNX 输出 bbox 坐标反向: {(x0, y0, x1, y1)}")
        roi_bbox = (x0, y0, x1, y1)
        mapped = _map_roi_bbox_to_source(roi_bbox, feature_group)
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
        cache_key = self._cache_key(model_key, config)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._create_model(config)
        return self._cache[cache_key]

    def _cache_key(self, model_key: str, config: ModelConfig) -> tuple[Any, ...]:
        return (
            model_key,
            config.backend,
            config.model_path or "",
            config.fake_mode,
            config.model_family,
            config.role,
            config.input_channels,
            float(config.input_scale),
            config.class_names,
            config.output_decode,
            config.bbox_format,
            float(config.score_threshold),
        )

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


def _map_roi_bbox_to_source(
    roi_bbox_xyxy_pixel: tuple[float, float, float, float],
    feature_group: FeatureGroup,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = roi_bbox_xyxy_pixel
    matrix = feature_group.roi_to_source_matrix
    if matrix is None:
        roi_x0, roi_y0, _roi_x1, _roi_y1 = feature_group.roi_bbox_xyxy_pixel
        return (
            int(round(roi_x0 + x0)),
            int(round(roi_y0 + y0)),
            int(round(roi_x0 + x1)),
            int(round(roi_y0 + y1)),
        )

    corners = (
        (x0, y0),
        (x1, y0),
        (x1, y1),
        (x0, y1),
    )
    mapped_points = [_apply_homography(matrix, x, y) for x, y in corners]
    if any(point is None for point in mapped_points):
        raise RuntimeError("ROI 到原图 bbox 映射矩阵无效")
    xs = [point[0] for point in mapped_points if point is not None]
    ys = [point[1] for point in mapped_points if point is not None]
    roi_x0, roi_y0, roi_x1, roi_y1 = feature_group.roi_bbox_xyxy_pixel
    return (
        int(max(roi_x0, min(roi_x1, math.floor(min(xs))))),
        int(max(roi_y0, min(roi_y1, math.floor(min(ys))))),
        int(max(roi_x0, min(roi_x1, math.ceil(max(xs))))),
        int(max(roi_y0, min(roi_y1, math.ceil(max(ys))))),
    )


def _apply_homography(matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float] | None:
    denom = matrix[6] * x + matrix[7] * y + matrix[8]
    if abs(denom) < 1e-9:
        return None
    mapped_x = (matrix[0] * x + matrix[1] * y + matrix[2]) / denom
    mapped_y = (matrix[3] * x + matrix[4] * y + matrix[5]) / denom
    return mapped_x, mapped_y
