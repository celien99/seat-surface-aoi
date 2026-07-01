import pytest

from python_detector.config.recipe_schema import RecipeManager, RecipeValidationError, recipe_from_dict


def test_recipe_manager_loads_default_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.recipe_id == "seat_a_black_leather_v1"
    assert recipe.cameras[0].camera_id == "TOP_BACK"
    assert recipe.models["fake_default"].backend == "fake"
    assert recipe.models["fake_default"].input_channels == (
        "light:DIFFUSE",
        "light:POLAR_DIFFUSE",
        "light:HIGH_LEFT",
    )


def test_recipe_manager_loads_production_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_production_v1")

    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.roi_locator.output_decode == "ultralytics_yolo_seg"
    assert recipe.roi_locator.mask_threshold == 0.5
    assert recipe.roi_locator.input_width == 1024
    assert recipe.roi_locator.input_height == 1024
    assert recipe.roi_locator.input_channels == 3
    assert recipe.registration.method == "ecc"
    assert recipe.models["patchcore_detector"].backend == "patchcore_knn"
    assert recipe.models["patchcore_detector"].role == "primary"


def test_recipe_manager_loads_robot_production_yaml() -> None:
    recipe = RecipeManager().load("seat_a_robot_flyshot_production_v1")

    assert [(camera.camera_id, camera.pose_id) for camera in recipe.cameras] == [
        ("EYE_IN_HAND", "T1_BACKREST"),
        ("EYE_IN_HAND", "T2_CUSHION"),
    ]
    assert recipe.model_key_for("EYE_IN_HAND", "seat", "T2_CUSHION") == "patchcore_detector"


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


def test_recipe_defaults_required_lights_and_registration_from_light_order() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "two_light_defaults",
            "sku": "sku",
            "light_order": ["KEY", "SIDE"],
            "cameras": {"TOP": {"model_key": "detector"}},
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.20},
            "models": {"detector": {"backend": "fake", "role": "primary"}},
        }
    )

    assert recipe.quality.required_lights == ("KEY", "SIDE")
    assert recipe.registration.base_light_id == "KEY"
    assert recipe.registration.base_light_fallback == "KEY"
    assert recipe.semantic_light_id("DOME") == "KEY"
    assert recipe.semantic_light_id("DARKFIELD_L") == "DARKFIELD_L"
    assert recipe.camera_defaults.light_order == ("KEY", "SIDE")
    assert recipe.models["detector"].input_channels == ("light:KEY", "light:SIDE")


def test_recipe_rejects_registration_lights_not_in_light_order() -> None:
    with pytest.raises(RecipeValidationError, match="registration.base_light_id 不在 light_order 中"):
        recipe_from_dict(
            {
                "recipe_id": "bad_base_light",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "registration": {"base_light_id": "LOW_LEFT", "base_light_fallback": "DIFFUSE"},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="registration.base_light_fallback 必须属于 quality.required_lights"):
        recipe_from_dict(
            {
                "recipe_id": "bad_fallback_light",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT", "LOW_LEFT"],
                "quality": {"required_lights": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"]},
                "registration": {"base_light_id": "POLAR_DIFFUSE", "base_light_fallback": "LOW_LEFT"},
                "cameras": {
                    "TOP": {
                        "model_key": "default",
                        "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT", "LOW_LEFT"],
                    }
                },
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_camera_light_order_missing_required_lights() -> None:
    with pytest.raises(RecipeValidationError, match="cameras.TOP.light_order 缺少 required_lights"):
        recipe_from_dict(
            {
                "recipe_id": "bad_camera_required_lights",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"required_lights": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"]},
                "cameras": {
                    "TOP": {
                        "model_key": "default",
                        "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"],
                    }
                },
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_camera_base_light_not_in_camera_light_order() -> None:
    with pytest.raises(RecipeValidationError, match="cameras.TOP.base_light_id 不在该机位 light_order 中"):
        recipe_from_dict(
            {
                "recipe_id": "bad_camera_base_light",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {
                    "TOP": {
                        "model_key": "default",
                        "base_light_id": "LOW_LEFT",
                        "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                    }
                },
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_accepts_patchcore_as_primary_detector() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "patchcore_primary",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": {"TOP": {"model_key": "patchcore_primary"}},
            "decision_threshold": {"ng_score": 0.55, "recheck_score": 0.20},
            "models": {
                "patchcore_primary": {
                    "backend": "patchcore_knn",
                    "model_family": "patchcore",
                    "role": "primary",
                    "embedding_backend": "statistical",
                    "memory_bank_path": "model/patchcore/seat_patchcore_bank.json",
                }
            },
        }
    )

    assert recipe.model_key_for("TOP", "seat") == "patchcore_primary"


def test_recipe_rejects_safety_net_as_primary_roi_model() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_safety_net_ref",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"roi_models": {"seat": "patchcore_safety_net"}}},
                "models": {
                    "patchcore_safety_net": {
                        "backend": "fake",
                        "model_family": "patchcore",
                        "role": "safety_net",
                    }
                },
            }
        )


def test_recipe_accepts_roi_primary_and_safety_net_models() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.model_key_for("TOP_BACK", "seat") == "fake_default"
    assert recipe.safety_net_model_keys_for("TOP_BACK", "seat") == ("patchcore_safety_net",)


def test_recipe_applies_camera_defaults_to_reduce_per_camera_repetition() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "camera_defaults_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"],
            "v4_lights": {
                "semantic_to_light_id": {
                    "DOME": "DIFFUSE",
                    "DARKFIELD_L": "HIGH_LEFT",
                    "BRIGHTFIELD": "POLAR_DIFFUSE",
                }
            },
            "camera_defaults": {
                "model_key": "detector",
                "safety_net_model_key": "patchcore",
                "roi_template": "python_detector/config/roi/production_full_roi.yaml",
                "base_light_id": "POLAR_DIFFUSE",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"],
                "roi_models": {"seat": "detector"},
                "roi_safety_net_models": {"seat": "patchcore"},
            },
            "quality": {"required_lights": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"]},
            "cameras": {
                "TOP_BACK": {"calibration_id": "calib/top_back_production_v1"},
                "TOP_CUSHION": {"calibration_id": "calib/top_cushion_production_v1"},
            },
            "decision_threshold": {"ng_score": 0.55, "recheck_score": 0.20},
            "models": {
                "detector": {"backend": "fake", "role": "primary"},
                "patchcore": {
                    "backend": "fake",
                    "model_family": "patchcore",
                    "role": "safety_net",
                },
            },
        }
    )

    top_back = recipe.camera("TOP_BACK")
    assert top_back is not None
    assert top_back.model_key == "detector"
    assert top_back.safety_net_model_key == "patchcore"
    assert top_back.roi_template == "python_detector/config/roi/production_full_roi.yaml"
    assert top_back.calibration_id == "calib/top_back_production_v1"
    assert top_back.light_order == ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    assert recipe.model_key_for("TOP_CUSHION", "seat") == "detector"
    assert recipe.safety_net_model_keys_for("TOP_CUSHION", "seat") == ("patchcore",)


def test_recipe_parses_onnx_model_io_contract() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "onnx_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": {"TOP": {"model_key": "defect_onnx"}},
            "decision_threshold": {"ng_score": 0.30, "recheck_score": 0.18, "min_area_px": 20},
            "models": {
                "defect_onnx": {
                    "backend": "onnx",
                    "model_path": "models/defect.onnx",
                    "model_family": "supervised",
                    "role": "primary",
                    "input_channels": ["light:DIFFUSE", "max_min:HIGH_LEFT:HIGH_RIGHT"],
                    "input_scale": 255.0,
                    "output_decode": "detection_rows",
                    "bbox_format": "xyxy_normalized",
                    "score_threshold": 0.25,
                }
            },
        }
    )
    model = recipe.models["defect_onnx"]
    assert model.input_channels == ("light:DIFFUSE", "max_min:HIGH_LEFT:HIGH_RIGHT")
    assert model.output_decode == "detection_rows"
    assert model.bbox_format == "xyxy_normalized"
    assert model.score_threshold == 0.25


def test_recipe_accepts_ultralytics_yolo_decode_for_training_exports() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "ultralytics_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "roi_locator": {
                "backend": "onnx_yolo",
                "model_path": "model/roi_yolo/seat_roi_yolo.onnx",
                "output_decode": "ultralytics_yolo",
            },
            "cameras": {"TOP": {"model_key": "detector"}},
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
            "models": {
                "detector": {
                    "backend": "onnx",
                    "model_path": "experiments/supervised_defect/seat_defect_presence.onnx",
                    "role": "primary",
                    "output_decode": "ultralytics_yolo",
                }
            },
        }
    )

    assert recipe.roi_locator.output_decode == "ultralytics_yolo"
    assert recipe.models["detector"].output_decode == "ultralytics_yolo"


def test_recipe_accepts_yolo_seg_roi_locator() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "seg_roi_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "roi_locator": {
                "backend": "onnx_yolo_seg",
                "model_path": "model/roi_yolo/seat_roi_seg.onnx",
                "output_decode": "ultralytics_yolo_seg",
                "mask_threshold": 0.45,
                "min_mask_area_px": 32,
                "min_mask_area_ratio": 0.05,
                "max_mask_area_ratio": 0.80,
                "input_width": 1024,
                "input_height": 1024,
                "input_channels": 3,
            },
            "cameras": {"TOP": {"model_key": "detector"}},
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
            "models": {
                "detector": {
                    "backend": "fake",
                    "role": "primary",
                }
            },
        }
    )

    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.roi_locator.model_path == "model/roi_yolo/seat_roi_seg.onnx"
    assert recipe.roi_locator.output_decode == "ultralytics_yolo_seg"
    assert recipe.roi_locator.min_mask_area_px == 32
    assert recipe.roi_locator.min_mask_area_ratio == 0.05
    assert recipe.roi_locator.input_channels == 3


def test_recipe_rejects_yolo_seg_with_bbox_decode() -> None:
    with pytest.raises(RecipeValidationError, match="onnx_yolo_seg"):
        recipe_from_dict(
            {
                "recipe_id": "bad_seg_decode",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "roi_locator": {
                    "backend": "onnx_yolo_seg",
                    "model_path": "model/roi_yolo/seat_roi_seg.onnx",
                    "output_decode": "ultralytics_yolo",
                },
                "cameras": {"TOP": {"model_key": "detector"}},
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
                "models": {"detector": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_accepts_arbitrary_positive_roi_locator_input_channels() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "two_channel_roi",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "roi_locator": {
                "backend": "onnx_yolo_seg",
                "model_path": "model/roi_yolo/seat_roi_seg.onnx",
                "output_decode": "ultralytics_yolo_seg",
                "input_channels": 2,
            },
            "cameras": {"TOP": {"model_key": "detector"}},
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
            "models": {"detector": {"backend": "fake", "role": "primary"}},
        }
    )

    assert recipe.roi_locator.input_channels == 2


def test_recipe_rejects_non_positive_roi_locator_input_channels() -> None:
    with pytest.raises(RecipeValidationError, match="input_channels"):
        recipe_from_dict(
            {
                "recipe_id": "bad_roi_channels",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "roi_locator": {
                    "backend": "onnx_yolo_seg",
                    "model_path": "model/roi_yolo/seat_roi_seg.onnx",
                    "output_decode": "ultralytics_yolo_seg",
                    "input_channels": 0,
                },
                "cameras": {"TOP": {"model_key": "detector"}},
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
                "models": {"detector": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_parses_patchcore_faiss_index_path() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "patchcore_faiss_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": {
                "TOP": {
                    "model_key": "default",
                    "safety_net_model_key": "patchcore_safety_net",
                }
            },
            "decision_threshold": {"ng_score": 0.55, "recheck_score": 0.20, "min_area_px": 1},
            "models": {
                "default": {"backend": "fake", "role": "primary"},
                "patchcore_safety_net": {
                    "backend": "patchcore_knn",
                    "model_family": "patchcore",
                    "role": "safety_net",
                    "embedding_backend": "statistical",
                    "memory_bank_path": "model/patchcore/seat_patchcore_bank.json",
                    "faiss_index_path": "model/patchcore/seat_patchcore.faiss",
                },
            },
        }
    )

    assert recipe.models["patchcore_safety_net"].faiss_index_path == "model/patchcore/seat_patchcore.faiss"


def test_recipe_parses_fusion_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.fusion.iou_threshold == 0.5
    assert recipe.fusion.max_candidates_per_roi == 16


def test_recipe_preserves_list_cameras_with_same_camera_different_pose() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "robot_views",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": [
                {"camera_id": "EYE_IN_HAND", "pose_id": "T1_BACKREST", "model_key": "default"},
                {"camera_id": "EYE_IN_HAND", "pose_id": "T2_CUSHION", "model_key": "default"},
            ],
            "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
            "models": {"default": {"backend": "fake", "role": "primary"}},
        }
    )

    assert [(camera.camera_id, camera.pose_id) for camera in recipe.cameras] == [
        ("EYE_IN_HAND", "T1_BACKREST"),
        ("EYE_IN_HAND", "T2_CUSHION"),
    ]
    assert recipe.model_key_for("EYE_IN_HAND", "seat", "T2_CUSHION") == "default"


def test_default_camera_accepts_dynamic_pose_without_explicit_pose_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")

    assert recipe.accepts_camera_pose("TOP_BACK", "PITCH_15") is True
    assert recipe.pose_uses_default_camera("TOP_BACK", "PITCH_15") is True
    assert recipe.camera("TOP_BACK", "PITCH_15") == recipe.default_camera("TOP_BACK")
    assert recipe.model_key_for("TOP_BACK", "seat", "PITCH_15") == "fake_default"


def test_explicit_robot_pose_recipe_rejects_unknown_pose_fallback() -> None:
    recipe = RecipeManager().load("seat_a_robot_flyshot_v1")

    assert recipe.accepts_camera_pose("EYE_IN_HAND", "T3_UNKNOWN") is False
    assert recipe.pose_uses_default_camera("EYE_IN_HAND", "T3_UNKNOWN") is False
    assert recipe.camera("EYE_IN_HAND", "T3_UNKNOWN") is None
    assert recipe.model_key_for("EYE_IN_HAND", "seat", "T3_UNKNOWN") == "default"


def test_recipe_rejects_duplicate_list_camera_pose() -> None:
    with pytest.raises(RecipeValidationError, match="重复视角配置"):
        recipe_from_dict(
            {
                "recipe_id": "duplicate_robot_view",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": [
                    {"camera_id": "EYE_IN_HAND", "pose_id": "T1_BACKREST", "model_key": "default"},
                    {"camera_id": "EYE_IN_HAND", "pose_id": "T1_BACKREST", "model_key": "default"},
                ],
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.2},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_removed_top_level_thresholds() -> None:
    with pytest.raises(RecipeValidationError, match="thresholds 已移除"):
        recipe_from_dict(
            {
                "recipe_id": "removed_thresholds",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "thresholds": {"defect": {"ng_score": 0.35, "recheck_score": 0.20}},
                "models": {"detector": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_removed_model_class_names() -> None:
    with pytest.raises(RecipeValidationError, match="models.detector.class_names 已移除"):
        recipe_from_dict(
            {
                "recipe_id": "removed_model_class_names",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "decision_threshold": {"ng_score": 0.35, "recheck_score": 0.20, "min_area_px": 8},
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "class_names": ["defect", "defect"],
                    }
                },
            }
        )


def test_recipe_rejects_removed_fusion_class_aware() -> None:
    with pytest.raises(RecipeValidationError, match="fusion.class_aware 已移除"):
        recipe_from_dict(
            {
                "recipe_id": "removed_class_aware",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "fusion": {"class_aware": True},
                "cameras": {"TOP": {"model_key": "detector"}},
                "models": {"detector": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_parses_capture_quality_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.quality.max_saturation_ratio == 0.01
    assert recipe.quality.max_dark_ratio == 0.01
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
    with pytest.raises(RecipeValidationError, match="quality.max_dark_ratio"):
        recipe_from_dict(
            {
                "recipe_id": "bad_dark_quality",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "quality": {"max_dark_ratio": -0.1},
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
                "decision_threshold": {"ng_score": 0.3, "recheck_score": 0.5, "min_area_px": 1},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )


def test_recipe_rejects_invalid_threshold_ranges() -> None:
    with pytest.raises(RecipeValidationError, match="decision_threshold.ng_score"):
        recipe_from_dict(
            {
                "recipe_id": "bad_threshold_score",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "decision_threshold": {"ng_score": 1.2, "recheck_score": 0.2, "min_area_px": 1},
                "cameras": {"TOP": {"model_key": "default"}},
                "models": {"default": {"backend": "fake", "role": "primary"}},
            }
        )
    with pytest.raises(RecipeValidationError, match="decision_threshold.min_area_px"):
        recipe_from_dict(
            {
                "recipe_id": "bad_threshold_area",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "decision_threshold": {"ng_score": 0.5, "recheck_score": 0.2, "min_area_px": -1},
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


def test_recipe_rejects_patchcore_model_score_threshold() -> None:
    with pytest.raises(RecipeValidationError, match="PatchCore 判定阈值必须来自 memory bank thresholds"):
        recipe_from_dict(
            {
                "recipe_id": "bad_patchcore_score_threshold",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"],
                "cameras": {"TOP": {"model_key": "patchcore"}},
                "models": {
                    "patchcore": {
                        "backend": "patchcore_knn",
                        "model_family": "patchcore",
                        "role": "primary",
                        "embedding_backend": "statistical",
                        "memory_bank_path": "model/patchcore/seat_patchcore_bank.json",
                        "score_threshold": 0.1,
                    }
                },
            }
        )


def test_recipe_rejects_unsafe_model_io_config() -> None:
    with pytest.raises(RecipeValidationError, match="models.detector.input_channels 存在重复项"):
        recipe_from_dict(
            {
                "recipe_id": "bad_duplicate_input_channels",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "input_channels": ["light:DIFFUSE", "light:DIFFUSE"],
                    }
                },
            }
        )
    with pytest.raises(RecipeValidationError, match="models.detector.fake_mode"):
        recipe_from_dict(
            {
                "recipe_id": "bad_fake_mode",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "fake_mode": "maybe_ok",
                    }
                },
            }
        )
