from __future__ import annotations

import time

import numpy as np

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.inference_engine import (
    InferenceEngine,
    ModelAssetUnavailableInferenceError,
    ModelInferenceError,
    ModelRegistry,
)
from python_detector.pipeline.feature_builder import FeatureBuilder
from python_detector.pipeline.fusion_engine import FusionEngine
from python_detector.pipeline.preprocessor import PreprocessRecheckError, Preprocessor
from python_detector.pipeline.quality_gate import ImageQualityGate
from python_detector.pipeline.reflectance_cube import ReflectanceCubeBuilder
from python_detector.pipeline.rule_engine import RuleEngine


def _sanitize_anomaly_summary(anomaly_summary: dict[str, object] | None) -> dict[str, object] | None:
    """精简 anomaly_summary：移除大数据数组，仅保留统计信息。"""
    if anomaly_summary is None:
        return None
    if not anomaly_summary.get("spatial_mode"):
        return anomaly_summary
    anomaly_map = anomaly_summary.get("anomaly_map")
    sanitized = {k: v for k, v in anomaly_summary.items() if k not in ("anomaly_map", "nearest_distances")}
    if anomaly_map is not None:
        flat = np.asarray(anomaly_map, dtype=np.float64).ravel()
        if flat.size:
            sanitized["anomaly_map_min"] = float(flat.min())
            sanitized["anomaly_map_max"] = float(flat.max())
            sanitized["anomaly_map_mean"] = float(flat.mean())
            sanitized["anomaly_map_pixels"] = int(flat.size)
    return sanitized


class InspectionPipeline:
    def __init__(
        self,
        quality_gate: ImageQualityGate | None = None,
        preprocessor: Preprocessor | None = None,
        reflectance_cube_builder: ReflectanceCubeBuilder | None = None,
        feature_builder: FeatureBuilder | None = None,
        inference_engine: InferenceEngine | None = None,
        fusion_engine: FusionEngine | None = None,
        rule_engine: RuleEngine | None = None,
    ) -> None:
        self.quality_gate = quality_gate or ImageQualityGate()
        self.preprocessor = preprocessor or Preprocessor()
        self.reflectance_cube_builder = reflectance_cube_builder or ReflectanceCubeBuilder()
        self.feature_builder = feature_builder or FeatureBuilder()
        self.inference_engine = inference_engine or InferenceEngine(ModelRegistry())
        self.fusion_engine = fusion_engine or FusionEngine()
        self.rule_engine = rule_engine or RuleEngine()
        self.last_context: dict = {}

    def process(self, job: SeatInspectionJob, recipe: Recipe) -> InspectionResult:
        started = time.perf_counter()
        timings: dict[str, float] = {}
        try:
            step_started = time.perf_counter()
            quality_report = self.quality_gate.check(job, recipe)
            timings["quality_ms"] = (time.perf_counter() - step_started) * 1000.0
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if not quality_report.is_pass:
                self.last_context = {"quality_report": quality_report, "timings": timings}
                return self.rule_engine.make_quality_fail_result(job, quality_report, elapsed_ms)
            step_started = time.perf_counter()
            prepared = self.preprocessor.run(job, recipe)
            timings["preprocess_ms"] = (time.perf_counter() - step_started) * 1000.0
            step_started = time.perf_counter()
            cubes = self.reflectance_cube_builder.build(job, prepared, recipe)
            timings["cube_ms"] = (time.perf_counter() - step_started) * 1000.0
            for cube in cubes:
                if not cube.registration.is_pass:
                    quality_report.is_pass = False
                    quality_report.messages.append(cube.registration.message)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    self.last_context = {
                        "quality_report": quality_report,
                        "prepared_bundles": prepared,
                        "roi_location_reports": [
                            bundle.roi_location_report for bundle in prepared if bundle.roi_location_report is not None
                        ],
                        "registration_reports": [cube.registration for cube in cubes],
                        "timings": timings,
                    }
                    return self.rule_engine.make_quality_fail_result(job, quality_report, elapsed_ms)
            step_started = time.perf_counter()
            features = self.feature_builder.build(cubes, recipe)
            timings["feature_ms"] = (time.perf_counter() - step_started) * 1000.0
            step_started = time.perf_counter()
            candidates = self.inference_engine.infer(features, recipe)
            timings["inference_ms"] = (time.perf_counter() - step_started) * 1000.0
            step_started = time.perf_counter()
            fused = self.fusion_engine.fuse(candidates, recipe.fusion)
            timings["fusion_ms"] = (time.perf_counter() - step_started) * 1000.0
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            self.last_context = {
                "quality_report": quality_report,
                "prepared_bundles": prepared,
                "roi_location_reports": [
                    bundle.roi_location_report for bundle in prepared if bundle.roi_location_report is not None
                ],
                "registration_reports": [cube.registration for cube in cubes],
                "feature_summary": self._feature_summary(features),
                "spatial_maps": self._spatial_maps(features),
                "fusion_summary": {
                    "input_count": len(candidates),
                    "output_count": len(fused.candidates),
                    "suppressed_count": fused.suppressed_count,
                    "overflow_count": fused.overflow_count,
                },
                "timings": timings,
            }
            return self.rule_engine.decide(job, fused, quality_report, recipe, elapsed_ms)
        except ModelAssetUnavailableInferenceError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            return self._make_model_asset_unavailable_result(job, elapsed_ms, timings, exc.context(), locals())
        except ModelInferenceError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            self.last_context = {
                "timings": timings,
                "error": exc.context(),
            }
            return self.rule_engine.make_error_result(job, ErrorCode.INTERNAL_ERROR, elapsed_ms)
        except PreprocessRecheckError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            quality_report = locals().get("quality_report")
            if quality_report is not None:
                quality_report.is_pass = False
                quality_report.messages.append(str(exc))
            self.last_context = {
                "quality_report": quality_report,
                "timings": timings,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
            return self.rule_engine.make_quality_fail_result(job, quality_report, elapsed_ms)
        except ModelAssetUnavailableError as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            error_context = {
                "type": exc.__class__.__name__,
                "message": str(exc),
                "asset_unavailable": True,
                "asset": exc.context(),
            }
            return self._make_model_asset_unavailable_result(job, elapsed_ms, timings, error_context, locals())
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            self.last_context = {
                "timings": timings,
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                },
            }
            return self.rule_engine.make_error_result(job, ErrorCode.INTERNAL_ERROR, elapsed_ms)

    def _make_model_asset_unavailable_result(
        self,
        job: SeatInspectionJob,
        elapsed_ms: float,
        timings: dict[str, float],
        error_context: dict[str, object],
        scope: dict[str, object],
    ) -> InspectionResult:
        quality_report = scope.get("quality_report")
        prepared = scope.get("prepared", [])
        cubes = scope.get("cubes", [])
        self.last_context = {
            "quality_report": quality_report,
            "prepared_bundles": prepared,
            "roi_location_reports": [
                bundle.roi_location_report
                for bundle in prepared
                if getattr(bundle, "roi_location_report", None) is not None
            ],
            "registration_reports": [cube.registration for cube in cubes],
            "feature_summary": self._feature_summary(scope.get("features", [])),
            "timings": timings,
            "error": error_context,
            "sample_collection": {
                "enabled": True,
                "reason": "model_asset_unavailable",
                "decision": "RECHECK",
            },
        }
        return self.rule_engine.make_recheck_result(
            job,
            ErrorCode.CONFIGURATION_ERROR,
            elapsed_ms,
            quality_pass=bool(getattr(quality_report, "is_pass", False)),
        )

    def _feature_summary(self, features: object) -> list[dict[str, object]]:
        return [
            {
                "camera_id": group.camera_id,
                "pose_id": group.pose_id,
                "roi_name": group.roi_name,
                "model_key": group.model_key,
                "feature_names": sorted(group.features),
                "tensor_channel_names": list(group.tensor_channel_names),
                "tensor_shape_nchw": [
                    1,
                    len(group.tensor_channel_names),
                    group.feature_shape_hw[0],
                    group.feature_shape_hw[1],
                ],
                "embedding_summary": group.embedding_summary,
                "pca_summary": group.pca_summary,
                "anomaly_summary": _sanitize_anomaly_summary(group.anomaly_summary),
            }
            for group in features
        ]

    def _spatial_maps(self, features: object) -> list[dict[str, object]]:
        """提取空间 anomaly_map 原始数据，供 trace writer 热力图渲染使用（不入 JSON）。"""
        maps: list[dict[str, object]] = []
        for group in features:
            anomaly_summary = group.anomaly_summary
            if not anomaly_summary or not anomaly_summary.get("spatial_mode"):
                continue
            anomaly_map = anomaly_summary.get("anomaly_map")
            if anomaly_map is None:
                continue
            maps.append({
                "camera_id": group.camera_id,
                "pose_id": group.pose_id,
                "roi_name": group.roi_name,
                "anomaly_map": anomaly_map,
                "spatial_shape": anomaly_summary.get("spatial_shape"),
            })
        return maps
