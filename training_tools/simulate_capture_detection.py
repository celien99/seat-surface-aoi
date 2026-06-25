from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import Recipe, RecipeManager
from python_detector.image_codec import ImageCodecError, load_gray_image, load_raster_image, write_gray_png, write_rgb_png
from python_detector.ipc.data_types import CameraBundle, DefectResult, LightFrame, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter
from training_tools.collect_capture_dataset import CaptureImage, _default_light_map, _group_capture_images
from training_tools.training_errors import TrainingDataError


def simulate_capture_detection(
    input_dir: Path,
    output_dir: Path,
    *,
    recipe_id: str = "seat_a_black_leather_production_v1",
    sample_index: int = 1,
    write_trace: bool = False,
) -> dict[str, Any]:
    if sample_index <= 0:
        raise TrainingDataError("--sample-index 必须大于 0")
    recipe = RecipeManager().load(recipe_id)
    grouped = _group_capture_images(input_dir, _default_light_map(recipe))
    if not grouped:
        raise TrainingDataError(f"采图目录没有可用 PNG: {input_dir}")
    selected_groups = _select_capture_groups(grouped, recipe, sample_index)
    job = _job_from_capture_groups(selected_groups, recipe, input_dir.name, sample_index)

    output_dir.mkdir(parents=True, exist_ok=True)
    original_paths = _write_original_images(output_dir / "original_images", selected_groups)
    trace_root = output_dir / "trace"
    trace_recipe = replace(
        recipe,
        trace=replace(
            recipe.trace,
            enabled=True,
            root_dir=str(trace_root),
            save_ok_ratio=1.0,
            save_ng=True,
            save_recheck=True,
        ),
    )
    pipeline = InspectionPipeline()
    result = pipeline.process(job, trace_recipe)
    trace_dir = (
        TraceWriter(trace_recipe.trace.root_dir).write(job, trace_recipe, result, pipeline.last_context)
        if write_trace
        else None
    )
    image_result = result
    image_context = pipeline.last_context
    model_check_trace_dir = None
    if not image_context.get("prepared_bundles"):
        model_check_recipe = _model_check_recipe(trace_recipe, output_dir / "trace_model_check")
        model_check_pipeline = InspectionPipeline()
        image_result = model_check_pipeline.process(job, model_check_recipe)
        image_context = model_check_pipeline.last_context
        if write_trace:
            model_check_trace_dir = TraceWriter(model_check_recipe.trace.root_dir).write(
                job,
                model_check_recipe,
                image_result,
                image_context,
            )
    image_paths = _write_detection_images(
        output_dir / "detection_images",
        image_result.decision,
        image_result.defects,
        image_context,
    )
    summary = {
        "input_dir": str(input_dir),
        "recipe_id": recipe_id,
        "sample_index": sample_index,
        "decision": result.decision,
        "quality_pass": result.quality_pass,
        "error_code": result.error_code,
        "defect_count": len(result.defects),
        "trace_enabled": write_trace,
        "trace_dir": str(trace_dir) if trace_dir is not None else None,
        "model_check_decision": image_result.decision,
        "model_check_quality_pass": image_result.quality_pass,
        "model_check_error_code": image_result.error_code,
        "model_check_defect_count": len(image_result.defects),
        "model_check_trace_dir": str(model_check_trace_dir) if model_check_trace_dir is not None else None,
        "original_images": [str(path) for path in original_paths],
        "detection_images": [str(path) for path in image_paths],
        "feature_summary": image_context.get("feature_summary", []),
    }
    (output_dir / "detection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _write_original_images(
    output_dir: Path,
    capture_groups: list[tuple[CaptureImage, ...]],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for capture_group in capture_groups:
        if not capture_group:
            continue
        first = capture_group[0]
        for item in capture_group:
            image = load_gray_image(item.path)
            path = output_dir / _safe_name(item.camera_id) / _safe_name(first.camera_id) / item.path.name
            write_gray_png(path, image.width, image.height, image.pixels)
            paths.append(path)
    return paths


def _model_check_recipe(recipe: Recipe, trace_root: Path) -> Recipe:
    return replace(
        recipe,
        quality=replace(
            recipe.quality,
            max_saturation_ratio=1.0,
            max_capture_span_us=10_000_000,
            max_light_mean_delta=255.0,
        ),
        trace=replace(
            recipe.trace,
            enabled=True,
            root_dir=str(trace_root),
            save_ok_ratio=1.0,
            save_ng=True,
            save_recheck=True,
        ),
    )


def _select_capture_groups(
    grouped: dict[str, list[tuple[CaptureImage, ...]]],
    recipe: Recipe,
    sample_index: int,
) -> list[tuple[CaptureImage, ...]]:
    selected: list[tuple[CaptureImage, ...]] = []
    for camera in sorted(recipe.cameras, key=lambda item: item.camera_id):
        camera_groups = grouped.get(camera.camera_id)
        if not camera_groups:
            raise TrainingDataError(f"{camera.camera_id}: 采图目录缺少该机位")
        if sample_index > len(camera_groups):
            raise TrainingDataError(f"{camera.camera_id}: sample_index={sample_index} 超出样本数 {len(camera_groups)}")
        selected.append(camera_groups[sample_index - 1])
    return selected


def _job_from_capture_groups(
    capture_groups: list[tuple[CaptureImage, ...]],
    recipe: Recipe,
    seat_name: str,
    sample_index: int,
) -> SeatInspectionJob:
    bundles: list[CameraBundle] = []
    for capture_group in capture_groups:
        first = capture_group[0]
        camera_recipe = recipe.camera(first.camera_id, first.camera_id)
        if camera_recipe is None:
            raise TrainingDataError(f"{first.camera_id}: 配方未启用该机位")
        frames: dict[str, LightFrame] = {}
        for item in capture_group:
            try:
                image = load_gray_image(item.path)
            except ImageCodecError as exc:
                raise TrainingDataError(str(exc)) from exc
            light_seq_index = recipe.light_order.index(item.light_id)
            frames[item.light_id] = LightFrame(
                camera_id=item.camera_id,
                pose_id=item.camera_id,
                light_id=item.light_id,
                frame_index=sample_index * 10 + light_seq_index,
                light_seq_index=light_seq_index,
                width=image.width,
                height=image.height,
                channels=1,
                stride_bytes=image.width,
                pixel_format="MONO8",
                bit_depth=8,
                color_order="MONO",
                dtype="UINT8",
                timestamp_us=item.timestamp_us,
                exposure_us=800,
                gain=1.0,
                calibration_id=camera_recipe.calibration_id,
                image_crc32=0,
                image=memoryview(image.pixels),
                shot_id=sample_index,
                origin_xy=(0, 0),
                source_width=image.width,
                source_height=image.height,
            )
        bundles.append(CameraBundle(camera_id=first.camera_id, pose_id=first.camera_id, light_frames=frames))
    return SeatInspectionJob(
        sequence_id=sample_index,
        trigger_id=sample_index,
        seat_id=f"{_safe_name(seat_name)}_{sample_index:04d}",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=bundles,
    )


def _write_detection_images(
    output_dir: Path,
    decision: str,
    defects: list[DefectResult],
    context: dict[str, Any],
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    prepared = context.get("prepared_bundles", [])
    feature_by_view = {
        (item.get("camera_id"), item.get("pose_id"), item.get("roi_name")): item
        for item in context.get("feature_summary", [])
        if isinstance(item, dict)
    }
    images: list[Path] = []
    for bundle in prepared or []:
        pose_id = getattr(bundle, "pose_id", "") or bundle.camera_id
        for roi_name, frames in getattr(bundle, "rois", {}).items():
            frame = frames.get("DIFFUSE") or next(iter(frames.values()), None)
            if frame is None:
                continue
            view_defects = [
                defect
                for defect in defects
                if defect.camera_id == bundle.camera_id and (defect.pose_id or defect.camera_id) == pose_id and defect.roi_name == roi_name
            ]
            feature_summary = feature_by_view.get((bundle.camera_id, pose_id, roi_name), {})
            annotated = _annotated_roi(frame, decision, view_defects, feature_summary)
            path = output_dir / f"{_safe_name(bundle.camera_id)}_{_safe_name(pose_id)}_{_safe_name(roi_name)}_detection.png"
            write_rgb_png(path, annotated["width"], annotated["height"], annotated["pixels"])
            images.append(path)
    if images:
        composite = _composite_images(images)
        composite_path = output_dir / "capture_detection_overview.png"
        write_rgb_png(composite_path, composite["width"], composite["height"], composite["pixels"])
        images.append(composite_path)
    return images


def _annotated_roi(
    frame: LightFrame,
    result_decision: str,
    defects: list[DefectResult],
    feature_summary: dict[str, Any],
) -> dict[str, Any]:
    banner_height = 48
    width = frame.width
    height = frame.height + banner_height
    rgb = bytearray([20, 24, 28] * width * banner_height)
    gray = _frame_bytes(frame)
    for value in gray:
        rgb.extend((value, value, value))
    decision = result_decision if not defects else max((defect.decision for defect in defects), key=_decision_rank)
    color = _decision_color(decision)
    _draw_rect(rgb, width, height, 0, 0, width - 1, banner_height - 1, color, thickness=4)
    score_text = _score_text(feature_summary)
    label = f"{frame.camera_id} {frame.pose_id or frame.camera_id} {frame.light_id} {decision} {score_text}"
    _draw_text(rgb, width, height, 12, 14, label, (255, 255, 255), scale=2)
    _draw_rect(rgb, width, height, 0, banner_height, width - 1, height - 1, color, thickness=5)
    for defect in defects:
        x0, y0, x1, y1 = _bbox_in_frame(defect.bbox_xyxy_pixel, frame)
        _draw_rect(rgb, width, height, x0, y0 + banner_height, x1, y1 + banner_height, _decision_color(defect.decision), thickness=6)
        _draw_text(
            rgb,
            width,
            height,
            max(0, x0),
            max(banner_height, y0 + banner_height - 22),
            f"{defect.class_name} {defect.score:.3f}",
            (255, 255, 255),
            scale=2,
        )
    return {"width": width, "height": height, "pixels": bytes(rgb)}


def _score_text(feature_summary: dict[str, Any]) -> str:
    anomaly = feature_summary.get("anomaly_summary") if isinstance(feature_summary, dict) else None
    if not isinstance(anomaly, dict):
        return "SCORE N/A"
    score = anomaly.get("anomaly_score")
    nearest = anomaly.get("nearest_distance")
    if isinstance(score, (float, int)) and isinstance(nearest, (float, int)):
        return f"SCORE {float(score):.3f} DIST {float(nearest):.3f}"
    return "SCORE N/A"


def _frame_bytes(frame: LightFrame) -> bytes:
    if frame.stride_bytes == frame.width:
        return bytes(frame.image[: frame.width * frame.height])
    rows = bytearray()
    for row in range(frame.height):
        start = row * frame.stride_bytes
        rows.extend(frame.image[start : start + frame.width])
    return bytes(rows)


def _bbox_in_frame(bbox_xyxy_pixel: tuple[int, int, int, int], frame: LightFrame) -> tuple[int, int, int, int]:
    if frame.source_to_roi_matrix is not None:
        x0, y0, x1, y1 = bbox_xyxy_pixel
        points = (
            _apply_homography(frame.source_to_roi_matrix, float(x0), float(y0)),
            _apply_homography(frame.source_to_roi_matrix, float(x1), float(y0)),
            _apply_homography(frame.source_to_roi_matrix, float(x1), float(y1)),
            _apply_homography(frame.source_to_roi_matrix, float(x0), float(y1)),
        )
        if any(point is None for point in points):
            return (0, 0, frame.width - 1, frame.height - 1)
        xs = [point[0] for point in points if point is not None]
        ys = [point[1] for point in points if point is not None]
        return (
            max(0, min(frame.width - 1, math.floor(min(xs)))),
            max(0, min(frame.height - 1, math.floor(min(ys)))),
            max(0, min(frame.width - 1, math.ceil(max(xs)))),
            max(0, min(frame.height - 1, math.ceil(max(ys)))),
        )
    origin_x, origin_y = frame.origin_xy
    x0, y0, x1, y1 = bbox_xyxy_pixel
    return (
        max(0, min(frame.width - 1, x0 - origin_x)),
        max(0, min(frame.height - 1, y0 - origin_y)),
        max(0, min(frame.width - 1, x1 - origin_x)),
        max(0, min(frame.height - 1, y1 - origin_y)),
    )


def _apply_homography(matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float] | None:
    denominator = matrix[6] * x + matrix[7] * y + matrix[8]
    if abs(denominator) < 1e-9:
        return None
    return (
        (matrix[0] * x + matrix[1] * y + matrix[2]) / denominator,
        (matrix[3] * x + matrix[4] * y + matrix[5]) / denominator,
    )


def _composite_images(paths: list[Path]) -> dict[str, Any]:
    images = [load_gray_or_rgb(path) for path in paths]
    max_panel_width = 900
    panels = [_resize_rgb(image, min(1.0, max_panel_width / image["width"])) for image in images]
    gap = 20
    width = sum(panel["width"] for panel in panels) + gap * (len(panels) - 1)
    height = max(panel["height"] for panel in panels)
    canvas = bytearray([18, 20, 24] * width * height)
    x_offset = 0
    for panel in panels:
        _paste_rgb(canvas, width, height, panel, x_offset, 0)
        x_offset += panel["width"] + gap
    return {"width": width, "height": height, "pixels": bytes(canvas)}


def load_gray_or_rgb(path: Path) -> dict[str, Any]:
    image = load_raster_image(path)
    rgb = bytearray()
    if image.channels == 1:
        for value in image.pixels:
            rgb.extend((value, value, value))
    elif image.channels == 3:
        rgb.extend(image.pixels)
    else:
        raise ValueError(f"unsupported image channel count: {image.channels}")
    return {"width": image.width, "height": image.height, "pixels": bytes(rgb)}


def _resize_rgb(image: dict[str, Any], scale: float) -> dict[str, Any]:
    if scale >= 0.999:
        return image
    source_width = int(image["width"])
    source_height = int(image["height"])
    target_width = max(1, int(round(source_width * scale)))
    target_height = max(1, int(round(source_height * scale)))
    source = image["pixels"]
    resized = bytearray(target_width * target_height * 3)
    for y in range(target_height):
        sy = min(source_height - 1, int(y / scale))
        for x in range(target_width):
            sx = min(source_width - 1, int(x / scale))
            source_index = (sy * source_width + sx) * 3
            target_index = (y * target_width + x) * 3
            resized[target_index : target_index + 3] = source[source_index : source_index + 3]
    return {"width": target_width, "height": target_height, "pixels": bytes(resized)}


def _paste_rgb(canvas: bytearray, canvas_width: int, canvas_height: int, image: dict[str, Any], x_offset: int, y_offset: int) -> None:
    source = image["pixels"]
    width = int(image["width"])
    height = int(image["height"])
    for y in range(min(height, canvas_height - y_offset)):
        source_start = y * width * 3
        target_start = ((y + y_offset) * canvas_width + x_offset) * 3
        canvas[target_start : target_start + width * 3] = source[source_start : source_start + width * 3]


def _draw_rect(
    rgb: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
    *,
    thickness: int,
) -> None:
    x0 = max(0, min(width - 1, x0))
    x1 = max(0, min(width - 1, x1))
    y0 = max(0, min(height - 1, y0))
    y1 = max(0, min(height - 1, y1))
    if x1 < x0 or y1 < y0:
        return
    for offset in range(thickness):
        _draw_hline(rgb, width, height, x0, x1, y0 + offset, color)
        _draw_hline(rgb, width, height, x0, x1, y1 - offset, color)
        _draw_vline(rgb, width, height, x0 + offset, y0, y1, color)
        _draw_vline(rgb, width, height, x1 - offset, y0, y1, color)


def _draw_hline(rgb: bytearray, width: int, height: int, x0: int, x1: int, y: int, color: tuple[int, int, int]) -> None:
    if y < 0 or y >= height:
        return
    for x in range(max(0, x0), min(width - 1, x1) + 1):
        _set_pixel(rgb, width, x, y, color)


def _draw_vline(rgb: bytearray, width: int, height: int, x: int, y0: int, y1: int, color: tuple[int, int, int]) -> None:
    if x < 0 or x >= width:
        return
    for y in range(max(0, y0), min(height - 1, y1) + 1):
        _set_pixel(rgb, width, x, y, color)


def _draw_text(
    rgb: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    text: str,
    color: tuple[int, int, int],
    *,
    scale: int,
) -> None:
    cursor = x
    for char in text.upper():
        glyph = _FONT.get(char, _FONT[" "])
        for gy, row in enumerate(glyph):
            for gx, value in enumerate(row):
                if value != "1":
                    continue
                for sy in range(scale):
                    for sx in range(scale):
                        px = cursor + gx * scale + sx
                        py = y + gy * scale + sy
                        if 0 <= px < width and 0 <= py < height:
                            _set_pixel(rgb, width, px, py, color)
        cursor += 6 * scale
        if cursor >= width:
            break


def _set_pixel(rgb: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    index = (y * width + x) * 3
    rgb[index : index + 3] = bytes(color)


def _decision_rank(decision: str) -> int:
    return {"OK": 0, "RECHECK": 1, "NG": 2, "ERROR": 3}.get(decision, 0)


def _decision_color(decision: str) -> tuple[int, int, int]:
    return {
        "OK": (0, 180, 90),
        "RECHECK": (255, 190, 40),
        "NG": (255, 64, 64),
        "ERROR": (180, 80, 255),
    }.get(decision, (255, 255, 255))


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


_FONT = {
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "_": ("00000", "00000", "00000", "00000", "00000", "00000", "11111"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
    ":": ("00000", "01100", "01100", "00000", "01100", "01100", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10011", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00001", "00001", "00001", "00001", "10001", "10001", "01110"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 images_capture 样本模拟完整检测链路并生成检测图")
    parser.add_argument("--input", required=True, type=Path, help="images_capture 下的单次采图目录")
    parser.add_argument("--output", required=True, type=Path, help="检测报告和检测图输出目录")
    parser.add_argument("--recipe", default="seat_a_black_leather_production_v1")
    parser.add_argument("--sample-index", type=int, default=1, help="选择每个机位的第 N 组 L1/L2/L3 样本")
    parser.add_argument("--write-trace", action="store_true", help="同时写出完整 trace 便于排障")
    args = parser.parse_args(argv)
    try:
        summary = simulate_capture_detection(
            input_dir=args.input,
            output_dir=args.output,
            recipe_id=args.recipe,
            sample_index=args.sample_index,
            write_trace=args.write_trace,
        )
    except (TrainingDataError, ValueError, OSError) as exc:
        print(f"simulate_capture_detection_failed={exc}")
        return 2
    print(
        f"decision={summary['decision']} defects={summary['defect_count']} "
        f"model_check={summary['model_check_decision']} model_defects={summary['model_check_defect_count']} "
        f"trace_enabled={summary['trace_enabled']} trace={summary['trace_dir']} "
        f"originals={len(summary['original_images'])} images={len(summary['detection_images'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
