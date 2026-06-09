from __future__ import annotations

from python_detector import RecipeManager, SeatSurfaceAoiAlgorithm
from python_detector.ipc.shm_protocol import ErrorCode
from training_tools.job_fixture import make_simulated_job


def test_algorithm_public_api_processes_simulated_job_without_ipc() -> None:
    algorithm = SeatSurfaceAoiAlgorithm()
    run = algorithm.process(make_simulated_job(), write_trace=False)

    assert run.result.decision == "OK"
    assert run.result.quality_pass is True
    assert run.trace_dir is None
    assert "timings" in run.context


def test_algorithm_public_api_fails_closed_for_missing_recipe() -> None:
    job = make_simulated_job()
    job.recipe_id = "missing_recipe"

    run = SeatSurfaceAoiAlgorithm().process(job, write_trace=False)

    assert run.result.decision == "ERROR"
    assert run.result.quality_pass is False
    assert run.result.error_code == ErrorCode.INTERNAL_ERROR
    assert run.context["error"]["type"] == "RecipeValidationError"


def test_recipe_manager_default_path_is_independent_from_cwd(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    recipe = RecipeManager().load("seat_a_black_leather_v1")

    assert recipe.recipe_id == "seat_a_black_leather_v1"
