from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_detector.config.calibration_manager import CalibrationManager, RoiTemplate
from python_detector.config.recipe_schema import Recipe, RecipeManager, RecipeValidationError
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob
from python_detector.pipeline.preprocessor import PreprocessRecheckError, Preprocessor
from training_tools.collect_trace_dataset import DatasetSample
from training_tools.training_errors import TrainingDataError


_CAPTURE_RE = re.compile(r"^(?P<camera>.+)_(?P<timestamp>\d+)_(?P<light>L\d+)_.+\.png$", re.IGNORECASE)


@dataclass(frozen=True)
class CaptureImage:
    path: Path
    camera_id: str
    timestamp_us: int
    capture_light_id: str
    light_id: str
    sequence_index: int


@dataclass(frozen=True)
class PngGrayImage:
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class CaptureDatasetResult:
    manifest_path: Path
    summary_path: Path
    samples: tuple[DatasetSample, ...]
    skipped: tuple[dict[str, Any], ...]


def collect_capture_dataset(
    input_dir: Path,
    output_dir: Path,
    *,
    recipe_id: str = "seat_a_black_leather_production_v1",
    light_map: dict[str, str] | None = None,
    split: str = "train",
    label_status: str = "unverified_ok",
    decision: str = "OK",
    quality_pass: bool = True,
    roi_output_size: tuple[int, int] | None = None,
    skip_failed: bool = False,
) -> CaptureDatasetResult:
    """把 C++ capture_only 平铺 PNG 目录转换为训练 manifest 和 ROI PGM 图。"""
    if not input_dir.is_dir():
        raise TrainingDataError(f"采图目录不存在: {input_dir}")
    recipe = RecipeManager().load(recipe_id)
    resolved_light_map = light_map or _default_light_map(recipe)
    grouped = _group_capture_images(input_dir, resolved_light_map)
    if not grouped:
        raise TrainingDataError(f"采图目录没有匹配的 PNG: {input_dir}")

    preprocessor = Preprocessor(calibration_manager=CalibrationManager())
    samples: list[DatasetSample] = []
    skipped: list[dict[str, Any]] = []
    sequence_id = 0
    for camera_id in sorted(grouped):
        for capture_group in grouped[camera_id]:
            sequence_id += 1
            try:
                samples.extend(
                    _collect_capture_group(
                        capture_group,
                        output_dir,
                        recipe,
                        preprocessor,
                        source_dir=input_dir,
                        sequence_id=sequence_id,
                        split=split,
                        label_status=label_status,
                        decision=decision,
                        quality_pass=quality_pass,
                        roi_output_size=roi_output_size,
                    )
                )
            except Exception as exc:
                if not skip_failed:
                    if isinstance(exc, TrainingDataError):
                        raise
                    raise TrainingDataError(f"{camera_id}: 采集组转换失败: {exc}") from exc
                skipped.append(
                    {
                        "camera_id": camera_id,
                        "sequence_id": sequence_id,
                        "files": [str(item.path) for item in capture_group],
                        "error": str(exc),
                        "error_type": exc.__class__.__name__,
                    }
                )

    if not samples:
        raise TrainingDataError("没有生成任何 ROI 样本")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "dataset_manifest.jsonl"
    manifest_path.write_text(
        "\n".join(json.dumps(sample.as_dict(), ensure_ascii=False, sort_keys=True) for sample in samples) + "\n",
        encoding="utf-8",
    )
    summary_path = output_dir / "dataset_summary.json"
    summary_path.write_text(
        json.dumps(_summary(samples, skipped, input_dir, recipe_id), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if skipped:
        (output_dir / "skipped_capture_groups.jsonl").write_text(
            "\n".join(json.dumps(item, ensure_ascii=False, sort_keys=True) for item in skipped) + "\n",
            encoding="utf-8",
        )
    return CaptureDatasetResult(
        manifest_path=manifest_path,
        summary_path=summary_path,
        samples=tuple(samples),
        skipped=tuple(skipped),
    )


def _group_capture_images(input_dir: Path, light_map: dict[str, str]) -> dict[str, list[tuple[CaptureImage, ...]]]:
    by_camera_light: dict[str, dict[str, list[CaptureImage]]] = {}
    for path in sorted(input_dir.glob("*.png")):
        match = _CAPTURE_RE.match(path.name)
        if match is None:
            continue
        capture_light_id = match.group("light").upper()
        if capture_light_id not in light_map:
            continue
        camera_id = match.group("camera")
        by_camera_light.setdefault(camera_id, {}).setdefault(capture_light_id, []).append(
            CaptureImage(
                path=path,
                camera_id=camera_id,
                timestamp_us=int(match.group("timestamp")),
                capture_light_id=capture_light_id,
                light_id=light_map[capture_light_id],
                sequence_index=0,
            )
        )

    grouped: dict[str, list[tuple[CaptureImage, ...]]] = {}
    required_lights = tuple(sorted(light_map, key=_capture_light_sort_key))
    for camera_id, light_entries in by_camera_light.items():
        missing = [light_id for light_id in required_lights if light_id not in light_entries]
        if missing:
            raise TrainingDataError(f"{camera_id}: 缺少采集光源文件: {missing}")
        counts = {light_id: len(light_entries[light_id]) for light_id in required_lights}
        if len(set(counts.values())) != 1:
            raise TrainingDataError(f"{camera_id}: 各光源采图数量不一致: {counts}")
        for light_id in required_lights:
            light_entries[light_id].sort(key=lambda item: item.timestamp_us)
        capture_count = next(iter(counts.values()))
        camera_groups: list[tuple[CaptureImage, ...]] = []
        for index in range(capture_count):
            camera_groups.append(
                tuple(
                    CaptureImage(
                        path=light_entries[light_id][index].path,
                        camera_id=camera_id,
                        timestamp_us=light_entries[light_id][index].timestamp_us,
                        capture_light_id=light_id,
                        light_id=light_entries[light_id][index].light_id,
                        sequence_index=index + 1,
                    )
                    for light_id in required_lights
                )
            )
        grouped[camera_id] = camera_groups
    return grouped


def _collect_capture_group(
    capture_group: tuple[CaptureImage, ...],
    output_dir: Path,
    recipe: Recipe,
    preprocessor: Preprocessor,
    *,
    source_dir: Path,
    sequence_id: int,
    split: str,
    label_status: str,
    decision: str,
    quality_pass: bool,
    roi_output_size: tuple[int, int] | None,
) -> list[DatasetSample]:
    first = capture_group[0]
    camera_recipe = recipe.camera(first.camera_id, first.camera_id)
    if camera_recipe is None:
        raise TrainingDataError(f"{first.camera_id}: 配方未启用该机位")
    frames = {}
    for index, item in enumerate(capture_group):
        image = read_png_gray(item.path)
        frames[item.light_id] = LightFrame(
            camera_id=item.camera_id,
            pose_id=item.camera_id,
            light_id=item.light_id,
            frame_index=sequence_id * 10 + index,
            light_seq_index=index,
            width=image.width,
            height=image.height,
            channels=1,
            stride_bytes=image.width,
            pixel_format="MONO8",
            bit_depth=8,
            color_order="MONO",
            dtype="UINT8",
            timestamp_us=item.timestamp_us,
            exposure_us=0,
            gain=1.0,
            calibration_id=camera_recipe.calibration_id,
            image_crc32=0,
            image=memoryview(image.pixels),
            shot_id=sequence_id,
            origin_xy=(0, 0),
            source_width=image.width,
            source_height=image.height,
        )

    job = SeatInspectionJob(
        sequence_id=sequence_id,
        trigger_id=sequence_id,
        seat_id=f"{_safe_name(source_dir.name)}_{sequence_id:04d}",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[
            CameraBundle(
                camera_id=first.camera_id,
                pose_id=first.camera_id,
                light_frames=frames,
            )
        ],
    )
    try:
        calibration = preprocessor.calibration_manager.load(
            first.camera_id,
            camera_recipe.calibration_id,
            camera_recipe.roi_template,
        )
        preprocessor._assert_calibration_matches(frames, calibration)
        located_templates, roi_report = preprocessor.roi_locator.locate(
            first.camera_id,
            frames,
            calibration.roi_templates,
            recipe,
        )
    except (PreprocessRecheckError, RecipeValidationError) as exc:
        raise TrainingDataError(str(exc)) from exc
    if not roi_report.is_pass:
        raise TrainingDataError(f"{first.camera_id}: ROI 定位失败: {roi_report.message}")

    samples: list[DatasetSample] = []
    for roi_name, roi_template in sorted(located_templates.items()):
        output_template = roi_template
        if roi_output_size is not None:
            output_template = RoiTemplate(
                roi_name=roi_template.roi_name,
                polygon_xy=roi_template.polygon_xy,
                output_size=roi_output_size,
            )
        roi_frames = {
            light_id: preprocessor._crop_to_roi(frame, output_template)
            for light_id, frame in frames.items()
        }
        for light_id, frame in sorted(roi_frames.items()):
            sample_base = "_".join(
                [
                    _safe_name(source_dir.name),
                    f"{sequence_id:04d}",
                    _safe_name(first.camera_id),
                    _safe_name(first.camera_id),
                    _safe_name(roi_name),
                ]
            )
            sample_id = f"{sample_base}_{light_id}"
            image_path = Path("images") / first.camera_id / first.camera_id / roi_name / light_id / f"{sample_id}.pgm"
            destination = output_dir / image_path
            _write_pgm(destination, frame)
            samples.append(
                DatasetSample(
                    sample_id=sample_id,
                    source_trace_dir=str(source_dir),
                    recipe_id=recipe.recipe_id,
                    seat_id=job.seat_id,
                    sequence_id=sequence_id,
                    decision=decision,
                    quality_pass=quality_pass,
                    camera_id=first.camera_id,
                    pose_id=first.camera_id,
                    roi_name=roi_name,
                    light_id=light_id,
                    image_path=image_path.as_posix(),
                    has_defect=False,
                    defect_classes=[],
                    bbox_xyxy_pixel=[],
                    split=split,
                    label_status=label_status,
                )
            )
    return samples


def read_png_gray(path: Path) -> PngGrayImage:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise TrainingDataError(f"不是 PNG 文件: {path}")
    offset = 8
    width = 0
    height = 0
    bit_depth = -1
    color_type = -1
    interlace = -1
    compressed = bytearray()
    while offset < len(data):
        if offset + 8 > len(data):
            raise TrainingDataError(f"PNG chunk 截断: {path}")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_data_start = offset + 8
        chunk_data_end = chunk_data_start + length
        if chunk_data_end + 4 > len(data):
            raise TrainingDataError(f"PNG chunk 数据截断: {path}")
        chunk_data = data[chunk_data_start:chunk_data_end]
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            compressed.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
        offset = chunk_data_end + 4

    if width <= 0 or height <= 0:
        raise TrainingDataError(f"PNG 缺少有效 IHDR: {path}")
    if bit_depth != 8 or color_type != 0 or interlace != 0:
        raise TrainingDataError(f"仅支持 8bit 非隔行灰度 PNG: {path}")
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as exc:
        raise TrainingDataError(f"PNG 解压失败: {path}: {exc}") from exc
    pixels = _unfilter_png_gray(raw, width, height, path)
    return PngGrayImage(width=width, height=height, pixels=pixels)


def _unfilter_png_gray(raw: bytes, width: int, height: int, path: Path) -> bytes:
    stride = width
    expected = (stride + 1) * height
    if len(raw) != expected:
        raise TrainingDataError(f"PNG 解压长度不匹配: {path}: {len(raw)} != {expected}")
    rows: list[bytearray] = []
    offset = 0
    previous = bytearray(stride)
    for _row_index in range(height):
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 0:
            pass
        elif filter_type == 1:
            for x in range(stride):
                current[x] = (current[x] + (current[x - 1] if x > 0 else 0)) & 0xFF
        elif filter_type == 2:
            for x in range(stride):
                current[x] = (current[x] + previous[x]) & 0xFF
        elif filter_type == 3:
            for x in range(stride):
                left = current[x - 1] if x > 0 else 0
                up = previous[x]
                current[x] = (current[x] + ((left + up) // 2)) & 0xFF
        elif filter_type == 4:
            for x in range(stride):
                left = current[x - 1] if x > 0 else 0
                up = previous[x]
                upper_left = previous[x - 1] if x > 0 else 0
                current[x] = (current[x] + _paeth(left, up, upper_left)) & 0xFF
        else:
            raise TrainingDataError(f"PNG filter 不支持: {path}: {filter_type}")
        rows.append(current)
        previous = current
    return b"".join(rows)


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    pa = abs(estimate - left)
    pb = abs(estimate - up)
    pc = abs(estimate - upper_left)
    if pa <= pb and pa <= pc:
        return left
    if pb <= pc:
        return up
    return upper_left


def _write_pgm(path: Path, frame: LightFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = bytes(frame.image[:frame.stride_bytes * frame.height])
    if frame.stride_bytes != frame.width:
        compact = bytearray(frame.width * frame.height)
        for row in range(frame.height):
            source_start = row * frame.stride_bytes
            compact[row * frame.width:(row + 1) * frame.width] = frame.image[source_start:source_start + frame.width]
        pixels = bytes(compact)
    path.write_bytes(f"P5\n{frame.width} {frame.height}\n255\n".encode("ascii") + pixels)


def _parse_light_map(values: list[str] | None) -> dict[str, str]:
    if not values:
        return {}
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"--light-map 必须是 Lx=LIGHT_ID: {value}")
        key, mapped = value.split("=", 1)
        key = key.strip().upper()
        mapped = mapped.strip()
        if not key or not mapped:
            raise ValueError(f"--light-map 包含空值: {value}")
        result[key] = mapped
    return result


def _default_light_map(recipe: Recipe) -> dict[str, str]:
    return {f"L{index}": light_id for index, light_id in enumerate(recipe.light_order, start=1)}


def _capture_light_sort_key(light_id: str) -> tuple[int, int | str]:
    match = re.fullmatch(r"L(\d+)", light_id.upper())
    if match is not None:
        return (0, int(match.group(1)))
    return (1, light_id)


def _parse_roi_output_size(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    if "x" not in value.lower():
        raise ValueError("--roi-output-size 格式应为 WIDTHxHEIGHT")
    width_raw, height_raw = value.lower().split("x", 1)
    width = int(width_raw)
    height = int(height_raw)
    if width <= 0 or height <= 0:
        raise ValueError("--roi-output-size 必须为正整数")
    return (width, height)


def _summary(samples: list[DatasetSample], skipped: list[dict[str, Any]], input_dir: Path, recipe_id: str) -> dict[str, Any]:
    cameras: dict[str, int] = {}
    lights: dict[str, int] = {}
    roi_names: dict[str, int] = {}
    for sample in samples:
        cameras[sample.camera_id] = cameras.get(sample.camera_id, 0) + 1
        lights[sample.light_id] = lights.get(sample.light_id, 0) + 1
        roi_names[sample.roi_name] = roi_names.get(sample.roi_name, 0) + 1
    return {
        "input_dir": str(input_dir),
        "recipe_id": recipe_id,
        "sample_count": len(samples),
        "group_count": len({(sample.sequence_id, sample.camera_id, sample.pose_id, sample.roi_name) for sample in samples}),
        "camera_counts": cameras,
        "light_counts": lights,
        "roi_counts": roi_names,
        "split_counts": {split: sum(1 for sample in samples if sample.split == split) for split in sorted({sample.split for sample in samples})},
        "label_status_counts": {
            status: sum(1 for sample in samples if sample.label_status == status)
            for status in sorted({sample.label_status for sample in samples})
        },
        "skipped_count": len(skipped),
    }


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 capture_only 平铺 PNG 目录生成 ROI 训练 manifest")
    parser.add_argument("--input", required=True, type=Path, help="images_capture 下的单次采图目录")
    parser.add_argument("--output", required=True, type=Path, help="输出训练数据集目录")
    parser.add_argument("--recipe", default="seat_a_black_leather_production_v1", help="用于 ROI 定位和裁剪的配方 ID")
    parser.add_argument("--light-map", action="append", help="采集文件光源到配方光源映射，例如 L1=DIFFUSE，可重复")
    parser.add_argument("--split", default="train")
    parser.add_argument("--label-status", default="unverified_ok")
    parser.add_argument("--decision", default="OK", choices=["OK", "NG", "RECHECK", "ERROR"])
    parser.add_argument("--quality-pass", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--roi-output-size", default=None, help="可选：覆盖 ROI 输出尺寸，格式 WIDTHxHEIGHT，例如 64x48")
    parser.add_argument("--skip-failed", action="store_true", help="单组 ROI 失败时跳过并继续")
    args = parser.parse_args(argv)

    try:
        result = collect_capture_dataset(
            input_dir=args.input,
            output_dir=args.output,
            recipe_id=args.recipe,
            light_map=_parse_light_map(args.light_map),
            split=args.split,
            label_status=args.label_status,
            decision=args.decision,
            quality_pass=args.quality_pass,
            roi_output_size=_parse_roi_output_size(args.roi_output_size),
            skip_failed=args.skip_failed,
        )
    except (TrainingDataError, ValueError, OSError) as exc:
        print(f"collect_capture_dataset_failed={exc}")
        return 2

    print(
        f"dataset={args.output} manifest={result.manifest_path} "
        f"samples={len(result.samples)} skipped={len(result.skipped)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
