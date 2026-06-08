from __future__ import annotations

from python_detector.config.recipe_schema import Recipe, ThresholdConfig
from python_detector.ipc.data_types import DefectResult, InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.pipeline.fusion_engine import FusedResult
from python_detector.pipeline.quality_gate import QualityReport


class RuleEngine:
    def decide(
        self,
        job: SeatInspectionJob,
        fused_result: FusedResult,
        quality_report: QualityReport,
        recipe: Recipe,
        elapsed_ms: float,
    ) -> InspectionResult:
        if not quality_report.is_pass:
            return self.make_quality_fail_result(job, quality_report, elapsed_ms)

        defects: list[DefectResult] = []
        decision = "OK"
        for index, candidate in enumerate(fused_result.candidates):
            threshold = recipe.thresholds.get(candidate.class_name, ThresholdConfig())
            if candidate.score >= threshold.ng_score and candidate.area_px >= threshold.min_area_px:
                defect_decision = "NG"
                decision = "NG"
            else:
                defect_decision = "RECHECK"
                if decision != "NG":
                    decision = "RECHECK"
            defects.append(
                DefectResult(
                    defect_id=f"{job.sequence_id}-{index}",
                    class_name=candidate.class_name,
                    severity="suspect" if defect_decision == "RECHECK" else "critical",
                    camera_id=candidate.camera_id,
                    roi_name=candidate.roi_name,
                    bbox_xyxy_pixel=candidate.bbox_xyxy_pixel,
                    score=candidate.score,
                    area_px=candidate.area_px,
                    evidence_lights=candidate.evidence_lights,
                    mask_offset=None,
                    decision=defect_decision,
                )
            )

        return InspectionResult(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            decision=decision,
            defects=defects,
            quality_pass=True,
            error_code=ErrorCode.NONE,
            elapsed_ms=elapsed_ms,
        )

    def make_quality_fail_result(
        self,
        job: SeatInspectionJob,
        quality_report: QualityReport,
        elapsed_ms: float = 0.0,
    ) -> InspectionResult:
        return InspectionResult(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            decision="RECHECK",
            defects=[],
            quality_pass=False,
            error_code=ErrorCode.QUALITY_FAILED,
            elapsed_ms=elapsed_ms,
        )

    def make_error_result(self, job: SeatInspectionJob, error_code: int, elapsed_ms: float = 0.0) -> InspectionResult:
        return InspectionResult(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            decision="ERROR",
            defects=[],
            quality_pass=False,
            error_code=error_code,
            elapsed_ms=elapsed_ms,
        )
