from pathlib import Path

from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter
from tools.job_fixture import make_simulated_job


def test_trace_writer_generates_result_files(tmp_path: Path) -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = recipe.__class__(
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        light_order=recipe.light_order,
        cameras=recipe.cameras,
        quality=recipe.quality,
        registration=recipe.registration,
        thresholds=recipe.thresholds,
        models=recipe.models,
        trace=recipe.trace.__class__(enabled=True, root_dir=str(tmp_path), save_ok_ratio=1.0),
    )
    pipeline = InspectionPipeline()
    job = make_simulated_job()
    result = pipeline.process(job, recipe)
    trace_dir = TraceWriter(recipe.trace.root_dir).write(job, recipe, result, pipeline.last_context)
    assert trace_dir is not None
    assert (trace_dir / "result.json").exists()
    assert (trace_dir / "quality_report.json").exists()
    assert (trace_dir / "timings.json").exists()

