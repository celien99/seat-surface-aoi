from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from python_detector.config.calibration_manager import Calibration
from python_detector.config.recipe_schema import ModelConfig, RecipeManager, RecipeValidationError, recipe_from_dict
from python_detector.ipc.data_types import LightFrame, SeatInspectionJob
from python_detector.models.inference_engine import InferenceEngine, ModelRegistry
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.preprocessor import PreparedBundle, Preprocessor
from python_detector.pipeline.reflectance_cube import ReflectanceCubeBuilder
from python_detector.pipeline.roi_locator import RoiLocator
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.job_fixture import make_simulated_job


def test_default_recipe_declares_v4_light_mapping_and_roi_locator() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.semantic_light_id("DOME") == "DIFFUSE"
    assert recipe.semantic_light_id("DARKFIELD_L") == "HIGH_LEFT"
    assert recipe.semantic_light_id("DARKFIELD_R") == "HIGH_RIGHT"
    assert recipe.roi_locator.backend == "template"
    assert recipe.registration.method == "fixed_calibration"


def test_recipe_rejects_v4_semantic_light_not_in_light_order() -> None:
    with pytest.raises(RecipeValidationError, match="v4_lights.semantic_to_light_id.DOME 不在 light_order"):
        recipe_from_dict(
            {
                "recipe_id": "bad_v4_light",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "v4_lights": {"semantic_to_light_id": {"DOME": "LIGHT_99", "DARKFIELD_L": "HIGH_LEFT", "DARKFIELD_R": "HIGH_RIGHT"}},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_dome_roi_locator_fake_yolo_returns_traceable_report() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        roi_locator=replace(recipe.roi_locator, backend="fake_yolo", model_path="simulated-yolo.onnx"),
    )
    pipeline = InspectionPipeline(preprocessor=Preprocessor(roi_locator=RoiLocator()))
    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision == "OK"
    report = pipeline.last_context["roi_location_reports"][0]
    assert report.backend == "fake_yolo"
    assert report.dome_light_id == "DIFFUSE"
    assert report.is_pass is True
    assert report.locations[0].roi_name == "full"
    assert report.locations[0].confidence == pytest.approx(0.99)


def test_dome_roi_locator_missing_light_returns_error_not_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, roi_locator=replace(recipe.roi_locator, backend="template"))
    job = make_simulated_job()
    for bundle in job.camera_bundles:
        bundle.light_frames.pop("DIFFUSE")

    result = InspectionPipeline().process(job, recipe)

    assert result.decision == "RECHECK"
    assert result.quality_pass is False


def test_ecc_registration_reports_alignment_details() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        registration=replace(
            recipe.registration,
            method="ecc",
            base_light_id="DIFFUSE",
            base_light_fallback="DIFFUSE",
            min_correlation=0.01,
            search_radius_px=1,
        ),
    )
    result = InspectionPipeline().process(make_simulated_job(), recipe)

    assert result.decision == "OK"


def test_ecc_registration_details_are_in_pipeline_context() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        registration=replace(
            recipe.registration,
            method="ecc",
            base_light_id="DIFFUSE",
            base_light_fallback="DIFFUSE",
            min_correlation=0.01,
            search_radius_px=1,
        ),
    )
    pipeline = InspectionPipeline()

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision == "OK"
    first_report = pipeline.last_context["registration_reports"][0]
    assert first_report.method == "ecc"
    assert first_report.is_pass is True
    assert first_report.details
    assert {"light_id", "matrix_3x3", "correlation", "iterations", "converged"}.issubset(first_report.details[0])


def test_ecc_registration_applies_translation_before_feature_building() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        cameras=(replace(recipe.cameras[0], base_light_id="DIFFUSE"),),
        quality=replace(recipe.quality, max_registration_error_px=2.0),
        registration=replace(
            recipe.registration,
            method="ecc",
            base_light_id="DIFFUSE",
            base_light_fallback="DIFFUSE",
            min_correlation=0.05,
            search_radius_px=1,
        ),
    )
    width = 8
    height = 6
    base_pixels = bytearray(40 + ((x * 17 + y * 29 + ((x * y) % 11)) % 170) for y in range(height) for x in range(width))
    shifted_pixels = _shift_image_right(base_pixels, width, height, 1)
    frames = {
        "DIFFUSE": _ecc_test_frame("DIFFUSE", base_pixels, width, height),
        "POLAR_DIFFUSE": _ecc_test_frame("POLAR_DIFFUSE", shifted_pixels, width, height),
        "HIGH_LEFT": _ecc_test_frame("HIGH_LEFT", shifted_pixels, width, height),
        "HIGH_RIGHT": _ecc_test_frame("HIGH_RIGHT", shifted_pixels, width, height),
    }
    calibration = Calibration(
        calibration_id="calib/simulated_v1",
        camera_id="TOP_BACK",
        image_size=(width, height),
        pixel_size_mm=0.12,
        base_light_id="DIFFUSE",
        light_alignment={},
        roi_templates={},
    )
    prepared = [
        PreparedBundle(
            camera_id="TOP_BACK",
            calibration=calibration,
            rois={"full": frames},
            roi_templates={},
        )
    ]
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[],
    )

    cube = ReflectanceCubeBuilder().build(job, prepared, recipe)[0]
    high_left = cube.frames["HIGH_LEFT"]

    assert cube.registration.is_pass is True
    assert cube.registration.details[1]["light_id"] == "HIGH_LEFT"
    assert cube.registration.details[1]["shift_xy"] == [1, 0]
    assert cube.registration.details[1]["applied"] is True
    for y in range(height):
        for x in range(width - 1):
            assert high_left.image[y * high_left.stride_bytes + x] == base_pixels[y * width + x]


def test_patchcore_knn_backend_emits_unknown_anomaly_and_trace(tmp_path: Path) -> None:
    bank_path = tmp_path / "memory_bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "version": "bank_v1",
                "model_family": "patchcore",
                "embedding_dim": 10,
                "coreset_ratio": 1.0,
                "pca_version": None,
                "vectors": [[0.0] * 10],
            }
        ),
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
        class_names=("unknown_anomaly",),
        input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        memory_bank_path=str(bank_path),
        score_threshold=0.01,
        anomaly_score_scale=2.0,
        knn_k=1,
    )
    recipe = replace(
        recipe,
        models={**recipe.models, "unknown_safety_net": patchcore},
    )
    pipeline = InspectionPipeline(inference_engine=InferenceEngine(ModelRegistry()))

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision in {"RECHECK", "NG"}
    assert result.defects
    assert result.defects[0].class_name == "unknown_anomaly"
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "unknown_safety_net"]
    assert summaries[0]["embedding_summary"]["backend"] == "statistical"
    assert summaries[0]["anomaly_summary"]["memory_bank_version"] == "bank_v1"
    assert summaries[0]["anomaly_summary"]["backend"] == "exact_knn"


def _shift_image_right(data: bytearray, width: int, height: int, shift_px: int) -> bytearray:
    shifted = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            source_x = max(0, x - shift_px)
            shifted[y * width + x] = data[y * width + source_x]
    return shifted


def _ecc_test_frame(light_id: str, data: bytearray, width: int, height: int) -> LightFrame:
    return LightFrame(
        camera_id="TOP_BACK",
        light_id=light_id,
        frame_index=1,
        light_seq_index=0,
        width=width,
        height=height,
        channels=1,
        stride_bytes=width,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=1_000,
        exposure_us=800,
        gain=1.0,
        calibration_id="calib/simulated_v1",
        image_crc32=0,
        image=memoryview(data),
    )


def test_patchcore_knn_backend_applies_pca_projection(tmp_path: Path) -> None:
    pca_path = tmp_path / "pca.json"
    pca_path.write_text(
        json.dumps(
            {
                "version": "pca_v1",
                "mean": [0.0] * 10,
                "components": [
                    [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                ],
            }
        ),
        encoding="utf-8",
    )
    bank_path = tmp_path / "memory_bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "version": "bank_v1",
                "model_family": "patchcore",
                "embedding_dim": 2,
                "coreset_ratio": 1.0,
                "pca_version": "pca_v1",
                "vectors": [[0.0, 0.0]],
            }
        ),
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
        class_names=("unknown_anomaly",),
        input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        pca_path=str(pca_path),
        pca_version="pca_v1",
        memory_bank_path=str(bank_path),
        score_threshold=0.01,
        anomaly_score_scale=2.0,
        knn_k=1,
    )
    recipe = replace(recipe, models={**recipe.models, "unknown_safety_net": patchcore})
    pipeline = InspectionPipeline()

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.defects
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "unknown_safety_net"]
    assert summaries[0]["pca_summary"] == {"version": "pca_v1", "input_dim": 10, "output_dim": 2}


def test_patchcore_memory_bank_builder_uses_coreset_stride(tmp_path: Path) -> None:
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

    assert output.exists()
    assert bank["embedding_dim"] == 2
    assert bank["pca_version"] == "pca_v1"
    assert bank["faiss_enabled"] is True
    assert len(bank["vectors"]) == 2


def test_patchcore_faiss_metadata_falls_back_to_exact_knn_when_index_missing(tmp_path: Path) -> None:
    bank_path = tmp_path / "memory_bank.json"
    bank_path.write_text(
        json.dumps(
            {
                "version": "bank_v1",
                "model_family": "patchcore",
                "embedding_dim": 10,
                "coreset_ratio": 1.0,
                "pca_version": None,
                "faiss_enabled": True,
                "vectors": [[0.0] * 10],
            }
        ),
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
        class_names=("unknown_anomaly",),
        input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        memory_bank_path=str(bank_path),
        faiss_index_path=str(tmp_path / "missing.faiss"),
        score_threshold=0.01,
        anomaly_score_scale=2.0,
        knn_k=1,
    )
    recipe = replace(recipe, models={**recipe.models, "unknown_safety_net": patchcore})
    pipeline = InspectionPipeline()

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.defects
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "unknown_safety_net"]
    assert summaries[0]["anomaly_summary"]["backend"] == "exact_knn"
    assert summaries[0]["anomaly_summary"]["fallback_reason"] == "faiss_index_missing"
