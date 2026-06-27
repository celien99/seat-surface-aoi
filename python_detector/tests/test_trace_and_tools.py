from dataclasses import replace
from pathlib import Path
import json

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.image_codec import load_raster_image, write_gray_png, write_rgb_png
from python_detector.ipc.data_types import CameraBundle, DefectResult, InspectionResult, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.quality_gate import FrameQuality, QualityReport
from python_detector.trace.trace_writer import TraceWriter
from training_tools.job_fixture import make_simulated_job
from training_tools.pipeline_report import (
    benchmark_failures,
    format_benchmark_report,
    format_replay_line,
    parse_step_thresholds,
    quality_reasons,
)


def _recipe(root_dir: Path, save_ok_ratio: float = 1.0, fake_mode: str = "auto"):
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    return replace(
        recipe,
        models={
            **recipe.models,
            "fake_default": replace(recipe.models["fake_default"], fake_mode=fake_mode),
            "unknown_safety_net": ModelConfig(backend="fake", fake_mode="ok", model_family="patchcore", role="safety_net"),
        },
        trace=recipe.trace.__class__(enabled=True, root_dir=str(root_dir), save_ok_ratio=save_ok_ratio),
    )


def test_trace_writer_generates_result_files(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)
    assert trace_dir is not None
    assert (trace_dir / "result.json").exists()
    assert (trace_dir / "quality_report.json").exists()
    assert (trace_dir / "feature_summary.json").exists()
    assert (trace_dir / "fusion_summary.json").exists()
    assert (trace_dir / "timings.json").exists()
    assert (trace_dir / "error.json").exists()
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.png").exists()
    assert (trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "seat" / "DIFFUSE.png").exists()
    assert (trace_dir / "overlays" / "TOP_BACK" / "TOP_BACK" / "seat.png").exists()


def test_png_writer_roundtrips_multirow_gray_and_rgb(tmp_path: Path) -> None:
    gray_pixels = bytes(range(12))
    gray_path = tmp_path / "gray.png"
    write_gray_png(gray_path, 4, 3, gray_pixels)
    gray = load_raster_image(gray_path)
    assert gray.width == 4
    assert gray.height == 3
    assert gray.channels == 1
    assert gray.pixels == gray_pixels

    rgb_pixels = bytes((index * 7) % 256 for index in range(4 * 3 * 3))
    rgb_path = tmp_path / "rgb.png"
    write_rgb_png(rgb_path, 4, 3, rgb_pixels)
    rgb = load_raster_image(rgb_path)
    assert rgb.width == 4
    assert rgb.height == 3
    assert rgb.channels == 3
    assert rgb.pixels == rgb_pixels


def test_trace_writer_generates_defect_overlay(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=0.0, fake_mode="ng")
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)

    assert result.decision == "NG"
    assert trace_dir is not None
    overlays = list((trace_dir / "overlays").glob("*/*/*.png"))
    assert overlays
    assert overlays[0].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_trace_writer_overlay_drawn_on_raw_image_resolution(tmp_path: Path) -> None:
    """验证 overlay PNG 尺寸等于 raw 原图尺寸，而非 ROI 裁剪尺寸。"""
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)
    assert trace_dir is not None
    overlay_path = trace_dir / "overlays" / "TOP_BACK" / "TOP_BACK" / "seat.png"
    assert overlay_path.exists()
    overlay_img = load_raster_image(overlay_path)
    # raw 帧尺寸 = 64x48（来自 make_simulated_job）
    assert overlay_img.width == 64
    assert overlay_img.height == 48
    # RGB 三通道
    assert overlay_img.channels == 3
    assert overlay_img.pixels[:3] == bytes((0, 180, 90))


def test_trace_writer_defect_bboxes_at_raw_coordinates(tmp_path: Path) -> None:
    """验证缺陷 bbox 直接按 raw 坐标绘制，无需转换。"""
    recipe = _recipe(tmp_path, save_ok_ratio=1.0, fake_mode="ng")
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)
    assert trace_dir is not None
    assert result.decision == "NG"
    # NG 结果应该产生 overlay PNG
    overlay_path = trace_dir / "overlays" / "TOP_BACK" / "TOP_BACK" / "seat.png"
    assert overlay_path.exists()
    overlay_img = load_raster_image(overlay_path)
    assert overlay_img.width == 64
    assert overlay_img.height == 48
    first_defect = result.defects[0]
    x0, y0, _x1, _y1 = first_defect.bbox_xyxy_pixel
    offset = (y0 * overlay_img.width + x0) * 3
    assert overlay_img.pixels[offset : offset + 3] == bytes((255, 64, 64))


def test_trace_writer_heatmap_without_defect_keeps_roi_pixels_unchanged(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    prepared = pipeline.last_context["prepared_bundles"][0]
    roi_frame = prepared.rois["seat"]["DIFFUSE"]
    context = {
        **pipeline.last_context,
        "spatial_maps": [
            {
                "camera_id": prepared.camera_id,
                "pose_id": prepared.pose_id,
                "roi_name": "seat",
                "spatial_shape": [2, 2],
                "score_threshold": 1.0,
                "anomaly_binarize_min_ratio": 1.0,
                "anomaly_binarize_relative": 1.0,
                "anomaly_map": ((0.1, 0.2), (0.3, 1.0)),
            }
        ],
    }

    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, context)

    assert trace_dir is not None
    overlay_img = load_raster_image(trace_dir / "overlays" / "TOP_BACK" / "TOP_BACK" / "seat.png")
    raw_frame = job.camera_bundles[0].light_frames[roi_frame.light_id]
    origin_x, origin_y = roi_frame.origin_xy
    cold_x = origin_x + max(1, roi_frame.width // 4)
    cold_y = origin_y + max(1, roi_frame.height // 4)
    cold_offset = (cold_y * raw_frame.width + cold_x) * 3
    cold_raw = bytes(raw_frame.image)[cold_y * raw_frame.stride_bytes + cold_x]
    assert overlay_img.pixels[cold_offset : cold_offset + 3] == bytes((cold_raw, cold_raw, cold_raw))


def test_trace_writer_writes_continuous_patchcore_heatmap(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    prepared = pipeline.last_context["prepared_bundles"][0]
    roi_frame = prepared.rois["seat"]["DIFFUSE"]
    context = {
        **pipeline.last_context,
        "spatial_maps": [
            {
                "camera_id": prepared.camera_id,
                "pose_id": prepared.pose_id,
                "roi_name": "seat",
                "spatial_shape": [2, 2],
                "score_threshold": 1.0,
                "anomaly_map": ((0.0, 1.0), (0.5, 0.25)),
            }
        ],
    }

    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, context)

    assert trace_dir is not None
    heatmap_img = load_raster_image(trace_dir / "patchcore_heatmaps" / "TOP_BACK" / "TOP_BACK" / "seat.png")
    raw_frame = job.camera_bundles[0].light_frames[roi_frame.light_id]
    sample_x = min(raw_frame.width - 2, roi_frame.origin_xy[0] + max(1, roi_frame.width // 2))
    sample_y = min(raw_frame.height - 2, roi_frame.origin_xy[1] + max(1, roi_frame.height // 2))
    pixel_offset = (sample_y * raw_frame.width + sample_x) * 3
    raw_value = bytes(raw_frame.image)[sample_y * raw_frame.stride_bytes + sample_x]
    assert heatmap_img.pixels[pixel_offset : pixel_offset + 3] != bytes((raw_value, raw_value, raw_value))


def test_trace_writer_heatmap_only_renders_inside_defect_bbox(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    prepared = pipeline.last_context["prepared_bundles"][0]
    roi_frame = prepared.rois["seat"]["DIFFUSE"]
    origin_x, origin_y = roi_frame.origin_xy
    result = replace(
        result,
        decision="RECHECK",
        defects=[
            DefectResult(
                defect_id="1-0",
                class_name="unknown_anomaly",
                severity="suspect",
                camera_id=prepared.camera_id,
                pose_id=prepared.pose_id,
                roi_name="seat",
                bbox_xyxy_pixel=(origin_x + 2, origin_y + 2, origin_x + 12, origin_y + 12),
                score=1.0,
                area_px=121,
                evidence_lights=["DIFFUSE"],
                mask_offset=None,
                decision="RECHECK",
            )
        ],
    )
    context = {
        **pipeline.last_context,
        "spatial_maps": [
            {
                "camera_id": prepared.camera_id,
                "pose_id": prepared.pose_id,
                "roi_name": "seat",
                "spatial_shape": [1, 1],
                "score_threshold": 1.0,
                "anomaly_map": ((1.0,),),
            }
        ],
    }

    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, context)

    assert trace_dir is not None
    overlay_img = load_raster_image(trace_dir / "overlays" / "TOP_BACK" / "TOP_BACK" / "seat.png")
    raw_frame = job.camera_bundles[0].light_frames[roi_frame.light_id]
    outside_x = min(raw_frame.width - 5, origin_x + max(8, roi_frame.width // 2))
    outside_y = min(raw_frame.height - 5, origin_y + max(8, roi_frame.height // 2))
    outside_offset = (outside_y * raw_frame.width + outside_x) * 3
    outside_raw = bytes(raw_frame.image)[outside_y * raw_frame.stride_bytes + outside_x]
    assert overlay_img.pixels[outside_offset : outside_offset + 3] == bytes((outside_raw, outside_raw, outside_raw))

    inside_x = origin_x + 7
    inside_y = origin_y + 7
    inside_offset = (inside_y * raw_frame.width + inside_x) * 3
    inside_pixel = overlay_img.pixels[inside_offset : inside_offset + 3]
    assert inside_pixel[0] > inside_pixel[1]
    assert inside_pixel[0] > inside_pixel[2]


def test_trace_writer_raw_index_matches_light_frames(tmp_path: Path) -> None:
    """验证 _raw_frame_index 正确索引 job.camera_bundles 中的 raw 帧。"""
    writer = TraceWriter(str(tmp_path))
    job = make_simulated_job()
    index = writer._raw_frame_index(job)
    assert len(index) == 6  # 2 cameras × 3 lights
    top_back_diffuse = index.get(("TOP_BACK", "TOP_BACK", "DIFFUSE"))
    assert top_back_diffuse is not None
    assert top_back_diffuse.width == 64
    assert top_back_diffuse.height == 48
    assert top_back_diffuse.light_id == "DIFFUSE"


def test_trace_writer_separates_robot_flyshot_pose_images(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    frame_a = make_simulated_job().camera_bundles[0].light_frames
    frame_b = make_simulated_job().camera_bundles[0].light_frames
    job = SeatInspectionJob(
        sequence_id=11,
        trigger_id=1011,
        seat_id="SIM_ROBOT_TRACE",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[
            CameraBundle(camera_id="EYE_IN_HAND", pose_id="T1_BACKREST", light_frames=frame_a),
            CameraBundle(camera_id="EYE_IN_HAND", pose_id="T2_CUSHION", light_frames=frame_b),
        ],
    )
    prepared = [
        type(
            "Prepared",
            (),
            {
                "camera_id": "EYE_IN_HAND",
                "pose_id": "T1_BACKREST",
                "rois": {"seat": frame_a},
            },
        )(),
        type(
            "Prepared",
            (),
            {
                "camera_id": "EYE_IN_HAND",
                "pose_id": "T2_CUSHION",
                "rois": {"seat": frame_b},
            },
        )(),
    ]
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="RECHECK",
        quality_pass=False,
        error_code=7,
    )

    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, {"prepared_bundles": prepared})

    assert trace_dir is not None
    assert (trace_dir / "images" / "EYE_IN_HAND" / "T1_BACKREST" / "seat" / "DIFFUSE.png").exists()
    assert (trace_dir / "images" / "EYE_IN_HAND" / "T2_CUSHION" / "seat" / "DIFFUSE.png").exists()


def test_trace_writer_uses_deterministic_ok_sampling(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=0.5)
    writer = TraceWriter(recipe.trace.root_dir)
    result = InspectionResult(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM_1",
        decision="OK",
        quality_pass=True,
    )
    jobs = [replace(make_simulated_job(index), sequence_id=index, trigger_id=1000 + index) for index in range(1, 21)]

    first = [writer._should_write(job, recipe, replace(result, sequence_id=job.sequence_id, trigger_id=job.trigger_id, seat_id=job.seat_id)) for job in jobs]
    second = [writer._should_write(job, recipe, replace(result, sequence_id=job.sequence_id, trigger_id=job.trigger_id, seat_id=job.seat_id)) for job in jobs]

    assert first == second
    assert any(first)
    assert not all(first)


def test_trace_writer_ok_sampling_ratio_edges(tmp_path: Path) -> None:
    job = make_simulated_job()
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="OK",
        quality_pass=True,
    )
    assert TraceWriter(tmp_path)._should_write(job, _recipe(tmp_path, save_ok_ratio=0.0), result) is False
    assert TraceWriter(tmp_path)._should_write(job, _recipe(tmp_path, save_ok_ratio=1.0), result) is True


def test_trace_writer_persists_error_context(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    job = make_simulated_job()
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="ERROR",
        quality_pass=False,
        error_code=9,
    )

    trace_dir = TraceWriter(recipe.trace.root_dir).write(
        job,
        recipe,
        result,
        {"error": {"type": "RuntimeError", "message": "模型输出异常"}},
    )

    assert trace_dir is not None
    error = json.loads((trace_dir / "error.json").read_text(encoding="utf-8"))
    assert error == {"type": "RuntimeError", "message": "模型输出异常"}


def test_pipeline_model_error_context_is_traceable(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    recipe = replace(
        recipe,
        models={
            **recipe.models,
            "fake_default": replace(recipe.models["fake_default"], backend="onnx", model_path="missing.onnx"),
        },
    )
    pipeline = InspectionPipeline()
    job = make_simulated_job()

    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)

    assert result.decision == "RECHECK"
    assert result.quality_pass is True
    assert result.error_code == 13
    assert pipeline.last_context["sample_collection"] == {
        "enabled": True,
        "reason": "model_asset_unavailable",
        "decision": "RECHECK",
    }
    assert pipeline.last_context["error"]["type"] == "ModelAssetUnavailableInferenceError"
    assert pipeline.last_context["error"]["asset_unavailable"] is True
    assert pipeline.last_context["error"]["model_key"] == "fake_default"
    assert pipeline.last_context["error"]["backend"] == "onnx"
    assert pipeline.last_context["error"]["camera_id"] == "TOP_BACK"
    assert pipeline.last_context["error"]["roi_name"] == "seat"
    assert pipeline.last_context["error"]["tensor_shape_nchw"] == [1, 3, 48, 64]
    assert trace_dir is not None
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.png").exists()
    assert (trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "seat" / "DIFFUSE.png").exists()
    error = json.loads((trace_dir / "error.json").read_text(encoding="utf-8"))
    assert error["type"] == "ModelAssetUnavailableInferenceError"
    assert error["model_key"] == "fake_default"
    assert error["asset"]["reason"] == "missing"


def test_pipeline_roi_model_asset_unavailable_saves_raw_images(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    recipe = replace(
        recipe,
        roi_locator=replace(recipe.roi_locator, backend="onnx_yolo", model_path="missing_roi.onnx"),
    )
    pipeline = InspectionPipeline()
    job = make_simulated_job()

    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)

    assert result.decision == "RECHECK"
    assert result.quality_pass is True
    assert result.error_code == 13
    assert pipeline.last_context["error"]["asset"]["asset_path"] == "missing_roi.onnx"
    assert trace_dir is not None
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.png").exists()
    assert not (trace_dir / "images").exists()


def test_trace_png_is_decodable(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=1.0)
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)
    assert trace_dir is not None
    image = load_raster_image(trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "seat" / "DIFFUSE.png")
    assert image.width > 0
    assert image.height > 0
    assert image.channels == 1


def test_replay_report_includes_quality_and_error_context() -> None:
    report = QualityReport(
        is_pass=False,
        messages=["TOP_BACK: missing required light HIGH_LEFT"],
        frame_reports=[
            FrameQuality(
                camera_id="TOP_BACK",
                light_id="DIFFUSE",
                mean_gray=0.0,
                saturation_ratio=0.0,
                dark_ratio=0.0,
                sharpness=0.0,
                motion_gradient=0.0,
                is_pass=False,
                messages=["underexposure mean gray below threshold"],
            )
        ],
    )
    result = InspectionResult(
        sequence_id=3,
        trigger_id=1003,
        seat_id="SIM_3",
        decision="RECHECK",
        quality_pass=False,
        error_code=7,
    )

    line = format_replay_line(
        result,
        {
            "quality_report": report,
            "error": {"type": "RuntimeError", "message": "模型输出异常"},
        },
        summary_limit=2,
    )

    assert quality_reasons({"quality_report": report}) == [
        "TOP_BACK: missing required light HIGH_LEFT",
        "TOP_BACK/DIFFUSE: underexposure mean gray below threshold",
    ]
    assert "decision=RECHECK" in line
    assert 'quality_reasons="TOP_BACK: missing required light HIGH_LEFT | TOP_BACK/DIFFUSE: underexposure mean gray below threshold"' in line
    assert 'error="RuntimeError: 模型输出异常"' in line


def test_benchmark_report_and_threshold_failures() -> None:
    samples = [
        {"quality_ms": 1.0, "preprocess_ms": 2.0, "total_ms": 10.0},
        {"quality_ms": 3.0, "preprocess_ms": 4.0, "total_ms": 20.0},
    ]

    report = format_benchmark_report(2, [10.0, 20.0], samples)
    failures = benchmark_failures(
        [10.0, 20.0],
        samples,
        max_avg_ms=12.0,
        max_ms=15.0,
        max_step_ms=parse_step_thresholds(["quality_ms=2.0"]),
    )

    assert "avg_ms=15.00" in report
    assert "p95_ms=20.00" in report
    assert "quality_ms_avg=2.00" in report
    assert "total_ms_max=20.00" in report
    assert failures == [
        "avg_ms 15.00 exceeds 12.00",
        "max_ms 20.00 exceeds 15.00",
        "quality_ms_max 3.00 exceeds 2.00",
    ]
