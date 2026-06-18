from __future__ import annotations

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import DefectResult, InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.pipeline.defect_filter import DefectFilter
from python_detector.pipeline.fusion_engine import FusedResult
from python_detector.pipeline.quality_gate import QualityReport


class RuleEngine:
    def __init__(self, defect_filter: DefectFilter | None = None) -> None:
        self.defect_filter = defect_filter or DefectFilter()

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
        for index, filtered in enumerate(self.defect_filter.filter(fused_result.candidates, recipe)):
            candidate = filtered.candidate
            if filtered.decision == "NG":
                decision = "NG"
            elif filtered.decision == "RECHECK":
                if decision != "NG":
                    decision = "RECHECK"
            defects.append(
                DefectResult(
                    defect_id=f"{job.sequence_id}-{index}",
                    class_name=candidate.class_name,
                    severity=filtered.severity,
                    camera_id=candidate.camera_id,
                    pose_id=candidate.pose_id or candidate.camera_id,
                    roi_name=candidate.roi_name,
                    bbox_xyxy_pixel=candidate.bbox_xyxy_pixel,
                    score=candidate.score,
                    area_px=candidate.area_px,
                    evidence_lights=candidate.evidence_lights,
                    mask_offset=None,
                    decision=filtered.decision,
                )
            )
        if fused_result.overflow_count > 0 and decision == "OK":
            decision = "RECHECK"
            error_code = ErrorCode.CONFIGURATION_ERROR
        else:
            error_code = ErrorCode.NONE

        return InspectionResult(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            decision=decision,
            defects=defects,
            quality_pass=True,
            error_code=error_code,
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

    def make_recheck_result(
        self,
        job: SeatInspectionJob,
        error_code: int,
        elapsed_ms: float = 0.0,
        *,
        quality_pass: bool = False,
    ) -> InspectionResult:
        return InspectionResult(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            decision="RECHECK",
            defects=[],
            quality_pass=quality_pass,
            error_code=error_code,
            elapsed_ms=elapsed_ms,
        )
