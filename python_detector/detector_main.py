from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from python_detector.algorithm import SeatSurfaceAoiAlgorithm
from python_detector.config.calibration_manager import CalibrationManager
from python_detector.config.recipe_schema import RecipeManager
from python_detector.display_channel import DisplayChannelWriter
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    DEFAULT_FRAME_SLOT_SIZE,
    DEFAULT_RESULT_SLOT_SIZE,
    DEFAULT_SLOT_COUNT,
)
from python_detector.paths import resolve_runtime_path
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.preprocessor import Preprocessor


def _resolve_config_path(config_path: str | None) -> Path | None:
    if not config_path:
        return None
    path = Path(config_path)
    if path.is_absolute() or path.exists():
        return path
    return resolve_runtime_path(config_path)


def _load_runtime_config(config_path: str | None) -> tuple[int, int, int, str]:
    slot_count = DEFAULT_SLOT_COUNT
    frame_slot_size = DEFAULT_FRAME_SLOT_SIZE
    result_slot_size = DEFAULT_RESULT_SLOT_SIZE
    trace_root = "trace"
    path = _resolve_config_path(config_path)
    if path is None:
        return slot_count, frame_slot_size, result_slot_size, trace_root
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
        elif key == "trace_root":
            trace_root = value
    return slot_count, frame_slot_size, result_slot_size, trace_root


def _load_runtime_ipc_layout(config_path: str | None) -> tuple[int, int, int]:
    slot_count, frame_slot_size, result_slot_size, _trace_root = _load_runtime_config(config_path)
    return slot_count, frame_slot_size, result_slot_size


def validate_detector_config(config_path: str | None, recipe_dir: str | Path | None = None) -> int:
    _slot_count, _frame_slot_size, _result_slot_size, trace_root = _load_runtime_config(config_path)
    recipe_manager = RecipeManager(recipe_dir) if recipe_dir is not None else RecipeManager()
    calibration_manager = CalibrationManager(recipe_dir) if recipe_dir is not None else CalibrationManager()
    recipes = recipe_manager.all_recipes()
    if not recipes:
        raise RuntimeError(f"配方目录为空: {recipe_manager.recipe_dir}")
    for recipe in recipes:
        for camera_recipe in recipe.cameras:
            calibration_manager.load(
                camera_recipe.camera_id,
                camera_recipe.calibration_id,
                camera_recipe.roi_template,
            )
    print(
        f"detector_config_valid recipe_dir={recipe_manager.recipe_dir} "
        f"recipes={len(recipes)} config={config_path or '<default>'} trace_root={trace_root}",
        flush=True,
    )
    return 0


class DetectorProcess:
    def __init__(
        self,
        *,
        slot_count: int = DEFAULT_SLOT_COUNT,
        frame_slot_size: int = DEFAULT_FRAME_SLOT_SIZE,
        result_slot_size: int = DEFAULT_RESULT_SLOT_SIZE,
        display_root: str | Path = "trace",
        enable_display_channel: bool = True,
        recipe_dir: str | Path | None = None,
        trace_root_override: str | Path | None = None,
    ) -> None:
        self.shm_client: ShmClient | None = None
        recipe_manager = RecipeManager(recipe_dir) if recipe_dir is not None else RecipeManager()
        pipeline = None
        if recipe_dir is not None:
            pipeline = InspectionPipeline(
                preprocessor=Preprocessor(
                    calibration_manager=CalibrationManager(recipe_dir),
                )
            )
        self.algorithm = SeatSurfaceAoiAlgorithm(
            recipe_manager=recipe_manager,
            pipeline=pipeline,
            trace_root_override=trace_root_override,
        )
        self.display_channel = DisplayChannelWriter(display_root) if enable_display_channel else None
        self.slot_count = slot_count
        self.frame_slot_size = frame_slot_size
        self.result_slot_size = result_slot_size

    def __enter__(self) -> "DetectorProcess":
        self.initialize()
        return self

    def __exit__(self, *_: object) -> None:
        self.shutdown()

    def initialize(self) -> None:
        self.shm_client = ShmClient(
            slot_count=self.slot_count,
            frame_slot_size=self.frame_slot_size,
            result_slot_size=self.result_slot_size,
        )

    def shutdown(self) -> None:
        """释放共享内存映射等系统资源。可重复调用，多次调用无副作用。"""
        if self.shm_client is not None:
            try:
                self.shm_client.close()
            except Exception:
                pass
            finally:
                self.shm_client = None

    def run_forever(self) -> None:
        if self.shm_client is None:
            raise RuntimeError("检测进程尚未初始化")
        while True:
            job = self.shm_client.wait_next_job(timeout_ms=100)
            if job is None:
                # 空闲时短暂休眠，避免在 C++ 无触发期间持续 500Hz 轮询消耗 CPU；
                # 最坏情况增加 ~10ms 响应延迟，对秒级触发节拍无影响。
                time.sleep(0.01)
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
        run = self.algorithm.process(job)
        result = run.result
        try:
            self.shm_client.publish_result(result)
        except Exception:
            self.shm_client.release_frame_slot(job.sequence_id)
            raise
        if self.display_channel is not None:
            try:
                self.display_channel.write(job, run)
            except Exception as exc:
                print(
                    f"display_channel_write_failed sequence_id={result.sequence_id} "
                    f"trigger_id={result.trigger_id} error={exc}",
                    file=sys.stderr,
                    flush=True,
                )
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="座椅 AOI Python 检测进程")
    parser.add_argument("--once", action="store_true", help="处理一个任务后退出")
    parser.add_argument("--timeout-ms", type=int, default=5000)
    parser.add_argument("--config", default="", help="读取 C++ 运行配置中的共享内存 slot 参数")
    parser.add_argument("--slot-count", type=int, default=0, help="覆盖共享内存 slot_count")
    parser.add_argument("--frame-slot-size", type=int, default=0, help="覆盖 frame_slot_size")
    parser.add_argument("--result-slot-size", type=int, default=0, help="覆盖 result_slot_size")
    parser.add_argument("--display-root", default="", help="PySide6/QML 展示通道输出目录，默认使用 C++ 配置 trace_root")
    parser.add_argument("--disable-display-channel", action="store_true", help="关闭展示通道 JSON 输出")
    parser.add_argument("--recipe-dir", default="", help="配方 YAML 目录，默认使用包内 python_detector/config")
    parser.add_argument("--validate-config-only", action="store_true", help="只校验运行配置、配方、标定和 ROI 后退出")
    args = parser.parse_args(argv)

    if args.validate_config_only:
        return validate_detector_config(args.config or None, args.recipe_dir or None)

    slot_count, frame_slot_size, result_slot_size, trace_root = _load_runtime_config(args.config or None)
    if args.slot_count > 0:
        slot_count = args.slot_count
    if args.frame_slot_size > 0:
        frame_slot_size = args.frame_slot_size
    if args.result_slot_size > 0:
        result_slot_size = args.result_slot_size
    display_root = args.display_root or trace_root

    process = DetectorProcess(
        slot_count=slot_count,
        frame_slot_size=frame_slot_size,
        result_slot_size=result_slot_size,
        display_root=display_root,
        enable_display_channel=not args.disable_display_channel,
        recipe_dir=args.recipe_dir or None,
        trace_root_override=trace_root,
    )
    process.initialize()
    if args.once:
        return 0 if process.run_once(args.timeout_ms) else 2
    process.run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
