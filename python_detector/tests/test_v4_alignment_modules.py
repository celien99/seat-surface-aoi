from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
import numpy as np

from python_detector.config.calibration_manager import Calibration, RoiTemplate
from python_detector.config.recipe_schema import CameraDefaults, ModelConfig, RecipeManager, RecipeValidationError, recipe_from_dict
from python_detector.ipc.data_types import LightFrame, SeatInspectionJob
from python_detector.models.yolo_decode import SegmentationCandidate
from python_detector.models.inference_engine import InferenceEngine, ModelRegistry
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.preprocessor import PreparedBundle, Preprocessor
from python_detector.pipeline.reflectance_cube import ReflectanceCubeBuilder
from python_detector.pipeline.roi_locator import RoiLocator
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.job_fixture import make_simulated_job


def _write_patchcore_bank(
    bank_path: Path,
    vectors: np.ndarray,
    *,
    pca_version: str | None = None,
    faiss_enabled: bool = False,
) -> None:
    vectors = np.asarray(vectors, dtype=np.float32)
    vectors_path = bank_path.with_suffix(".npy")
    np.save(vectors_path, vectors)
    bank_path.write_text(
        json.dumps(
            {
                "version": "bank_v1",
                "model_family": "patchcore",
                "embedding_dim": int(vectors.shape[1]),
                "coreset_ratio": 1.0,
                "pca_version": pca_version,
                "faiss_enabled": faiss_enabled,
                "vector_count": int(vectors.shape[0]),
                "vectors_path": vectors_path.name,
                "distance_mean": 0.0,
                "distance_p99": 0.01,
            }
        ),
        encoding="utf-8",
    )


def test_default_recipe_declares_v4_light_mapping_and_roi_locator() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.semantic_light_id("DOME") == "DIFFUSE"
    assert recipe.semantic_light_id("DARKFIELD_L") == "HIGH_LEFT"
    assert recipe.semantic_light_id("BRIGHTFIELD") == "POLAR_DIFFUSE"
    assert recipe.roi_locator.backend == "template"
    assert recipe.registration.method == "fixed_calibration"


def test_recipe_rejects_v4_semantic_light_not_in_light_order() -> None:
    with pytest.raises(RecipeValidationError, match="v4_lights.semantic_to_light_id.DOME 不在 light_order"):
        recipe_from_dict(
            {
                "recipe_id": "bad_v4_light",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "v4_lights": {
                    "semantic_to_light_id": {
                        "DOME": "LIGHT_99",
                        "DARKFIELD_L": "HIGH_LEFT",
                        "BRIGHTFIELD": "POLAR_DIFFUSE",
                    }
                },
                "cameras": {"TOP": {"model_key": "default"}},
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
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
    assert report.locations[0].roi_name == "seat"
    assert report.locations[0].confidence == pytest.approx(0.99)


class DuplicateRoiLocator(RoiLocator):
    def _fake_yolo_rows(self, templates, recipe):  # type: ignore[no-untyped-def]
        return [
            [0.0, 0.0, 63.0, 47.0, 0.99, 0.0],
            [1.0, 0.0, 63.0, 47.0, 0.98, 0.0],
        ]


class SegRoiLocator(RoiLocator):
    def __init__(
        self,
        mask: np.ndarray,
        *,
        bbox_xyxy: tuple[float, float, float, float] | None = None,
        mask_bbox_xyxy: tuple[float, float, float, float] | None = None,
    ) -> None:
        super().__init__()
        self.mask = mask
        self.bbox_xyxy = bbox_xyxy
        self.mask_bbox_xyxy = mask_bbox_xyxy

    def _onnx_yolo_segmentation(self, dome_frame, recipe):  # type: ignore[no-untyped-def]
        return [
            SegmentationCandidate(
                bbox_xyxy=self.bbox_xyxy or (0.0, 0.0, float(dome_frame.width - 1), float(dome_frame.height - 1)),
                score=0.97,
                class_id=0,
                mask=self.mask,
                mask_bbox_xyxy=self.mask_bbox_xyxy,
            )
        ]


class DuplicateSegRoiLocator(RoiLocator):
    def __init__(self, candidates: list[SegmentationCandidate]) -> None:
        super().__init__()
        self.candidates = candidates

    def _onnx_yolo_segmentation(self, dome_frame, recipe):  # type: ignore[no-untyped-def]
        return self.candidates


def test_dome_roi_locator_rechecks_duplicate_conflicting_detections() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="fake_yolo",
            model_path="simulated-yolo.onnx",
            max_pose_error_px=4.0,
        ),
    )
    pipeline = InspectionPipeline(preprocessor=Preprocessor(roi_locator=DuplicateRoiLocator()))

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision == "RECHECK"
    assert "seat: duplicate conflicting ROI detections" in pipeline.last_context["error"]["message"]


def test_dome_roi_locator_yolo_seg_generates_runtime_polygon() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:6, 1:7] = 1
    mask[2, 1] = 0
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="onnx_yolo_seg",
            model_path="simulated-seg.onnx",
            output_decode="segmentation_rows",
            min_confidence=0.5,
            min_mask_area_px=4,
            max_pose_error_px=0.0,
        ),
    )
    pipeline = InspectionPipeline(preprocessor=Preprocessor(roi_locator=SegRoiLocator(mask)))

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision == "OK"
    report = pipeline.last_context["roi_location_reports"][0]
    assert report.backend == "onnx_yolo_seg"
    assert report.is_pass is True
    assert report.locations[0].polygon_xy == ((8, 12), (55, 12), (55, 35), (8, 35))
    assert report.locations[0].output_size == (48, 24)
    assert report.locations[0].source == "onnx_yolo_seg"
    roi_frame = pipeline.last_context["prepared_bundles"][0].rois["seat"]["DIFFUSE"]
    assert roi_frame.width == 48
    assert roi_frame.height == 24
    assert 0 in bytes(roi_frame.image)
    assert max(roi_frame.image) > 0


def test_dome_roi_locator_yolo_seg_keeps_best_overlapping_duplicate() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="onnx_yolo_seg",
            model_path="simulated-seg.onnx",
            output_decode="segmentation_rows",
            min_confidence=0.5,
            min_mask_area_px=4,
            max_pose_error_px=0.0,
        ),
    )
    mask = np.ones((8, 8), dtype=np.uint8)
    pipeline = InspectionPipeline(
        preprocessor=Preprocessor(
            roi_locator=DuplicateSegRoiLocator(
                [
                    SegmentationCandidate(
                        bbox_xyxy=(8.0, 12.0, 55.0, 35.0),
                        score=0.97,
                        class_id=0,
                        mask=mask,
                        mask_bbox_xyxy=(8.0, 12.0, 55.0, 35.0),
                    ),
                    SegmentationCandidate(
                        bbox_xyxy=(9.0, 12.0, 55.0, 35.0),
                        score=0.96,
                        class_id=0,
                        mask=mask,
                        mask_bbox_xyxy=(9.0, 12.0, 55.0, 35.0),
                    ),
                ]
            )
        )
    )

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision == "OK"
    report = pipeline.last_context["roi_location_reports"][0]
    assert report.is_pass is True
    assert report.locations[0].polygon_xy == ((8, 12), (55, 12), (55, 35), (8, 35))


def test_dome_roi_locator_yolo_seg_input_letterbox_transform() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="onnx_yolo_seg",
            model_path="simulated-seg.onnx",
            output_decode="segmentation_rows",
            input_width=128,
            input_height=128,
            input_channels=2,
            min_confidence=0.5,
            min_mask_area_px=4,
            max_pose_error_px=0.0,
        ),
    )
    frame = make_simulated_job().camera_bundles[0].light_frames["DIFFUSE"]
    locator = RoiLocator()

    tensor, transform = locator._frame_to_nchw(frame, recipe, np)  # noqa: SLF001
    mapped = locator._bbox_from_model_input((32.0, 40.0, 95.0, 87.0), transform, frame)  # noqa: SLF001

    assert tensor.shape == (1, 2, 128, 128)
    assert transform.scale == pytest.approx(2.0)
    assert transform.pad_y == pytest.approx(16.0)
    assert mapped == pytest.approx((16.0, 12.0, 47.5, 35.5))


def test_dome_roi_locator_ultralytics_seg_uses_detection_bbox_for_mask_mapping() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="onnx_yolo_seg",
            output_decode="ultralytics_yolo_seg",
            input_width=128,
            input_height=128,
            input_channels=1,
            min_confidence=0.5,
            min_mask_area_px=4,
            max_pose_error_px=0.0,
        ),
    )
    frame = make_simulated_job().camera_bundles[0].light_frames["DIFFUSE"]
    locator = RoiLocator()
    _, transform = locator._frame_to_nchw(frame, recipe, np)  # noqa: SLF001
    candidate = SegmentationCandidate(
        bbox_xyxy=(32.0, 40.0, 95.0, 87.0),
        score=0.97,
        class_id=0,
        mask=np.ones((64, 64), dtype=np.uint8),
        mask_bbox_xyxy=(0.0, 0.0, 63.0, 63.0),
    )

    mapped = locator._map_segmentation_candidate_from_model_input(  # noqa: SLF001
        candidate,
        transform,
        frame,
        output_decode="ultralytics_yolo_seg",
    )
    location = locator._location_from_segmentation(  # noqa: SLF001
        mapped,
        frame,
        {
            0: RoiTemplate(
                roi_name="seat",
                polygon_xy=((16, 12), (47, 12), (47, 35), (16, 35)),
                output_size=(64, 48),
            )
        },
        recipe,
    )

    assert mapped.mask.shape == (24, 32)
    assert mapped.mask_bbox_xyxy == pytest.approx((16.0, 12.0, 47.5, 35.5))
    assert location.polygon_xy == ((16, 12), (47, 12), (47, 35), (16, 35))


def test_dome_roi_locator_yolo_seg_rechecks_outside_safety_template() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[0:4, 0:4] = 1
    recipe = replace(
        recipe,
        roi_locator=replace(
            recipe.roi_locator,
            backend="onnx_yolo_seg",
            model_path="simulated-seg.onnx",
            output_decode="segmentation_rows",
            min_confidence=0.5,
            min_mask_area_px=4,
            max_pose_error_px=1.0,
        ),
    )
    job = make_simulated_job()
    frame = job.camera_bundles[0].light_frames["DIFFUSE"]
    templates = {
        "seat": RoiTemplate(
            roi_name="seat",
            polygon_xy=((16, 12), (47, 12), (47, 35), (16, 35)),
            output_size=(64, 48),
        )
    }

    _, report = SegRoiLocator(mask).locate("TOP_BACK", {"DIFFUSE": frame}, templates, recipe)

    assert report.is_pass is False
    assert "mask boundary error" in report.message


def test_dome_roi_locator_missing_light_returns_error_not_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, roi_locator=replace(recipe.roi_locator, backend="template"))
    job = make_simulated_job()
    for bundle in job.camera_bundles:
        bundle.light_frames.pop("DIFFUSE")

    result = InspectionPipeline().process(job, recipe)

    assert result.decision == "RECHECK"
    assert result.quality_pass is False


def test_production_roi_source_reuses_diffuse_without_extra_feature_channel() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_production_v1")
    recipe = replace(
        recipe,
        camera_defaults=CameraDefaults(
            model_key=recipe.camera_defaults.model_key,
            safety_net_model_key=recipe.camera_defaults.safety_net_model_key,
            roi_template="python_detector/config/roi/default_roi.yaml",
            calibration_id="calib/simulated_v1",
            base_light_id=recipe.camera_defaults.base_light_id,
            light_order=recipe.camera_defaults.light_order,
            roi_models=recipe.camera_defaults.roi_models,
            roi_safety_net_models=recipe.camera_defaults.roi_safety_net_models,
        ),
        cameras=tuple(
            replace(
                camera,
                calibration_id="calib/simulated_v1",
                roi_template="python_detector/config/roi/default_roi.yaml",
            )
            for camera in recipe.cameras
        ),
        roi_locator=replace(recipe.roi_locator, backend="fake_yolo", model_path="simulated-yolo.onnx"),
        models={
            **recipe.models,
            "patchcore_detector": replace(recipe.models["patchcore_detector"], backend="fake"),
        },
    )
    job = make_simulated_job()
    for bundle in job.camera_bundles:
        bundle.light_frames.pop("HIGH_RIGHT", None)
        for light_id, frame in bundle.light_frames.items():
            frame.light_seq_index = {"DIFFUSE": 0, "POLAR_DIFFUSE": 1, "HIGH_LEFT": 2}[light_id]

    pipeline = InspectionPipeline()
    result = pipeline.process(job, recipe)

    assert result.decision == "OK"
    report = pipeline.last_context["roi_location_reports"][0]
    assert report.dome_light_id == "DIFFUSE"
    assert all("DOME_ROI" not in summary["tensor_channel_names"] for summary in pipeline.last_context["feature_summary"])
    assert all(
        summary["tensor_channel_names"] == ["light:DIFFUSE", "light:POLAR_DIFFUSE", "light:HIGH_LEFT"]
        for summary in pipeline.last_context["feature_summary"]
    )


def test_production_three_strobe_light_seq_index_is_validated() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_production_v1")
    recipe = replace(
        recipe,
        camera_defaults=CameraDefaults(
            model_key=recipe.camera_defaults.model_key,
            safety_net_model_key=recipe.camera_defaults.safety_net_model_key,
            roi_template="python_detector/config/roi/default_roi.yaml",
            calibration_id="calib/simulated_v1",
            base_light_id=recipe.camera_defaults.base_light_id,
            light_order=recipe.camera_defaults.light_order,
            roi_models=recipe.camera_defaults.roi_models,
            roi_safety_net_models=recipe.camera_defaults.roi_safety_net_models,
        ),
        cameras=tuple(
            replace(
                camera,
                calibration_id="calib/simulated_v1",
                roi_template="python_detector/config/roi/default_roi.yaml",
            )
            for camera in recipe.cameras
        ),
        roi_locator=replace(recipe.roi_locator, backend="fake_yolo", model_path="simulated-yolo.onnx"),
        models={
            **recipe.models,
            "patchcore_detector": replace(recipe.models["patchcore_detector"], backend="fake"),
        },
    )
    job = make_simulated_job()
    for bundle in job.camera_bundles:
        bundle.light_frames.pop("HIGH_RIGHT", None)
        for light_id, frame in bundle.light_frames.items():
            frame.light_seq_index = {"DIFFUSE": 0, "POLAR_DIFFUSE": 1, "HIGH_LEFT": 2}[light_id]
        bundle.light_frames["DIFFUSE"].light_seq_index = 9

    pipeline = InspectionPipeline()
    result = pipeline.process(job, recipe)

    assert result.decision == "RECHECK"
    assert (
        "TOP_BACK/DIFFUSE: light_seq_index 9 does not match configured order 0"
        in pipeline.last_context["quality_report"].messages
    )


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
        base_light_id="DIFFUSE",
        light_alignment={},
        roi_templates={},
    )
    prepared = [
        PreparedBundle(
            camera_id="TOP_BACK",
            calibration=calibration,
            rois={"seat": frames},
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


def test_patchcore_knn_backend_emits_defect_and_trace(tmp_path: Path) -> None:
    bank_path = tmp_path / "memory_bank.json"
    _write_patchcore_bank(bank_path, np.asarray([[0.0] * 10], dtype=np.float32))
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
                input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        memory_bank_path=str(bank_path),
        score_threshold=0.01,
        knn_k=1,
    )
    recipe = replace(
        recipe,
        models={**recipe.models, "patchcore_safety_net": patchcore},
    )
    pipeline = InspectionPipeline(inference_engine=InferenceEngine(ModelRegistry()))

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.decision in {"RECHECK", "NG"}
    assert result.defects
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "patchcore_safety_net"]
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
    _write_patchcore_bank(bank_path, np.asarray([[0.0, 0.0]], dtype=np.float32), pca_version="pca_v1")
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
                input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        pca_path=str(pca_path),
        pca_version="pca_v1",
        memory_bank_path=str(bank_path),
        score_threshold=0.01,
        knn_k=1,
    )
    recipe = replace(recipe, models={**recipe.models, "patchcore_safety_net": patchcore})
    pipeline = InspectionPipeline()

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.defects
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "patchcore_safety_net"]
    assert summaries[0]["pca_summary"] == {"version": "pca_v1", "input_dim": 10, "output_dim": 2}


def test_patchcore_memory_bank_builder_uses_coreset_stride(tmp_path: Path) -> None:
    embeddings = tmp_path / "embeddings.npy"
    np.save(
        embeddings,
        np.asarray([[float(index), float(index + 1)] for index in range(4)], dtype=np.float32),
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
    assert bank["vector_count"] == 2


def test_patchcore_faiss_metadata_falls_back_to_exact_knn_when_index_missing(tmp_path: Path) -> None:
    bank_path = tmp_path / "memory_bank.json"
    _write_patchcore_bank(bank_path, np.asarray([[0.0] * 10], dtype=np.float32), faiss_enabled=True)
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    patchcore = ModelConfig(
        backend="patchcore_knn",
        model_family="patchcore",
        role="safety_net",
                input_channels=recipe.models["fake_default"].input_channels,
        embedding_backend="statistical",
        embedding_version="stat_v1",
        embedding_dim=10,
        memory_bank_path=str(bank_path),
        faiss_index_path=str(tmp_path / "missing.faiss"),
        score_threshold=0.01,
        knn_k=1,
    )
    recipe = replace(recipe, models={**recipe.models, "patchcore_safety_net": patchcore})
    pipeline = InspectionPipeline()

    result = pipeline.process(make_simulated_job(), recipe)

    assert result.defects
    summaries = [item for item in pipeline.last_context["feature_summary"] if item["model_key"] == "patchcore_safety_net"]
    assert summaries[0]["anomaly_summary"]["backend"] == "exact_knn"
    assert summaries[0]["anomaly_summary"]["fallback_reason"] == "faiss_index_missing"
