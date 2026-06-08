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

    def process(self, job: SeatInspectionJob, recipe: Recipe) -> InspectionResult:
        started = time.perf_counter()
        try:
            quality_report = self.quality_gate.check(job, recipe)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if not quality_report.is_pass:
                return self.rule_engine.make_quality_fail_result(job, quality_report, elapsed_ms)
            prepared = self.preprocessor.run(job, recipe)
            cubes = self.reflectance_cube_builder.build(job, prepared, recipe)
            for cube in cubes:
                if not cube.registration.is_pass:
                    quality_report.is_pass = False
                    quality_report.messages.append(cube.registration.message)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    return self.rule_engine.make_quality_fail_result(job, quality_report, elapsed_ms)
            features = self.feature_builder.build(cubes, recipe)
            candidates = self.inference_engine.infer(features)
            fused = self.fusion_engine.fuse(candidates)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return self.rule_engine.decide(job, fused, quality_report, elapsed_ms)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return self.rule_engine.make_error_result(job, ErrorCode.INTERNAL_ERROR, elapsed_ms)
