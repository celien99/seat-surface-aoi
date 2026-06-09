from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class RecipeValidationError(ValueError):
    """配方校验失败。"""


@dataclass(frozen=True)
class QualityConfig:
    required_lights: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    max_saturation_ratio: float = 0.01
    min_mean_gray: float = 20.0
    max_mean_gray: float = 235.0
    min_sharpness: float = 1.0
    min_motion_gradient: float = 1.0
    max_light_mean_delta: float = 80.0
    max_registration_error_px: float = 1.5
    max_capture_span_us: int = 500_000
    max_exposure_delta_us: int = 200
    max_gain_delta: float = 0.2
    require_monotonic_timestamps: bool = True
    require_unique_frame_indices: bool = True


@dataclass(frozen=True)
class RegistrationConfig:
    base_light_id: str = "POLAR_DIFFUSE"
    base_light_fallback: str = "DIFFUSE"
    fail_policy: str = "RECHECK"


@dataclass(frozen=True)
class CameraRecipe:
    camera_id: str
    enabled: bool = True
    model_key: str = "default"
    safety_net_model_key: str | None = None
    roi_template: str = "config/roi/default_roi.yaml"
    calibration_id: str = "calib/simulated_v1"
    base_light_id: str = "POLAR_DIFFUSE"
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    roi_models: dict[str, str] = field(default_factory=dict)
    roi_safety_net_models: dict[str, str] = field(default_factory=dict)
    pixel_size_mm: float | None = None


@dataclass(frozen=True)
class ThresholdConfig:
    ng_score: float = 0.35
    recheck_score: float = 0.20
    min_area_px: int = 1


@dataclass(frozen=True)
class FusionConfig:
    iou_threshold: float = 0.5
    class_aware: bool = True
    max_candidates_per_roi: int = 16


@dataclass(frozen=True)
class ModelConfig:
    backend: str = "fake"
    model_path: str | None = None
    fake_mode: str = "auto"
    model_family: str = "supervised"
    role: str = "primary"
    input_channels: tuple[str, ...] = (
        "ch0_diffuse",
        "ch1_polar_diffuse",
        "ch2_high_left",
        "ch3_high_right",
        "ch4_high_max_min",
    )
    input_scale: float = 255.0
    class_names: tuple[str, ...] = ("scratch",)
    output_decode: str = "none"
    bbox_format: str = "xyxy_pixel"
    score_threshold: float = 0.0


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool = True
    root_dir: str = "trace"
    save_ok_ratio: float = 0.0
    save_ng: bool = True
    save_recheck: bool = True


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    sku: str
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    cameras: tuple[CameraRecipe, ...] = field(default_factory=tuple)
    quality: QualityConfig = field(default_factory=QualityConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    thresholds: dict[str, ThresholdConfig] = field(default_factory=dict)
    models: dict[str, ModelConfig] = field(default_factory=lambda: {"default": ModelConfig()})
    trace: TraceConfig = field(default_factory=TraceConfig)

    def camera(self, camera_id: str) -> CameraRecipe | None:
        for camera in self.cameras:
            if camera.camera_id == camera_id:
                return camera
        return None

    def model_key_for(self, camera_id: str, roi_name: str) -> str:
        camera = self.camera(camera_id)
        if camera is None:
            return "default"
        return camera.roi_models.get(roi_name, camera.model_key)

    def safety_net_model_keys_for(self, camera_id: str, roi_name: str) -> tuple[str, ...]:
        camera = self.camera(camera_id)
        if camera is None:
            return ()
        model_key = camera.roi_safety_net_models.get(roi_name, camera.safety_net_model_key)
        return () if model_key is None else (model_key,)


class RecipeManager:
    def __init__(self, recipe_dir: str | Path = "python_detector/config") -> None:
        self.recipe_dir = Path(recipe_dir)
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


def recipe_from_dict(data: dict[str, Any]) -> Recipe:
    recipe_id = _required_str(data, "recipe_id")
    sku = _required_str(data, "sku")
    light_order = _str_tuple(data.get("light_order", ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")), "light_order")
    quality = _quality_from_dict(_dict(data.get("quality", {}), "quality"))
    registration = _registration_from_dict(_dict(data.get("registration", {}), "registration"))
    fusion = _fusion_from_dict(_dict(data.get("fusion", {}), "fusion"))
    cameras = _cameras_from_dict(data.get("cameras", {}), light_order, registration.base_light_id)
    thresholds = _thresholds_from_dict(_dict(data.get("thresholds", {}), "thresholds"))
    models = _models_from_dict(_dict(data.get("models", {"default": {"backend": "fake"}}), "models"))
    trace = _trace_from_dict(_dict(data.get("trace", {}), "trace"))

    _validate_lights(light_order, quality.required_lights)
    _validate_model_refs(cameras, models)
    if not cameras:
        raise RecipeValidationError("至少需要配置一个启用机位")
    return Recipe(
        recipe_id=recipe_id,
        sku=sku,
        light_order=light_order,
        cameras=cameras,
        quality=quality,
        registration=registration,
        fusion=fusion,
        thresholds=thresholds,
        models=models,
        trace=trace,
    )


def _quality_from_dict(data: dict[str, Any]) -> QualityConfig:
    min_mean_gray = _gray_value(data.get("min_mean_gray", 20.0), "quality.min_mean_gray")
    max_mean_gray = _gray_value(data.get("max_mean_gray", 235.0), "quality.max_mean_gray")
    if min_mean_gray > max_mean_gray:
        raise RecipeValidationError(
            f"quality.min_mean_gray 不能大于 max_mean_gray: {min_mean_gray} > {max_mean_gray}"
        )
    return QualityConfig(
        required_lights=_str_tuple(data.get("required_lights", ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")), "quality.required_lights"),
        max_saturation_ratio=_ratio(data.get("max_saturation_ratio", 0.01), "quality.max_saturation_ratio"),
        min_mean_gray=min_mean_gray,
        max_mean_gray=max_mean_gray,
        min_sharpness=_non_negative_float(data.get("min_sharpness", 1.0), "quality.min_sharpness"),
        min_motion_gradient=_non_negative_float(data.get("min_motion_gradient", 1.0), "quality.min_motion_gradient"),
        max_light_mean_delta=_non_negative_float(data.get("max_light_mean_delta", 80.0), "quality.max_light_mean_delta"),
        max_registration_error_px=_non_negative_float(data.get("max_registration_error_px", 1.5), "quality.max_registration_error_px"),
        max_capture_span_us=_non_negative_int(data.get("max_capture_span_us", 500_000), "quality.max_capture_span_us"),
        max_exposure_delta_us=_non_negative_int(data.get("max_exposure_delta_us", 200), "quality.max_exposure_delta_us"),
        max_gain_delta=_non_negative_float(data.get("max_gain_delta", 0.2), "quality.max_gain_delta"),
        require_monotonic_timestamps=bool(data.get("require_monotonic_timestamps", True)),
        require_unique_frame_indices=bool(data.get("require_unique_frame_indices", True)),
    )


def _registration_from_dict(data: dict[str, Any]) -> RegistrationConfig:
    return RegistrationConfig(
        base_light_id=_str(data.get("base_light_id", "POLAR_DIFFUSE"), "registration.base_light_id"),
        base_light_fallback=_str(data.get("base_light_fallback", "DIFFUSE"), "registration.base_light_fallback"),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "registration.fail_policy"),
    )


def _fusion_from_dict(data: dict[str, Any]) -> FusionConfig:
    return FusionConfig(
        iou_threshold=_ratio(data.get("iou_threshold", 0.5), "fusion.iou_threshold"),
        class_aware=bool(data.get("class_aware", True)),
        max_candidates_per_roi=_positive_int(data.get("max_candidates_per_roi", 16), "fusion.max_candidates_per_roi"),
    )


def _cameras_from_dict(data: Any, default_light_order: tuple[str, ...], default_base_light_id: str) -> tuple[CameraRecipe, ...]:
    if isinstance(data, list):
        items = {str(item.get("camera_id", "")): item for item in data if isinstance(item, dict)}
    elif isinstance(data, dict):
        items = data
    else:
        raise RecipeValidationError("cameras 必须是字典或列表")
    cameras: list[CameraRecipe] = []
    for camera_id, raw in items.items():
        raw = _dict(raw, f"cameras.{camera_id}")
        enabled = bool(raw.get("enabled", True))
        if not enabled:
            continue
        light_order = _str_tuple(raw.get("light_order", default_light_order), f"cameras.{camera_id}.light_order")
        _validate_lights(light_order, ())
        cameras.append(
            CameraRecipe(
                camera_id=_str(raw.get("camera_id", camera_id), f"cameras.{camera_id}.camera_id"),
                enabled=enabled,
                model_key=_str(raw.get("model_key", "default"), f"cameras.{camera_id}.model_key"),
                safety_net_model_key=_optional_str(raw.get("safety_net_model_key"), f"cameras.{camera_id}.safety_net_model_key"),
                roi_template=_str(raw.get("roi_template", "python_detector/config/roi/default_roi.yaml"), f"cameras.{camera_id}.roi_template"),
                calibration_id=_str(raw.get("calibration_id", "calib/simulated_v1"), f"cameras.{camera_id}.calibration_id"),
                base_light_id=_str(raw.get("base_light_id", default_base_light_id), f"cameras.{camera_id}.base_light_id"),
                light_order=light_order,
                roi_models={str(k): _str(v, f"cameras.{camera_id}.roi_models.{k}") for k, v in _dict(raw.get("roi_models", {}), f"cameras.{camera_id}.roi_models").items()},
                roi_safety_net_models={
                    str(k): _str(v, f"cameras.{camera_id}.roi_safety_net_models.{k}")
                    for k, v in _dict(raw.get("roi_safety_net_models", {}), f"cameras.{camera_id}.roi_safety_net_models").items()
                },
                pixel_size_mm=_optional_float(raw.get("pixel_size_mm"), f"cameras.{camera_id}.pixel_size_mm"),
            )
        )
    return tuple(cameras)


def _thresholds_from_dict(data: dict[str, Any]) -> dict[str, ThresholdConfig]:
    thresholds: dict[str, ThresholdConfig] = {}
    for class_name, raw in data.items():
        raw = _dict(raw, f"thresholds.{class_name}")
        ng_score = _ratio(raw.get("ng_score", 0.35), f"thresholds.{class_name}.ng_score")
        recheck_score = _ratio(raw.get("recheck_score", 0.20), f"thresholds.{class_name}.recheck_score")
        if recheck_score > ng_score:
            raise RecipeValidationError(
                f"thresholds.{class_name}.recheck_score 不能大于 ng_score: {recheck_score} > {ng_score}"
            )
        thresholds[str(class_name)] = ThresholdConfig(
            ng_score=ng_score,
            recheck_score=recheck_score,
            min_area_px=_non_negative_int(raw.get("min_area_px", 1), f"thresholds.{class_name}.min_area_px"),
        )
    return thresholds


def _models_from_dict(data: dict[str, Any]) -> dict[str, ModelConfig]:
    models: dict[str, ModelConfig] = {}
    for model_key, raw in data.items():
        raw = _dict(raw, f"models.{model_key}")
        backend = _str(raw.get("backend", "fake"), f"models.{model_key}.backend")
        if backend not in {"fake", "onnx"}:
            raise RecipeValidationError(f"不支持的模型后端: {backend}")
        model_family = _str(raw.get("model_family", "supervised"), f"models.{model_key}.model_family")
        if model_family not in {"supervised", "patchcore", "efficientad", "yolo_seg", "classifier"}:
            raise RecipeValidationError(f"不支持的模型家族: {model_family}")
        role = _str(raw.get("role", "primary"), f"models.{model_key}.role")
        if role not in {"primary", "safety_net"}:
            raise RecipeValidationError(f"模型角色必须是 primary 或 safety_net: {model_key}")
        if model_family == "patchcore" and role != "safety_net":
            raise RecipeValidationError("PatchCore 只能作为 unknown defect safety_net，不能作为全座椅 primary detector")
        models[str(model_key)] = ModelConfig(
            backend=backend,
            model_path=None if raw.get("model_path") in (None, "") else _str(raw.get("model_path"), f"models.{model_key}.model_path"),
            fake_mode=_str(raw.get("fake_mode", "auto"), f"models.{model_key}.fake_mode"),
            model_family=model_family,
            role=role,
            input_channels=_str_tuple(
                raw.get(
                    "input_channels",
                    ("ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left", "ch3_high_right", "ch4_high_max_min"),
                ),
                f"models.{model_key}.input_channels",
            ),
            input_scale=_positive_float(raw.get("input_scale", 255.0), f"models.{model_key}.input_scale"),
            class_names=_str_tuple(raw.get("class_names", ("scratch",)), f"models.{model_key}.class_names"),
            output_decode=_output_decode(raw.get("output_decode", "none"), f"models.{model_key}.output_decode"),
            bbox_format=_bbox_format(raw.get("bbox_format", "xyxy_pixel"), f"models.{model_key}.bbox_format"),
            score_threshold=_ratio(raw.get("score_threshold", 0.0), f"models.{model_key}.score_threshold"),
        )
    if "default" not in models:
        models["default"] = ModelConfig()
    return models


def _trace_from_dict(data: dict[str, Any]) -> TraceConfig:
    return TraceConfig(
        enabled=bool(data.get("enabled", True)),
        root_dir=_str(data.get("root_dir", "trace"), "trace.root_dir"),
        save_ok_ratio=_float(data.get("save_ok_ratio", 0.0), "trace.save_ok_ratio"),
        save_ng=bool(data.get("save_ng", True)),
        save_recheck=bool(data.get("save_recheck", True)),
    )


def _validate_lights(light_order: tuple[str, ...], required_lights: tuple[str, ...]) -> None:
    if not light_order:
        raise RecipeValidationError("light_order 不能为空")
    duplicated = sorted({light for light in light_order if light_order.count(light) > 1})
    if duplicated:
        raise RecipeValidationError(f"light_order 存在重复光源: {duplicated}")
    missing = [light for light in required_lights if light not in light_order]
    if missing:
        raise RecipeValidationError(f"required_lights 不在 light_order 中: {missing}")


def _validate_model_refs(cameras: tuple[CameraRecipe, ...], models: dict[str, ModelConfig]) -> None:
    for camera in cameras:
        _validate_model_ref(models, camera.model_key, f"机位 {camera.camera_id}", expected_role="primary")
        if camera.safety_net_model_key is not None:
            _validate_model_ref(
                models,
                camera.safety_net_model_key,
                f"机位 {camera.camera_id} safety_net_model_key",
                expected_role="safety_net",
            )
        for roi_name, model_key in camera.roi_models.items():
            _validate_model_ref(models, model_key, f"机位 {camera.camera_id} ROI {roi_name}", expected_role="primary")
        for roi_name, model_key in camera.roi_safety_net_models.items():
            _validate_model_ref(
                models,
                model_key,
                f"机位 {camera.camera_id} ROI {roi_name} safety_net",
                expected_role="safety_net",
            )


def _validate_model_ref(models: dict[str, ModelConfig], model_key: str, location: str, expected_role: str) -> None:
    model = models.get(model_key)
    if model is None:
        raise RecipeValidationError(f"{location} 引用了不存在的模型: {model_key}")
    if model.role != expected_role:
        raise RecipeValidationError(f"{location} 引用的模型角色必须是 {expected_role}: {model_key}")


def _required_str(data: dict[str, Any], key: str) -> str:
    if key not in data:
        raise RecipeValidationError(f"缺少必填字段: {key}")
    return _str(data[key], key)


def _optional_str(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    return _str(value, name)


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeValidationError(f"{name} 必须是非空字符串")
    return value


def _decision(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"RECHECK", "ERROR", "NG"}:
        raise RecipeValidationError(f"{name} 必须是 RECHECK、ERROR 或 NG")
    return value


def _output_decode(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"none", "detection_rows"}:
        raise RecipeValidationError(f"{name} 必须是 none 或 detection_rows")
    return value


def _bbox_format(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"xyxy_pixel", "xyxy_normalized"}:
        raise RecipeValidationError(f"{name} 必须是 xyxy_pixel 或 xyxy_normalized")
    return value


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecipeValidationError(f"{name} 必须是字典")
    return value


def _str_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RecipeValidationError(f"{name} 必须是字符串列表")
    result = tuple(_str(item, name) for item in value)
    return result


def _float(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{name} 必须是数字")
    return float(value)


def _positive_float(value: Any, name: str) -> float:
    result = _float(value, name)
    if result <= 0:
        raise RecipeValidationError(f"{name} 必须大于 0")
    return result


def _non_negative_float(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0:
        raise RecipeValidationError(f"{name} 必须大于等于 0")
    return result


def _ratio(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0 or result > 1:
        raise RecipeValidationError(f"{name} 必须在 [0, 1] 范围内")
    return result


def _gray_value(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0 or result > 255:
        raise RecipeValidationError(f"{name} 必须在 [0, 255] 范围内")
    return result


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _float(value, name)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int):
        raise RecipeValidationError(f"{name} 必须是整数")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _int(value, name)
    if result <= 0:
        raise RecipeValidationError(f"{name} 必须大于 0")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    result = _int(value, name)
    if result < 0:
        raise RecipeValidationError(f"{name} 必须大于等于 0")
    return result
