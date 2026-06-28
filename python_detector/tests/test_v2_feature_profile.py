from dataclasses import replace

import pytest

from python_detector.config.recipe_schema import RecipeManager, recipe_from_dict
from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.feature_builder import FeatureBuilder
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.reflectance_cube import ReflectanceCube, RegistrationReport
from training_tools.job_fixture import make_simulated_job


def test_v2_feature_profile_uses_recipe_declared_channels() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    prepared = pipeline.preprocessor.run(make_simulated_job(), recipe)
    cubes = pipeline.reflectance_cube_builder.build(make_simulated_job(), prepared, recipe)
    features = FeatureBuilder().build(cubes, recipe)
    first = features[0]
    expected_channels = recipe.models[first.model_key].input_channels
    assert first.model_key == "fake_default"
    assert set(expected_channels).issubset(first.features)
    assert "optional_dark_low_lr_diff" not in first.features
    assert first.tensor_channel_names == expected_channels
    assert first.feature_shape_hw == (48, 64)
    assert len(first.features[expected_channels[0]]) == 48 * 64
    assert len(first.tensor_nchw or []) == 1
    assert len(first.tensor_nchw[0]) == len(expected_channels)
    assert len(first.tensor_nchw[0][0]) == 48
    assert len(first.tensor_nchw[0][0][0]) == 64
    assert 0.0 <= first.tensor_nchw[0][0][0][0] <= 1.0
    assert first.evidence_lights_by_channel[expected_channels[0]] == ("DIFFUSE",)


def test_feature_builder_supports_arbitrary_light_channel_expressions() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        models={
            **recipe.models,
            "fake_default": replace(
                recipe.models["fake_default"],
                input_channels=(
                    "light:DIFFUSE",
                    "light:POLAR_DIFFUSE",
                    "max_min:HIGH_LEFT:HIGH_RIGHT:LOW_LEFT",
                    "abs_diff:LOW_LEFT:LOW_RIGHT",
                    "local_contrast:DIFFUSE",
                ),
            ),
        },
    )
    cube = ReflectanceCube(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM_1",
        camera_id="TOP_BACK",
        roi_name="seat",
        base_light_id="POLAR_DIFFUSE",
        light_order=("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT", "LOW_LEFT", "LOW_RIGHT"),
        frames={
            "DIFFUSE": _roi_frame("DIFFUSE", 4, 4),
            "POLAR_DIFFUSE": _roi_frame("POLAR_DIFFUSE", 4, 4),
            "HIGH_LEFT": _roi_frame("HIGH_LEFT", 4, 4),
            "HIGH_RIGHT": _roi_frame("HIGH_RIGHT", 4, 4),
            "LOW_LEFT": _roi_frame("LOW_LEFT", 4, 4),
            "LOW_RIGHT": _roi_frame("LOW_RIGHT", 4, 4),
        },
        registration=RegistrationReport(
            camera_id="TOP_BACK",
            roi_name="seat",
            base_light_id="POLAR_DIFFUSE",
            calibration_id="calib/simulated_v1",
            max_error_px=0.0,
            mean_error_px=0.0,
            method="fixed_calibration",
            is_pass=True,
            message="ok",
        ),
        pixel_size_mm=0.12,
        calibration_id="calib/simulated_v1",
        roi_bbox_xyxy_pixel=(0, 0, 3, 3),
    )

    first = FeatureBuilder().build([cube], recipe)[0]

    assert first.tensor_channel_names == (
        "light:DIFFUSE",
        "light:POLAR_DIFFUSE",
        "max_min:HIGH_LEFT:HIGH_RIGHT:LOW_LEFT",
        "abs_diff:LOW_LEFT:LOW_RIGHT",
        "local_contrast:DIFFUSE",
    )
    assert first.evidence_lights_by_channel["max_min:HIGH_LEFT:HIGH_RIGHT:LOW_LEFT"] == (
        "HIGH_LEFT",
        "HIGH_RIGHT",
        "LOW_LEFT",
    )
    assert first.evidence_lights_by_channel["abs_diff:LOW_LEFT:LOW_RIGHT"] == ("LOW_LEFT", "LOW_RIGHT")


def test_feature_builder_defaults_model_channels_from_two_light_recipe() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "two_light_recipe",
            "sku": "sku",
            "light_order": ["KEY", "SIDE"],
            "v4_lights": {
                "semantic_to_light_id": {
                    "DOME": "KEY",
                    "DARKFIELD_L": "SIDE",
                    "BRIGHTFIELD": "KEY",
                }
            },
            "quality": {"required_lights": ["KEY", "SIDE"]},
            "registration": {"base_light_id": "KEY", "base_light_fallback": "KEY"},
            "cameras": {
                "TOP": {
                    "model_key": "detector",
                    "base_light_id": "KEY",
                    "light_order": ["KEY", "SIDE"],
                }
            },
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.20},
            "models": {"detector": {"backend": "fake", "role": "primary"}},
        }
    )
    cube = ReflectanceCube(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM_1",
        camera_id="TOP",
        roi_name="seat",
        base_light_id="KEY",
        light_order=("KEY", "SIDE"),
        frames={
            "KEY": _roi_frame("KEY", 4, 4),
            "SIDE": _roi_frame("SIDE", 4, 4),
        },
        registration=RegistrationReport(
            camera_id="TOP",
            roi_name="seat",
            base_light_id="KEY",
            calibration_id="calib/simulated_v1",
            max_error_px=0.0,
            mean_error_px=0.0,
            method="fixed_calibration",
            is_pass=True,
            message="ok",
        ),
        pixel_size_mm=0.12,
        calibration_id="calib/simulated_v1",
        roi_bbox_xyxy_pixel=(0, 0, 3, 3),
    )

    first = FeatureBuilder().build([cube], recipe)[0]

    assert recipe.models["detector"].input_channels == ("light:KEY", "light:SIDE")
    assert first.tensor_channel_names == ("light:KEY", "light:SIDE")
    assert len(first.tensor_nchw[0]) == 2
    assert first.evidence_lights_by_channel == {
        "light:KEY": ("KEY",),
        "light:SIDE": ("SIDE",),
    }


def test_v2_roi_features_include_primary_and_safety_net_models() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    prepared = pipeline.preprocessor.run(make_simulated_job(), recipe)
    cubes = pipeline.reflectance_cube_builder.build(make_simulated_job(), prepared, recipe)
    features = FeatureBuilder().build(cubes, recipe)
    model_keys = {(group.camera_id, group.roi_name, group.model_key) for group in features}
    assert ("TOP_BACK", "seat", "fake_default") in model_keys
    assert ("TOP_BACK", "seat", "patchcore_safety_net") in model_keys


def test_feature_builder_rejects_mismatched_feature_source_shapes() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(
        recipe,
        models={
            **recipe.models,
            "fake_default": replace(
                recipe.models["fake_default"],
                input_channels=(
                    "light:DIFFUSE",
                    "light:POLAR_DIFFUSE",
                    "light:HIGH_LEFT",
                    "max_min:HIGH_LEFT:HIGH_RIGHT",
                ),
            ),
        },
    )
    cube = ReflectanceCube(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM_1",
        camera_id="TOP_BACK",
        roi_name="seat",
        base_light_id="POLAR_DIFFUSE",
        light_order=("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"),
        frames={
            "DIFFUSE": _roi_frame("DIFFUSE", 4, 4),
            "POLAR_DIFFUSE": _roi_frame("POLAR_DIFFUSE", 4, 4),
            "HIGH_LEFT": _roi_frame("HIGH_LEFT", 4, 4),
            "HIGH_RIGHT": _roi_frame("HIGH_RIGHT", 3, 4),
        },
        registration=RegistrationReport(
            camera_id="TOP_BACK",
            roi_name="seat",
            base_light_id="POLAR_DIFFUSE",
            calibration_id="calib/simulated_v1",
            max_error_px=0.0,
            mean_error_px=0.0,
            method="fixed_calibration",
            is_pass=True,
            message="ok",
        ),
        pixel_size_mm=0.12,
        calibration_id="calib/simulated_v1",
        roi_bbox_xyxy_pixel=(0, 0, 3, 3),
    )

    with pytest.raises(ValueError, match="max_min feature source length mismatch"):
        FeatureBuilder().build([cube], recipe)


def test_feature_builder_does_not_read_unrequested_extension_channels() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    cube = ReflectanceCube(
        sequence_id=1,
        trigger_id=1001,
        seat_id="SIM_1",
        camera_id="TOP_BACK",
        roi_name="seat",
        base_light_id="POLAR_DIFFUSE",
        light_order=("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"),
        frames={
            "DIFFUSE": _roi_frame("DIFFUSE", 4, 4),
            "POLAR_DIFFUSE": _roi_frame("POLAR_DIFFUSE", 4, 4),
            "HIGH_LEFT": _roi_frame("HIGH_LEFT", 4, 4),
            "HIGH_RIGHT": _roi_frame("HIGH_RIGHT", 3, 4),
        },
        registration=RegistrationReport(
            camera_id="TOP_BACK",
            roi_name="seat",
            base_light_id="POLAR_DIFFUSE",
            calibration_id="calib/simulated_v1",
            max_error_px=0.0,
            mean_error_px=0.0,
            method="fixed_calibration",
            is_pass=True,
            message="ok",
        ),
        pixel_size_mm=0.12,
        calibration_id="calib/simulated_v1",
        roi_bbox_xyxy_pixel=(0, 0, 3, 3),
    )

    first = FeatureBuilder().build([cube], recipe)[0]

    expected_channels = recipe.models[first.model_key].input_channels
    assert first.tensor_channel_names == expected_channels
    assert set(first.features) == set(expected_channels)


def _roi_frame(light_id: str, width: int, height: int) -> LightFrame:
    data = bytearray(80 + ((x + y) % 8) for y in range(height) for x in range(width))
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
