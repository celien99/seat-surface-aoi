from __future__ import annotations

import json
from pathlib import Path

import pytest

from python_detector.config.recipe_schema import CameraRecipe, Recipe
from python_detector.image_codec import write_gray_png
from python_detector.ipc.data_types import DefectResult, LightFrame
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.collect_capture_dataset import CaptureImage, _resize_frame_letterbox, collect_capture_dataset
from training_tools.collect_shm_dataset import collect_shm_dataset
from training_tools.collect_trace_dataset import TraceDatasetError, collect_trace_dataset, main as collect_main
from training_tools.job_fixture import make_simulated_job
from training_tools.simulate_capture_detection import _select_capture_groups, _write_detection_images
from training_tools.training_errors import TrainingDataError


def test_collect_trace_dataset_generates_manifest_and_images(tmp_path: Path) -> None:
    trace_dir = _write_trace(tmp_path / "trace" / "20260609" / "SIM_1_1")
    output = tmp_path / "dataset"

    samples = collect_trace_dataset([tmp_path / "trace"], output, split="train")

    manifest_path = output / "dataset_manifest.jsonl"
    summary_path = output / "dataset_summary.json"
    assert len(samples) == 2
    assert manifest_path.exists()
    assert summary_path.exists()
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    assert {row["light_id"] for row in rows} == {"DIFFUSE", "HIGH_LEFT"}
    assert rows[0]["source_trace_dir"] == str(trace_dir)
    assert rows[0]["recipe_id"] == "seat_a_black_leather_v1"
    assert rows[0]["seat_id"] == "SIM_1"
    assert rows[0]["sequence_id"] == 1
    assert rows[0]["pose_id"] == "TOP_BACK"
    assert rows[0]["decision"] == "NG"
    assert rows[0]["split"] == "train"
    assert rows[0]["label_status"] == "unlabeled"
    assert all(row["has_defect"] is True for row in rows)
    assert all(row["defect_classes"] == ["scratch"] for row in rows)
    assert all(row["bbox_xyxy_pixel"] == [[1, 2, 10, 12]] for row in rows)
    for row in rows:
        assert (output / row["image_path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_collect_trace_dataset_keeps_robot_pose_images_separate(tmp_path: Path) -> None:
    trace_dir = _write_robot_pose_trace(tmp_path / "trace" / "20260609" / "SIM_ROBOT_1")
    output = tmp_path / "dataset"

    samples = collect_trace_dataset([trace_dir], output, split="train")

    rows = [sample.as_dict() for sample in samples]
    assert len(rows) == 4
    assert {(row["camera_id"], row["pose_id"], row["light_id"]) for row in rows} == {
        ("EYE_IN_HAND", "T1_BACKREST", "DIFFUSE"),
        ("EYE_IN_HAND", "T1_BACKREST", "HIGH_LEFT"),
        ("EYE_IN_HAND", "T2_CUSHION", "DIFFUSE"),
        ("EYE_IN_HAND", "T2_CUSHION", "HIGH_LEFT"),
    }
    assert all("EYE_IN_HAND/T1_BACKREST" in row["image_path"] for row in rows if row["pose_id"] == "T1_BACKREST")
    assert all("EYE_IN_HAND/T2_CUSHION" in row["image_path"] for row in rows if row["pose_id"] == "T2_CUSHION")
    assert {row["pose_id"]: row["has_defect"] for row in rows if row["light_id"] == "DIFFUSE"} == {
        "T1_BACKREST": True,
        "T2_CUSHION": False,
    }


def test_collect_trace_dataset_fails_on_empty_trace_root(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace"
    trace_root.mkdir()

    with pytest.raises(TraceDatasetError, match="没有发现可用 trace 记录"):
        collect_trace_dataset([trace_root], tmp_path / "dataset")


def test_collect_trace_dataset_fails_on_missing_images(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace" / "SIM_1_1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "result.json").write_text('{"sequence_id":1,"seat_id":"SIM_1","decision":"OK"}', encoding="utf-8")

    with pytest.raises(TraceDatasetError, match="trace 缺少 ROI 图像目录"):
        collect_trace_dataset([trace_dir], tmp_path / "dataset")


def test_collect_trace_dataset_cli_reports_broken_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_dir = tmp_path / "trace" / "SIM_1_1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "result.json").write_text("{broken", encoding="utf-8")

    code = collect_main(["--trace-root", str(trace_dir), "--output", str(tmp_path / "dataset")])

    captured = capsys.readouterr()
    assert code == 2
    assert "collect_trace_dataset_failed=JSON 解析失败" in captured.out


def test_patchcore_memory_bank_builder_is_available_from_training_tools(tmp_path: Path) -> None:
    embeddings = tmp_path / "embeddings.jsonl"
    embeddings.write_text(
        "\n".join(json.dumps({"embedding": [float(index), float(index + 1)]}) for index in range(4)),
        encoding="utf-8",
    )
    output = tmp_path / "bank.json"

    bank = build_memory_bank(
        embeddings,
        output,
        version="bank_v1",
        coreset_ratio=0.5,
        pca_version="pca_v1",
        faiss_enabled=True,
    )

    assert bank["embedding_dim"] == 2
    assert len(bank["vectors"]) == 2


def test_collect_shm_dataset_reuses_detector_trace_and_manifest(tmp_path: Path) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.jobs = [make_simulated_job()]
            self.published = []
            self.released = []
            self.closed = False

        def wait_next_job(self, _timeout_ms: int):
            return self.jobs.pop(0) if self.jobs else None

        def publish_result(self, result):
            self.published.append(result)

        def release_frame_slot(self, sequence_id: int) -> None:
            self.released.append(sequence_id)

        def close(self) -> None:
            self.closed = True

    client = FakeClient()
    output = tmp_path / "dataset"

    result = collect_shm_dataset(
        output,
        max_jobs=1,
        trace_root=tmp_path / "trace",
        split="train",
        label_status="raw_shm",
        shm_client=client,
    )

    assert result.processed_jobs == 1
    assert len(result.trace_dirs) == 1
    assert result.manifest_path.exists()
    assert result.raw_frame_manifest_path.exists()
    assert len(result.samples) == 6
    assert client.published[0].decision == "OK"
    assert client.closed is True
    rows = [json.loads(line) for line in result.manifest_path.read_text(encoding="utf-8").splitlines()]
    assert {row["camera_id"] for row in rows} == {"TOP_BACK", "TOP_CUSHION"}
    assert {row["light_id"] for row in rows} == {"DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"}
    raw_rows = [json.loads(line) for line in result.raw_frame_manifest_path.read_text(encoding="utf-8").splitlines()]
    assert len(raw_rows) == 6
    assert {row["camera_id"] for row in raw_rows} == {"TOP_BACK", "TOP_CUSHION"}
    assert (output / raw_rows[0]["image_path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_collect_capture_dataset_from_flat_png_dir(tmp_path: Path) -> None:
    input_dir = tmp_path / "capture"
    input_dir.mkdir()
    for camera_id in ("TOP_BACK", "TOP_CUSHION"):
        for index, light_id in enumerate(("L1", "L2", "L3"), start=1):
            _write_flat_png(
                input_dir / f"{camera_id}_{1000 + index}_{light_id}_original.png",
                width=64,
                height=48,
                value=20 + index,
            )
    output = tmp_path / "dataset"

    result = collect_capture_dataset(
        input_dir,
        output,
        recipe_id="seat_a_black_leather_v1",
        light_map={"L1": "DIFFUSE", "L2": "POLAR_DIFFUSE", "L3": "HIGH_LEFT"},
        split="train",
        label_status="verified_ok",
        roi_output_size=(16, 12),
    )

    rows = [json.loads(line) for line in result.manifest_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert len(rows) == 6
    assert {row["camera_id"] for row in rows} == {"TOP_BACK", "TOP_CUSHION"}
    assert {row["light_id"] for row in rows} == {"DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"}
    assert all(row["decision"] == "OK" for row in rows)
    assert all(row["quality_pass"] is True for row in rows)
    assert summary["roi_size_policy"] == "letterbox"
    assert summary["roi_output_size"] == [16, 12]
    assert summary["roi_image_size_summary"]["TOP_BACK/TOP_BACK/seat"]["width_min"] == 16
    assert summary["roi_image_size_summary"]["TOP_BACK/TOP_BACK/seat"]["height_min"] == 12
    first_image = output / rows[0]["image_path"]
    assert first_image.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_collect_capture_dataset_default_light_map_uses_recipe_order(tmp_path: Path) -> None:
    input_dir = tmp_path / "capture"
    input_dir.mkdir()
    for camera_id in ("TOP_BACK", "TOP_CUSHION"):
        for index, light_id in enumerate(("L1", "L2", "L3"), start=1):
            _write_flat_png(
                input_dir / f"{camera_id}_{2000 + index}_{light_id}_original.png",
                width=64,
                height=48,
                value=25 + index,
            )
    output = tmp_path / "dataset"

    result = collect_capture_dataset(
        input_dir,
        output,
        recipe_id="seat_a_black_leather_v1",
        split="train",
        label_status="verified_ok",
        roi_output_size=(16, 12),
    )

    rows = [json.loads(line) for line in result.manifest_path.read_text(encoding="utf-8").splitlines()]
    assert {row["light_id"] for row in rows} == {"DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"}


def test_collect_capture_dataset_resize_letterbox_keeps_aspect_ratio() -> None:
    source = LightFrame(
        camera_id="TOP_BACK",
        pose_id="TOP_BACK",
        light_id="DIFFUSE",
        frame_index=1,
        light_seq_index=0,
        width=4,
        height=2,
        channels=1,
        stride_bytes=4,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=1000,
        exposure_us=0,
        gain=1.0,
        calibration_id="calib/simulated_v1",
        image_crc32=0,
        image=memoryview(bytearray([10, 20, 30, 40, 50, 60, 70, 80])),
        shot_id=1,
        origin_xy=(0, 0),
        source_width=4,
        source_height=2,
    )

    resized = _resize_frame_letterbox(source, (8, 8))
    pixels = list(bytes(resized.image))

    assert (resized.width, resized.height) == (8, 8)
    assert all(pixels[row * 8 + col] == 0 for row in range(2) for col in range(8))
    assert all(pixels[row * 8 + col] == 0 for row in range(6, 8) for col in range(8))
    assert any(pixels[row * 8 + col] > 0 for row in range(2, 6) for col in range(8))


def test_simulate_capture_detection_selects_same_index_for_all_cameras() -> None:
    test_recipe = Recipe(
        recipe_id="test_recipe",
        sku="seat",
        light_order=("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"),
        cameras=(
            CameraRecipe(camera_id="TOP_BACK", calibration_id="calib/back"),
            CameraRecipe(camera_id="TOP_CUSHION", calibration_id="calib/cushion"),
        ),
    )
    grouped = {
        "TOP_BACK": [
            (_capture_image("TOP_BACK", "L1", 1001), _capture_image("TOP_BACK", "L2", 1002)),
            (_capture_image("TOP_BACK", "L1", 2001), _capture_image("TOP_BACK", "L2", 2002)),
        ],
        "TOP_CUSHION": [
            (_capture_image("TOP_CUSHION", "L1", 1101), _capture_image("TOP_CUSHION", "L2", 1102)),
            (_capture_image("TOP_CUSHION", "L1", 2101), _capture_image("TOP_CUSHION", "L2", 2102)),
        ],
    }

    selected = _select_capture_groups(grouped, test_recipe, 2)

    assert [group[0].camera_id for group in selected] == ["TOP_BACK", "TOP_CUSHION"]
    assert [group[0].timestamp_us for group in selected] == [2001, 2101]
    with pytest.raises(TrainingDataError, match="sample_index=3"):
        _select_capture_groups(grouped, test_recipe, 3)


def test_simulate_capture_detection_writes_png_detection_images(tmp_path: Path) -> None:
    frame = LightFrame(
        camera_id="TOP_BACK",
        pose_id="TOP_BACK",
        light_id="DIFFUSE",
        frame_index=1,
        light_seq_index=0,
        width=6,
        height=4,
        channels=1,
        stride_bytes=6,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=1000,
        exposure_us=0,
        gain=1.0,
        calibration_id="calib/simulated_v1",
        image_crc32=0,
        image=memoryview(bytes(range(24))),
        shot_id=1,
        origin_xy=(10, 20),
        source_width=64,
        source_height=48,
    )
    bundle = type(
        "PreparedBundle",
        (),
        {"camera_id": "TOP_BACK", "pose_id": "TOP_BACK", "rois": {"seat": {"DIFFUSE": frame}}},
    )()
    defect = DefectResult(
        defect_id="D1",
        class_name="scratch",
        severity="minor",
        camera_id="TOP_BACK",
        pose_id="TOP_BACK",
        roi_name="seat",
        bbox_xyxy_pixel=(11, 21, 13, 22),
        score=0.42,
        area_px=4,
        evidence_lights=["DIFFUSE"],
        mask_offset=None,
        decision="NG",
    )
    context = {
        "prepared_bundles": [bundle],
        "feature_summary": [
            {
                "camera_id": "TOP_BACK",
                "pose_id": "TOP_BACK",
                "roi_name": "seat",
                "anomaly_summary": {"anomaly_score": 0.0, "nearest_distance": 0.0},
            }
        ],
    }

    paths = _write_detection_images(tmp_path / "detection_images", "OK", [defect], context)

    assert {path.name for path in paths} == {
        "TOP_BACK_TOP_BACK_seat_detection.png",
        "capture_detection_overview.png",
    }
    assert all(path.suffix == ".png" for path in paths)
    assert all(path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n") for path in paths)


def _write_trace(trace_dir: Path) -> Path:
    image_dir = trace_dir / "images" / "TOP_BACK" / "seat"
    image_dir.mkdir(parents=True)
    for light_id in ("DIFFUSE", "HIGH_LEFT"):
        write_gray_png(image_dir / f"{light_id}.png", 2, 2, b"\x01\x02\x03\x04")
    (trace_dir / "job.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_1",
                "recipe_id": "seat_a_black_leather_v1",
                "sku": "seat_a_black_leather",
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "recipe_summary.json").write_text(
        json.dumps({"recipe_id": "seat_a_black_leather_v1", "sku": "seat_a_black_leather"}),
        encoding="utf-8",
    )
    (trace_dir / "result.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_1",
                "decision": "NG",
                "quality_pass": True,
                "defects": [
                    {
                        "defect_id": "defect_1",
                        "class_name": "scratch",
                        "camera_id": "TOP_BACK",
                        "roi_name": "seat",
                        "bbox_xyxy_pixel": [1, 2, 10, 12],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return trace_dir


def _write_flat_png(path: Path, *, width: int, height: int, value: int) -> None:
    write_gray_png(path, width, height, bytes([value] * width * height))


def _capture_image(camera_id: str, capture_light_id: str, timestamp_us: int) -> CaptureImage:
    return CaptureImage(
        path=Path(f"{camera_id}_{timestamp_us}_{capture_light_id}_original.png"),
        camera_id=camera_id,
        timestamp_us=timestamp_us,
        capture_light_id=capture_light_id,
        light_id={
            "L1": "DIFFUSE",
            "L2": "POLAR_DIFFUSE",
            "L3": "HIGH_LEFT",
        }[capture_light_id],
        sequence_index=0,
    )


def _write_robot_pose_trace(trace_dir: Path) -> Path:
    for pose_id, pixel in (("T1_BACKREST", b"\x01\x02\x03\x04"), ("T2_CUSHION", b"\x05\x06\x07\x08")):
        image_dir = trace_dir / "images" / "EYE_IN_HAND" / pose_id / "seat"
        image_dir.mkdir(parents=True)
        for light_id in ("DIFFUSE", "HIGH_LEFT"):
            write_gray_png(image_dir / f"{light_id}.png", 2, 2, pixel)
    (trace_dir / "job.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_ROBOT",
                "recipe_id": "seat_a_robot_flyshot_v1",
                "sku": "seat_a_black_leather",
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "recipe_summary.json").write_text(
        json.dumps({"recipe_id": "seat_a_robot_flyshot_v1", "sku": "seat_a_black_leather"}),
        encoding="utf-8",
    )
    (trace_dir / "result.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_ROBOT",
                "decision": "NG",
                "quality_pass": True,
                "defects": [
                    {
                        "defect_id": "defect_1",
                        "class_name": "scratch",
                        "camera_id": "EYE_IN_HAND",
                        "pose_id": "T1_BACKREST",
                        "roi_name": "seat",
                        "bbox_xyxy_pixel": [1, 2, 10, 12],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return trace_dir
