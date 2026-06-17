from pathlib import Path

from python_detector.detector_main import _load_runtime_ipc_layout


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
