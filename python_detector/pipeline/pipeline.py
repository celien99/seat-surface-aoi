from __future__ import annotations

import time

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.models.inference_engine import InferenceEngine, ModelRegistry
from python_detector.pipeline.feature_builder import FeatureBuilder
from python_detector.pipeline.fusion_engine import FusionEngine
from python_detector.pipeline.preprocessor import Preprocessor
from python_detector.pipeline.quality_gate import ImageQualityGate
from python_detector.pipeline.reflectance_cube import ReflectanceCubeBuilder
from python_detector.pipeline.rule_engine import RuleEngine


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
            fused = self.fusion_engine.fuse(candidates)
            timings["fusion_ms"] = (time.perf_counter() - step_started) * 1000.0
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            self.last_context = {
                "quality_report": quality_report,
                "registration_reports": [cube.registration for cube in cubes],
                "feature_summary": [
                    {
                        "camera_id": group.camera_id,
                        "roi_name": group.roi_name,
                        "model_key": group.model_key,
                        "feature_names": sorted(group.features),
                    }
                    for group in features
                ],
                "timings": timings,
            }
            return self.rule_engine.decide(job, fused, quality_report, recipe, elapsed_ms)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            timings["total_ms"] = elapsed_ms
            self.last_context = {"timings": timings}
            return self.rule_engine.make_error_result(job, ErrorCode.INTERNAL_ERROR, elapsed_ms)
