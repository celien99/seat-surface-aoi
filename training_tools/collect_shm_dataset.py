from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from python_detector.algorithm import SeatSurfaceAoiAlgorithm
from python_detector.config.recipe_schema import Recipe, RecipeManager, TraceConfig
from python_detector.ipc.data_types import InspectionResult, LightFrame, SeatInspectionJob
from python_detector.ipc.shm_client import ShmClient
from python_detector.trace.trace_writer import TraceWriter
from training_tools.collect_trace_dataset import DatasetSample, collect_trace_dataset
from training_tools.training_errors import TrainingDataError


class _ShmClientLike(Protocol):
    def wait_next_job(self, timeout_ms: int) -> SeatInspectionJob | None: ...
    def publish_result(self, result: InspectionResult) -> None: ...
    def release_frame_slot(self, sequence_id: int) -> None: ...


@dataclass(frozen=True)
class ShmDatasetCollection:
    processed_jobs: int
    trace_dirs: tuple[Path, ...]
    samples: tuple[DatasetSample, ...]
    manifest_path: Path
    raw_frame_manifest_path: Path


def collect_shm_dataset(
    output_dir: Path,
    *,
    max_jobs: int = 1,
    timeout_ms: int = 8000,
    trace_root: Path | str = "trace/training_shm",
    split: str = "unassigned",
    label_status: str = "unlabeled",
    filter_decision: str | None = None,
    publish_results: bool = True,
    shm_client: _ShmClientLike | None = None,
    algorithm: SeatSurfaceAoiAlgorithm | None = None,
) -> ShmDatasetCollection:
    """从共享内存消费 C++ 传来的多相机多光源图像，生成 trace 和训练 manifest。"""
    if max_jobs <= 0:
        raise TrainingDataError("max_jobs 必须大于 0")
    client = shm_client or ShmClient()
    detector = algorithm or _trace_all_algorithm(trace_root)
    trace_dirs: list[Path] = []
    processed = 0
    try:
        while processed < max_jobs:
            job = client.wait_next_job(timeout_ms)
            if job is None:
                break
            _write_raw_frames(job, output_dir)
            run = detector.process(job, write_trace=True)
            if publish_results:
                client.publish_result(run.result)
            else:
                client.release_frame_slot(job.sequence_id)
            if run.trace_dir is not None:
                trace_dirs.append(run.trace_dir)
            processed += 1
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    if processed == 0:
        raise TrainingDataError(f"共享内存在 {timeout_ms} ms 内没有可用检测任务")
    if not trace_dirs:
        raise TrainingDataError("检测任务已处理，但没有生成 trace；请检查配方 trace 策略")

    samples = collect_trace_dataset(
        trace_dirs,
        output_dir,
        split=split,
        label_status=label_status,
        filter_decision=filter_decision,
    )
    return ShmDatasetCollection(
        processed_jobs=processed,
        trace_dirs=tuple(trace_dirs),
        samples=tuple(samples),
        manifest_path=output_dir / "dataset_manifest.jsonl",
        raw_frame_manifest_path=output_dir / "raw_frame_manifest.jsonl",
    )


def _trace_all_algorithm(trace_root: Path | str) -> SeatSurfaceAoiAlgorithm:
    class TraceAllRecipeManager(RecipeManager):
        def load(self, recipe_id: str) -> Recipe:
            recipe = super().load(recipe_id)
            return recipe.__class__(
                recipe_id=recipe.recipe_id,
                sku=recipe.sku,
                light_order=recipe.light_order,
                v4_lights=recipe.v4_lights,
                cameras=recipe.cameras,
                quality=recipe.quality,
                roi_locator=recipe.roi_locator,
                registration=recipe.registration,
                fusion=recipe.fusion,
                thresholds=recipe.thresholds,
                models=recipe.models,
                trace=TraceConfig(
                    enabled=True,
                    root_dir=str(trace_root),
                    save_ok_ratio=1.0,
                    save_ng=True,
                    save_recheck=True,
                ),
            )

    return SeatSurfaceAoiAlgorithm(recipe_manager=TraceAllRecipeManager(), trace_writer=TraceWriter(trace_root))


def _write_raw_frames(job: SeatInspectionJob, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "raw_frame_manifest.jsonl"
    rows: list[str] = []
    for bundle in job.camera_bundles:
        for light_id, frame in sorted(bundle.light_frames.items()):
            image_path = _raw_frame_path(output_dir, job, frame)
            image_path.parent.mkdir(parents=True, exist_ok=True)
            _write_pgm(image_path, frame)
            rows.append(
                json.dumps(
                    {
                        "sequence_id": job.sequence_id,
                        "trigger_id": job.trigger_id,
                        "seat_id": job.seat_id,
                        "recipe_id": job.recipe_id,
                        "sku": job.sku,
                        "camera_id": frame.camera_id,
                        "pose_id": frame.pose_id,
                        "light_id": light_id,
                        "frame_index": frame.frame_index,
                        "shot_id": frame.shot_id,
                        "timestamp_us": frame.timestamp_us,
                        "width": frame.width,
                        "height": frame.height,
                        "pixel_format": frame.pixel_format,
                        "dtype": frame.dtype,
                        "image_path": image_path.relative_to(output_dir).as_posix(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
    if rows:
        with manifest_path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")


def _raw_frame_path(output_dir: Path, job: SeatInspectionJob, frame: LightFrame) -> Path:
    filename = (
        f"{_safe_name(job.seat_id)}_{job.sequence_id}_{frame.frame_index}_"
        f"{_safe_name(frame.light_id)}.pgm"
    )
    pose = frame.pose_id or frame.camera_id
    return output_dir / "raw_images" / _safe_name(frame.camera_id) / _safe_name(pose) / _safe_name(frame.light_id) / filename


def _write_pgm(path: Path, frame: LightFrame) -> None:
    if frame.dtype != "UINT8" or frame.channels != 1:
        raise TrainingDataError(f"raw frame 仅支持 UINT8 单通道图像: {frame.camera_id}/{frame.light_id}")
    expected = frame.stride_bytes * frame.height
    if len(frame.image) < expected:
        raise TrainingDataError(f"raw frame 图像长度不足: {frame.camera_id}/{frame.light_id}")
    rows = bytearray()
    for row in range(frame.height):
        start = row * frame.stride_bytes
        rows.extend(frame.image[start : start + frame.width])
    header = f"P5\n{frame.width} {frame.height}\n255\n".encode("ascii")
    path.write_bytes(header + bytes(rows))


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从共享内存采集 C++ 传来的多光源图像并生成训练 manifest")
    parser.add_argument("--output", required=True, type=Path, help="输出数据集目录")
    parser.add_argument("--max-jobs", type=int, default=1)
    parser.add_argument("--timeout-ms", type=int, default=8000)
    parser.add_argument("--trace-root", type=Path, default=Path("trace/training_shm"))
    parser.add_argument("--split", default="unassigned")
    parser.add_argument("--label-status", default="unlabeled")
    parser.add_argument("--filter-decision", default=None)
    parser.add_argument("--no-publish-result", action="store_true", help="仅采集并释放输入 slot，不向 C++ 发布结果")
    args = parser.parse_args(argv)

    try:
        result = collect_shm_dataset(
            output_dir=args.output,
            max_jobs=args.max_jobs,
            timeout_ms=args.timeout_ms,
            trace_root=args.trace_root,
            split=args.split,
            label_status=args.label_status,
            filter_decision=args.filter_decision,
            publish_results=not args.no_publish_result,
        )
    except TrainingDataError as exc:
        print(f"collect_shm_dataset_failed={exc}")
        return 2

    print(
        f"dataset={args.output} manifest={result.manifest_path} "
        f"raw_manifest={result.raw_frame_manifest_path} jobs={result.processed_jobs} samples={len(result.samples)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
