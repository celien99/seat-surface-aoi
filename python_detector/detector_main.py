from __future__ import annotations

import argparse
import sys
import time

from python_detector.config.recipe_schema import RecipeManager
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import ErrorCode
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter


class DetectorProcess:
    def __init__(self) -> None:
        self.shm_client: ShmClient | None = None
        self.recipe_manager = RecipeManager()
        self.pipeline = InspectionPipeline()
        self.trace_writer = TraceWriter()

    def initialize(self) -> None:
        self.shm_client = ShmClient()

    def run_forever(self) -> None:
        if self.shm_client is None:
            raise RuntimeError("检测进程尚未初始化")
        while True:
            job = self.shm_client.wait_next_job(timeout_ms=100)
            if job is None:
                continue
            self._process_and_publish(job)

    def run_once(self, timeout_ms: int = 5000) -> bool:
        if self.shm_client is None:
            raise RuntimeError("检测进程尚未初始化")
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            job = self.shm_client.wait_next_job(timeout_ms=100)
            if job is None:
                continue
            result = self._process_and_publish(job)
            print(
                f"processed sequence_id={result.sequence_id} trigger_id={result.trigger_id} "
                f"decision={result.decision} quality_pass={result.quality_pass} "
                f"error_code={result.error_code} elapsed_ms={result.elapsed_ms:.2f}",
                flush=True,
            )
            return True
        return False

    def _process_and_publish(self, job: SeatInspectionJob) -> InspectionResult:
        if self.shm_client is None:
            raise RuntimeError("检测进程尚未初始化")
        recipe = None
        try:
            recipe = self.recipe_manager.load(job.recipe_id)
            result = self.pipeline.process(job, recipe)
        except Exception as exc:
            self.pipeline.last_context = {
                "error": {
                    "type": exc.__class__.__name__,
                    "message": str(exc),
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

        if recipe is not None:
            try:
                self.trace_writer.root_dir = self.trace_writer.root_dir.__class__(recipe.trace.root_dir)
                self.trace_writer.write(job, recipe, result, self.pipeline.last_context)
            except Exception:
                pass
        try:
            self.shm_client.publish_result(result)
        except Exception:
            self.shm_client.release_frame_slot(job.sequence_id)
            raise
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="座椅 AOI Python 检测进程")
    parser.add_argument("--once", action="store_true", help="处理一个任务后退出")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    args = parser.parse_args(argv)

    process = DetectorProcess()
    process.initialize()
    if args.once:
        return 0 if process.run_once(args.timeout_ms) else 2
    process.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
