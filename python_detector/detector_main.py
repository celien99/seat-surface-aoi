from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from python_detector.algorithm import SeatSurfaceAoiAlgorithm
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    DEFAULT_FRAME_SLOT_SIZE,
    DEFAULT_RESULT_SLOT_SIZE,
    DEFAULT_SLOT_COUNT,
)


def _load_runtime_ipc_layout(config_path: str | None) -> tuple[int, int, int]:
    slot_count = DEFAULT_SLOT_COUNT
    frame_slot_size = DEFAULT_FRAME_SLOT_SIZE
    result_slot_size = DEFAULT_RESULT_SLOT_SIZE
    if not config_path:
        return slot_count, frame_slot_size, result_slot_size
    path = Path(config_path)
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key == "slot_count":
            slot_count = int(value)
        elif key == "frame_slot_size":
            frame_slot_size = int(value)
        elif key == "result_slot_size":
            result_slot_size = int(value)
    return slot_count, frame_slot_size, result_slot_size


class DetectorProcess:
    def __init__(
        self,
        *,
        slot_count: int = DEFAULT_SLOT_COUNT,
        frame_slot_size: int = DEFAULT_FRAME_SLOT_SIZE,
        result_slot_size: int = DEFAULT_RESULT_SLOT_SIZE,
    ) -> None:
        self.shm_client: ShmClient | None = None
        self.algorithm = SeatSurfaceAoiAlgorithm()
        self.slot_count = slot_count
        self.frame_slot_size = frame_slot_size
        self.result_slot_size = result_slot_size

    def initialize(self) -> None:
        self.shm_client = ShmClient(
            slot_count=self.slot_count,
            frame_slot_size=self.frame_slot_size,
            result_slot_size=self.result_slot_size,
        )

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
        result = self.algorithm.process(job).result
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
    parser.add_argument("--config", default="", help="读取 C++ 运行配置中的共享内存 slot 参数")
    parser.add_argument("--slot-count", type=int, default=0, help="覆盖共享内存 slot_count")
    parser.add_argument("--frame-slot-size", type=int, default=0, help="覆盖 frame_slot_size")
    parser.add_argument("--result-slot-size", type=int, default=0, help="覆盖 result_slot_size")
    args = parser.parse_args(argv)

    slot_count, frame_slot_size, result_slot_size = _load_runtime_ipc_layout(args.config or None)
    if args.slot_count > 0:
        slot_count = args.slot_count
    if args.frame_slot_size > 0:
        frame_slot_size = args.frame_slot_size
    if args.result_slot_size > 0:
        result_slot_size = args.result_slot_size

    process = DetectorProcess(
        slot_count=slot_count,
        frame_slot_size=frame_slot_size,
        result_slot_size=result_slot_size,
    )
    process.initialize()
    if args.once:
        return 0 if process.run_once(args.timeout_ms) else 2
    process.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
