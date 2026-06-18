from dataclasses import replace
from pathlib import Path
import json

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.ipc.data_types import CameraBundle, InspectionResult, SeatInspectionJob
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
    return recipe.__class__(
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        light_order=recipe.light_order,
        cameras=recipe.cameras,
        quality=recipe.quality,
        registration=recipe.registration,
        fusion=recipe.fusion,
        thresholds=recipe.thresholds,
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
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.pgm").exists()
    assert (trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "full" / "DIFFUSE.pgm").exists()


def test_trace_writer_generates_defect_overlay(tmp_path: Path) -> None:
    recipe = _recipe(tmp_path, save_ok_ratio=0.0, fake_mode="ng")
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)

    assert result.decision == "NG"
    assert trace_dir is not None
    overlays = list((trace_dir / "overlays").glob("*.ppm"))
    assert overlays
    assert overlays[0].read_bytes().startswith(b"P6\n")


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
                "rois": {"full": frame_a},
            },
        )(),
        type(
            "Prepared",
            (),
            {
                "camera_id": "EYE_IN_HAND",
                "pose_id": "T2_CUSHION",
                "rois": {"full": frame_b},
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
    assert (trace_dir / "images" / "EYE_IN_HAND" / "T1_BACKREST" / "full" / "DIFFUSE.pgm").exists()
    assert (trace_dir / "images" / "EYE_IN_HAND" / "T2_CUSHION" / "full" / "DIFFUSE.pgm").exists()


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
            "fake_default": ModelConfig(backend="onnx", model_path="missing.onnx", role="primary"),
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
    assert pipeline.last_context["error"]["roi_name"] == "full"
    assert pipeline.last_context["error"]["tensor_shape_nchw"] == [1, 5, 48, 64]
    assert trace_dir is not None
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.pgm").exists()
    assert (trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "full" / "DIFFUSE.pgm").exists()
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
    assert (trace_dir / "raw_images" / "TOP_BACK" / "TOP_BACK" / "DIFFUSE.pgm").exists()
    assert not (trace_dir / "images").exists()


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
