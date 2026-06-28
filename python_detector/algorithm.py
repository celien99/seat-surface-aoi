from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import Recipe, RecipeManager
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter


@dataclass(frozen=True)
class AlgorithmRun:
    result: InspectionResult
    context: dict[str, Any]
    trace_dir: Path | None


class SeatSurfaceAoiAlgorithm:
    """不包含 IPC 控制逻辑的座椅表面 AOI 检测算法模块。"""

    def __init__(
        self,
        recipe_manager: RecipeManager | None = None,
        pipeline: InspectionPipeline | None = None,
        trace_writer: TraceWriter | None = None,
    ) -> None:
        self.recipe_manager = recipe_manager or RecipeManager()
        self.pipeline = pipeline or InspectionPipeline()
        self.trace_writer = trace_writer or TraceWriter()

    def process(self, job: SeatInspectionJob, recipe_id: str | None = None, write_trace: bool = True) -> AlgorithmRun:
        recipe: Recipe | None = None
        trace_dir: Path | None = None
        try:
            recipe = self.recipe_manager.load(recipe_id or job.recipe_id)
            result = self.pipeline.process(job, recipe)
        except Exception as exc:
            self.pipeline.last_context = {
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            }
            result = InspectionResult(
                sequence_id=job.sequence_id,
                trigger_id=job.trigger_id,
                seat_id=job.seat_id,
                decision="ERROR",
                defects=[],
                quality_pass=False,
                error_code=ErrorCode.INTERNAL_ERROR,
                elapsed_ms=0.0,
            )

        if write_trace and recipe is not None:
            try:
                self.trace_writer.root_dir = Path(recipe.trace.root_dir)
                trace_dir = self.trace_writer.write(job, recipe, result, self.pipeline.last_context)
            except Exception as exc:
                trace_error = {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
                self.pipeline.last_context.setdefault("trace_error", trace_error)
                result = InspectionResult(
                    sequence_id=job.sequence_id,
                    trigger_id=job.trigger_id,
                    seat_id=job.seat_id,
                    decision="RECHECK",
                    defects=result.defects,
                    quality_pass=False,
                    error_code=ErrorCode.DEVICE_FAULT,
                    elapsed_ms=result.elapsed_ms,
                )
        return AlgorithmRun(result=result, context=self.pipeline.last_context, trace_dir=trace_dir)
