import pytest

from python_detector.config.recipe_schema import FusionConfig
from python_detector.models.inference_engine import DefectCandidate
from python_detector.pipeline.fusion_engine import FusionEngine


def _candidate(
    score: float,
    bbox_xyxy_pixel: tuple[int, int, int, int],
    camera_id: str = "TOP_BACK",
    evidence_lights: list[str] | None = None,
) -> DefectCandidate:
    x0, y0, x1, y1 = bbox_xyxy_pixel
    return DefectCandidate(
        camera_id=camera_id,
        roi_name="seat",
        score=score,
        bbox_xyxy_pixel=bbox_xyxy_pixel,
        area_px=(x1 - x0 + 1) * (y1 - y0 + 1),
        evidence_lights=["HIGH_LEFT"] if evidence_lights is None else evidence_lights,
    )


def test_fusion_suppresses_overlapping_same_roi_candidates() -> None:
    candidates = [
        _candidate(0.80, (10, 10, 30, 30), evidence_lights=["HIGH_LEFT"]),
        _candidate(0.90, (11, 11, 29, 29), evidence_lights=["HIGH_RIGHT"]),
        _candidate(0.70, (40, 40, 50, 50), evidence_lights=["DIFFUSE"]),
    ]

    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))

    assert fused.suppressed_count == 1
    assert len(fused.candidates) == 2
    best = next(candidate for candidate in fused.candidates if candidate.score == 0.90)
    # 合并框取外包矩形：(min(10,11), min(10,11), max(30,29), max(30,29)) = (10,10,30,30)
    assert best.bbox_xyxy_pixel == (10, 10, 30, 30)
    assert best.area_px == 21 * 21
    assert best.evidence_lights == ["HIGH_RIGHT", "HIGH_LEFT"]


def test_fusion_suppresses_same_roi_and_keeps_different_camera_candidates() -> None:
    candidates = [
        _candidate(0.90, (10, 10, 30, 30), camera_id="TOP_BACK"),
        _candidate(0.85, (11, 11, 29, 29), camera_id="TOP_BACK"),
        _candidate(0.80, (11, 11, 29, 29), camera_id="TOP_CUSHION"),
    ]

    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))

    assert fused.suppressed_count == 1
    assert len(fused.candidates) == 2
    assert {candidate.camera_id for candidate in fused.candidates} == {"TOP_BACK", "TOP_CUSHION"}


def test_fusion_caps_candidates_per_roi() -> None:
    candidates = [_candidate(0.90 - index * 0.01, (index * 10, 0, index * 10 + 4, 4)) for index in range(4)]

    fused = FusionEngine().fuse(candidates, FusionConfig(max_candidates_per_roi=2))

    assert fused.suppressed_count == 0
    assert fused.overflow_count == 2
    assert len(fused.candidates) == 2


@pytest.mark.parametrize("score", [float("nan"), -0.1, 1.1])
def test_fusion_rejects_invalid_candidate_score(score: float) -> None:
    with pytest.raises(ValueError, match="invalid candidate score"):
        FusionEngine().fuse([_candidate(score, (10, 10, 30, 30))])


def test_fusion_rejects_empty_candidate_evidence_lights() -> None:
    with pytest.raises(ValueError, match="candidate evidence_lights is empty"):
        FusionEngine().fuse([_candidate(0.8, (10, 10, 30, 30), evidence_lights=[])])


# ── 合并框取外包矩形边界测试 ──

def test_merge_union_bbox_larger_secondary():
    """低分候选框范围更大时，合并框应为两者的外包矩形。"""
    candidates = [
        _candidate(0.90, (20, 20, 30, 30), evidence_lights=["DIFFUSE"]),       # 高分小框
        _candidate(0.70, (18, 18, 32, 32), evidence_lights=["POLAR_DIFFUSE"]),  # 低分大框（IoU≈0.54）
    ]
    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))
    assert fused.suppressed_count == 1
    assert len(fused.candidates) == 1
    merged = fused.candidates[0]
    assert merged.score == 0.90
    assert merged.bbox_xyxy_pixel == (18, 18, 32, 32)  # 外包矩形
    assert merged.area_px == 15 * 15
    assert set(merged.evidence_lights) == {"DIFFUSE", "POLAR_DIFFUSE"}


def test_merge_union_bbox_primary_larger():
    """高分候选框范围更大时，合并框正确。"""
    candidates = [
        _candidate(0.90, (18, 18, 32, 32), evidence_lights=["DIFFUSE"]),
        _candidate(0.70, (20, 20, 30, 30), evidence_lights=["POLAR_DIFFUSE"]),
    ]
    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))
    assert fused.suppressed_count == 1
    assert len(fused.candidates) == 1
    merged = fused.candidates[0]
    assert merged.score == 0.90
    assert merged.bbox_xyxy_pixel == (18, 18, 32, 32)


def test_merge_union_partial_overlap():
    """部分重叠：两个候选各延伸不同方向。"""
    candidates = [
        _candidate(0.88, (0, 0, 25, 22), evidence_lights=["DIFFUSE"]),        # 偏左上
        _candidate(0.75, (10, 8, 35, 28), evidence_lights=["HIGH_LEFT"]),      # 偏右下（IoU≈0.27）
    ]
    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.25))
    assert fused.suppressed_count == 1
    merged = fused.candidates[0]
    assert merged.bbox_xyxy_pixel == (0, 0, 35, 28)  # 外包矩形


def test_merge_same_bbox_identity():
    """两框完全相同时，合并结果为相同 bbox。"""
    candidates = [
        _candidate(0.90, (10, 10, 30, 30), evidence_lights=["DIFFUSE"]),
        _candidate(0.85, (10, 10, 30, 30), evidence_lights=["POLAR_DIFFUSE"]),
    ]
    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))
    assert fused.suppressed_count == 1
    merged = fused.candidates[0]
    assert merged.bbox_xyxy_pixel == (10, 10, 30, 30)
    assert merged.area_px == 21 * 21


def test_merge_no_overlap_keeps_both():
    """完全不重叠时两个候选都保留。"""
    candidates = [
        _candidate(0.88, (0, 0, 10, 10)),
        _candidate(0.75, (50, 50, 60, 60)),
    ]
    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))
    assert fused.suppressed_count == 0
    assert len(fused.candidates) == 2


def test_fusion_rejects_reversed_bbox():
    """x1 < x0 的无效 bbox 应在校验阶段抛出异常。"""
    with pytest.raises(ValueError, match="invalid candidate bbox"):
        FusionEngine().fuse([_candidate(0.8, (30, 10, 10, 30))])
