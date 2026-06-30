from pathlib import Path

from python_detector import paths as detector_paths
from python_detector.detector_main import DetectorProcess, _load_runtime_config, _load_runtime_ipc_layout, main, validate_detector_config


def test_detector_main_reads_ipc_layout_from_cpp_config(tmp_path: Path) -> None:
    config_path = tmp_path / "station_runtime.conf"
    config_path.write_text(
        "\n".join(
            [
                "slot_count=6",
                "frame_slot_size=67108864",
                "result_slot_size=131072",
            ]
        ),
        encoding="utf-8",
    )

    assert _load_runtime_ipc_layout(str(config_path)) == (6, 67108864, 131072)


def test_detector_main_resolves_config_relative_to_project_root(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "seat-surface-aoi"
    config_path = project_root / "cpp_controller" / "config" / "station_runtime.production.conf"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("slot_count=3\ntrace_root=runtime_trace\n", encoding="utf-8")
    monkeypatch.setattr(detector_paths, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(tmp_path)

    slot_count, _frame_size, _result_size, trace_root = _load_runtime_config(
        "cpp_controller\\config\\station_runtime.production.conf"
    )

    assert slot_count == 3
    assert trace_root == "runtime_trace"


def test_detector_process_uses_recipe_dir_for_calibration_manager() -> None:
    config_dir = Path(__file__).resolve().parents[1] / "config"

    process = DetectorProcess(recipe_dir=config_dir, enable_display_channel=False)

    assert process.algorithm.recipe_manager.recipe_dir == config_dir
    assert process.algorithm.pipeline.preprocessor.calibration_manager.base_dir == config_dir


def test_detector_process_uses_trace_root_override_for_algorithm_trace(tmp_path: Path) -> None:
    process = DetectorProcess(display_root=tmp_path / "display", trace_root_override=tmp_path / "trace", enable_display_channel=False)

    assert process.algorithm.trace_root_override == tmp_path / "trace"


def test_validate_detector_config_loads_recipes_calibration_and_roi() -> None:
    config_dir = Path(__file__).resolve().parents[1] / "config"

    assert validate_detector_config(None, config_dir) == 0


def test_validate_config_only_does_not_initialize_shared_memory(monkeypatch, tmp_path: Path) -> None:
    config_dir = Path(__file__).resolve().parents[1] / "config"
    runtime_config = tmp_path / "station_runtime.conf"
    runtime_config.write_text("slot_count=2\nframe_slot_size=1024\nresult_slot_size=1024\n", encoding="utf-8")

    def fail_initialize(*_args, **_kwargs):
        raise AssertionError("validate-config-only 不应初始化共享内存")

    monkeypatch.setattr(DetectorProcess, "initialize", fail_initialize)

    assert main(["--config", str(runtime_config), "--recipe-dir", str(config_dir), "--validate-config-only"]) == 0
