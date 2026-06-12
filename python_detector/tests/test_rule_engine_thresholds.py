from dataclasses import replace

from python_detector.config.recipe_schema import RecipeManager, ThresholdConfig
from python_detector.ipc.data_types import SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.models.inference_engine import DefectCandidate
from python_detector.pipeline.fusion_engine import FusedResult
from python_detector.pipeline.quality_gate import QualityReport
from python_detector.pipeline.rule_engine import RuleEngine


def test_rule_engine_uses_recipe_thresholds() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, thresholds={"scratch": ThresholdConfig(ng_score=0.95, recheck_score=0.2, min_area_px=8)})
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[],
    )
    fused = FusedResult(
        candidates=[
            DefectCandidate(
                camera_id="TOP_BACK",
                roi_name="full",
                class_name="scratch",
                score=0.88,
                bbox_xyxy_pixel=(1, 1, 8, 8),
                area_px=49,
                evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
            )
        ]
    )
    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)
    assert result.decision == "RECHECK"
    assert result.defects[0].decision == "RECHECK"


def test_rule_engine_ignores_candidates_below_recheck_threshold() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, thresholds={"scratch": ThresholdConfig(ng_score=0.95, recheck_score=0.5, min_area_px=8)})
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[],
    )
    fused = FusedResult(
        candidates=[
            DefectCandidate(
                camera_id="TOP_BACK",
                roi_name="full",
                class_name="scratch",
                score=0.22,
                bbox_xyxy_pixel=(1, 1, 8, 8),
                area_px=49,
                evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
            )
        ]
    )
    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)
    assert result.decision == "OK"
    assert result.defects == []


def test_rule_engine_rechecks_when_fusion_overflow_hides_candidates() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, thresholds={"scratch": ThresholdConfig(ng_score=0.95, recheck_score=0.5, min_area_px=8)})
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[],
    )
    fused = FusedResult(candidates=[], overflow_count=1)

    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)

    assert result.decision == "RECHECK"
    assert result.error_code == ErrorCode.CONFIGURATION_ERROR
    assert result.defects == []
