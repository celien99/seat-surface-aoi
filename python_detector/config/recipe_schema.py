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
    max_registration_error_px: float = 1.5


@dataclass(frozen=True)
class RegistrationConfig:
    base_light_id: str = "POLAR_DIFFUSE"
    base_light_fallback: str = "DIFFUSE"
    fail_policy: str = "RECHECK"


@dataclass(frozen=True)
class CameraRecipe:
    camera_id: str
    enabled: bool = True
    model_key: str = "fake_default"
    roi_template: str = "config/roi/default_roi.yaml"
    calibration_id: str = "calib/simulated_v1"
    base_light_id: str = "POLAR_DIFFUSE"
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    pixel_size_mm: float | None = None


@dataclass(frozen=True)
class ThresholdConfig:
    ng_score: float = 0.35
    recheck_score: float = 0.20
    min_area_px: int = 1


@dataclass(frozen=True)
class ModelConfig:
    backend: str = "fake"
    model_path: str | None = None


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
    thresholds: dict[str, ThresholdConfig] = field(default_factory=dict)
    models: dict[str, ModelConfig] = field(default_factory=lambda: {"default": ModelConfig()})
    trace: TraceConfig = field(default_factory=TraceConfig)

    def camera(self, camera_id: str) -> CameraRecipe | None:
        for camera in self.cameras:
            if camera.camera_id == camera_id:
                return camera
        return None


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
    cameras = _cameras_from_dict(data.get("cameras", {}), light_order, registration.base_light_id)
    thresholds = _thresholds_from_dict(_dict(data.get("thresholds", {}), "thresholds"))
    models = _models_from_dict(_dict(data.get("models", {"default": {"backend": "fake"}}), "models"))
    trace = _trace_from_dict(_dict(data.get("trace", {}), "trace"))

    _validate_lights(light_order, quality.required_lights)
    if not cameras:
        raise RecipeValidationError("至少需要配置一个启用机位")
    return Recipe(
        recipe_id=recipe_id,
        sku=sku,
        light_order=light_order,
        cameras=cameras,
        quality=quality,
        registration=registration,
        thresholds=thresholds,
        models=models,
        trace=trace,
    )


def _quality_from_dict(data: dict[str, Any]) -> QualityConfig:
    return QualityConfig(
        required_lights=_str_tuple(data.get("required_lights", ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")), "quality.required_lights"),
        max_saturation_ratio=_float(data.get("max_saturation_ratio", 0.01), "quality.max_saturation_ratio"),
        min_mean_gray=_float(data.get("min_mean_gray", 20.0), "quality.min_mean_gray"),
        max_mean_gray=_float(data.get("max_mean_gray", 235.0), "quality.max_mean_gray"),
        min_sharpness=_float(data.get("min_sharpness", 1.0), "quality.min_sharpness"),
        max_registration_error_px=_float(data.get("max_registration_error_px", 1.5), "quality.max_registration_error_px"),
    )


def _registration_from_dict(data: dict[str, Any]) -> RegistrationConfig:
    return RegistrationConfig(
        base_light_id=_str(data.get("base_light_id", "POLAR_DIFFUSE"), "registration.base_light_id"),
        base_light_fallback=_str(data.get("base_light_fallback", "DIFFUSE"), "registration.base_light_fallback"),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "registration.fail_policy"),
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
                model_key=_str(raw.get("model_key", "fake_default"), f"cameras.{camera_id}.model_key"),
                roi_template=_str(raw.get("roi_template", "python_detector/config/roi/default_roi.yaml"), f"cameras.{camera_id}.roi_template"),
                calibration_id=_str(raw.get("calibration_id", "calib/simulated_v1"), f"cameras.{camera_id}.calibration_id"),
                base_light_id=_str(raw.get("base_light_id", default_base_light_id), f"cameras.{camera_id}.base_light_id"),
                light_order=light_order,
                pixel_size_mm=_optional_float(raw.get("pixel_size_mm"), f"cameras.{camera_id}.pixel_size_mm"),
            )
        )
    return tuple(cameras)


def _thresholds_from_dict(data: dict[str, Any]) -> dict[str, ThresholdConfig]:
    thresholds: dict[str, ThresholdConfig] = {}
    for class_name, raw in data.items():
        raw = _dict(raw, f"thresholds.{class_name}")
        thresholds[str(class_name)] = ThresholdConfig(
            ng_score=_float(raw.get("ng_score", 0.35), f"thresholds.{class_name}.ng_score"),
            recheck_score=_float(raw.get("recheck_score", 0.20), f"thresholds.{class_name}.recheck_score"),
            min_area_px=_int(raw.get("min_area_px", 1), f"thresholds.{class_name}.min_area_px"),
        )
    return thresholds


def _models_from_dict(data: dict[str, Any]) -> dict[str, ModelConfig]:
    models: dict[str, ModelConfig] = {}
    for model_key, raw in data.items():
        raw = _dict(raw, f"models.{model_key}")
        backend = _str(raw.get("backend", "fake"), f"models.{model_key}.backend")
        if backend not in {"fake", "onnx"}:
            raise RecipeValidationError(f"不支持的模型后端: {backend}")
        models[str(model_key)] = ModelConfig(
            backend=backend,
            model_path=None if raw.get("model_path") in (None, "") else _str(raw.get("model_path"), f"models.{model_key}.model_path"),
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


def _required_str(data: dict[str, Any], key: str) -> str:
    if key not in data:
        raise RecipeValidationError(f"缺少必填字段: {key}")
    return _str(data[key], key)


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeValidationError(f"{name} 必须是非空字符串")
    return value


def _decision(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"RECHECK", "ERROR", "NG"}:
        raise RecipeValidationError(f"{name} 必须是 RECHECK、ERROR 或 NG")
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


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _float(value, name)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int):
        raise RecipeValidationError(f"{name} 必须是整数")
    return value
