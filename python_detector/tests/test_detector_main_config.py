from pathlib import Path

from python_detector.detector_main import DetectorProcess, _load_runtime_ipc_layout


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


def test_detector_process_uses_recipe_dir_for_calibration_manager() -> None:
    config_dir = Path(__file__).resolve().parents[1] / "config"

    process = DetectorProcess(recipe_dir=config_dir, enable_display_channel=False)

    assert process.algorithm.recipe_manager.recipe_dir == config_dir
    assert process.algorithm.pipeline.preprocessor.calibration_manager.base_dir == config_dir
