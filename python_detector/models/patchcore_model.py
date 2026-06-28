"""PatchCore 无监督异常检测模型后端。

从 inference_engine.py 抽取以控制文件规模。
支持全局嵌入（标量异常分数）和空间嵌入（像素级 anomaly_map）两种模式。
"""

from __future__ import annotations

from python_detector.config.recipe_schema import ModelConfig
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.embedding import EmbeddingExtractor
from python_detector.models.patchcore import PatchCoreKnnIndex
from python_detector.models.pca import PcaProjector
from python_detector.models.spatial_utils import (
    DefectCandidate,
    _anomaly_map_bboxes,
    _as_anomaly_map_array,
    _map_roi_bbox_to_source,
)
from python_detector.pipeline.feature_builder import FeatureGroup


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
        if self.config.spatial_mode and self.config.spatial_layers:
            return self._run_spatial(feature_group)
        return self._run_global(feature_group)

    def _run_global(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        """全局嵌入路径：整个 ROI → 1 个向量 → 标量异常分数（保持向后兼容）。"""
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
            self.config.pca_version,
            self.config.faiss_index_path,
        )
        feature_group.anomaly_summary = {
            "model_family": self.config.model_family,
            "memory_bank_version": score.version,
            "backend": score.backend,
            "faiss_index_path": score.faiss_index_path,
            "fallback_reason": score.fallback_reason,
            "score_threshold": float(self.config.score_threshold),
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
        return [
            DefectCandidate(
                camera_id=feature_group.camera_id,
                pose_id=feature_group.pose_id,
                roi_name=feature_group.roi_name,
                score=score.anomaly_score,
                bbox_xyxy_pixel=bbox,
                area_px=area_px,
                evidence_lights=feature_group.evidence_lights(),
            )
        ]

    def _run_spatial(self, feature_group: FeatureGroup) -> list[DefectCandidate]:
        """空间 PatchCore 路径：逐 patch KNN 评分 → anomaly_map → 连通域 bbox。"""
        spatial = self.embedding_extractor.extract_spatial(feature_group, self.config)
        feature_group.embedding_summary = {
            "backend": spatial.backend,
            "version": spatial.version,
            "embedding_dim": spatial.patch_dim,
            "layer_names": list(spatial.layer_names),
            "spatial_shape": list(spatial.spatial_shape),
            "layer_shapes": {k: list(v) for k, v in spatial.layer_shapes.items()},
            "input_shape_nchw": list(spatial.input_shape_nchw) if spatial.input_shape_nchw is not None else None,
        }
        patch_embeddings = spatial.patch_embeddings

        if self.config.pca_path:
            patch_embeddings, pca_version, pca_input_dim, pca_output_dim = self.pca_projector.project_batch(
                patch_embeddings, self.config.pca_path, self.config.pca_version
            )
            feature_group.pca_summary = {
                "version": pca_version,
                "input_dim": pca_input_dim,
                "output_dim": pca_output_dim,
            }

        score = self.knn_index.score_spatial(
            patch_embeddings,
            spatial.spatial_shape,
            self.config.memory_bank_path,
            self.config.knn_k,
            self.config.pca_version,
            self.config.faiss_index_path,
        )
        anomaly_map = _as_anomaly_map_array(score.anomaly_map)
        nearest_distances = _as_anomaly_map_array(score.nearest_distances, name="nearest_distances")
        spatial_shape = (int(score.spatial_shape[0]), int(score.spatial_shape[1]))
        if anomaly_map.shape != spatial_shape:
            raise RuntimeError(f"PatchCore anomaly_map shape 必须与 spatial_shape 一致: {anomaly_map.shape} != {spatial_shape}")
        if nearest_distances.shape != anomaly_map.shape:
            raise RuntimeError(
                f"PatchCore nearest_distances shape 必须与 anomaly_map 一致: {nearest_distances.shape} != {anomaly_map.shape}"
            )
        max_anomaly = float(anomaly_map.max())
        feature_group.anomaly_summary = {
            "model_family": self.config.model_family,
            "memory_bank_version": score.version,
            "backend": score.backend,
            "faiss_index_path": score.faiss_index_path,
            "fallback_reason": score.fallback_reason,
            "spatial_mode": True,
            "spatial_shape": list(score.spatial_shape),
            "anomaly_map": anomaly_map,
            "nearest_distances": nearest_distances,
            "memory_bank_size": score.memory_bank_size,
            "embedding_dim": score.embedding_dim,
            "max_anomaly": max_anomaly,
            "score_threshold": float(self.config.score_threshold),
            "anomaly_binarize_min_ratio": float(self.config.anomaly_binarize_min_ratio),
            "anomaly_binarize_relative": float(self.config.anomaly_binarize_relative),
        }

        if max_anomaly < self.config.score_threshold:
            return []

        bboxes = _anomaly_map_bboxes(
            anomaly_map,
            score.spatial_shape,
            self.config.score_threshold,
            feature_group,
            binarize_min_ratio=self.config.anomaly_binarize_min_ratio,
            binarize_relative=self.config.anomaly_binarize_relative,
        )
        if not bboxes:
            return []

        candidates: list[DefectCandidate] = []
        for bbox_xyxy, bbox_score in bboxes:
            area_px = max(bbox_xyxy[2] - bbox_xyxy[0] + 1, 0) * max(bbox_xyxy[3] - bbox_xyxy[1] + 1, 0)
            if area_px <= 0:
                continue
            candidates.append(
                DefectCandidate(
                    camera_id=feature_group.camera_id,
                    pose_id=feature_group.pose_id,
                    roi_name=feature_group.roi_name,
                    score=bbox_score,
                    bbox_xyxy_pixel=bbox_xyxy,
                    area_px=area_px,
                    evidence_lights=feature_group.evidence_lights(),
                )
            )
        return candidates
