import pytest

from python_detector.config.recipe_schema import RecipeManager, RecipeValidationError, recipe_from_dict


def test_recipe_manager_loads_default_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.recipe_id == "seat_a_black_leather_v1"
    assert recipe.cameras[0].camera_id == "TOP_BACK"
    assert recipe.models["fake_default"].backend == "fake"


def test_recipe_manager_loads_production_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_production_v1")

    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.roi_locator.output_decode == "ultralytics_yolo_seg"
    assert recipe.roi_locator.mask_threshold == 0.5
    assert recipe.roi_locator.input_width == 1024
    assert recipe.roi_locator.input_height == 1024
    assert recipe.roi_locator.input_channels == 3
    assert recipe.registration.method == "ecc"
    assert recipe.models["supervised_defect_onnx"].backend == "onnx"
    assert recipe.models["patchcore_unknown_safety_net"].backend == "patchcore_knn"


def test_recipe_manager_loads_robot_production_yaml() -> None:
    recipe = RecipeManager().load("seat_a_robot_flyshot_production_v1")

    assert [(camera.camera_id, camera.pose_id) for camera in recipe.cameras] == [
        ("EYE_IN_HAND", "T1_BACKREST"),
        ("EYE_IN_HAND", "T2_CUSHION"),
    ]
    assert recipe.model_key_for("EYE_IN_HAND", "full", "T2_CUSHION") == "supervised_defect_onnx"


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
                "cameras": {
                    "TOP": {
                        "model_key": "default",
                        "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"],
                    }
                },
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
                "roi_models": {"full": "detector"},
                "roi_safety_net_models": {"full": "patchcore"},
            },
            "quality": {"required_lights": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"]},
            "cameras": {
                "TOP_BACK": {"calibration_id": "calib/top_back_production_v1"},
                "TOP_CUSHION": {"calibration_id": "calib/top_cushion_production_v1"},
            },
            "thresholds": {
                "scratch": {"ng_score": 0.35, "recheck_score": 0.20},
                "unknown_anomaly": {"ng_score": 0.55, "recheck_score": 0.20},
            },
            "models": {
                "detector": {"backend": "fake", "role": "primary", "class_names": ["scratch"]},
                "patchcore": {
                    "backend": "fake",
                    "model_family": "patchcore",
                    "role": "safety_net",
                    "class_names": ["unknown_anomaly"],
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
    assert recipe.model_key_for("TOP_CUSHION", "full") == "detector"
    assert recipe.safety_net_model_keys_for("TOP_CUSHION", "full") == ("patchcore",)


def test_recipe_parses_onnx_model_io_contract() -> None:
    recipe = recipe_from_dict(
        {
            "recipe_id": "onnx_recipe",
            "sku": "sku",
            "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
            "cameras": {"TOP": {"model_key": "scratch_onnx"}},
            "thresholds": {
                "scratch": {"ng_score": 0.35, "recheck_score": 0.20, "min_area_px": 8},
                "dent": {"ng_score": 0.30, "recheck_score": 0.18, "min_area_px": 20},
            },
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
            "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
            "models": {
                "detector": {
                    "backend": "onnx",
                    "model_path": "model/supervised_defect/seat_defect_detector.onnx",
                    "role": "primary",
                    "class_names": ["scratch"],
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
                "max_mask_area_ratio": 0.80,
                "input_width": 1024,
                "input_height": 1024,
                "input_channels": 3,
            },
            "cameras": {"TOP": {"model_key": "detector"}},
            "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
            "models": {
                "detector": {
                    "backend": "fake",
                    "role": "primary",
                    "class_names": ["scratch"],
                }
            },
        }
    )

    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.roi_locator.model_path == "model/roi_yolo/seat_roi_seg.onnx"
    assert recipe.roi_locator.output_decode == "ultralytics_yolo_seg"
    assert recipe.roi_locator.min_mask_area_px == 32
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
                "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
                "models": {"detector": {"backend": "fake", "role": "primary", "class_names": ["scratch"]}},
            }
        )


def test_recipe_rejects_invalid_roi_locator_input_channels() -> None:
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
                    "input_channels": 2,
                },
                "cameras": {"TOP": {"model_key": "detector"}},
                "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
                "models": {"detector": {"backend": "fake", "role": "primary", "class_names": ["scratch"]}},
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
                    "safety_net_model_key": "unknown_safety_net",
                }
            },
            "thresholds": {
                "scratch": {"ng_score": 0.35, "recheck_score": 0.20, "min_area_px": 8},
                "unknown_anomaly": {"ng_score": 0.55, "recheck_score": 0.20, "min_area_px": 1},
            },
            "models": {
                "default": {"backend": "fake", "role": "primary", "class_names": ["scratch"]},
                "unknown_safety_net": {
                    "backend": "patchcore_knn",
                    "model_family": "patchcore",
                    "role": "safety_net",
                    "class_names": ["unknown_anomaly"],
                    "embedding_backend": "statistical",
                    "memory_bank_path": "model/patchcore/seat_patchcore_bank.json",
                    "faiss_index_path": "model/patchcore/seat_patchcore.faiss",
                },
            },
        }
    )

    assert recipe.models["unknown_safety_net"].faiss_index_path == "model/patchcore/seat_patchcore.faiss"


def test_recipe_parses_fusion_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.fusion.iou_threshold == 0.5
    assert recipe.fusion.class_aware is True
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
            "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
            "models": {"default": {"backend": "fake", "role": "primary", "class_names": ["scratch"]}},
        }
    )

    assert [(camera.camera_id, camera.pose_id) for camera in recipe.cameras] == [
        ("EYE_IN_HAND", "T1_BACKREST"),
        ("EYE_IN_HAND", "T2_CUSHION"),
    ]
    assert recipe.model_key_for("EYE_IN_HAND", "full", "T2_CUSHION") == "default"


def test_default_camera_accepts_dynamic_pose_without_explicit_pose_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")

    assert recipe.accepts_camera_pose("TOP_BACK", "PITCH_15") is True
    assert recipe.pose_uses_default_camera("TOP_BACK", "PITCH_15") is True
    assert recipe.camera("TOP_BACK", "PITCH_15") == recipe.default_camera("TOP_BACK")
    assert recipe.model_key_for("TOP_BACK", "full", "PITCH_15") == "fake_default"


def test_explicit_robot_pose_recipe_rejects_unknown_pose_fallback() -> None:
    recipe = RecipeManager().load("seat_a_robot_flyshot_v1")

    assert recipe.accepts_camera_pose("EYE_IN_HAND", "T3_UNKNOWN") is False
    assert recipe.pose_uses_default_camera("EYE_IN_HAND", "T3_UNKNOWN") is False
    assert recipe.camera("EYE_IN_HAND", "T3_UNKNOWN") is None
    assert recipe.model_key_for("EYE_IN_HAND", "full", "T3_UNKNOWN") == "default"


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
                "thresholds": {"scratch": {"ng_score": 0.35, "recheck_score": 0.2}},
                "models": {"default": {"backend": "fake", "role": "primary", "class_names": ["scratch"]}},
            }
        )


def test_recipe_rejects_model_class_without_explicit_threshold() -> None:
    with pytest.raises(RecipeValidationError, match="models.detector.class_names 缺少显式 thresholds 配置"):
        recipe_from_dict(
            {
                "recipe_id": "missing_class_threshold",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "thresholds": {
                    "scratch": {"ng_score": 0.35, "recheck_score": 0.20, "min_area_px": 8},
                },
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "class_names": ["scratch", "dent"],
                    }
                },
            }
        )


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
                        "input_channels": ["ch0_diffuse", "ch0_diffuse"],
                    }
                },
            }
        )
    with pytest.raises(RecipeValidationError, match="models.detector.class_names 存在重复项"):
        recipe_from_dict(
            {
                "recipe_id": "bad_duplicate_class_names",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "detector"}},
                "models": {
                    "detector": {
                        "backend": "fake",
                        "role": "primary",
                        "class_names": ["scratch", "scratch"],
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
