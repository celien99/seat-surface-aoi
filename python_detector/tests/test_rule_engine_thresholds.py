from dataclasses import replace

import pytest

from python_detector.config.recipe_schema import DecisionThresholdConfig, RecipeManager
from python_detector.ipc.data_types import SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.models.inference_engine import DefectCandidate
from python_detector.pipeline.defect_filter import DefectFilter
from python_detector.pipeline.fusion_engine import FusedResult
from python_detector.pipeline.quality_gate import FrameQuality, QualityReport
from python_detector.pipeline.rule_engine import RuleEngine


def _job(recipe) -> SeatInspectionJob:
    return SeatInspectionJob(
        sequence_id=1, trigger_id=2, seat_id="SIM",
        recipe_id=recipe.recipe_id, sku=recipe.sku, camera_bundles=[],
    )


def _candidate(score, bbox, area=None, lights=None) -> DefectCandidate:
    x0, y0, x1, y1 = bbox
    return DefectCandidate(
        camera_id="CAM", roi_name="R", score=score,
        bbox_xyxy_pixel=bbox,
        area_px=area or (x1 - x0 + 1) * (y1 - y0 + 1),
        evidence_lights=lights or ["L1"],
    )


# ── 判定阈值 ──

def test_rule_engine_uses_recipe_thresholds():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, decision_threshold=DecisionThresholdConfig(ng_score=0.95, recheck_score=0.2, min_area_px=8))
    job = _job(recipe)
    fused = FusedResult(candidates=[_candidate(0.88, (1, 1, 8, 8), area=64)])
    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)
    assert result.decision == "RECHECK"


def test_rule_engine_ignores_below_recheck():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, decision_threshold=DecisionThresholdConfig(ng_score=0.95, recheck_score=0.5, min_area_px=8))
    job = _job(recipe)
    fused = FusedResult(candidates=[_candidate(0.22, (1, 1, 8, 8), area=64)])
    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)
    assert result.decision == "OK"


def test_rule_engine_rechecks_on_fusion_overflow():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, decision_threshold=DecisionThresholdConfig(ng_score=0.95, recheck_score=0.5, min_area_px=8))
    job = _job(recipe)
    fused = FusedResult(candidates=[], overflow_count=1)
    result = RuleEngine().decide(job, fused, QualityReport(True, []), recipe, elapsed_ms=1.0)
    assert result.decision == "RECHECK"
    assert result.error_code == ErrorCode.CONFIGURATION_ERROR


# ── 质量失败 / 错误 / RECHECK 辅助方法 ──

def test_quality_fail_produces_recheck():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(recipe)
    fail_report = QualityReport(False, [FrameQuality("CAM", "L1", 0, 0, 0, 0, 0, False, ["fail"])])
    result = RuleEngine().decide(job, FusedResult([]), fail_report, recipe, elapsed_ms=1.0)
    assert result.decision == "RECHECK"
    assert result.error_code == ErrorCode.QUALITY_FAILED


def test_make_error_result():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(recipe)
    result = RuleEngine().make_error_result(job, ErrorCode.INTERNAL_ERROR, elapsed_ms=5.0)
    assert result.decision == "ERROR"
    assert result.error_code == ErrorCode.INTERNAL_ERROR
    assert not result.quality_pass


def test_make_recheck_result():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(recipe)
    result = RuleEngine().make_recheck_result(job, ErrorCode.QUALITY_FAILED, elapsed_ms=3.0)
    assert result.decision == "RECHECK"
    assert result.error_code == ErrorCode.QUALITY_FAILED


def test_make_quality_fail_result():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(recipe)
    result = RuleEngine().make_quality_fail_result(job, QualityReport(False, []), elapsed_ms=0.5)
    assert result.decision == "RECHECK"
    assert result.error_code == ErrorCode.QUALITY_FAILED
    assert not result.quality_pass


# ── 长宽比过滤 ──

@pytest.mark.parametrize(
    "bbox,min_ar,max_ar,expected_pass",
    [
        ((0, 0, 20, 10), 0.1, 10.0, True),     # ar=2.1, OK
        ((0, 0, 20, 10), 0.1, 1.5, False),      # ar=2.1 > 1.5 → fail
        ((0, 0, 2, 50), 0.1, 10.0, False),      # ar=0.06 < 0.1 → fail
        ((0, 0, 10, 10), 0.5, 2.0, True),        # ar=1.0, OK
        ((0, 0, 100, 1), 0.02, 50.0, False),     # ar=101, > 50 → fail
    ],
)
def test_aspect_ratio_filter(bbox, min_ar, max_ar, expected_pass):
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        decision_threshold=replace(
            recipe.decision_threshold,
            ng_score=0.5, recheck_score=0.2,
            min_aspect_ratio=min_ar, max_aspect_ratio=max_ar,
        ),
    )
    candidates = [_candidate(0.6, bbox)]
    result = DefectFilter().filter(candidates, recipe)
    if expected_pass:
        assert len(result) == 1
    else:
        # 不通过：要么被过滤掉，要么降级为 RECHECK
        assert len(result) == 0 or all(r.decision == "RECHECK" for r in result)


def test_ng_with_bad_aspect_downgrades():
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        decision_threshold=replace(
            recipe.decision_threshold,
            ng_score=0.5, recheck_score=0.2, min_aspect_ratio=0.5, max_aspect_ratio=2.0,
        ),
    )
    candidate = _candidate(0.95, (0, 0, 100, 1))
    result = DefectFilter().filter([candidate], recipe)
    assert len(result) == 1
    assert result[0].decision == "RECHECK"
