"""座椅 AOI 配方加载与校验。

从 YAML 文件加载配方并转换为 frozen dataclass。
数据模型定义在 schema_types.py，校验逻辑在 schema_validators.py。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from python_detector.config.schema_types import (
    CameraDefaults,
    CameraRecipe,
    DecisionThresholdConfig,
    FusionConfig,
    ModelConfig,
    QualityConfig,
    Recipe,
    RecipeValidationError,
    RegistrationConfig,
    RoiLocatorConfig,
    TraceConfig,
    V4LightConfig,
)
from python_detector.config.schema_validators import (
    _bbox_format,
    _decision,
    _dict,
    _float,
    _gray_value,
    _non_negative_float,
    _non_negative_int,
    _optional_str,
    _optional_unique_str_tuple,
    _output_decode,
    _positive_float,
    _positive_int,
    _ratio,
    _required_str,
    _str,
    _str_tuple,
    _unique_str_tuple,
    _validate_camera_lights,
    _validate_lights,
    _validate_model_configs,
    _validate_model_refs,
    _validate_registration_lights,
    _validate_roi_locator_light,
    _validate_v4_lights,
)
from python_detector.paths import DEFAULT_CONFIG_DIR


# ---------------------------------------------------------------------------
# RecipeManager
# ---------------------------------------------------------------------------


class RecipeManager:
    def __init__(self, recipe_dir: str | Path | None = None) -> None:
        self.recipe_dir = Path(recipe_dir) if recipe_dir is not None else DEFAULT_CONFIG_DIR
        self._recipes: dict[str, Recipe] = {}
        self._load_recipe_dir()

    def load(self, recipe_id: str) -> Recipe:
        if recipe_id in self._recipes:
            return self._recipes[recipe_id]
        raise RecipeValidationError(f"配方不存在: {recipe_id}")

    def _load_recipe_dir(self) -> None:
        if not self.recipe_dir.exists():
            raise RecipeValidationError(f"配方目录不存在: {self.recipe_dir}")
        for path in sorted(self.recipe_dir.glob("*.yaml")):
            if path.name.endswith(".example.yaml"):
                continue
            recipe = load_recipe_file(path)
            self._recipes[recipe.recipe_id] = recipe


def load_recipe_file(path: str | Path) -> Recipe:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RecipeValidationError(f"配方文件必须是字典: {path}")
    return recipe_from_dict(data)


# ---------------------------------------------------------------------------
# YAML → Recipe 主转换
# ---------------------------------------------------------------------------


def recipe_from_dict(data: dict[str, Any]) -> Recipe:
    if "thresholds" in data:
        raise RecipeValidationError("thresholds 已移除；当前缺陷检测只使用 decision_threshold 单一判定阈值")
    recipe_id = _required_str(data, "recipe_id")
    sku = _required_str(data, "sku")
    light_order = _recipe_light_order_from_dict(data)
    v4_lights = _v4_lights_from_dict(_dict(data.get("v4_lights", {}), "v4_lights"), light_order)
    quality = _quality_from_dict(_dict(data.get("quality", {}), "quality"), light_order)
    roi_locator = _roi_locator_from_dict(_dict(data.get("roi_locator", {}), "roi_locator"))
    registration = _registration_from_dict(
        _dict(data.get("registration", {}), "registration"), quality.required_lights
    )
    fusion = _fusion_from_dict(_dict(data.get("fusion", {}), "fusion"))
    camera_defaults = _camera_defaults_from_dict(
        _dict(data.get("camera_defaults", {}), "camera_defaults"),
        light_order,
        registration.base_light_id,
    )
    cameras = _cameras_from_dict(data.get("cameras", {}), camera_defaults)
    decision_threshold = _decision_threshold_from_dict(
        _dict(data.get("decision_threshold", {}), "decision_threshold")
    )
    models = _models_from_dict(
        _dict(data.get("models", {"default": {"backend": "fake"}}), "models"), light_order
    )
    trace = _trace_from_dict(_dict(data.get("trace", {}), "trace"))

    _validate_lights(light_order, quality.required_lights)
    _validate_v4_lights(light_order, v4_lights)
    _validate_roi_locator_light(v4_lights, roi_locator, light_order)
    _validate_registration_lights(light_order, quality.required_lights, registration)
    _validate_camera_lights(cameras, quality.required_lights, registration)
    _validate_model_refs(cameras, models)
    _validate_model_configs(models)
    if not cameras:
        raise RecipeValidationError("至少需要配置一个启用机位")
    return Recipe(
        recipe_id=recipe_id,
        sku=sku,
        light_order=light_order,
        v4_lights=v4_lights,
        camera_defaults=camera_defaults,
        cameras=cameras,
        quality=quality,
        roi_locator=roi_locator,
        registration=registration,
        fusion=fusion,
        decision_threshold=decision_threshold,
        models=models,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# _*_from_dict 子转换函数
# ---------------------------------------------------------------------------


def _recipe_light_order_from_dict(data: dict[str, Any]) -> tuple[str, ...]:
    raw = data.get("light_order")
    if raw is not None:
        return _str_tuple(raw, "light_order")
    quality = data.get("quality")
    if isinstance(quality, dict) and "required_lights" in quality:
        return _str_tuple(quality["required_lights"], "quality.required_lights")
    camera_defaults = data.get("camera_defaults")
    if isinstance(camera_defaults, dict) and "light_order" in camera_defaults:
        return _str_tuple(camera_defaults["light_order"], "camera_defaults.light_order")
    cameras = data.get("cameras")
    if isinstance(cameras, dict):
        for camera_id, raw_camera in cameras.items():
            if isinstance(raw_camera, dict) and "light_order" in raw_camera:
                return _str_tuple(raw_camera["light_order"], f"cameras.{camera_id}.light_order")
    if isinstance(cameras, list):
        for index, raw_camera in enumerate(cameras):
            if isinstance(raw_camera, dict) and "light_order" in raw_camera:
                return _str_tuple(raw_camera["light_order"], f"cameras[{index}].light_order")
    raise RecipeValidationError("缺少必填字段: light_order")


def _quality_from_dict(data: dict[str, Any], default_required_lights: tuple[str, ...]) -> QualityConfig:
    min_mean_gray = _gray_value(data.get("min_mean_gray", 20.0), "quality.min_mean_gray")
    max_mean_gray = _gray_value(data.get("max_mean_gray", 235.0), "quality.max_mean_gray")
    if min_mean_gray > max_mean_gray:
        raise RecipeValidationError(
            f"quality.min_mean_gray 不能大于 max_mean_gray: {min_mean_gray} > {max_mean_gray}"
        )
    return QualityConfig(
        required_lights=_str_tuple(data.get("required_lights", default_required_lights), "quality.required_lights"),
        max_saturation_ratio=_ratio(data.get("max_saturation_ratio", 0.01), "quality.max_saturation_ratio"),
        max_dark_ratio=_ratio(data.get("max_dark_ratio", 0.01), "quality.max_dark_ratio"),
        min_mean_gray=min_mean_gray,
        max_mean_gray=max_mean_gray,
        min_sharpness=_non_negative_float(data.get("min_sharpness", 1.0), "quality.min_sharpness"),
        min_motion_gradient=_non_negative_float(data.get("min_motion_gradient", 1.0), "quality.min_motion_gradient"),
        max_light_mean_delta=_non_negative_float(
            data.get("max_light_mean_delta", 80.0), "quality.max_light_mean_delta"
        ),
        max_registration_error_px=_non_negative_float(
            data.get("max_registration_error_px", 1.5), "quality.max_registration_error_px"
        ),
        max_capture_span_us=_non_negative_int(
            data.get("max_capture_span_us", 500_000), "quality.max_capture_span_us"
        ),
        max_exposure_delta_us=_non_negative_int(
            data.get("max_exposure_delta_us", 200), "quality.max_exposure_delta_us"
        ),
        max_gain_delta=_non_negative_float(data.get("max_gain_delta", 0.2), "quality.max_gain_delta"),
        require_monotonic_timestamps=bool(data.get("require_monotonic_timestamps", True)),
        require_unique_frame_indices=bool(data.get("require_unique_frame_indices", True)),
        max_pose_delta=_non_negative_float(data.get("max_pose_delta", 1e-4), "quality.max_pose_delta"),
    )


def _v4_lights_from_dict(data: dict[str, Any], light_order: tuple[str, ...]) -> V4LightConfig:
    raw_mapping = data.get("semantic_to_light_id", {"DOME": light_order[0]})
    mapping = {
        _str(key, f"v4_lights.semantic_to_light_id.{key}"): _str(
            value,
            f"v4_lights.semantic_to_light_id.{key}",
        )
        for key, value in _dict(raw_mapping, "v4_lights.semantic_to_light_id").items()
    }
    return V4LightConfig(semantic_to_light_id=mapping)


def _roi_locator_from_dict(data: dict[str, Any]) -> RoiLocatorConfig:
    backend = _str(data.get("backend", "template"), "roi_locator.backend")
    if backend not in {"template", "fake_yolo", "onnx_yolo", "onnx_yolo_seg"}:
        raise RecipeValidationError(f"roi_locator.backend 不支持: {backend}")
    output_decode = _str(data.get("output_decode", "yolo_xyxy_rows"), "roi_locator.output_decode")
    if output_decode not in {"yolo_xyxy_rows", "ultralytics_yolo", "segmentation_rows", "ultralytics_yolo_seg"}:
        raise RecipeValidationError(
            "roi_locator.output_decode 必须是 yolo_xyxy_rows、ultralytics_yolo、"
            "segmentation_rows 或 ultralytics_yolo_seg"
        )
    if backend == "onnx_yolo_seg" and output_decode not in {"segmentation_rows", "ultralytics_yolo_seg"}:
        raise RecipeValidationError(
            "roi_locator.backend=onnx_yolo_seg 必须使用 segmentation_rows 或 ultralytics_yolo_seg"
        )
    if backend in {"fake_yolo", "onnx_yolo"} and output_decode not in {"yolo_xyxy_rows", "ultralytics_yolo"}:
        raise RecipeValidationError("roi_locator bbox 后端必须使用 yolo_xyxy_rows 或 ultralytics_yolo")
    input_channels = _positive_int(data.get("input_channels", 1), "roi_locator.input_channels")
    bbox_format = _bbox_format(data.get("bbox_format", "xyxy_pixel"), "roi_locator.bbox_format")
    return RoiLocatorConfig(
        backend=backend,
        dome_semantic_light=_str(data.get("dome_semantic_light", "DOME"), "roi_locator.dome_semantic_light"),
        model_path=None
        if data.get("model_path") in (None, "")
        else _str(data.get("model_path"), "roi_locator.model_path"),
        min_confidence=_ratio(data.get("min_confidence", 0.5), "roi_locator.min_confidence"),
        max_pose_error_px=_non_negative_float(
            data.get("max_pose_error_px", 4.0), "roi_locator.max_pose_error_px"
        ),
        mask_threshold=_ratio(data.get("mask_threshold", 0.5), "roi_locator.mask_threshold"),
        min_mask_area_px=_positive_int(data.get("min_mask_area_px", 1), "roi_locator.min_mask_area_px"),
        max_mask_area_ratio=_ratio(data.get("max_mask_area_ratio", 1.0), "roi_locator.max_mask_area_ratio"),
        input_width=_non_negative_int(data.get("input_width", 0), "roi_locator.input_width"),
        input_height=_non_negative_int(data.get("input_height", 0), "roi_locator.input_height"),
        input_channels=input_channels,
        output_decode=output_decode,
        bbox_format=bbox_format,
        class_names=_unique_str_tuple(data.get("class_names", ("seat",)), "roi_locator.class_names"),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "roi_locator.fail_policy"),
    )


def _registration_from_dict(
    data: dict[str, Any],
    default_required_lights: tuple[str, ...],
) -> RegistrationConfig:
    method = _str(data.get("method", "fixed_calibration"), "registration.method")
    if method not in {"fixed_calibration", "ecc"}:
        raise RecipeValidationError("registration.method 必须是 fixed_calibration 或 ecc")
    default_base_light_id = default_required_lights[0]
    return RegistrationConfig(
        base_light_id=_str(
            data.get("base_light_id", default_base_light_id), "registration.base_light_id"
        ),
        base_light_fallback=_str(
            data.get("base_light_fallback", default_base_light_id), "registration.base_light_fallback"
        ),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "registration.fail_policy"),
        method=method,
        max_iterations=_positive_int(data.get("max_iterations", 30), "registration.max_iterations"),
        convergence_epsilon=_positive_float(
            data.get("convergence_epsilon", 1e-4), "registration.convergence_epsilon"
        ),
        search_radius_px=_non_negative_int(
            data.get("search_radius_px", 2), "registration.search_radius_px"
        ),
        min_correlation=_ratio(data.get("min_correlation", 0.05), "registration.min_correlation"),
    )


def _fusion_from_dict(data: dict[str, Any]) -> FusionConfig:
    if "class_aware" in data:
        raise RecipeValidationError("fusion.class_aware 已移除；当前缺陷候选统一按 camera/pose/ROI 做融合")
    return FusionConfig(
        iou_threshold=_ratio(data.get("iou_threshold", 0.5), "fusion.iou_threshold"),
        max_candidates_per_roi=_positive_int(
            data.get("max_candidates_per_roi", 16), "fusion.max_candidates_per_roi"
        ),
    )


def _camera_defaults_from_dict(
    data: dict[str, Any],
    default_light_order: tuple[str, ...],
    default_base_light_id: str,
) -> CameraDefaults:
    light_order = _str_tuple(data.get("light_order", default_light_order), "camera_defaults.light_order")
    _validate_lights(light_order, ())
    return CameraDefaults(
        model_key=_str(data.get("model_key", "default"), "camera_defaults.model_key"),
        safety_net_model_key=_optional_str(
            data.get("safety_net_model_key"),
            "camera_defaults.safety_net_model_key",
        ),
        roi_template=_str(
            data.get("roi_template", "python_detector/config/roi/default_roi.yaml"),
            "camera_defaults.roi_template",
        ),
        calibration_id=_str(
            data.get("calibration_id", "calib/simulated_v1"), "camera_defaults.calibration_id"
        ),
        base_light_id=_str(
            data.get("base_light_id", default_base_light_id), "camera_defaults.base_light_id"
        ),
        light_order=light_order,
        roi_models={
            str(k): _str(v, f"camera_defaults.roi_models.{k}")
            for k, v in _dict(data.get("roi_models", {}), "camera_defaults.roi_models").items()
        },
        roi_safety_net_models={
            str(k): _str(v, f"camera_defaults.roi_safety_net_models.{k}")
            for k, v in _dict(
                data.get("roi_safety_net_models", {}), "camera_defaults.roi_safety_net_models"
            ).items()
        },
    )


def _cameras_from_dict(data: Any, camera_defaults: CameraDefaults) -> tuple[CameraRecipe, ...]:
    if isinstance(data, list):
        items = []
        for index, item in enumerate(data):
            raw = _dict(item, f"cameras[{index}]")
            camera_id = _str(raw.get("camera_id"), f"cameras[{index}].camera_id")
            pose_id = raw.get("pose_id", camera_id)
            pose_label = pose_id if isinstance(pose_id, str) and pose_id else camera_id
            items.append((f"{camera_id}/{pose_label}", raw))
    elif isinstance(data, dict):
        items = [(str(camera_id), raw) for camera_id, raw in data.items()]
    else:
        raise RecipeValidationError("cameras 必须是字典或列表")
    cameras: list[CameraRecipe] = []
    for camera_id, raw in items:
        raw = _dict(raw, f"cameras.{camera_id}")
        enabled = bool(raw.get("enabled", True))
        if not enabled:
            continue
        light_order = _str_tuple(
            raw.get("light_order", camera_defaults.light_order), f"cameras.{camera_id}.light_order"
        )
        _validate_lights(light_order, ())
        cameras.append(
            CameraRecipe(
                camera_id=_str(
                    raw.get("camera_id", camera_id), f"cameras.{camera_id}.camera_id"
                ),
                pose_id=_str(
                    raw.get("pose_id", raw.get("camera_id", camera_id)), f"cameras.{camera_id}.pose_id"
                ),
                enabled=enabled,
                model_key=_str(
                    raw.get("model_key", camera_defaults.model_key), f"cameras.{camera_id}.model_key"
                ),
                safety_net_model_key=_optional_str(
                    raw.get("safety_net_model_key", camera_defaults.safety_net_model_key),
                    f"cameras.{camera_id}.safety_net_model_key",
                ),
                roi_template=_str(
                    raw.get("roi_template", camera_defaults.roi_template),
                    f"cameras.{camera_id}.roi_template",
                ),
                calibration_id=_str(
                    raw.get("calibration_id", camera_defaults.calibration_id),
                    f"cameras.{camera_id}.calibration_id",
                ),
                base_light_id=_str(
                    raw.get("base_light_id", camera_defaults.base_light_id),
                    f"cameras.{camera_id}.base_light_id",
                ),
                light_order=light_order,
                roi_models={
                    str(k): _str(v, f"cameras.{camera_id}.roi_models.{k}")
                    for k, v in _dict(
                        raw.get("roi_models", camera_defaults.roi_models),
                        f"cameras.{camera_id}.roi_models",
                    ).items()
                },
                roi_safety_net_models={
                    str(k): _str(v, f"cameras.{camera_id}.roi_safety_net_models.{k}")
                    for k, v in _dict(
                        raw.get("roi_safety_net_models", camera_defaults.roi_safety_net_models),
                        f"cameras.{camera_id}.roi_safety_net_models",
                    ).items()
                },
            )
        )
    return tuple(cameras)


def _decision_threshold_from_dict(data: dict[str, Any]) -> DecisionThresholdConfig:
    ng_score = _ratio(data.get("ng_score", 0.35), "decision_threshold.ng_score")
    recheck_score = _ratio(data.get("recheck_score", 0.20), "decision_threshold.recheck_score")
    if recheck_score > ng_score:
        raise RecipeValidationError(
            f"decision_threshold.recheck_score 不能大于 ng_score: {recheck_score} > {ng_score}"
        )
    return DecisionThresholdConfig(
        ng_score=ng_score,
        recheck_score=recheck_score,
        min_area_px=_non_negative_int(data.get("min_area_px", 1), "decision_threshold.min_area_px"),
        min_aspect_ratio=_non_negative_float(
            data.get("min_aspect_ratio", 0.0), "decision_threshold.min_aspect_ratio"
        ),
        max_aspect_ratio=_non_negative_float(
            data.get("max_aspect_ratio", 0.0), "decision_threshold.max_aspect_ratio"
        ),
    )


def _models_from_dict(data: dict[str, Any], light_order: tuple[str, ...]) -> dict[str, ModelConfig]:
    models: dict[str, ModelConfig] = {}
    for model_key, raw in data.items():
        raw = _dict(raw, f"models.{model_key}")
        if "class_names" in raw:
            raise RecipeValidationError(
                f"models.{model_key}.class_names 已移除；当前缺陷检测统一输出固定 defect 结果"
            )
        backend = _str(raw.get("backend", "fake"), f"models.{model_key}.backend")
        if backend not in {"fake", "onnx", "patchcore_knn"}:
            raise RecipeValidationError(f"不支持的模型后端: {backend}")
        model_family = _str(raw.get("model_family", "supervised"), f"models.{model_key}.model_family")
        if model_family not in {"supervised", "patchcore", "efficientad", "yolo_seg"}:
            raise RecipeValidationError(f"不支持的模型家族: {model_family}")
        role = _str(raw.get("role", "primary"), f"models.{model_key}.role")
        if role not in {"primary", "safety_net"}:
            raise RecipeValidationError(f"模型角色必须是 primary 或 safety_net: {model_key}")
        if backend == "patchcore_knn" and model_family != "patchcore":
            raise RecipeValidationError(
                f"models.{model_key}.backend=patchcore_knn 必须配置 model_family=patchcore"
            )
        fake_mode = _str(raw.get("fake_mode", "auto"), f"models.{model_key}.fake_mode")
        if fake_mode not in {"auto", "ok", "ng", "recheck"}:
            raise RecipeValidationError(
                f"models.{model_key}.fake_mode 必须是 auto、ok、ng 或 recheck"
            )
        input_channels = _unique_str_tuple(
            raw.get("input_channels", _default_model_input_channels(light_order)),
            f"models.{model_key}.input_channels",
        )
        embedding_backend = _str(
            raw.get("embedding_backend", "none"), f"models.{model_key}.embedding_backend"
        )
        if embedding_backend not in {"none", "statistical", "onnx_wideresnet50"}:
            raise RecipeValidationError(
                f"models.{model_key}.embedding_backend 必须是 none、statistical 或 onnx_wideresnet50"
            )
        coreset_ratio = _ratio(raw.get("coreset_ratio", 1.0), f"models.{model_key}.coreset_ratio")
        if coreset_ratio <= 0.0:
            raise RecipeValidationError(f"models.{model_key}.coreset_ratio 必须大于 0")
        models[str(model_key)] = ModelConfig(
            backend=backend,
            model_path=None
            if raw.get("model_path") in (None, "")
            else _str(raw.get("model_path"), f"models.{model_key}.model_path"),
            fake_mode=fake_mode,
            model_family=model_family,
            role=role,
            input_channels=input_channels,
            input_scale=_positive_float(
                raw.get("input_scale", 255.0), f"models.{model_key}.input_scale"
            ),
            output_decode=_output_decode(
                raw.get("output_decode", "none"), f"models.{model_key}.output_decode"
            ),
            bbox_format=_bbox_format(
                raw.get("bbox_format", "xyxy_pixel"), f"models.{model_key}.bbox_format"
            ),
            score_threshold=_ratio(
                raw.get("score_threshold", 0.0), f"models.{model_key}.score_threshold"
            ),
            embedding_backend=embedding_backend,
            embedding_model_path=None
            if raw.get("embedding_model_path") in (None, "")
            else _str(raw.get("embedding_model_path"), f"models.{model_key}.embedding_model_path"),
            embedding_version=_str(
                raw.get("embedding_version", "none"), f"models.{model_key}.embedding_version"
            ),
            embedding_dim=_positive_int(
                raw.get("embedding_dim", 10), f"models.{model_key}.embedding_dim"
            ),
            embedding_layers=_optional_unique_str_tuple(
                raw.get("embedding_layers", ()),
                f"models.{model_key}.embedding_layers",
            ),
            pca_path=None
            if raw.get("pca_path") in (None, "")
            else _str(raw.get("pca_path"), f"models.{model_key}.pca_path"),
            pca_version=None
            if raw.get("pca_version") in (None, "")
            else _str(raw.get("pca_version"), f"models.{model_key}.pca_version"),
            memory_bank_path=None
            if raw.get("memory_bank_path") in (None, "")
            else _str(raw.get("memory_bank_path"), f"models.{model_key}.memory_bank_path"),
            faiss_index_path=None
            if raw.get("faiss_index_path") in (None, "")
            else _str(raw.get("faiss_index_path"), f"models.{model_key}.faiss_index_path"),
            coreset_ratio=coreset_ratio,
            knn_k=_positive_int(raw.get("knn_k", 1), f"models.{model_key}.knn_k"),
            anomaly_score_scale=_non_negative_float(
                raw.get("anomaly_score_scale", 0.0),
                f"models.{model_key}.anomaly_score_scale",
            ),
            spatial_mode=bool(raw.get("spatial_mode", False)),
            spatial_layers=_optional_unique_str_tuple(
                raw.get("spatial_layers", ()),
                f"models.{model_key}.spatial_layers",
            ),
            spatial_upsample_height=_positive_int(
                raw.get("spatial_upsample_height", 32),
                f"models.{model_key}.spatial_upsample_height",
            ),
            spatial_upsample_width=_positive_int(
                raw.get("spatial_upsample_width", 32),
                f"models.{model_key}.spatial_upsample_width",
            ),
            anomaly_binarize_min_ratio=_ratio(
                raw.get("anomaly_binarize_min_ratio", 0.5),
                f"models.{model_key}.anomaly_binarize_min_ratio",
            ),
            anomaly_binarize_relative=_ratio(
                raw.get("anomaly_binarize_relative", 0.3),
                f"models.{model_key}.anomaly_binarize_relative",
            ),
        )
    if "default" not in models:
        models["default"] = ModelConfig(input_channels=_default_model_input_channels(light_order))
    return models


def _default_model_input_channels(light_order: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"light:{light_id}" for light_id in light_order)


def _trace_from_dict(data: dict[str, Any]) -> TraceConfig:
    return TraceConfig(
        enabled=bool(data.get("enabled", True)),
        root_dir=_str(data.get("root_dir", "trace"), "trace.root_dir"),
        save_ok_ratio=_float(data.get("save_ok_ratio", 0.0), "trace.save_ok_ratio"),
        save_ng=bool(data.get("save_ng", True)),
        save_recheck=bool(data.get("save_recheck", True)),
    )
