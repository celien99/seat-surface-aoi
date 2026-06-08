from dataclasses import replace
from pathlib import Path
import json

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.ipc.data_types import InspectionResult
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter
from tools.job_fixture import make_simulated_job


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
    assert (trace_dir / "images" / "TOP_BACK" / "full" / "DIFFUSE.pgm").exists()


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
