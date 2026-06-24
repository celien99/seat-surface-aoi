from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Protocol

from python_detector.config.recipe_schema import ModelConfig, Recipe
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.embedding import EmbeddingExtractor
from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
from python_detector.models.patchcore import PatchCoreKnnIndex
from python_detector.models.pca import PcaProjector
from python_detector.models.yolo_decode import decode_yolo_rows
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
    pose_id: str = ""


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
            class_name="scratch",
            score=score,
            bbox_xyxy_pixel=bbox,
            area_px=(bbox[2] - bbox[0] + 1) * (bbox[3] - bbox[1] + 1),
            evidence_lights=self._evidence_lights(feature_group),
        )

    def _evidence_lights(self, feature_group: FeatureGroup) -> list[str]:
        evidence: list[str] = []
        for channel_name in feature_group.tensor_channel_names:
            evidence.extend(feature_group.evidence_lights_by_channel.get(channel_name, ()))
        return list(dict.fromkeys(evidence))

    def _max_tensor_feature_value(self, feature_group: FeatureGroup) -> int:
        values: list[int] = []
        for channel_name in feature_group.tensor_channel_names:
            values.extend(feature_group.features.get(channel_name, ()))
        return max(values, default=0)


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
            if class_id < 0 or class_id >= len(self.config.class_names):
                raise RuntimeError(f"ONNX 输出 class_id 越界: {class_id}")
            bbox = self._map_bbox_xyxy(row[:4], feature_group)
            area_px = max(bbox[2] - bbox[0] + 1, 0) * max(bbox[3] - bbox[1] + 1, 0)
            candidates.append(
                DefectCandidate(
                    camera_id=feature_group.camera_id,
                    pose_id=feature_group.pose_id,
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


class PatchCoreModel:
    def __init__(
        self,
        config: ModelConfig,
        embedding_extractor: EmbeddingExtractor | None = None,
        pca_projector: PcaProjector | None = None,
        knn_index: PatchCoreKnnIndex | None = None,
    ) -> None:
        if config.embedding_backend == "none":
            raise RuntimeError("PatchCore 必须配置 embedding_backend")
        if not config.memory_bank_path:
            raise ModelAssetUnavailableError(
                "PatchCore memory_bank_path 不能为空",
                asset_kind="patchcore_memory_bank",
                asset_path="",
                reason="path_not_configured",
            )
        self.config = config
        self.embedding_extractor = embedding_extractor or EmbeddingExtractor()
        self.pca_projector = pca_projector or PcaProjector()
        self.knn_index = knn_index or PatchCoreKnnIndex()

    def run(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        embedding = self.embedding_extractor.extract(feature_group, self.config)
        embedding_values = embedding.values
        feature_group.embedding_summary = {
            "backend": embedding.backend,
            "version": embedding.version,
            "embedding_dim": len(embedding.values),
            "layer_names": list(embedding.layer_names),
            "input_shape_nchw": list(embedding.input_shape_nchw) if embedding.input_shape_nchw is not None else None,
        }
        if self.config.pca_path:
            pca = self.pca_projector.project(embedding_values, self.config.pca_path, self.config.pca_version)
            embedding_values = pca.values
            feature_group.pca_summary = {
                "version": pca.version,
                "input_dim": pca.input_dim,
                "output_dim": pca.output_dim,
            }
        score = self.knn_index.score(
            embedding_values,
            self.config.memory_bank_path,
            self.config.knn_k,
            self.config.anomaly_score_scale,
            self.config.pca_version,
            self.config.faiss_index_path,
        )
        feature_group.anomaly_summary = {
            "model_family": self.config.model_family,
            "memory_bank_version": score.version,
            "backend": score.backend,
            "faiss_index_path": score.faiss_index_path,
            "fallback_reason": score.fallback_reason,
            "anomaly_score": score.anomaly_score,
            "nearest_distance": score.nearest_distance,
            "knn_distances": list(score.knn_distances),
            "memory_bank_size": score.memory_bank_size,
            "embedding_dim": score.embedding_dim,
        }
        if score.anomaly_score < self.config.score_threshold:
            return []
        bbox = feature_group.roi_bbox_xyxy_pixel
        area_px = max(bbox[2] - bbox[0] + 1, 0) * max(bbox[3] - bbox[1] + 1, 0)
        if area_px <= 0:
            height, width = feature_group.feature_shape_hw
            bbox = _map_roi_bbox_to_source((0.0, 0.0, float(width - 1), float(height - 1)), feature_group)
            area_px = max(bbox[2] - bbox[0] + 1, 0) * max(bbox[3] - bbox[1] + 1, 0)
        class_name = self.config.class_names[0] if self.config.class_names else "unknown_anomaly"
        return [
            DefectCandidate(
                camera_id=feature_group.camera_id,
                pose_id=feature_group.pose_id,
                roi_name=feature_group.roi_name,
                class_name=class_name,
                score=score.anomaly_score,
                bbox_xyxy_pixel=bbox,
                area_px=area_px,
                evidence_lights=self._evidence_lights(feature_group),
            )
        ]

    def _evidence_lights(self, feature_group: FeatureGroup) -> list[str]:
        evidence: list[str] = []
        for channel_name in feature_group.tensor_channel_names:
            evidence.extend(feature_group.evidence_lights_by_channel.get(channel_name, ()))
        return list(dict.fromkeys(evidence))


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
            config.class_names,
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
            float(config.anomaly_score_scale),
        )

    def _create_model(self, config: ModelConfig) -> ModelBackend:
        if config.backend == "fake":
            return FakeModel(config.fake_mode)
        if config.backend == "onnx":
            return OnnxModel(config)
        if config.backend == "patchcore_knn":
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
                    tensor_shape_nchw=_tensor_shape_nchw(group),
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
                    tensor_shape_nchw=_tensor_shape_nchw(group),
                    cause_type=exc.__class__.__name__,
                ) from exc
        return candidates


def _tensor_shape_nchw(feature_group: FeatureGroup) -> tuple[int, int, int, int] | None:
    tensor = feature_group.tensor_nchw
    if tensor is None:
        return None
    batch = len(tensor)
    channels = len(tensor[0]) if batch > 0 else 0
    height = len(tensor[0][0]) if channels > 0 else 0
    width = len(tensor[0][0][0]) if height > 0 else 0
    return (batch, channels, height, width)


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
