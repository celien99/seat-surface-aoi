import pytest

from python_detector.config.recipe_schema import RecipeManager, RecipeValidationError, recipe_from_dict


def test_recipe_manager_loads_default_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.recipe_id == "seat_a_black_leather_v1"
    assert recipe.cameras[0].camera_id == "TOP_BACK"
    assert recipe.models["fake_default"].backend == "fake"


def test_recipe_rejects_missing_required_field() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict({"recipe_id": "bad"})


def test_recipe_rejects_required_light_not_in_light_order() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad",
                "sku": "sku",
                "light_order": ["DIFFUSE"],
                "quality": {"required_lights": ["POLAR_DIFFUSE"]},
                "cameras": {"TOP": {"light_order": ["DIFFUSE"]}},
            }
        )


def test_recipe_rejects_patchcore_as_primary_detector() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_patchcore",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "patchcore_primary"}},
                "models": {
                    "patchcore_primary": {
                        "backend": "fake",
                        "model_family": "patchcore",
                        "role": "primary",
                    }
                },
            }
        )


def test_recipe_rejects_safety_net_as_primary_roi_model() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_safety_net_ref",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"roi_models": {"full": "unknown_safety_net"}}},
                "models": {
                    "unknown_safety_net": {
                        "backend": "fake",
                        "model_family": "patchcore",
                        "role": "safety_net",
                    }
                },
            }
        )


def test_recipe_accepts_roi_primary_and_safety_net_models() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.model_key_for("TOP_BACK", "full") == "fake_default"
    assert recipe.safety_net_model_keys_for("TOP_BACK", "full") == ("unknown_safety_net",)


def test_recipe_parses_onnx_model_io_contract() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "onnx_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": {"TOP": {"model_key": "scratch_onnx"}},
            "models": {
                "scratch_onnx": {
                    "backend": "onnx",
                    "model_path": "models/scratch.onnx",
                    "model_family": "supervised",
                    "role": "primary",
                    "input_channels": ["ch0_diffuse", "ch4_high_max_min"],
                    "input_scale": 255.0,
                    "class_names": ["scratch", "dent"],
                    "output_decode": "detection_rows",
                    "bbox_format": "xyxy_normalized",
                    "score_threshold": 0.25,
                }
            },
        }
    )
    model = recipe.models["scratch_onnx"]
    assert model.input_channels == ("ch0_diffuse", "ch4_high_max_min")
    assert model.class_names == ("scratch", "dent")
    assert model.output_decode == "detection_rows"
    assert model.bbox_format == "xyxy_normalized"
    assert model.score_threshold == 0.25


def test_recipe_parses_fusion_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.fusion.iou_threshold == 0.5
    assert recipe.fusion.class_aware is True
    assert recipe.fusion.max_candidates_per_roi == 16


def test_recipe_parses_capture_quality_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.quality.min_motion_gradient == 1.0
    assert recipe.quality.max_light_mean_delta == 80.0
    assert recipe.quality.max_capture_span_us == 500_000
    assert recipe.quality.max_exposure_delta_us == 200
    assert recipe.quality.max_gain_delta == 0.2
    assert recipe.quality.require_monotonic_timestamps is True
    assert recipe.quality.require_unique_frame_indices is True


def test_recipe_rejects_invalid_fusion_threshold() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_fusion",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "fusion": {"iou_threshold": 1.5},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_invalid_capture_quality_config() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"max_capture_span_us": -1},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="quality.min_motion_gradient"):
        recipe_from_dict(
            {
                "recipe_id": "bad_motion_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"min_motion_gradient": -0.1},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="quality.max_light_mean_delta"):
        recipe_from_dict(
            {
                "recipe_id": "bad_light_stability_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"max_light_mean_delta": -1.0},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="quality.min_mean_gray 不能大于 max_mean_gray"):
        recipe_from_dict(
            {
                "recipe_id": "bad_gray_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"min_mean_gray": 200, "max_mean_gray": 100},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="quality.max_saturation_ratio"):
        recipe_from_dict(
            {
                "recipe_id": "bad_saturation_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"max_saturation_ratio": 1.2},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_threshold_recheck_above_ng() -> None:
    with pytest.raises(RecipeValidationError, match="recheck_score 不能大于 ng_score"):
        recipe_from_dict(
            {
                "recipe_id": "bad_threshold_order",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "thresholds": {"scratch": {"ng_score": 0.3, "recheck_score": 0.5, "min_area_px": 1}},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_invalid_threshold_ranges() -> None:
    with pytest.raises(RecipeValidationError, match="thresholds.scratch.ng_score"):
        recipe_from_dict(
            {
                "recipe_id": "bad_threshold_score",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "thresholds": {"scratch": {"ng_score": 1.2, "recheck_score": 0.2, "min_area_px": 1}},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="thresholds.scratch.min_area_px"):
        recipe_from_dict(
            {
                "recipe_id": "bad_threshold_area",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "thresholds": {"scratch": {"ng_score": 0.5, "recheck_score": 0.2, "min_area_px": -1}},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_invalid_model_score_threshold() -> None:
    with pytest.raises(RecipeValidationError, match="models.detector.score_threshold"):
        recipe_from_dict(
            {
                "recipe_id": "bad_model_score",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "score_threshold": -0.1,
                    }
                },
            }
        )
