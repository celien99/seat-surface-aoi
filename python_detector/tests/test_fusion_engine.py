import pytest

from python_detector.config.recipe_schema import FusionConfig
from python_detector.models.inference_engine import DefectCandidate
from python_detector.pipeline.fusion_engine import FusionEngine


def _candidate(
    score: float,
    bbox_xyxy_pixel: tuple[int, int, int, int],
    class_name: str = "scratch",
    camera_id: str = "TOP_BACK",
    evidence_lights: list[str] | None = None,
) -> DefectCandidate:
    x0, y0, x1, y1 = bbox_xyxy_pixel
    return DefectCandidate(
        camera_id=camera_id,
        roi_name="seat",
        class_name=class_name,
        score=score,
        bbox_xyxy_pixel=bbox_xyxy_pixel,
        area_px=(x1 - x0 + 1) * (y1 - y0 + 1),
        evidence_lights=["HIGH_LEFT"] if evidence_lights is None else evidence_lights,
    )


def test_fusion_suppresses_overlapping_same_class_candidates() -> None:
    candidates = [
        _candidate(0.80, (10, 10, 30, 30), evidence_lights=["HIGH_LEFT"]),
        _candidate(0.90, (11, 11, 29, 29), evidence_lights=["HIGH_RIGHT"]),
        _candidate(0.70, (40, 40, 50, 50), evidence_lights=["DIFFUSE"]),
    ]

    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5))

    assert fused.suppressed_count == 1
    assert len(fused.candidates) == 2
    best = next(candidate for candidate in fused.candidates if candidate.score == 0.90)
    assert best.bbox_xyxy_pixel == (11, 11, 29, 29)
    assert best.evidence_lights == ["HIGH_RIGHT", "HIGH_LEFT"]


def test_fusion_keeps_different_class_or_camera_candidates() -> None:
    candidates = [
        _candidate(0.90, (10, 10, 30, 30), class_name="scratch", camera_id="TOP_BACK"),
        _candidate(0.85, (11, 11, 29, 29), class_name="dent", camera_id="TOP_BACK"),
        _candidate(0.80, (11, 11, 29, 29), class_name="scratch", camera_id="TOP_CUSHION"),
    ]

    fused = FusionEngine().fuse(candidates, FusionConfig(iou_threshold=0.5, class_aware=True))

    assert fused.suppressed_count == 0
    assert len(fused.candidates) == 3


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
