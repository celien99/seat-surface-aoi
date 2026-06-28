from __future__ import annotations

import math
from typing import Any, Protocol

import numpy as np

from python_detector.config.recipe_schema import ModelConfig, Recipe
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.embedding import EmbeddingExtractor
from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
from python_detector.models.patchcore import PatchCoreKnnIndex
from python_detector.models.pca import PcaProjector
from python_detector.models.spatial_utils import (
    DefectCandidate,
    _map_roi_bbox_to_source,
)
from python_detector.models.yolo_decode import decode_yolo_rows
from python_detector.pipeline.feature_builder import FeatureGroup


class ModelInferenceError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        model_key: str,
        backend: str,
        camera_id: str,
        roi_name: str,
        tensor_shape_nchw: tuple[int, int, int, int] | None,
        cause_type: str,
    ) -> None:
        super().__init__(message)
        self.model_key = model_key
        self.backend = backend
        self.camera_id = camera_id
        self.roi_name = roi_name
        self.tensor_shape_nchw = tensor_shape_nchw
        self.cause_type = cause_type

    def context(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": str(self),
            "model_key": self.model_key,
            "backend": self.backend,
            "camera_id": self.camera_id,
            "roi_name": self.roi_name,
            "tensor_shape_nchw": list(self.tensor_shape_nchw) if self.tensor_shape_nchw is not None else None,
            "cause_type": self.cause_type,
        }


class ModelAssetUnavailableInferenceError(ModelInferenceError):
    def __init__(
        self,
        message: str,
        *,
        model_key: str,
        backend: str,
        camera_id: str,
        roi_name: str,
        tensor_shape_nchw: tuple[int, int, int, int] | None,
        asset_error: ModelAssetUnavailableError,
    ) -> None:
        super().__init__(
            message,
            model_key=model_key,
            backend=backend,
            camera_id=camera_id,
            roi_name=roi_name,
            tensor_shape_nchw=tensor_shape_nchw,
            cause_type=asset_error.__class__.__name__,
        )
        self.asset_error = asset_error

    def context(self) -> dict[str, Any]:
        context = super().context()
        context["asset_unavailable"] = True
        context["asset"] = self.asset_error.context()
        return context


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
        suspicious = self._max_tensor_feature_value(feature_group)
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
            pose_id=feature_group.pose_id,
            roi_name=feature_group.roi_name,
            score=score,
            bbox_xyxy_pixel=bbox,
            area_px=(bbox[2] - bbox[0] + 1) * (bbox[3] - bbox[1] + 1),
            evidence_lights=feature_group.evidence_lights(),
        )

    def _max_tensor_feature_value(self, feature_group: FeatureGroup) -> int:
        max_value: int | None = None
        for channel_name in feature_group.tensor_channel_names:
            values = feature_group.features.get(channel_name)
            if values is None:
                continue
            array = np.asarray(values)
            if array.size == 0:
                continue
            channel_max = int(array.max())
            max_value = channel_max if max_value is None else max(max_value, channel_max)
        return max_value if max_value is not None else 0


class OnnxModel:
    def __init__(self, config: ModelConfig) -> None:
        if not config.model_path:
            raise ModelAssetUnavailableError(
                "ONNX 模型路径不能为空",
                asset_kind="onnx_model",
                asset_path="",
                reason="path_not_configured",
            )
        self.session = create_onnx_session(config.model_path, "ONNX detection")
        self.config = config

    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        if self.config.output_decode == "none":
            raise RuntimeError("ONNX 输出解码未配置，不能默认输出 OK")
        if feature_group.tensor_nchw is None:
            raise RuntimeError("ONNX 输入 tensor 缺失")

        np = numpy_module("ONNX detection")
        tensor = np.asarray(feature_group.tensor_nchw, dtype=np.float32)
        outputs = run_first_input(self.session, tensor, "ONNX detection")
        if self.config.output_decode in {"detection_rows", "ultralytics_yolo"}:
            return self._decode_detection_rows(outputs, feature_group)
        raise RuntimeError(f"不支持的 ONNX 输出解码方式: {self.config.output_decode}")

    def _decode_detection_rows(self, outputs: list[Any], feature_group: FeatureGroup) -> list[DefectCandidate]:
        try:
            import numpy  # type: ignore  # noqa: F401
        except Exception as exc:
            raise RuntimeError("numpy 未安装，无法解析 ONNX 输出") from exc
        if not outputs:
            raise RuntimeError("ONNX 输出为空")
        rows = decode_yolo_rows(
            outputs[0],
            confidence_threshold=self.config.score_threshold,
            output_decode=self.config.output_decode,
        )

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
            if class_id < 0:
                raise RuntimeError(f"ONNX 输出 class_id 越界: {class_id}")
            bbox = self._map_bbox_xyxy(row[:4], feature_group)
            area_px = max(bbox[2] - bbox[0] + 1, 0) * max(bbox[3] - bbox[1] + 1, 0)
            candidates.append(
                DefectCandidate(
                    camera_id=feature_group.camera_id,
                    pose_id=feature_group.pose_id,
                    roi_name=feature_group.roi_name,
                    score=score,
                    bbox_xyxy_pixel=bbox,
                    area_px=area_px,
                    evidence_lights=feature_group.evidence_lights(),
                )
            )
        return candidates

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
    def __init__(
        self,
        embedding_extractor: EmbeddingExtractor | None = None,
        pca_projector: PcaProjector | None = None,
        patchcore_index: PatchCoreKnnIndex | None = None,
    ) -> None:
        self._cache: dict[str, ModelBackend] = {}
        self.embedding_extractor = embedding_extractor or EmbeddingExtractor()
        self.pca_projector = pca_projector or PcaProjector()
        self.patchcore_index = patchcore_index or PatchCoreKnnIndex()

    def get_model(self, model_key: str, recipe: Recipe) -> ModelBackend:
        config = recipe.models.get(model_key)
        if config is None:
            raise ModelAssetUnavailableError(
                f"配方引用了不存在的模型: {model_key}",
                asset_kind="model_config",
                asset_path=model_key,
                reason="model_key_missing",
            )
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
            config.output_decode,
            config.bbox_format,
            float(config.score_threshold),
            config.embedding_backend,
            config.embedding_model_path or "",
            config.embedding_version,
            int(config.embedding_dim),
            config.embedding_layers,
            config.pca_path or "",
            config.pca_version or "",
            config.memory_bank_path or "",
            config.faiss_index_path or "",
            float(config.coreset_ratio),
            int(config.knn_k),
            config.spatial_mode,
            config.spatial_layers,
            int(config.spatial_upsample_height),
            int(config.spatial_upsample_width),
            float(config.anomaly_binarize_min_ratio),
            float(config.anomaly_binarize_relative),
        )

    def _create_model(self, config: ModelConfig) -> ModelBackend:
        if config.backend == "fake":
            return FakeModel(config.fake_mode)
        if config.backend == "onnx":
            return OnnxModel(config)
        if config.backend == "patchcore_knn":
            from python_detector.models.patchcore_model import PatchCoreModel
            return PatchCoreModel(config, self.embedding_extractor, self.pca_projector, self.patchcore_index)
        raise RuntimeError(f"不支持的模型后端: {config.backend}")


class InferenceEngine:
    def __init__(self, model_registry: ModelRegistry) -> None:
        self.model_registry = model_registry

    def infer(self, feature_groups: list[FeatureGroup], recipe: Recipe) -> list[DefectCandidate]:
        candidates: list[DefectCandidate] = []
        for group in feature_groups:
            config = recipe.models.get(group.model_key)
            backend = config.backend if config is not None else "missing"
            try:
                model = self.model_registry.get_model(group.model_key, recipe)
                candidates.extend(model.run(group))
            except ModelAssetUnavailableError as exc:
                raise ModelAssetUnavailableInferenceError(
                    f"{group.camera_id}/{group.roi_name}/{group.model_key}: 模型资产未就绪，保存采集样本: {exc}",
                    model_key=group.model_key,
                    backend=backend,
                    camera_id=group.camera_id,
                    roi_name=group.roi_name,
                    tensor_shape_nchw=group.tensor_shape_nchw(),
                    asset_error=exc,
                ) from exc
            except ModelInferenceError:
                raise
            except Exception as exc:
                raise ModelInferenceError(
                    f"{group.camera_id}/{group.roi_name}/{group.model_key}: 模型推理失败: {exc}",
                    model_key=group.model_key,
                    backend=backend,
                    camera_id=group.camera_id,
                    roi_name=group.roi_name,
                    tensor_shape_nchw=group.tensor_shape_nchw(),
                    cause_type=exc.__class__.__name__,
                ) from exc
        return candidates


